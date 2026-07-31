"""ZeroMQ helpers for PiKaraoke <-> Auto-Tune DSP daemon communication."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable

import zmq

from .config import AutotuneParams

logger = logging.getLogger(__name__)


class AutotuneZmqPublisher:
    """Flask-side publisher: broadcast parameter updates to the DSP daemon."""

    def __init__(self, connect_addr: str, topic: str = "autotune") -> None:
        self._connect_addr = connect_addr
        self._topic = topic.encode("utf-8")
        self._lock = threading.Lock()
        self._ctx = zmq.Context.instance()
        self._socket = self._ctx.socket(zmq.PUB)
        # PUB binds so late SUB subscribers still receive subsequent messages.
        self._socket.bind(connect_addr)
        # Give slow subscribers a moment; first messages after bind can be lost.
        self._socket.setsockopt(zmq.LINGER, 0)
        logger.info("Auto-Tune ZMQ PUB bound on %s topic=%s", connect_addr, topic)

    def publish(self, params: AutotuneParams | dict[str, Any]) -> None:
        payload = params.to_dict() if isinstance(params, AutotuneParams) else dict(params)
        message = json.dumps({"type": "set_params", "params": payload}, separators=(",", ":"))
        with self._lock:
            self._socket.send_multipart([self._topic, message.encode("utf-8")])
        logger.debug("Published autotune params: %s", payload)

    def request_status(self) -> None:
        with self._lock:
            self._socket.send_multipart(
                [self._topic, b'{"type":"get_params"}']
            )

    def close(self) -> None:
        with self._lock:
            try:
                self._socket.close(0)
            except Exception:
                pass


class AutotuneZmqSubscriber:
    """DSP-side subscriber running on a background thread."""

    def __init__(
        self,
        connect_addr: str,
        topic: str,
        on_message: Callable[[dict[str, Any]], None],
    ) -> None:
        self._connect_addr = connect_addr
        self._topic = topic.encode("utf-8")
        self._on_message = on_message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ctx = zmq.Context.instance()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="autotune-zmq", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        socket = self._ctx.socket(zmq.SUB)
        socket.setsockopt(zmq.SUBSCRIBE, self._topic)
        socket.setsockopt(zmq.RCVTIMEO, 500)
        socket.connect(self._connect_addr)
        logger.info("Auto-Tune ZMQ SUB connected to %s topic=%s", self._connect_addr, self._topic.decode())
        try:
            while not self._stop.is_set():
                try:
                    frames = socket.recv_multipart()
                except zmq.Again:
                    continue
                except zmq.ZMQError as exc:
                    if self._stop.is_set():
                        break
                    logger.warning("ZMQ receive error: %s", exc)
                    continue

                if len(frames) < 2:
                    continue
                try:
                    payload = json.loads(frames[1].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    logger.warning("Invalid ZMQ JSON: %s", exc)
                    continue
                try:
                    self._on_message(payload)
                except Exception:
                    logger.exception("Error handling ZMQ message: %s", payload)
        finally:
            socket.close(0)
