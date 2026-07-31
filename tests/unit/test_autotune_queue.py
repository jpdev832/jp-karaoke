"""Tests for per-song Auto-Tune queue attachment."""

from __future__ import annotations

import pytest

from pikaraoke.lib.autotune_control import parse_queue_autotune, room_idle_params
from pikaraoke.lib.events import EventSystem
from pikaraoke.lib.preference_manager import PreferenceManager
from pikaraoke.lib.queue_manager import QueueManager


@pytest.fixture
def queue_manager(tmp_path):
    prefs = PreferenceManager(str(tmp_path / "prefs.ini"))
    events = EventSystem()
    return QueueManager(
        preferences=prefs,
        events=events,
        filename_from_path=lambda path, _remove=True: path.split("/")[-1],
    )


def test_parse_queue_autotune_off():
    assert parse_queue_autotune(None) is None
    assert parse_queue_autotune({"enabled": False}) is None
    assert parse_queue_autotune("") is None


def test_parse_queue_autotune_on():
    params = parse_queue_autotune(
        {"enabled": True, "key": "G", "scale": "minor", "correction_speed": 0.2, "wet_dry_mix": 0.8}
    )
    assert params is not None
    assert params["enabled"] is True
    assert params["key"] == "G"
    assert params["scale"] == "minor"


def test_enqueue_stores_autotune(queue_manager):
    at = parse_queue_autotune({"enabled": True, "key": "D", "scale": "major"})
    ok, _msg = queue_manager.enqueue("/songs/test---abc.mp4", "Alice", autotune=at)
    assert ok is True
    assert queue_manager.queue[0]["autotune"]["key"] == "D"
    assert queue_manager.queue[0]["autotune"]["enabled"] is True


def test_enqueue_without_autotune(queue_manager):
    ok, _msg = queue_manager.enqueue("/songs/test---xyz.mp4", "Bob")
    assert ok is True
    assert queue_manager.queue[0]["autotune"] is None


def test_room_idle_preserves_mic_volume(monkeypatch):
    from pikaraoke.lib import autotune_control as at

    at._state = at.AutotuneParams.from_dict(
        {"enabled": True, "key": "C", "scale": "major", "mic_volume": 0.4}
    )
    idle = at.room_idle_params()
    assert idle.enabled is False
    assert idle.mic_volume == 0.4


def test_apply_for_song_preserves_mic_volume(monkeypatch):
    from pikaraoke.lib import autotune_control as at

    published = []

    class FakePub:
        def publish(self, params):
            published.append(params.to_dict())

    monkeypatch.setattr(at, "_publisher", FakePub())
    monkeypatch.setattr(at, "_socketio", None)
    at._state = at.AutotuneParams.from_dict(
        {"enabled": False, "key": "C", "scale": "major", "mic_volume": 0.55}
    )
    at.apply_for_song(
        {"enabled": True, "key": "G", "scale": "minor", "correction_speed": 0.2, "wet_dry_mix": 1.0}
    )
    assert published[-1]["key"] == "G"
    assert published[-1]["mic_volume"] == 0.55
    assert published[-1]["enabled"] is True
