#!/usr/bin/env bash
# Start the Auto-Tune DSP daemon and jPiKaraoke web server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

CONFIG="${AUTOTUNE_CONFIG:-$ROOT/config/autotune.json}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
mkdir -p "$LOG_DIR"

PYTHON="${PYTHON:-python3}"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
fi

export AUTOTUNE_CONFIG="$CONFIG"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

echo "==> jPiKaraoke Auto-Tune launcher"
echo "    root:   $ROOT"
echo "    python: $PYTHON"
echo "    config: $CONFIG"

# ---------------------------------------------------------------------------
# 1) Identify USB-C ADC / USB microphone via PortAudio (cross-platform)
# ---------------------------------------------------------------------------
detect_usb_input() {
  local preferred="${AUTOTUNE_INPUT_DEVICE:-}"
  if [[ -n "$preferred" ]]; then
    echo "$preferred"
    return 0
  fi

  # Prefer PortAudio device names (works on Linux and macOS).
  if "$PYTHON" -c "from pedalboard.io import AudioStream" >/dev/null 2>&1; then
    local pb
    pb="$("$PYTHON" "$ROOT/autotune_engine.py" --detect-usb-input 2>/dev/null || true)"
    if [[ -n "${pb:-}" ]]; then
      echo "$pb"
      return 0
    fi
  fi

  # Optional Linux hints when PortAudio has no USB-named device yet.
  if command -v pactl >/dev/null 2>&1; then
    local src
    src="$(pactl list short sources 2>/dev/null | awk '/usb|USB|Usb/ && !/\.monitor/ {print $2; exit}')"
    if [[ -n "${src:-}" ]]; then
      echo "$src"
      return 0
    fi
  fi

  if command -v arecord >/dev/null 2>&1; then
    local card
    card="$(arecord -l 2>/dev/null | awk -F'[][]' '/USB|usb|Usb/ {print $2; exit}')"
    if [[ -n "${card:-}" ]]; then
      echo "$card"
      return 0
    fi
  fi

  # Fall back to default input (engine will use AudioStream.default_input_device_name)
  echo ""
}

list_audio_hints() {
  echo "-- Audio device hints --"
  if "$PYTHON" -c "from pedalboard.io import AudioStream" >/dev/null 2>&1; then
    echo "[pedalboard PortAudio devices]"
    "$PYTHON" "$ROOT/autotune_engine.py" --list-devices || true
  fi
  if command -v arecord >/dev/null 2>&1; then
    echo "[arecord -l]"
    arecord -l 2>/dev/null || true
  fi
  if command -v pactl >/dev/null 2>&1; then
    echo "[pactl list short sources]"
    pactl list short sources 2>/dev/null || true
  fi
}

INPUT_DEVICE="$(detect_usb_input)"
list_audio_hints
if [[ -n "$INPUT_DEVICE" ]]; then
  echo "==> Using input device: $INPUT_DEVICE"
else
  echo "==> No USB ADC auto-detected; DSP will use the system default input."
fi

ENGINE_ARGS=(--config "$CONFIG")
if [[ -n "$INPUT_DEVICE" ]]; then
  ENGINE_ARGS+=(--input-device "$INPUT_DEVICE")
fi
if [[ -n "${AUTOTUNE_OUTPUT_DEVICE:-}" ]]; then
  ENGINE_ARGS+=(--output-device "$AUTOTUNE_OUTPUT_DEVICE")
fi
if [[ -n "${AUTOTUNE_PLUGIN:-}" ]]; then
  ENGINE_ARGS+=(--plugin "$AUTOTUNE_PLUGIN")
fi
if [[ -n "${AUTOTUNE_ENGINE:-}" ]]; then
  ENGINE_ARGS+=(--engine "$AUTOTUNE_ENGINE")
fi

# Report which backend will be used before starting anything.
"$PYTHON" "$ROOT/autotune_engine.py" --list-plugins 2>/dev/null || true

# ---------------------------------------------------------------------------
# Launch order: web (ZMQ PUB bind) first, then DSP (ZMQ SUB connect).
# ---------------------------------------------------------------------------
ENGINE_PID=""
WEB_PID=""

cleanup() {
  echo
  echo "==> Shutting down…"
  if [[ -n "${ENGINE_PID}" ]] && kill -0 "$ENGINE_PID" 2>/dev/null; then
    kill "$ENGINE_PID" 2>/dev/null || true
  fi
  if [[ -n "${WEB_PID}" ]] && kill -0 "$WEB_PID" 2>/dev/null; then
    kill "$WEB_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting jPiKaraoke web server (ZMQ publisher)…"
"$PYTHON" "$ROOT/app.py" >"$LOG_DIR/jpikaraoke.log" 2>&1 &
WEB_PID=$!
sleep 0.6
if ! kill -0 "$WEB_PID" 2>/dev/null; then
  echo "!! Web server failed to start. Log:"
  tail -n 40 "$LOG_DIR/jpikaraoke.log" || true
  exit 1
fi

HOST_PORT="${JPIKARAOKE_PORT:-5550}"
echo "    web pid=$WEB_PID  http://0.0.0.0:${HOST_PORT}/"

echo "==> Starting Auto-Tune DSP daemon…"
set +e
"$PYTHON" "$ROOT/autotune_engine.py" "${ENGINE_ARGS[@]}" \
  >"$LOG_DIR/autotune_engine.log" 2>&1 &
ENGINE_PID=$!
set -e

sleep 0.8
if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
  echo "!! DSP daemon exited early. Last log lines:"
  tail -n 40 "$LOG_DIR/autotune_engine.log" || true
  echo
  echo "Common fixes:"
  echo "  - Install a pitch-correction plugin:  scripts/install_plugins.sh"
  echo "  - Force the plugin-free engine:       AUTOTUNE_ENGINE=native $0"
  echo "  - Export AUTOTUNE_INPUT_DEVICE to a valid PortAudio device name"
  echo "  - Inspect what is available:          python autotune_engine.py --list-plugins"
  # Continue so the web UI is still usable for configuration.
  ENGINE_PID=""
else
  echo "    DSP pid=$ENGINE_PID  log=$LOG_DIR/autotune_engine.log"
fi

echo
echo "Auto-Tune panel: http://127.0.0.1:${HOST_PORT}/admin"
echo "Press Ctrl+C to stop."

# Prefer waiting on the web server; keep DSP supervised.
while true; do
  if [[ -n "$WEB_PID" ]] && ! kill -0 "$WEB_PID" 2>/dev/null; then
    echo "Web server exited."
    exit 1
  fi
  if [[ -n "$ENGINE_PID" ]] && ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "DSP daemon exited; see $LOG_DIR/autotune_engine.log"
    ENGINE_PID=""
  fi
  sleep 1
done
