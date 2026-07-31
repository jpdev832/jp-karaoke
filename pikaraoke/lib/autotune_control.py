"""Shared Auto-Tune control for JP Karaoke (ZMQ + live state).

Room defaults come from config/autotune.json. Per-song presets apply only while
that song plays; idle / songs without Auto-Tune restore disabled room defaults.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path
from typing import Any

from autotune.config import (
    VALID_KEYS,
    VALID_SCALES,
    AutotuneConfig,
    AutotuneParams,
    load_config,
    merge_params,
)
from autotune.plugins import host_architecture, host_os, select_plugin
from autotune.zmq_client import AutotuneZmqPublisher

logger = logging.getLogger("jp_karaoke.autotune")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "autotune.json"

_config: AutotuneConfig | None = None
_state: AutotuneParams | None = None
_publisher: AutotuneZmqPublisher | None = None
_socketio = None
_active_song_autotune: dict[str, Any] | None = None


def config_path() -> Path:
    return Path(os.environ.get("AUTOTUNE_CONFIG", str(DEFAULT_CONFIG_PATH)))


def get_config() -> AutotuneConfig:
    global _config
    if _config is None:
        _config = load_config(config_path())
    return _config


def get_state() -> AutotuneParams:
    global _state
    if _state is None:
        _state = get_config().defaults
    return _state


def set_socketio(socketio) -> None:
    global _socketio
    _socketio = socketio


def init_zmq() -> AutotuneZmqPublisher:
    global _publisher
    if _publisher is None:
        cfg = get_config()
        _publisher = AutotuneZmqPublisher(
            connect_addr=cfg.zmq_bind,
            topic=cfg.zmq_topic,
        )
        # Start idle: Auto-Tune off until a song opts in (or admin enables live).
        idle = room_idle_params()
        _publisher.publish(idle)
        global _state
        _state = idle
        atexit.register(shutdown_zmq)
    return _publisher


def shutdown_zmq() -> None:
    global _publisher
    if _publisher is not None:
        _publisher.close()
        _publisher = None


def room_idle_params() -> AutotuneParams:
    """Defaults from config, forced disabled (between songs / no opt-in).

    Preserves the live room mic_volume so guest mix is not reset between songs.
    """
    base = get_config().defaults.to_dict()
    base["enabled"] = False
    if _state is not None:
        base["mic_volume"] = _state.mic_volume
    return AutotuneParams.from_dict(base)


def engine_info() -> dict[str, object]:
    cfg = get_config()
    arch = host_architecture()
    os_name = host_os()
    plugins_dir = Path(cfg.plugin_dir)
    if not plugins_dir.is_absolute():
        plugins_dir = REPO_ROOT / plugins_dir
    preferred = Path(cfg.plugin_path)
    if not preferred.is_absolute():
        preferred = REPO_ROOT / preferred

    chosen = select_plugin(plugins_dir, preferred)
    if cfg.engine == "native" or chosen is None or not chosen.compatible_with(arch, os_name):
        backend = "native"
    else:
        backend = "vst3"

    return {
        "configured": cfg.engine,
        "backend": backend,
        "os": os_name,
        "architecture": arch,
        "plugin": chosen.path.name if chosen else None,
        "plugin_usable": bool(chosen and chosen.compatible_with(arch, os_name)),
        "plugin_note": chosen.incompatibility_reason(arch, os_name) if chosen else None,
    }


def broadcast(params: AutotuneParams) -> None:
    global _state
    _state = params
    init_zmq().publish(params)
    payload = params.to_dict()
    if _socketio is not None:
        _socketio.emit("autotune_update", payload, namespace="/")
    logger.info("Auto-Tune state updated: %s", payload)


def update_live(updates: dict[str, Any]) -> AutotuneParams:
    """Merge updates into current live state (admin / Socket.IO override)."""
    next_state = merge_params(get_state(), updates)
    broadcast(next_state)
    if "mic_volume" in updates:
        _mirror_sound_manager_volume(next_state.mic_volume)
    return next_state


def _mirror_sound_manager_volume(volume: float) -> None:
    """Best-effort: mirror mic_volume onto active SoundManager passthrough mics."""
    try:
        from flask import current_app, has_app_context

        if not has_app_context():
            return
        k = current_app.config.get("KARAOKE_INSTANCE")
        if k is None or not getattr(k, "sound_manager", None):
            return
        sm = k.sound_manager
        active = getattr(sm, "_active_mics", {}) or {}
        for device_id in list(active.keys()):
            try:
                sm.update_volume(device_id, float(volume))
            except Exception as exc:
                logger.debug("SoundManager volume mirror failed for %s: %s", device_id, exc)
    except Exception as exc:
        logger.debug("SoundManager mic_volume mirror skipped: %s", exc)


def parse_queue_autotune(raw: Any) -> dict[str, Any] | None:
    """Validate optional enqueue autotune payload. None = off for this song."""
    if raw is None or raw is False:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw.lower() in ("0", "false", "off", "none"):
            return None
        import json

        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid autotune JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("autotune must be an object or omitted")
    if not raw.get("enabled", False):
        return None
    params = AutotuneParams.from_dict(
        {**get_config().defaults.to_dict(), **raw, "enabled": True}
    )
    # Per-song presets do not own room mic mix.
    data = params.to_dict()
    data.pop("mic_volume", None)
    return data


def apply_for_song(autotune: dict[str, Any] | None) -> None:
    """Apply per-song Auto-Tune when a track starts. None disables until next song."""
    global _active_song_autotune
    mic_volume = get_state().mic_volume
    _active_song_autotune = autotune
    if autotune:
        payload = {**autotune, "mic_volume": mic_volume}
        broadcast(AutotuneParams.from_dict(payload))
    else:
        broadcast(room_idle_params())


def restore_idle() -> None:
    """Restore disabled room defaults after a song ends (keeps mic_volume)."""
    global _active_song_autotune
    _active_song_autotune = None
    broadcast(room_idle_params())


def meta_payload() -> dict[str, Any]:
    cfg = get_config()
    return {
        "keys": list(VALID_KEYS),
        "scales": list(VALID_SCALES),
        "zmq": cfg.zmq_bind,
        "plugin_profile": cfg.plugin_profile,
        "engine": engine_info(),
        "active_song": _active_song_autotune,
    }
