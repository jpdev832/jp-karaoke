#!/usr/bin/env python3
"""jPiKaraoke — PiKaraoke fork with real-time Auto-Tune controls.

Provides Flask REST + Socket.IO endpoints that broadcast Auto-Tune parameter
changes to the DSP daemon (`autotune_engine.py`) over ZeroMQ.
"""

from __future__ import annotations

import atexit
import logging
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

from autotune.config import AutotuneParams, VALID_KEYS, VALID_SCALES, load_config, merge_params
from autotune.plugins import host_architecture, host_os, select_plugin
from autotune.zmq_client import AutotuneZmqPublisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("jpikaraoke")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = os.environ.get("AUTOTUNE_CONFIG", str(ROOT / "config" / "autotune.json"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("JPIKARAOKE_SECRET", "jpikaraoke-dev-secret")
app.config["TEMPLATES_AUTO_RELOAD"] = True

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

autotune_config = load_config(CONFIG_PATH)
autotune_state = autotune_config.defaults
zmq_publisher: AutotuneZmqPublisher | None = None


def init_zmq() -> AutotuneZmqPublisher:
    global zmq_publisher
    if zmq_publisher is None:
        zmq_publisher = AutotuneZmqPublisher(
            connect_addr=autotune_config.zmq_bind,
            topic=autotune_config.zmq_topic,
        )
        # Push current state so a late-starting DSP daemon receives defaults
        # once it subscribes (subscribers should also poll /api on start).
        zmq_publisher.publish(autotune_state)
    return zmq_publisher


def shutdown_zmq() -> None:
    global zmq_publisher
    if zmq_publisher is not None:
        zmq_publisher.close()
        zmq_publisher = None


atexit.register(shutdown_zmq)


def engine_info() -> dict[str, object]:
    """Describe which DSP backend the daemon will use, for display in the UI."""
    arch = host_architecture()
    os_name = host_os()
    plugins_dir = Path(autotune_config.plugin_dir)
    if not plugins_dir.is_absolute():
        plugins_dir = ROOT / plugins_dir
    preferred = Path(autotune_config.plugin_path)
    if not preferred.is_absolute():
        preferred = ROOT / preferred

    chosen = select_plugin(plugins_dir, preferred)
    if (
        autotune_config.engine == "native"
        or chosen is None
        or not chosen.compatible_with(arch, os_name)
    ):
        backend = "native"
    else:
        backend = "vst3"

    return {
        "configured": autotune_config.engine,
        "backend": backend,
        "os": os_name,
        "architecture": arch,
        "plugin": chosen.path.name if chosen else None,
        "plugin_usable": bool(chosen and chosen.compatible_with(arch, os_name)),
        "plugin_note": chosen.incompatibility_reason(arch, os_name) if chosen else None,
    }


def _broadcast(params: AutotuneParams) -> None:
    payload = params.to_dict()
    init_zmq().publish(params)
    socketio.emit("autotune_update", payload)
    logger.info("Auto-Tune state updated: %s", payload)


@app.route("/")
def home():
    return render_template(
        "home.html",
        site_title="jPiKaraoke",
        title="Home",
        autotune=autotune_state.to_dict(),
        keys=list(VALID_KEYS),
        scales=list(VALID_SCALES),
        engine=engine_info(),
    )


@app.route("/admin")
def admin():
    return render_template(
        "admin.html",
        site_title="jPiKaraoke",
        title="Admin",
        autotune=autotune_state.to_dict(),
        keys=list(VALID_KEYS),
        scales=list(VALID_SCALES),
        engine=engine_info(),
    )


@app.get("/api/autotune/config")
def get_autotune_config():
    return jsonify(
        {
            "ok": True,
            "params": autotune_state.to_dict(),
            "meta": {
                "keys": list(VALID_KEYS),
                "scales": list(VALID_SCALES),
                "zmq": autotune_config.zmq_bind,
                "plugin_profile": autotune_config.plugin_profile,
                "engine": engine_info(),
            },
        }
    )


@app.post("/api/autotune/config")
@app.put("/api/autotune/config")
def set_autotune_config():
    global autotune_state
    data = request.get_json(silent=True) or {}
    # Allow either flat body or {"params": {...}}
    updates = data.get("params", data)
    try:
        autotune_state = merge_params(autotune_state, updates)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    _broadcast(autotune_state)
    return jsonify({"ok": True, "params": autotune_state.to_dict()})


@socketio.on("connect")
def on_connect():
    emit("autotune_update", autotune_state.to_dict())


@socketio.on("autotune_set")
def on_autotune_set(data):
    global autotune_state
    updates = data.get("params", data) if isinstance(data, dict) else {}
    try:
        autotune_state = merge_params(autotune_state, updates)
    except (TypeError, ValueError) as exc:
        emit("autotune_error", {"error": str(exc)})
        return
    _broadcast(autotune_state)


@socketio.on("autotune_get")
def on_autotune_get():
    emit("autotune_update", autotune_state.to_dict())


def main() -> None:
    host = os.environ.get("JPIKARAOKE_HOST", "0.0.0.0")
    port = int(os.environ.get("JPIKARAOKE_PORT", "5550"))
    init_zmq()
    logger.info("Starting jPiKaraoke on http://%s:%s", host, port)
    socketio.run(app, host=host, port=port, allow_unsafe_werkzeug=True)


if __name__ == "__main__":
    main()
