#!/usr/bin/env bash
# Download / build pitch-correction VST3 plugins into ./plugins/.
#
# Usage:
#   scripts/install_plugins.sh              # best plugin for this OS/CPU
#   scripts/install_plugins.sh graillon     # Graillon Free (x86_64 Linux binary)
#   scripts/install_plugins.sh qpitch       # QPitch (prebuilt Linux x86_64, or source build)
#   scripts/install_plugins.sh --list
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT/plugins"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

GRAILLON_VERSION="3.1.1"
GRAILLON_URL="https://www.auburnsounds.com/downloads/Graillon-FREE-${GRAILLON_VERSION}.zip"
QPITCH_REPO="Skynse/qpitch"
QPITCH_GIT="https://github.com/${QPITCH_REPO}.git"

HOST_OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  amd64) HOST_ARCH="x86_64" ;;
esac
# Normalize arm64 → aarch64 on Linux only; keep arm64 on Darwin.
if [[ "$HOST_OS" == "linux" && "$HOST_ARCH" == "arm64" ]]; then
  HOST_ARCH="aarch64"
fi

info()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[31mxx\033[0m %s\n' "$*" >&2; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not installed."
}

require_cxx() {
  if command -v g++ >/dev/null 2>&1 \
    || command -v clang++ >/dev/null 2>&1 \
    || command -v c++ >/dev/null 2>&1; then
    return 0
  fi
  die "A C++ compiler (g++, clang++, or c++) is required."
}

cpu_jobs() {
  if command -v nproc >/dev/null 2>&1; then
    nproc
  elif [[ "$HOST_OS" == "darwin" ]]; then
    sysctl -n hw.ncpu 2>/dev/null || echo 2
  else
    echo 2
  fi
}

check_rubberband() {
  if [[ "$HOST_OS" == "darwin" ]]; then
    if command -v brew >/dev/null 2>&1 && brew --prefix rubberband >/dev/null 2>&1; then
      return 0
    fi
    warn "librubberband not found. QPitch loads it at runtime:"
    warn "  brew install rubberband"
    return 0
  fi
  if ! ldconfig -p 2>/dev/null | grep -q librubberband; then
    warn "librubberband not found. QPitch loads it at runtime:"
    warn "  sudo apt install librubberband2"
  fi
}

show_catalog() {
  cat <<EOF
Available pitch-correction plugins (host: ${HOST_OS}/${HOST_ARCH}):

  auto *      Best option for this OS/CPU.
              linux/aarch64  -> build QPitch from source
              linux/x86_64   -> download Graillon Free
              darwin/*       -> build QPitch from source

  graillon    Auburn Sounds Graillon 3 Free  (x86_64 Linux VST3)
              https://www.auburnsounds.com/products/Graillon.html
              On macOS: install via the .pkg from the Free zip, or copy a
              Mac .vst3 into plugins/ manually (not auto-installed).

  qpitch      QPitch (open source, JUCE)
              https://github.com/Skynse/qpitch
              linux/x86_64: download prebuilt release
              linux/aarch64 + darwin: clone + cmake build

  gsnap       GVST GSnap                     (manual)
              https://gvst.uk/Downloads/GSnap

  mautopitch  MeldaProduction MAutoPitch     (manual)
              https://www.meldaproduction.com/MAutoPitch

* = default for scripts/install_plugins.sh with no args
EOF
}

arch_warning() {
  local plugin_arch="$1" name="$2"
  # Treat arm64 (Darwin) and aarch64 (Linux) as the same CPU family.
  if [[ "$plugin_arch" == "$HOST_ARCH" ]] \
    || { [[ "$plugin_arch" == "aarch64" && "$HOST_ARCH" == "arm64" ]]; } \
    || { [[ "$plugin_arch" == "arm64" && "$HOST_ARCH" == "aarch64" ]]; }; then
    return 0
  fi
  warn "$name is built for $plugin_arch but this host is ${HOST_OS}/${HOST_ARCH}."
  warn "The plugin will install but cannot be loaded here."
  warn "On ARM run: scripts/install_plugins.sh qpitch"
}

install_graillon() {
  if [[ "$HOST_OS" == "darwin" ]]; then
    die "Graillon Free auto-install is Linux-only (copies the Linux VST3).
On macOS:
  1. Download Graillon Free and run the Mac/*.pkg installer, or
  2. Copy a macOS .vst3 into: $PLUGIN_DIR/
  3. Verify with: python autotune_engine.py --list-plugins
Or build QPitch instead: scripts/install_plugins.sh qpitch"
  fi

  require curl
  require unzip
  local archive="$WORK_DIR/graillon.zip"

  info "Downloading Graillon Free ${GRAILLON_VERSION} (~58 MB)…"
  curl -fL --progress-bar -o "$archive" "$GRAILLON_URL" \
    || die "Download failed: $GRAILLON_URL"

  info "Extracting…"
  unzip -q -o "$archive" -d "$WORK_DIR/graillon"

  local bundle
  bundle="$(find "$WORK_DIR/graillon" -maxdepth 4 -type d -name '*.vst3' -path '*Linux*' | head -n1)"
  [[ -n "$bundle" ]] || die "No Linux VST3 bundle found inside the archive."

  mkdir -p "$PLUGIN_DIR"
  rm -rf "$PLUGIN_DIR/$(basename "$bundle")"
  cp -a "$bundle" "$PLUGIN_DIR/"
  info "Installed: plugins/$(basename "$bundle")"
  arch_warning "x86_64" "Graillon Free"
}

install_qpitch_prebuilt() {
  require curl
  require unzip
  local asset
  asset="$(curl -fsSL "https://api.github.com/repos/${QPITCH_REPO}/releases/latest" \
    | grep -oE '"browser_download_url": "[^"]*linux-vst3\.zip"' \
    | cut -d'"' -f4 | head -n1)"
  [[ -n "$asset" ]] || die "Could not find a QPitch Linux VST3 release asset."

  info "Downloading QPitch prebuilt…"
  curl -fL --progress-bar -o "$WORK_DIR/qpitch.zip" "$asset"
  unzip -q -o "$WORK_DIR/qpitch.zip" -d "$WORK_DIR/qpitch"

  local bundle
  bundle="$(find "$WORK_DIR/qpitch" -maxdepth 4 -type d -name '*.vst3' | head -n1)"
  [[ -n "$bundle" ]] || die "No VST3 bundle found inside the QPitch archive."

  mkdir -p "$PLUGIN_DIR"
  rm -rf "$PLUGIN_DIR/$(basename "$bundle")"
  cp -a "$bundle" "$PLUGIN_DIR/"
  info "Installed: plugins/$(basename "$bundle")"
  arch_warning "x86_64" "QPitch prebuilt"
}

install_qpitch_from_source() {
  require git
  require cmake
  require pkg-config
  require_cxx

  info "Building QPitch from source for ${HOST_OS}/${HOST_ARCH} (this takes a few minutes)…"
  local src="$WORK_DIR/qpitch-src"
  git clone --recurse-submodules --depth 1 "$QPITCH_GIT" "$src"

  cmake -S "$src" -B "$src/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "$src/build" --target QPitch_VST3 -j"$(cpu_jobs)"

  local bundle="$src/build/QPitch_artefacts/Release/VST3/QPitch.vst3"
  [[ -d "$bundle" ]] || die "Build succeeded but VST3 bundle missing at $bundle"

  mkdir -p "$PLUGIN_DIR"
  rm -rf "$PLUGIN_DIR/QPitch.vst3"
  cp -a "$bundle" "$PLUGIN_DIR/QPitch.vst3"
  info "Installed: plugins/QPitch.vst3 (native ${HOST_OS}/${HOST_ARCH})"

  check_rubberband
}

install_qpitch() {
  if [[ "$HOST_OS" == "darwin" ]]; then
    install_qpitch_from_source
  elif [[ "$HOST_ARCH" == "aarch64" || "$HOST_ARCH" == "arm" ]]; then
    install_qpitch_from_source
  else
    install_qpitch_prebuilt
  fi
}

install_auto() {
  case "$HOST_OS" in
    darwin)
      info "Host is darwin/${HOST_ARCH}: installing QPitch built from source (VST3 path)."
      install_qpitch_from_source
      ;;
    linux)
      case "$HOST_ARCH" in
        aarch64|arm)
          info "Host is linux/${HOST_ARCH}: installing QPitch built from source (VST3 path)."
          install_qpitch_from_source
          ;;
        x86_64)
          info "Host is linux/x86_64: installing Graillon Free (default VST3)."
          install_graillon
          ;;
        *)
          die "Unsupported architecture '${HOST_ARCH}'. Try: scripts/install_plugins.sh qpitch"
          ;;
      esac
      ;;
    *)
      die "Unsupported OS '${HOST_OS}'. Supported: linux, darwin."
      ;;
  esac
}

manual_only() {
  local name="$1" url="$2"
  warn "$name has no stable direct download link."
  echo "  1. Download the VST3 for your OS from: $url"
  echo "  2. Copy the .vst3 bundle into:         $PLUGIN_DIR/"
  echo "  3. Verify with:                        python autotune_engine.py --list-plugins"
}

main() {
  local target="${1:-auto}"
  case "$target" in
    --list|-l|list)  show_catalog ;;
    auto)            install_auto ;;
    graillon)        install_graillon ;;
    qpitch)          install_qpitch ;;
    gsnap)           manual_only "GSnap" "https://gvst.uk/Downloads/GSnap" ;;
    mautopitch)      manual_only "MAutoPitch" "https://www.meldaproduction.com/MAutoPitch" ;;
    -h|--help)       show_catalog ;;
    *)               die "Unknown plugin '$target'. Run with --list to see options." ;;
  esac

  if [[ "$target" != "--list" && "$target" != "-l" && "$target" != "list" && "$target" != "-h" && "$target" != "--help" ]]; then
    echo
    info "Installed plugins:"
    if [[ -x "$ROOT/.venv/bin/python" ]]; then
      PYTHONPATH="$ROOT" "$ROOT/.venv/bin/python" "$ROOT/autotune_engine.py" --list-plugins || true
    else
      ls -1 "$PLUGIN_DIR" 2>/dev/null || true
    fi
  fi
}

main "$@"
