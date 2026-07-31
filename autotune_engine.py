#!/usr/bin/env python3
"""Real-time Auto-Tune DSP daemon.

Captures USB-C ADC / USB microphone input via pedalboard, applies pitch
correction, and accepts live parameter updates over ZeroMQ without restarting
the audio stream.

Two engines are available:

  vst3    Native VST3 plugin hosted inside the realtime callback (lowest
          latency). Requires a plugin built for this OS and CPU architecture.
  native  Plugin-free correction using autocorrelation pitch detection plus
          a granular dual-tap shifter. Works on any architecture (Linux or
          macOS), at somewhat higher latency.

`engine: auto` (the default) prefers vst3 and falls back to native.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autotune.config import AutotuneConfig, AutotuneParams, load_config, merge_params
from autotune.plugin_map import apply_params, list_plugin_parameters
from autotune.plugins import (
    CATALOG,
    DiscoveredPlugin,
    discover_plugins,
    host_architecture,
    host_os,
    select_plugin,
)

if TYPE_CHECKING:
    from autotune.zmq_client import AutotuneZmqSubscriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("autotune_engine")

ROOT = Path(__file__).resolve().parent


def _linear_to_db(linear: float) -> float:
    """Convert 0..1 linear gain to pedalboard Gain gain_db."""
    import math

    gain = max(0.0, min(1.0, float(linear)))
    if gain <= 0.0:
        return -100.0
    return 20.0 * math.log10(gain)


class AutotuneEngine:
    """Live pitch-correction engine with VST3 and plugin-free backends."""

    def __init__(self, config: AutotuneConfig) -> None:
        self.config = config
        self.params = config.defaults
        self.active_engine: str | None = None
        self._plugin = None
        self._gain = None
        self._stream = None
        self._native = None
        self._lock = threading.RLock()
        self._running = threading.Event()
        self._subscriber: AutotuneZmqSubscriber | None = None

    # ------------------------------------------------------------------
    # Plugin selection
    # ------------------------------------------------------------------
    def _resolve(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else ROOT / path

    def choose_plugin(self) -> DiscoveredPlugin | None:
        plugins_dir = self._resolve(self.config.plugin_dir)
        preferred = self._resolve(self.config.plugin_path) if self.config.plugin_path else None
        return select_plugin(plugins_dir, preferred)

    def _load_plugin(self, chosen: DiscoveredPlugin):
        from pedalboard import load_plugin

        logger.info("Loading VST3 plugin: %s", chosen.path)
        plugin = load_plugin(str(chosen.path))
        names = list_plugin_parameters(plugin)
        if names:
            logger.info("Plugin exposes %d parameters", len(names))
            logger.debug("Parameters: %s", ", ".join(names))
        profile = chosen.profile or self.config.plugin_profile
        apply_params(plugin, self.params, profile)
        return plugin

    # ------------------------------------------------------------------
    # Parameter updates
    # ------------------------------------------------------------------
    def _latency_ms(self, multiplier: float = 2.0) -> float:
        return (multiplier * self.config.buffer_size / float(self.config.sample_rate)) * 1000.0

    def _on_zmq_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("type", "set_params")
        if msg_type == "get_params":
            logger.info("Current params: %s", self.params.to_dict())
            return
        if msg_type != "set_params":
            logger.warning("Unknown ZMQ message type: %s", msg_type)
            return

        updates = message.get("params") or message
        with self._lock:
            try:
                new_params = merge_params(self.params, updates)
            except ValueError as exc:
                logger.warning("Rejected autotune update: %s", exc)
                return
            self.params = new_params
            if self._plugin is not None:
                apply_params(self._plugin, new_params, self.config.plugin_profile)
            if self._gain is not None:
                self._gain.gain_db = _linear_to_db(new_params.mic_volume)
            if self._native is not None:
                self._native.params = new_params
                logger.info("Native Auto-Tune params: %s", new_params.to_dict())

    def _current_params(self) -> AutotuneParams:
        with self._lock:
            return self.params

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------
    def start(self) -> None:
        from autotune.zmq_client import AutotuneZmqSubscriber

        chosen = self.choose_plugin()
        mode = self._decide_engine(chosen)

        self._subscriber = AutotuneZmqSubscriber(
            connect_addr=self.config.zmq_connect,
            topic=self.config.zmq_topic,
            on_message=self._on_zmq_message,
        )
        self._subscriber.start()

        try:
            if mode == "vst3":
                self._start_vst3(chosen)
            else:
                self._start_native()
        finally:
            self.stop()

    def _decide_engine(self, chosen: DiscoveredPlugin | None) -> str:
        arch = host_architecture()
        requested = self.config.engine

        if requested == "native":
            logger.info("Engine: native (requested). Host architecture: %s", arch)
            return "native"

        if chosen is None:
            message = f"No VST3 plugin found in {self._resolve(self.config.plugin_dir)}"
            if requested == "vst3":
                raise FileNotFoundError(
                    f"{message}. Run scripts/install_plugins.sh, or set engine to 'native'."
                )
            logger.warning("%s; falling back to the native engine.", message)
            return "native"

        reason = chosen.incompatibility_reason(arch)
        if reason:
            if requested == "vst3":
                raise RuntimeError(
                    f"{reason}\nInstall a matching VST3 with scripts/install_plugins.sh, "
                    "or use engine 'native' / 'auto' on this host."
                )
            logger.warning("%s Falling back to the native engine.", reason)
            return "native"

        logger.info("Engine: vst3 using %s (%s/%s)", chosen.name, host_os(), arch)
        self.config.plugin_profile = chosen.profile or self.config.plugin_profile
        return "vst3"

    def _start_vst3(self, chosen: DiscoveredPlugin) -> None:
        from pedalboard import Gain, Pedalboard
        from pedalboard.io import AudioStream

        estimated = self._latency_ms()
        self._log_latency(estimated)

        self._plugin = self._load_plugin(chosen)
        self._gain = Gain(gain_db=_linear_to_db(self.params.mic_volume))

        input_device = self.config.input_device or AudioStream.default_input_device_name
        output_device = self.config.output_device or AudioStream.default_output_device_name
        logger.info("Audio devices: input=%r output=%r", input_device, output_device)

        # allow_feedback is required on Linux setups where the same interface
        # is both capture and playback.
        self._stream = AudioStream(
            input_device_name=input_device,
            output_device_name=output_device,
            sample_rate=float(self.config.sample_rate),
            buffer_size=int(self.config.buffer_size),
            allow_feedback=True,
        )
        self._stream.plugins = Pedalboard([self._plugin, self._gain])
        self.active_engine = "vst3"
        self._running.set()

        logger.info("Auto-Tune engine running (vst3). Ctrl+C to stop.")
        with self._stream:
            while self._running.is_set():
                time.sleep(0.25)

    def _start_native(self) -> None:
        from autotune.native_dsp import NativeAutotune, run_native_stream

        block = max(int(self.config.buffer_size), 256)
        # Native latency is the two stream buffers plus the shifter's grain,
        # which tracks the singer's pitch (~9 ms at 440 Hz, ~44 ms at 90 Hz).
        self._log_latency(self._latency_ms(3.0), engine="native")
        logger.info(
            "Native engine adds one pitch-synchronous grain of delay "
            "(roughly 10-45 ms depending on vocal range)."
        )

        self._native = NativeAutotune(self.config.sample_rate, self._current_params())
        self.active_engine = "native"
        self._running.set()

        def get_params() -> AutotuneParams:
            params = self._current_params()
            self._native.params = params
            return params

        logger.info("Auto-Tune engine running (native). Ctrl+C to stop.")
        run_native_stream(
            get_params=get_params,
            sample_rate=self.config.sample_rate,
            block_frames=block,
            input_device=self.config.input_device,
            output_device=self.config.output_device,
            should_stop=lambda: not self._running.is_set(),
        )

    def _log_latency(self, estimated: float, engine: str = "vst3") -> None:
        if estimated > 15.0:
            logger.warning(
                "Estimated round-trip latency %.1f ms exceeds the 15 ms target "
                "(engine=%s, buffer=%d @ %d Hz). Lower buffer_size if dropouts allow.",
                estimated,
                engine,
                self.config.buffer_size,
                self.config.sample_rate,
            )
        else:
            logger.info(
                "Estimated round-trip latency ~%.1f ms (engine=%s, buffer=%d @ %d Hz)",
                estimated,
                engine,
                self.config.buffer_size,
                self.config.sample_rate,
            )

    def stop(self) -> None:
        self._running.clear()
        if self._subscriber is not None:
            self._subscriber.stop()
            self._subscriber = None
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        self._native = None
        logger.info("Auto-Tune engine stopped.")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="jPiKaraoke real-time Auto-Tune DSP daemon")
    parser.add_argument("--config", default=str(ROOT / "config" / "autotune.json"))
    parser.add_argument(
        "--input-device",
        default=None,
        help="PortAudio input device name (ALSA/PipeWire/CoreAudio)",
    )
    parser.add_argument(
        "--output-device",
        default=None,
        help="PortAudio output device name (ALSA/PipeWire/CoreAudio)",
    )
    parser.add_argument("--plugin", default=None, help="Path to a VST3 pitch-correction plugin")
    parser.add_argument(
        "--plugin-profile",
        default=None,
        choices=("graillon", "gsnap", "mautopitch", "qpitch", "generic"),
    )
    parser.add_argument("--engine", default=None, choices=("auto", "vst3", "native"))
    parser.add_argument("--buffer-size", type=int, default=None, help="Audio block frames")
    parser.add_argument("--sample-rate", type=int, default=None, help="Sample rate in Hz")
    parser.add_argument("--zmq", default=None, help="ZMQ connect address, e.g. tcp://127.0.0.1:5555")
    parser.add_argument("--list-devices", action="store_true", help="List audio devices and exit")
    parser.add_argument(
        "--detect-usb-input",
        action="store_true",
        help="Print the first USB-matching PortAudio input device name and exit",
    )
    parser.add_argument("--list-plugins", action="store_true", help="List installed plugins and exit")
    parser.add_argument("--dry-run", action="store_true", help="Resolve config and plugins, then exit")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args(argv)


def list_devices() -> None:
    from pedalboard.io import AudioStream

    print("Default input: ", AudioStream.default_input_device_name)
    print("Default output:", AudioStream.default_output_device_name)
    print("\nInput devices:")
    for name in AudioStream.input_device_names:
        print(f"  - {name}")
    print("\nOutput devices:")
    for name in AudioStream.output_device_names:
        print(f"  - {name}")
    if host_os() == "linux":
        print("\nTip: `arecord -l` or `pactl list short sources` can also help identify USB ADCs.")
    else:
        print("\nTip: set AUTOTUNE_INPUT_DEVICE to a PortAudio name from the list above.")


def detect_usb_input() -> str:
    """Return the first PortAudio input device whose name looks like a USB ADC/mic."""
    from pedalboard.io import AudioStream

    for name in AudioStream.input_device_names:
        if "usb" in name.lower():
            return name
    return ""


def list_plugins(config: AutotuneConfig) -> None:
    arch = host_architecture()
    os_name = host_os()
    plugins_dir = Path(config.plugin_dir)
    if not plugins_dir.is_absolute():
        plugins_dir = ROOT / plugins_dir

    print(f"Host: {os_name}/{arch}")
    print(f"Plugin directory:  {plugins_dir}\n")

    discovered = discover_plugins(plugins_dir)
    if not discovered:
        print("No VST3 bundles installed. Run scripts/install_plugins.sh")
    else:
        print("Installed:")
        for plugin in discovered:
            arches = ", ".join(sorted(plugin.architectures)) or "unknown"
            plats = ", ".join(sorted(plugin.platforms)) or "unknown"
            status = "OK" if plugin.compatible_with(arch, os_name) else "INCOMPATIBLE"
            print(f"  [{status}] {plugin.path.name}")
            print(f"      profile={plugin.profile}  os: {plats}  arch: {arches}")

    print("\nKnown plugins:")
    for spec in CATALOG:
        marker = "*" if spec.slug == "qpitch" else " "
        print(f" {marker} {spec.slug:<11} {spec.name}")
        print(f"      arch: {', '.join(spec.architectures)}  {spec.url}")
        if spec.notes:
            print(f"      {spec.notes}")
    print("\n* = default VST3 path")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.list_devices:
        list_devices()
        return 0

    if args.detect_usb_input:
        print(detect_usb_input())
        return 0

    config = load_config(args.config)
    if args.input_device:
        config.input_device = args.input_device
    if args.output_device:
        config.output_device = args.output_device
    if args.plugin:
        config.plugin_path = args.plugin
    if args.plugin_profile:
        config.plugin_profile = args.plugin_profile
    if args.engine:
        config.engine = args.engine
    if args.buffer_size:
        config.buffer_size = args.buffer_size
    if args.sample_rate:
        config.sample_rate = args.sample_rate
    if args.zmq:
        config.zmq_connect = args.zmq

    if args.list_plugins:
        list_plugins(config)
        return 0

    engine = AutotuneEngine(config)

    def _handle_signal(signum, _frame):
        logger.info("Received signal %s", signum)
        engine.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    if args.dry_run:
        chosen = engine.choose_plugin()
        logger.info("Host: %s/%s", host_os(), host_architecture())
        logger.info("Config: %s", config.to_dict())
        if chosen is None:
            logger.info("No VST3 plugin discovered; engine would use: native")
        else:
            reason = chosen.incompatibility_reason()
            logger.info("Selected plugin: %s (profile=%s)", chosen.path.name, chosen.profile)
            logger.info("Engine would use: %s", "native" if reason else "vst3")
            if reason:
                logger.warning("%s", reason)
        return 0

    try:
        engine.start()
    except (FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 2
    except Exception:
        logger.exception("Auto-Tune engine failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
