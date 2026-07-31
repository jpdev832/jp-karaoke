"""Plugin-free pitch correction, used when no compatible VST3 is available.

Free Linux pitch-correction VST3s ship x86_64-only builds, so on aarch64 hosts
(Raspberry Pi, Jetson, Grace) there is no plugin to load. This module does the
same job in pure NumPy.

pedalboard's built-in `PitchShift` is not usable here: it is Rubber Band based
and buffers roughly one second before emitting audio. Instead this uses a
crossfaded dual-tap delay line, the classic low-latency granular shifter, whose
worst-case added latency is one grain. Grain length tracks the detected vocal
period, so latency stays in the 10-20 ms range.
"""

from __future__ import annotations

import logging
import math
import threading

import numpy as np

from .config import AutotuneParams
from .plugin_map import scale_note_mask

logger = logging.getLogger(__name__)

A4_HZ = 440.0
A4_MIDI = 69
MIN_HZ = 70.0
MAX_HZ = 1100.0
SILENCE_RMS = 1e-3
PERIODICITY_THRESHOLD = 0.3
MAX_SHIFT_SEMITONES = 12.0

MIN_GRAIN = 256
MAX_GRAIN = 4096

# The two delay taps sit half a grain apart. Their relative phase only cancels
# when that separation is a whole number of input periods, so the grain must be
# an *even* multiple of the detected period. Four periods measures within one
# cent across +/-3 semitones; two periods drifts by ~14 cents per semitone.
GRAIN_PERIODS = 4


def hz_to_midi(hz: float) -> float:
    return A4_MIDI + 12.0 * math.log2(hz / A4_HZ)


def midi_to_hz(midi: float) -> float:
    return A4_HZ * (2.0 ** ((midi - A4_MIDI) / 12.0))


def detect_pitch_hz(frame: np.ndarray, sample_rate: int) -> float | None:
    """Autocorrelation pitch detection. Returns None for silence or noise."""
    if frame.size < 2:
        return None
    signal = frame.astype(np.float64)
    signal -= signal.mean()

    if float(np.sqrt(np.mean(signal**2))) < SILENCE_RMS:
        return None

    min_lag = max(2, int(sample_rate / MAX_HZ))
    max_lag = min(signal.size - 1, int(sample_rate / MIN_HZ))
    if max_lag <= min_lag:
        return None

    corr = np.correlate(signal, signal, mode="full")[signal.size - 1 :]
    if corr[0] <= 0:
        return None

    window = corr[min_lag : max_lag + 1]
    if window.size == 0:
        return None
    lag = min_lag + int(np.argmax(window))

    if corr[lag] / corr[0] < PERIODICITY_THRESHOLD:
        return None

    # Parabolic interpolation for sub-sample accuracy.
    position = float(lag)
    if 0 < lag < corr.size - 1:
        prev, cur, nxt = corr[lag - 1], corr[lag], corr[lag + 1]
        denom = prev - 2.0 * cur + nxt
        if denom != 0:
            position += 0.5 * (prev - nxt) / denom

    if position <= 0:
        return None
    freq = sample_rate / position
    return freq if MIN_HZ <= freq <= MAX_HZ else None


def snap_to_scale(midi: float, key: str, scale: str) -> float:
    """Snap a MIDI note to the nearest pitch class allowed by key/scale."""
    mask = scale_note_mask(key, scale)
    allowed = [i for i, ok in enumerate(mask) if ok]
    if not allowed:
        return midi

    octave = math.floor(midi / 12.0)
    best = midi
    best_distance = float("inf")
    for oct_offset in (octave - 1, octave, octave + 1):
        for pitch_class in allowed:
            candidate = oct_offset * 12.0 + pitch_class
            distance = abs(candidate - midi)
            if distance < best_distance:
                best_distance = distance
                best = candidate
    return best


def pitch_synchronous_grain(detected_hz: float, sample_rate: int) -> int:
    """Grain length covering a whole, even number of input periods."""
    period = sample_rate / detected_hz
    grain = int(round(GRAIN_PERIODS * period))
    if grain < MIN_GRAIN:
        # Keep the multiple even so the taps stay phase-aligned.
        periods = GRAIN_PERIODS * math.ceil(MIN_GRAIN / (GRAIN_PERIODS * period))
        grain = int(round(periods * period))
    if grain > MAX_GRAIN:
        periods = max(2, int(MAX_GRAIN / period) // 2 * 2)
        grain = int(round(periods * period))
    return int(np.clip(grain, MIN_GRAIN, MAX_GRAIN))


class GranularPitchShifter:
    """Crossfaded dual-tap delay line pitch shifter.

    Two read taps sweep through a ring buffer half a grain out of phase. Each
    tap is amplitude-weighted by a Hann window; Hann windows offset by half a
    period sum to exactly 1.0, so the crossfade is gain-neutral.
    """

    def __init__(self, max_grain: int = MAX_GRAIN) -> None:
        self.size = int(2 ** math.ceil(math.log2(max_grain * 4)))
        self.buffer = np.zeros(self.size, dtype=np.float32)
        self.write_pos = 0
        self.phase = 0.0

    def _read(self, positions: np.ndarray) -> np.ndarray:
        base = np.floor(positions).astype(np.int64)
        frac = (positions - base).astype(np.float32)
        low = self.buffer[base % self.size]
        high = self.buffer[(base + 1) % self.size]
        return low * (1.0 - frac) + high * frac

    def process(self, block: np.ndarray, semitones: float, grain: int) -> np.ndarray:
        frames = block.size
        if frames == 0:
            return block

        indices = (self.write_pos + np.arange(frames)) % self.size
        self.buffer[indices] = block

        ratio = 2.0 ** (semitones / 12.0)
        grain = int(np.clip(grain, MIN_GRAIN, MAX_GRAIN))

        # Delay sweeps at (1 - ratio) samples per sample: shrinking delay reads
        # the buffer faster, which raises pitch.
        increment = (1.0 - ratio) / grain
        phases = self.phase + increment * np.arange(1, frames + 1)
        phase_a = np.mod(phases, 1.0)
        phase_b = np.mod(phases + 0.5, 1.0)

        # +1 keeps the tap strictly behind the write head so interpolation
        # never reads a sample that has not been written yet.
        absolute = self.write_pos + np.arange(frames)
        tap_a = self._read(absolute - (1.0 + phase_a * grain))
        tap_b = self._read(absolute - (1.0 + phase_b * grain))

        window_a = 0.5 * (1.0 - np.cos(2.0 * np.pi * phase_a))
        window_b = 0.5 * (1.0 - np.cos(2.0 * np.pi * phase_b))

        self.write_pos = (self.write_pos + frames) % self.size
        self.phase = float(np.mod(phases[-1], 1.0))
        return (tap_a * window_a + tap_b * window_b).astype(np.float32)

    def reset(self) -> None:
        self.buffer.fill(0.0)
        self.phase = 0.0


class NativeAutotune:
    """Block-based pitch corrector driven by live AutotuneParams."""

    def __init__(self, sample_rate: int, params: AutotuneParams, analysis_frames: int = 2048) -> None:
        self.sample_rate = sample_rate
        self.analysis_frames = analysis_frames
        self._params = params
        self._lock = threading.RLock()
        self._shifter = GranularPitchShifter()
        self._history = np.zeros(analysis_frames, dtype=np.float32)
        self._current_shift = 0.0
        self._grain = MIN_GRAIN
        self.last_detected_hz: float | None = None

    @property
    def params(self) -> AutotuneParams:
        with self._lock:
            return self._params

    @params.setter
    def params(self, value: AutotuneParams) -> None:
        with self._lock:
            self._params = value

    def _smoothing_alpha(self, speed: float, block_frames: int) -> float:
        """Per-block glide coefficient. speed 0 = instant snap, 1 = slow glide."""
        if speed <= 0.0:
            return 1.0
        tau_s = (1.0 + speed * 199.0) / 1000.0
        block_s = block_frames / float(self.sample_rate)
        return float(1.0 - math.exp(-block_s / tau_s))

    def process(self, block: np.ndarray) -> np.ndarray:
        """Correct one block of audio. Accepts (frames,) or (channels, frames)."""
        params = self.params
        if block.size == 0:
            return block

        multichannel = block.ndim > 1
        mono = np.ascontiguousarray(
            block.mean(axis=0) if multichannel else block, dtype=np.float32
        )

        if not params.enabled:
            self._current_shift = 0.0
            return block

        frames = mono.size
        if frames >= self.analysis_frames:
            self._history = mono[-self.analysis_frames :].copy()
        else:
            self._history = np.roll(self._history, -frames)
            self._history[-frames:] = mono

        detected = detect_pitch_hz(self._history, self.sample_rate)
        self.last_detected_hz = detected

        if detected is None:
            target_shift = 0.0
        else:
            midi = hz_to_midi(detected)
            snapped = snap_to_scale(midi, params.key, params.scale)
            target_shift = float(np.clip(snapped - midi, -MAX_SHIFT_SEMITONES, MAX_SHIFT_SEMITONES))
            self._grain = pitch_synchronous_grain(detected, self.sample_rate)

        alpha = self._smoothing_alpha(params.correction_speed, frames)
        self._current_shift += (target_shift - self._current_shift) * alpha

        wet = self._shifter.process(mono, self._current_shift, self._grain)

        mix = params.wet_dry_mix
        if mix < 1.0:
            wet = (wet * mix + mono * (1.0 - mix)).astype(np.float32)

        if multichannel:
            return np.repeat(wet[np.newaxis, :], block.shape[0], axis=0)
        return wet


def run_native_stream(
    get_params,
    sample_rate: int,
    block_frames: int,
    input_device: str | None,
    output_device: str | None,
    should_stop,
) -> None:
    """Read/process/write loop using two AudioStreams.

    Pedalboard's `read()` blocks when a single stream owns both devices
    (spotify/pedalboard#405), so input and output are opened separately.
    """
    from pedalboard.io import AudioStream

    in_name = input_device or AudioStream.default_input_device_name
    out_name = output_device or AudioStream.default_output_device_name
    logger.info("Native DSP: input=%r output=%r block=%d", in_name, out_name, block_frames)

    engine = NativeAutotune(sample_rate, get_params())

    with AudioStream(
        output_device_name=out_name, sample_rate=float(sample_rate), buffer_size=block_frames
    ) as out_stream:
        with AudioStream(
            input_device_name=in_name, sample_rate=float(sample_rate), buffer_size=block_frames
        ) as in_stream:
            in_stream.ignore_dropped_input = True
            logger.info("Native Auto-Tune running (no VST3 required).")
            while not should_stop():
                chunk = in_stream.read(block_frames)
                if chunk.size == 0:
                    continue
                engine.params = get_params()
                out_stream.write(engine.process(chunk), sample_rate)
