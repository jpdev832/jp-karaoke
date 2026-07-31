"""Map abstract Auto-Tune params onto real VST3 plugin parameters.

Plugins expose wildly different control names, so every write goes through a
resolver that matches candidate names against the parameter list the plugin
actually reports. Unmatched controls are skipped rather than raising, so a
partially-supported plugin still runs.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Sequence

from .config import AutotuneParams, VALID_KEYS

logger = logging.getLogger(__name__)

KEY_INDEX = {k: i for i, k in enumerate(VALID_KEYS)}
MAJOR_INTERVALS = (0, 2, 4, 5, 7, 9, 11)
MINOR_INTERVALS = (0, 2, 3, 5, 7, 8, 10)
CHROMATIC_INTERVALS = tuple(range(12))

SCALE_INTERVALS = {
    "major": MAJOR_INTERVALS,
    "minor": MINOR_INTERVALS,
    "chromatic": CHROMATIC_INTERVALS,
}

# Retune time bounds in milliseconds: robotic (0.0) -> natural (1.0).
RETUNE_MS_MIN = 1.0
RETUNE_MS_MAX = 200.0


def scale_note_mask(key: str, scale: str) -> list[bool]:
    """Return a 12-entry mask (index 0 = C) of pitch classes in the key/scale."""
    root = KEY_INDEX[key]
    enabled = {(root + step) % 12 for step in SCALE_INTERVALS[scale]}
    return [i in enabled for i in range(12)]


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower().replace("#", "sharp"))


class ParameterResolver:
    """Resolve friendly parameter aliases against a plugin's real parameters."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.index: dict[str, str] = {}
        params = getattr(plugin, "parameters", None) or {}
        try:
            keys = list(params.keys())
        except Exception:
            keys = []
        for key in keys:
            self.index.setdefault(_normalize(key), key)
            # Plugins report display names like "Wet Mix"; pedalboard exposes
            # them as snake_case attributes, so index both spellings.
            raw_name = getattr(params[key], "name", None) if hasattr(params, "__getitem__") else None
            if isinstance(raw_name, str):
                self.index.setdefault(_normalize(raw_name), key)
        self.available = bool(self.index)
        self.applied: list[str] = []
        self.missing: list[str] = []

    def find(self, candidates: Iterable[str]) -> str | None:
        for candidate in candidates:
            match = self.index.get(_normalize(candidate))
            if match:
                return match
        return None

    def _range(self, key: str) -> tuple[float, float] | None:
        params = getattr(self.plugin, "parameters", None) or {}
        try:
            param = params[key]
        except Exception:
            return None
        rng = getattr(param, "range", None)
        if isinstance(rng, Sequence) and len(rng) >= 2:
            low, high = rng[0], rng[1]
            if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                return float(low), float(high)
        low = getattr(param, "min_value", None)
        high = getattr(param, "max_value", None)
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            return float(low), float(high)
        return None

    def set(self, candidates: Sequence[str], value: Any) -> bool:
        """Write a literal value to the first matching parameter."""
        key = self.find(candidates)
        if key is None:
            self.missing.append(candidates[0])
            return False
        try:
            setattr(self.plugin, key, value)
        except Exception as exc:
            logger.debug("Could not set %s=%r: %s", key, value, exc)
            return False
        self.applied.append(f"{key}={value!r}")
        return True

    def set_normalized(self, candidates: Sequence[str], value: float) -> bool:
        """Write a 0.0-1.0 value, rescaled into the parameter's real range."""
        key = self.find(candidates)
        if key is None:
            self.missing.append(candidates[0])
            return False
        bounds = self._range(key)
        scaled: float = value
        if bounds:
            low, high = bounds
            scaled = low + (high - low) * max(0.0, min(1.0, value))
        try:
            setattr(self.plugin, key, scaled)
        except Exception as exc:
            logger.debug("Could not set %s=%r: %s", key, scaled, exc)
            return False
        self.applied.append(f"{key}={scaled:.4g}")
        return True

    def set_bypass(self, enabled: bool) -> None:
        # enabled=True means the effect is active, i.e. bypass is off.
        if self.set(("bypass", "bypassed"), not enabled):
            return
        self.set(("enabled", "on", "active", "power"), enabled)

    def set_retune_time(self, candidates: Sequence[str], speed: float) -> bool:
        """Map correction_speed onto a ms-style retune/smoothing control."""
        key = self.find(candidates)
        if key is None:
            self.missing.append(candidates[0])
            return False
        bounds = self._range(key)
        if bounds:
            low, high = bounds
            # Assume a millisecond-style control: low = hard, high = natural.
            value = low + (high - low) * speed
        else:
            value = RETUNE_MS_MIN + (RETUNE_MS_MAX - RETUNE_MS_MIN) * speed
        try:
            setattr(self.plugin, key, value)
        except Exception as exc:
            logger.debug("Could not set %s=%r: %s", key, value, exc)
            return False
        self.applied.append(f"{key}={value:.4g}")
        return True

    def set_scale_notes(self, params: AutotuneParams) -> int:
        """Enable/disable per-pitch-class controls (Graillon 'Allow C' etc.)."""
        mask = scale_note_mask(params.key, params.scale)
        note_names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        count = 0
        for i, note in enumerate(note_names):
            candidates = (
                f"Allow {note}",
                f"allow_{note.lower().replace('#', '_sharp')}",
                f"Note {note}",
                f"note_{note.lower().replace('#', '_sharp')}",
                note,
            )
            if self.set(candidates, bool(mask[i])):
                count += 1
        return count


def apply_graillon(res: ParameterResolver, params: AutotuneParams) -> None:
    """Auburn Sounds Graillon 3 (Free Edition).

    Real parameter names: Correction, Corr. Amount, Smooth, Inertia,
    Snap Min, Snap Max, Wet Mix, Dry Mix, Allow C..Allow B, Reference.
    """
    res.set_bypass(params.enabled)
    res.set(("Correction", "correction"), params.enabled)
    res.set_normalized(("Corr. Amount", "corr_amount", "correction_amount"), 1.0)

    # Smooth/Inertia act as the retune-time controls.
    res.set_retune_time(("Smooth", "smooth", "smoothness"), params.correction_speed)
    res.set_retune_time(("Inertia", "inertia"), params.correction_speed)

    # Graillon exposes independent wet and dry mixes.
    if not res.set_normalized(("Wet Mix", "wet_mix"), params.wet_dry_mix):
        res.set_normalized(("mix", "dry_wet", "dry/wet"), params.wet_dry_mix)
    res.set_normalized(("Dry Mix", "dry_mix"), 1.0 - params.wet_dry_mix)

    res.set_scale_notes(params)


def apply_gsnap(res: ParameterResolver, params: AutotuneParams) -> None:
    """GVST GSnap: Speed (ms, lower = harder), Amount, Threshold."""
    res.set_bypass(params.enabled)
    res.set_retune_time(("Speed", "speed"), params.correction_speed)
    res.set_normalized(("Amount", "amount"), params.wet_dry_mix)
    res.set_normalized(("Corr", "corr", "correction"), params.wet_dry_mix)
    res.set_scale_notes(params)


def apply_mautopitch(res: ParameterResolver, params: AutotuneParams) -> None:
    """MeldaProduction MAutoPitch: Depth, Speed, Dry/Wet."""
    res.set_bypass(params.enabled)
    res.set_normalized(("Depth", "depth"), params.wet_dry_mix)
    # Melda's Speed is "higher = faster correction", so invert.
    res.set_normalized(("Speed", "speed"), 1.0 - params.correction_speed)
    res.set_normalized(("Dry/Wet", "dry_wet", "drywet", "Mix"), params.wet_dry_mix)
    res.set(("Key", "key"), KEY_INDEX.get(params.key, 0))
    res.set(("Scale", "scale"), {"major": 0, "minor": 1, "chromatic": 2}.get(params.scale, 0))
    res.set_scale_notes(params)


def apply_qpitch(res: ParameterResolver, params: AutotuneParams) -> None:
    """Skynse QPitch — verified against the live VST3 parameter surface.

    Real controls: bypass, correction_on, correction (0-100), retune_speed (ms),
    key (choice), scale (choice), note_c … note_b, t_pain, snappiness.
    """
    # QPitch keys use flats for Eb/Bb; our abstract keys are sharp-spelled.
    key_for_plugin = {"D#": "Eb", "A#": "Bb"}.get(params.key, params.key)
    scale_for_plugin = {
        "chromatic": "Chromatic",
        "major": "Major",
        "minor": "Minor",
    }.get(params.scale, params.scale.capitalize())

    res.set_bypass(params.enabled)
    res.set(("correction_on", "Correction On"), params.enabled)

    # Retune Speed is milliseconds. Map our 0..1 control onto a musical
    # 1..200 ms window (QPitch's raw range goes to 800 ms, which is too slow
    # for live vocals at the "natural" end).
    retune_ms = RETUNE_MS_MIN + (RETUNE_MS_MAX - RETUNE_MS_MIN) * params.correction_speed
    res.set(("retune_speed", "Retune Speed"), retune_ms)
    # Extra robotic character when the user wants hard correction.
    res.set_normalized(("t_pain", "T-Pain"), 1.0 - params.correction_speed)
    res.set_normalized(("snappiness", "Snappiness"), 1.0 - params.correction_speed)

    # No dedicated wet/dry; Correction amount (0-100) is the closest control.
    res.set_normalized(("correction", "Correction", "correction_amount"), params.wet_dry_mix)

    res.set(("key", "Key"), key_for_plugin)
    res.set(("scale", "Scale"), scale_for_plugin)

    # Keep the note keyboard in sync for hosts that read it independently.
    note_ids = (
        "note_c",
        "note_c_sharp",
        "note_d",
        "note_d_sharp",
        "note_e",
        "note_f",
        "note_f_sharp",
        "note_g",
        "note_g_sharp",
        "note_a",
        "note_a_sharp",
        "note_b",
    )
    mask = scale_note_mask(params.key, params.scale)
    for note_id, enabled in zip(note_ids, mask):
        res.set((note_id,), bool(enabled))


def apply_generic(res: ParameterResolver, params: AutotuneParams) -> None:
    """Best-effort mapping for an unknown pitch-correction plugin."""
    res.set_bypass(params.enabled)
    res.set_retune_time(("Speed", "Smooth", "Retune", "speed", "smoothness"), params.correction_speed)
    res.set_normalized(("Mix", "Wet", "Amount", "Depth", "wet_mix"), params.wet_dry_mix)
    res.set_scale_notes(params)


PROFILES: dict[str, Callable[[ParameterResolver, AutotuneParams], None]] = {
    "graillon": apply_graillon,
    "gsnap": apply_gsnap,
    "mautopitch": apply_mautopitch,
    "qpitch": apply_qpitch,
    "generic": apply_generic,
}


def apply_params(plugin: Any, params: AutotuneParams, profile: str = "graillon") -> None:
    """Push abstract AutotuneParams onto a loaded pedalboard ExternalPlugin."""
    resolver = ParameterResolver(plugin)
    applicator = PROFILES.get(profile.lower(), apply_generic)
    applicator(resolver, params)

    logger.info(
        "Auto-Tune [%s] enabled=%s key=%s %s speed=%.2f mix=%.2f -> %d params set",
        profile,
        params.enabled,
        params.key,
        params.scale,
        params.correction_speed,
        params.wet_dry_mix,
        len(resolver.applied),
    )
    if resolver.applied:
        logger.debug("Applied: %s", ", ".join(resolver.applied))
    if resolver.missing:
        logger.debug("Unmatched controls for profile %s: %s", profile, ", ".join(sorted(set(resolver.missing))))


def list_plugin_parameters(plugin: Any) -> list[str]:
    params = getattr(plugin, "parameters", None)
    if params is None:
        return []
    try:
        return sorted(params.keys())
    except Exception:
        return []
