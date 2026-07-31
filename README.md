# jPiKaraoke

PiKaraoke-style karaoke host with a real-time Auto-Tune DSP pipeline.

Live path: USB mic → `pedalboard.AudioStream` → VST3 (QPitch / Graillon) → speakers,
with controls over Socket.IO + ZeroMQ.

Works on **Linux** and **macOS** (Apple Silicon and Intel). See
**[README_AUTOTUNE.md](README_AUTOTUNE.md)** for full setup.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
scripts/install_plugins.sh    # QPitch on ARM/macOS; Graillon on Linux x86_64
./start_autotune.sh
```
