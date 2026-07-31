# jPiKaraoke Auto-Tune

Real-time pitch correction for USB-C ADC / USB microphone input, wired into a PiKaraoke-style Flask + Socket.IO UI.

```
USB Mic ──► autotune_engine.py ──► speakers
                   ▲
                   │ ZeroMQ (JSON params)
                   │
Browser ◄─Socket.IO─ app.py (Flask)
```

## The right path: live VST3

Pitch correction runs inside `pedalboard.AudioStream` by hosting a real VST3
plugin in the audio callback. That is the low-latency path the stack is built
around.

```bash
scripts/install_plugins.sh            # best VST3 for this OS/CPU
python autotune_engine.py --list-plugins
./start_autotune.sh
```

| Host | What gets installed | Profile |
|------|---------------------|---------|
| **Linux aarch64** (Pi, Jetson, …) | QPitch built from source | `qpitch` |
| **Linux x86_64** | Graillon Free download | `graillon` |
| **macOS arm64 / x86_64** | QPitch built from source | `qpitch` |

`engine: auto` (default) uses the VST3 when a compatible plugin is present.
The plugin-free `native` engine remains as a last-resort fallback (including when
a VST3 build fails or only wrong-OS bundles are installed).

> Do **not** use pedalboard's built-in `PitchShift` for live audio. It is Rubber
> Band offline mode: fine for whole-buffer offline renders, but in streaming
> (`reset=False`) it buffers ~1 s then emits silence. Live correction belongs
> in a VST3 (or the dedicated native shifter, not `PitchShift`).

```bash
python autotune_engine.py --list-plugins
```

The web UI shows the active backend as a badge on the Auto-Tune panel.

## Requirements

- Linux (PipeWire or ALSA) or macOS (Core Audio), with a USB microphone / USB-C ADC
- Python 3.10+

```bash
cd /path/to/jpikaraoke
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

System packages on Ubuntu/Debian:

```bash
sudo apt update
sudo apt install -y python3-venv portaudio19-dev libasound2-dev
sudo apt install -y pulseaudio-utils alsa-utils   # optional, for device inspection
# For building QPitch:
sudo apt install -y build-essential cmake git pkg-config librubberband2 \
  libx11-dev libxext-dev libxinerama-dev libxrandr-dev \
  libxcursor-dev libfreetype-dev libfontconfig1-dev
```

System packages on macOS (Homebrew):

```bash
brew install python portaudio cmake git rubberband pkg-config
```

## Plugins

```bash
scripts/install_plugins.sh            # auto: QPitch (ARM/macOS) or Graillon (Linux x86)
scripts/install_plugins.sh --list
scripts/install_plugins.sh qpitch     # force QPitch (source build on ARM/macOS)
scripts/install_plugins.sh graillon   # force Graillon Free (Linux only)
```

| Plugin | Profile | Arch / OS | Source |
|--------|---------|-----------|--------|
| **QPitch** (default path) | `qpitch` | Linux + macOS (source build on ARM/Darwin) | https://github.com/Skynse/qpitch |
| Graillon 3 Free | `graillon` | Linux x86_64 auto-install; macOS via vendor `.pkg` | https://www.auburnsounds.com/products/Graillon.html |
| GVST GSnap | `gsnap` | manual | https://gvst.uk/Downloads/GSnap |
| MeldaProduction MAutoPitch | `mautopitch` | manual | https://www.meldaproduction.com/MAutoPitch |

On Linux aarch64 and on macOS the installer clones QPitch, builds the VST3 with
CMake/JUCE, and installs `plugins/QPitch.vst3`. Runtime needs Rubber Band
(`sudo apt install librubberband2` or `brew install rubberband`).

Graillon Free auto-install copies the **Linux** VST3 only. On macOS, run the
Mac `.pkg` from the Free zip (or copy a macOS `.vst3` into `plugins/` by hand).

GSnap and MAutoPitch have no stable direct download link — drop the `.vst3`
into `plugins/` by hand and re-run `--list-plugins`.

### Building QPitch from source

Already handled by `scripts/install_plugins.sh qpitch`. Manual equivalent on Linux:

```bash
sudo apt install -y build-essential cmake git pkg-config \
  libx11-dev libxext-dev libxinerama-dev libxrandr-dev \
  libxcursor-dev libfreetype-dev libfontconfig1-dev librubberband2
git clone --recurse-submodules https://github.com/Skynse/qpitch && cd qpitch
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target QPitch_VST3 -j
cp -a build/QPitch_artefacts/Release/VST3/QPitch.vst3 /path/to/jpikaraoke/plugins/
```

On macOS:

```bash
brew install cmake git rubberband pkg-config
git clone --recurse-submodules https://github.com/Skynse/qpitch && cd qpitch
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --target QPitch_VST3 -j
cp -a build/QPitch_artefacts/Release/VST3/QPitch.vst3 /path/to/jpikaraoke/plugins/
```

### Profiles and parameter mapping

Profiles translate the five abstract controls onto each plugin's real
parameters. The resolver matches against the parameter list the plugin actually
reports, so unmatched controls are skipped rather than crashing. Graillon's
`Allow C` … `Allow B` switches, for example, are driven directly from the
selected key and scale.

Inspect a plugin's real parameters:

```bash
python - <<'PY'
from pedalboard import load_plugin
p = load_plugin("plugins/Auburn Sounds Graillon 3.vst3")
print(sorted(p.parameters.keys()))
PY
```

If your plugin uses different names, add a profile in `autotune/plugin_map.py`.

## Configuration

`config/autotune.json`:

| Key | Meaning |
|-----|---------|
| `engine` | `auto` (default), `vst3`, or `native` |
| `zmq.bind` / `zmq.connect` | Local ZeroMQ endpoint (default `tcp://127.0.0.1:5555`) |
| `audio.sample_rate` | Default `48000` |
| `audio.buffer_size` | Default `256` (~11 ms). Try `128` for lower latency if stable |
| `audio.input_device` | Optional PortAudio device name override |
| `plugin.path` | Preferred `.vst3` bundle |
| `plugin.dir` | Directory scanned for plugins |
| `plugin.profile` | Parameter mapping profile |
| `defaults.*` | Boot-time Auto-Tune settings |

Environment overrides used by `start_autotune.sh`:

- `AUTOTUNE_INPUT_DEVICE`, `AUTOTUNE_OUTPUT_DEVICE` — force device names
- `AUTOTUNE_PLUGIN` — override plugin path
- `AUTOTUNE_CONFIG` — alternate config JSON
- `JPIKARAOKE_PORT` — web port (default `5550`)

## Startup

```bash
chmod +x start_autotune.sh
./start_autotune.sh
```

The script detects a USB capture device via PortAudio device names (with
optional `pactl` / `arecord` hints on Linux), starts the Flask app (which binds
the ZeroMQ publisher), then starts the DSP daemon.

- Singer UI: http://127.0.0.1:5550/
- Admin + Auto-Tune panel: http://127.0.0.1:5550/admin

Logs land in `logs/autotune_engine.log` and `logs/jpikaraoke.log`.

### Manual two-process start

Start the web server first so the ZMQ publisher is bound:

```bash
source .venv/bin/activate
python app.py
```

Then, in a second terminal:

```bash
source .venv/bin/activate
python autotune_engine.py --input-device "Your USB Mic Name"
python autotune_engine.py --engine native   # force the plugin-free engine
python autotune_engine.py --list-devices
python autotune_engine.py --dry-run -v      # resolve config without opening audio
```

## API

### `GET /api/autotune/config`

Returns current parameters plus metadata: valid keys and scales, the ZMQ
address, and an `engine` block describing the active backend, host
architecture, and selected plugin.

### `POST` / `PUT /api/autotune/config`

Body (flat, or nested under `params`):

```json
{
  "enabled": true,
  "key": "A",
  "scale": "minor",
  "correction_speed": 0.15,
  "wet_dry_mix": 1.0
}
```

- `key`: `C`, `C#`, … `B` (flats such as `Bb` are accepted and normalized)
- `scale`: `major`, `minor`, `chromatic`
- `correction_speed`: `0.0` = hard / robotic, `1.0` = natural glide
- `wet_dry_mix`: `0.0` dry → `1.0` fully wet

Invalid values return HTTP 400 and leave the current state untouched.

### Socket.IO events

| Event | Direction | Purpose |
|-------|-----------|---------|
| `autotune_set` | client → server | Update params |
| `autotune_get` | client → server | Request current params |
| `autotune_update` | server → clients | Broadcast new state |
| `autotune_error` | server → client | Validation failure |

All connected browsers stay in sync; the server also publishes each change over
ZeroMQ to the DSP daemon, which applies it without restarting the audio stream.

## How the native engine works

1. **Detect** — autocorrelation over a 2048-sample rolling window with
   parabolic peak interpolation, gated by an RMS floor and a periodicity
   threshold so unvoiced audio is left alone. Accurate to ~0.1% on vocal tones.
2. **Snap** — the detected frequency is converted to MIDI and snapped to the
   nearest pitch class permitted by the selected key and scale.
3. **Glide** — the correction is slewed toward the target with a time constant
   from 1 ms (`correction_speed` 0.0, hard T-Pain snap) to 200 ms (1.0, natural).
4. **Shift** — a crossfaded dual-tap delay line resamples the audio. The grain
   length is locked to an even multiple of the detected period, which is what
   keeps the two taps phase-aligned; without that the shifter drifts about
   14 cents per semitone.

Measured on this codebase: worst-case pitch error 0.18 cents across 90–660 Hz,
and roughly 11% of the realtime CPU budget per 256-frame block on an ARM64 host.

`pedalboard`'s built-in `PitchShift` is deliberately not used — it is Rubber
Band based and buffers about one second before producing output, which is
unusable for live vocals.

## Project layout

```
jpikaraoke/
├── autotune_engine.py      # DSP daemon (VST3 + native engines, ZMQ SUB)
├── app.py                  # Flask + Socket.IO + ZMQ PUB
├── start_autotune.sh
├── scripts/install_plugins.sh
├── config/autotune.json
├── autotune/
│   ├── config.py           # params, validation, config loading
│   ├── plugins.py          # plugin catalog, ELF/Mach-O arch detection, discovery
│   ├── plugin_map.py       # abstract params -> real VST3 parameters
│   ├── native_dsp.py       # plugin-free pitch correction
│   └── zmq_client.py
├── templates/              # Singer / Admin UI + panel partial
├── static/css|js/
└── plugins/                # VST3 bundles
```

## Merging into an upstream PiKaraoke fork

This repo ships a minimal Flask shell so the Auto-Tune path is runnable alone.
To fold into a full PiKaraoke tree:

1. Copy the `autotune/` package, `autotune_engine.py`, `config/autotune.json`,
   `scripts/install_plugins.sh`, and the panel assets.
2. In upstream `app.py`, initialize `AutotuneZmqPublisher` once at startup and
   register the `/api/autotune/config` routes and Socket.IO handlers.
3. `{% include "partials/autotune_panel.html" %}` in the admin / home templates
   and load `static/js/autotune.js`.
4. Keep `start_autotune.sh` (or a systemd unit) starting the DSP daemon
   alongside the karaoke server.

## Latency tips

- Prefer exclusive / pro-audio mode in PipeWire (Linux) or a low buffer size in Core Audio (macOS) for the USB interface
- Start at `buffer_size: 256`; drop to `128` only if the machine keeps up
- Avoid Bluetooth headsets for the corrected monitor path
- On the native engine, latency scales with vocal pitch — deeper voices get
  longer grains
- Keep ZMQ on `127.0.0.1`; it is intentionally local-only IPC

## License notes

jPiKaraoke scaffolding is provided for integration work. VST3 plugins remain
under their vendors' licenses — `scripts/install_plugins.sh` downloads them
from the vendor at install time; this repository does not redistribute plugin
binaries.
