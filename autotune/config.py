"""Shared Auto-Tune configuration helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "autotune.json"

VALID_KEYS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
VALID_SCALES = ("major", "minor", "chromatic")


@dataclass
class AutotuneParams:
    enabled: bool = True
    key: str = "C"
    scale: str = "major"
    correction_speed: float = 0.35
    wet_dry_mix: float = 1.0

    def normalize(self) -> "AutotuneParams":
        flat_map = {"DB": "C#", "EB": "D#", "GB": "F#", "AB": "G#", "BB": "A#"}
        key = flat_map.get(self.key.strip().upper(), self.key.strip().upper())
        if key not in VALID_KEYS:
            raise ValueError(f"Invalid key '{self.key}'. Expected one of {VALID_KEYS}")
        scale = self.scale.strip().lower()
        if scale not in VALID_SCALES:
            raise ValueError(f"Invalid scale '{self.scale}'. Expected one of {VALID_SCALES}")
        speed = float(self.correction_speed)
        mix = float(self.wet_dry_mix)
        if not 0.0 <= speed <= 1.0:
            raise ValueError("correction_speed must be between 0.0 and 1.0")
        if not 0.0 <= mix <= 1.0:
            raise ValueError("wet_dry_mix must be between 0.0 and 1.0")
        return AutotuneParams(
            enabled=bool(self.enabled),
            key=key,
            scale=scale,
            correction_speed=speed,
            wet_dry_mix=mix,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AutotuneParams":
        return cls(
            enabled=bool(data.get("enabled", True)),
            key=str(data.get("key", "C")),
            scale=str(data.get("scale", "major")),
            correction_speed=float(data.get("correction_speed", 0.35)),
            wet_dry_mix=float(data.get("wet_dry_mix", 1.0)),
        ).normalize()


VALID_ENGINES = ("auto", "vst3", "native")


@dataclass
class AutotuneConfig:
    zmq_bind: str = "tcp://127.0.0.1:5555"
    zmq_connect: str = "tcp://127.0.0.1:5555"
    zmq_topic: str = "autotune"
    sample_rate: int = 48000
    buffer_size: int = 256
    input_device: str | None = None
    output_device: str | None = None
    plugin_path: str = "plugins/QPitch.vst3"
    plugin_profile: str = "qpitch"
    plugin_dir: str = "plugins"
    engine: str = "auto"
    defaults: AutotuneParams = field(default_factory=AutotuneParams)

    def to_dict(self) -> dict[str, Any]:
        return {
            "zmq": {
                "bind": self.zmq_bind,
                "connect": self.zmq_connect,
                "topic": self.zmq_topic,
            },
            "audio": {
                "sample_rate": self.sample_rate,
                "buffer_size": self.buffer_size,
                "input_device": self.input_device,
                "output_device": self.output_device,
            },
            "plugin": {
                "path": self.plugin_path,
                "profile": self.plugin_profile,
                "dir": self.plugin_dir,
            },
            "engine": self.engine,
            "defaults": self.defaults.to_dict(),
        }


def load_config(path: str | Path | None = None) -> AutotuneConfig:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)

    zmq = raw.get("zmq", {})
    audio = raw.get("audio", {})
    plugin = raw.get("plugin", {})
    defaults = AutotuneParams.from_dict(raw.get("defaults", {}))

    engine = str(raw.get("engine", "auto")).lower()
    if engine not in VALID_ENGINES:
        raise ValueError(f"Invalid engine '{engine}'. Expected one of {VALID_ENGINES}")

    return AutotuneConfig(
        zmq_bind=str(zmq.get("bind", "tcp://127.0.0.1:5555")),
        zmq_connect=str(zmq.get("connect", "tcp://127.0.0.1:5555")),
        zmq_topic=str(zmq.get("topic", "autotune")),
        sample_rate=int(audio.get("sample_rate", 48000)),
        buffer_size=int(audio.get("buffer_size", 256)),
        input_device=audio.get("input_device"),
        output_device=audio.get("output_device"),
        plugin_path=str(plugin.get("path", "plugins/QPitch.vst3")),
        plugin_profile=str(plugin.get("profile", "qpitch")),
        plugin_dir=str(plugin.get("dir", "plugins")),
        engine=engine,
        defaults=defaults,
    )


def merge_params(base: AutotuneParams, updates: dict[str, Any]) -> AutotuneParams:
    merged = deepcopy(base.to_dict())
    merged.update({k: v for k, v in updates.items() if k in merged})
    return AutotuneParams.from_dict(merged)
