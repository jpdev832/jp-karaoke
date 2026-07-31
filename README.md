# JP Karaoke

Karaoke host with live Auto-Tune for USB mics. Guests scan a QR code, pick a song,
and can optionally enable Auto-Tune (key, scale, retune speed, mix) for **their
song only** — settings restore to off when the song ends.

Based on [PiKaraoke](https://github.com/vicwomg/pikaraoke) with an integrated
real-time pitch-correction DSP (`autotune_engine.py` + VST3 / native).

## Quick start

Requires **Python 3.10+** (macOS system / Xcode Python 3.9 is too old) and **ffmpeg**.

```bash
# macOS (Homebrew): use a modern Python, not Xcode's 3.9
brew install python@3.12 ffmpeg
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip   # needed for editable installs (hatchling)
pip install -e .
scripts/install_plugins.sh            # QPitch on ARM/macOS; Graillon on Linux x86_64
./start.sh                            # karaoke web + Auto-Tune DSP
```

On Linux, `python3` 3.10+ is usually fine:

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
scripts/install_plugins.sh
./start.sh
```

Plugin / audio extras: [docs/AUTOTUNE.md](docs/AUTOTUNE.md).

Open the splash URL (shown in the log / browser). Guests scan the QR code to
search, queue songs, and set Auto-Tune on the Search or Browse page.

| Page | Purpose |
|------|---------|
| `/splash` | TV player + QR |
| `/search` | Find / download songs + per-song Auto-Tune |
| `/queue` | Queue (AT badge when Auto-Tune is attached) |
| `/autotune` | Live host Auto-Tune override panel |

## How Auto-Tune works

```
USB Mic → autotune_engine.py → speakers
                 ▲
                 │ ZeroMQ
Browser / queue ─┘  (apply on song start, restore off when song ends)
```

Karaoke tracks play through PiKaraoke’s player; corrected vocals are a separate
mic monitor path. Details: [docs/AUTOTUNE.md](docs/AUTOTUNE.md).

## Attribution

Karaoke core is forked from PiKaraoke (Vic Wong / contributors). Auto-Tune
integration is JP Karaoke.
