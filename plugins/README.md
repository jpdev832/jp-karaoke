# Plugins

VST3 pitch-correction bundles live here. The live path hosts them inside
`pedalboard.AudioStream`.

```bash
scripts/install_plugins.sh            # auto-pick for this OS/CPU
python autotune_engine.py --list-plugins
```

| Host | Auto-install |
|------|--------------|
| Linux aarch64 | `QPitch.vst3` (built from source) |
| Linux x86_64 | Graillon Free (Linux VST3) |
| macOS arm64 / x86_64 | `QPitch.vst3` (built from source) |

Wrong-OS or wrong-arch bundles (for example a Linux ELF Graillon on macOS) are
detected and skipped automatically; `engine: auto` falls back to the native
DSP if nothing compatible is present.

On macOS, Graillon Free is not auto-installed (its Mac path is a `.pkg`). You
can run the pkg and copy a macOS `.vst3` into this directory manually, or stick
with QPitch.
