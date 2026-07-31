#!/usr/bin/env bash
# JP Karaoke — start karaoke web + Auto-Tune DSP together.
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

echo "==> JP Karaoke launcher"
echo "    root:   $ROOT"
echo "    python: $PYTHON"
echo "    config: $CONFIG"

# ---------------------------------------------------------------------------
# Preflight: Python 3.10+ and editable install deps
# ---------------------------------------------------------------------------
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  ver="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo unknown)"
  echo "xx Python 3.10+ is required (found $ver at $PYTHON)."
  echo "   On macOS with Homebrew:"
  echo "     brew install python@3.12"
  echo "     rm -rf .venv"
  echo "     /opt/homebrew/bin/python3.12 -m venv .venv"
  echo "     source .venv/bin/activate"
  echo "     python -m pip install --upgrade pip"
  echo "     pip install -e ."
  exit 1
fi

if ! "$PYTHON" -c 'import qrcode, flask, zmq' 2>/dev/null; then
  echo "xx Project dependencies are not installed in this environment."
  echo "   Run:"
  echo "     source .venv/bin/activate"
  echo "     python -m pip install --upgrade pip"
  echo "     pip install -e ."
  exit 1
fi

detect_usb_input() {
  local preferred="${AUTOTUNE_INPUT_DEVICE:-}"
  if [[ -n "$preferred" ]]; then
    echo "$preferred"
    return 0
  fi

  if "$PYTHON" -c "from pedalboard.io import AudioStream" >/dev/null 2>&1; then
    local pb
    pb="$("$PYTHON" "$ROOT/autotune_engine.py" --detect-usb-input 2>/dev/null || true)"
    if [[ -n "${pb:-}" ]]; then
      echo "$pb"
      return 0
    fi
  fi

  if command -v pactl >/dev/null 2>&1; then
    local src
    src="$(pactl list short sources 2>/dev/null | awk '/usb|USB|Usb/ && !/\.monitor/ {print $2; exit}')"
    if [[ -n "${src:-}" ]]; then
      echo "$src"
      return 0
    fi
  fi

  echo ""
}

INPUT_DEVICE="$(detect_usb_input)"
if [[ -n "$INPUT_DEVICE" ]]; then
  echo "==> Using Auto-Tune input device: $INPUT_DEVICE"
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

"$PYTHON" "$ROOT/autotune_engine.py" --list-plugins 2>/dev/null || true

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

# Web first so ZMQ PUB binds before DSP SUB connects.
echo "==> Starting JP Karaoke web…"
"$PYTHON" -m pikaraoke.app "$@" \
  >"$LOG_DIR/jp_karaoke.log" 2>&1 &
WEB_PID=$!
sleep 1
if ! kill -0 "$WEB_PID" 2>/dev/null; then
  echo "xx Web server failed to start. See $LOG_DIR/jp_karaoke.log"
  cat "$LOG_DIR/jp_karaoke.log" >&2 || true
  exit 1
fi

echo "==> Starting Auto-Tune DSP…"
"$PYTHON" "$ROOT/autotune_engine.py" "${ENGINE_ARGS[@]}" \
  >"$LOG_DIR/autotune_engine.log" 2>&1 &
ENGINE_PID=$!
sleep 1
if ! kill -0 "$ENGINE_PID" 2>/dev/null; then
  echo "!! Auto-Tune DSP failed to start (karaoke web still running)."
  echo "   See $LOG_DIR/autotune_engine.log"
  ENGINE_PID=""
fi

echo "==> JP Karaoke is up. Ctrl+C to stop."
echo "    Splash / QR: open the URL shown in $LOG_DIR/jp_karaoke.log"
echo "    Auto-Tune panel: /autotune"

# Supervise: exit if web dies; keep going if only DSP dies.
while kill -0 "$WEB_PID" 2>/dev/null; do
  if [[ -n "$ENGINE_PID" ]] && ! kill -0 "$ENGINE_PID" 2>/dev/null; then
    echo "!! Auto-Tune DSP exited. Karaoke continues without DSP."
    ENGINE_PID=""
  fi
  sleep 2
done

echo "xx Karaoke web exited."
exit 1
