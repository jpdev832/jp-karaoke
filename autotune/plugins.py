"""Catalog + discovery for pitch-correction VST3 plugins.

VST3 bundles are architecture- and OS-specific. Most free Linux pitch-correction
plugins ship x86_64-only ELF builds, which will not load on aarch64 (Raspberry Pi,
Jetson, Grace) or on macOS. Discovery therefore reports *why* a bundle is
unusable instead of failing with an opaque scan error.
"""

from __future__ import annotations

import logging
import platform
import struct
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ELF e_machine values we care about.
_ELF_MACHINES = {0x3E: "x86_64", 0xB7: "aarch64", 0x28: "arm", 0xF3: "riscv64"}

# Mach-O CPU types (cpu_type_t). ARM64 and X86_64 use the ABI64 bit.
_MACHO_CPU_TYPES = {
    0x00000007: "x86_64",  # CPU_TYPE_X86 (rare in VST3; usually ABI64)
    0x01000007: "x86_64",  # CPU_TYPE_X86_64
    0x0000000C: "arm",  # CPU_TYPE_ARM
    0x0100000C: "aarch64",  # CPU_TYPE_ARM64
}

_ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
    "armv7l": "arm",
}

# Thin Mach-O magics (native endian and swapped).
_MH_MAGIC = 0xFEEDFACE
_MH_CIGAM = 0xCEFAEDFE
_MH_MAGIC_64 = 0xFEEDFACF
_MH_CIGAM_64 = 0xCFFAEDFE
_FAT_MAGIC = 0xCAFEBABE
_FAT_CIGAM = 0xBEBAFECA


@dataclass(frozen=True)
class PluginSpec:
    """A known pitch-correction plugin and how to drive it."""

    slug: str
    name: str
    profile: str
    bundle: str
    url: str
    architectures: tuple[str, ...]
    notes: str = ""


CATALOG: tuple[PluginSpec, ...] = (
    PluginSpec(
        slug="qpitch",
        name="QPitch",
        profile="qpitch",
        bundle="QPitch.vst3",
        url="https://github.com/Skynse/qpitch",
        architectures=("x86_64", "aarch64"),
        notes="Default VST3 path. Open source; build from source on ARM/macOS via scripts/install_plugins.sh.",
    ),
    PluginSpec(
        slug="graillon",
        name="Auburn Sounds Graillon 3 (Free)",
        profile="graillon",
        bundle="Auburn Sounds Graillon 3.vst3",
        url="https://www.auburnsounds.com/downloads/Graillon-FREE-3.1.1.zip",
        architectures=("x86_64",),
        notes="Excellent free plugin; auto-install copies the Linux x86_64 VST3. On macOS install the .pkg manually.",
    ),
    PluginSpec(
        slug="gsnap",
        name="GVST GSnap",
        profile="gsnap",
        bundle="GSnap.vst3",
        url="https://gvst.uk/Downloads/GSnap",
        architectures=("x86_64",),
        notes="Classic free autotune; manual download.",
    ),
    PluginSpec(
        slug="mautopitch",
        name="MeldaProduction MAutoPitch",
        profile="mautopitch",
        bundle="MAutoPitch.vst3",
        url="https://www.meldaproduction.com/MAutoPitch",
        architectures=("x86_64",),
        notes="Free Melda effect; manual download via Melda installer.",
    ),
)

BY_SLUG = {spec.slug: spec for spec in CATALOG}
BY_BUNDLE = {spec.bundle.lower(): spec for spec in CATALOG}


def host_architecture() -> str:
    machine = platform.machine().lower()
    return _ARCH_ALIASES.get(machine, machine)


def host_os() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "linux":
        return "linux"
    return system


def _macho_cpu_arch(cpu_type: int) -> str | None:
    return _MACHO_CPU_TYPES.get(cpu_type & 0xFFFFFFFF)


def _parse_macho_arches(data: bytes) -> set[str]:
    """Return CPU arches from a thin or fat Mach-O header."""
    if len(data) < 8:
        return set()
    (magic,) = struct.unpack(">I", data[:4])
    arches: set[str] = set()

    if magic in (_FAT_MAGIC, _FAT_CIGAM):
        little = magic == _FAT_CIGAM
        fmt = "<II" if little else ">II"
        if len(data) < 8:
            return set()
        _, nfat = struct.unpack(fmt, data[:8])
        offset = 8
        for _ in range(min(nfat, 16)):
            if offset + 20 > len(data):
                break
            arch_fmt = "<iiIII" if little else ">iiIII"
            cpu_type, _cpu_subtype, _off, _size, _align = struct.unpack(
                arch_fmt, data[offset : offset + 20]
            )
            arch = _macho_cpu_arch(cpu_type)
            if arch:
                arches.add(arch)
            offset += 20
        return arches

    # Thin Mach-O: magic may be native or byte-swapped.
    if magic in (_MH_MAGIC, _MH_MAGIC_64):
        little = False
    elif magic in (_MH_CIGAM, _MH_CIGAM_64):
        little = True
    else:
        # Native-endian thin headers on little-endian hosts store swapped magic
        # when read as big-endian above; try little-endian magic check.
        (magic_le,) = struct.unpack("<I", data[:4])
        if magic_le in (_MH_MAGIC, _MH_MAGIC_64):
            little = True
            magic = magic_le
        elif magic_le in (_MH_CIGAM, _MH_CIGAM_64):
            little = False
            magic = magic_le
        else:
            return set()

    if len(data) < 8:
        return set()
    cpu_fmt = "<i" if little else ">i"
    (cpu_type,) = struct.unpack(cpu_fmt, data[4:8])
    arch = _macho_cpu_arch(cpu_type)
    if arch:
        arches.add(arch)
    return arches


def _binary_candidates(bundle_path: Path) -> list[Path]:
    if bundle_path.is_file():
        return [bundle_path]
    candidates: list[Path] = []
    candidates.extend(bundle_path.rglob("*.so"))
    candidates.extend(bundle_path.rglob("*.dylib"))
    for pattern in ("Contents/MacOS/*", "Contents/*-macos/*", "Contents/*-osx/*"):
        candidates.extend(p for p in bundle_path.glob(pattern) if p.is_file())
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def bundle_platforms(bundle_path: Path) -> set[str]:
    """Infer target OS from binary formats and Contents layout."""
    platforms: set[str] = set()
    for binary in _binary_candidates(bundle_path):
        try:
            with binary.open("rb") as handle:
                header = handle.read(4096)
        except OSError:
            continue
        if len(header) >= 4 and header[:4] == b"\x7fELF":
            platforms.add("linux")
        elif _parse_macho_arches(header):
            platforms.add("darwin")

    if not bundle_path.is_dir():
        return platforms

    for child in bundle_path.glob("Contents/*"):
        if not child.is_dir():
            continue
        name = child.name.lower()
        if name.endswith("-linux") or name == "linux":
            platforms.add("linux")
        elif name.endswith(("-macos", "-osx", "-darwin")) or name in ("macos", "mac"):
            platforms.add("darwin")
    return platforms


def bundle_architectures(bundle_path: Path) -> set[str]:
    """Read ELF/Mach-O headers (and Contents dir names) to learn bundle arches."""
    arches: set[str] = set()
    for binary in _binary_candidates(bundle_path):
        try:
            with binary.open("rb") as handle:
                header = handle.read(4096)
        except OSError:
            continue
        if len(header) >= 20 and header[:4] == b"\x7fELF":
            little = header[5] == 1
            (machine,) = struct.unpack("<H" if little else ">H", header[18:20])
            arches.add(_ELF_MACHINES.get(machine, f"unknown(0x{machine:x})"))
            continue
        arches.update(_parse_macho_arches(header))

    if not bundle_path.is_dir():
        return arches

    # VST3 bundles encode arch in directory names (x86_64-linux, arm64-macos, …).
    for child in bundle_path.glob("Contents/*"):
        if not child.is_dir():
            continue
        name = child.name
        lowered = name.lower()
        for suffix in ("-linux", "-macos", "-osx", "-darwin"):
            if lowered.endswith(suffix):
                raw = name[: -len(suffix)]
                arches.add(_ARCH_ALIASES.get(raw.lower(), raw.lower()))
                break
        else:
            if lowered in ("macos", "mac"):
                # Universal or arch-unspecified Mac layout; arches come from Mach-O.
                continue
    return arches


@dataclass
class DiscoveredPlugin:
    path: Path
    spec: PluginSpec | None
    architectures: set[str]
    platforms: set[str] = field(default_factory=set)

    @property
    def profile(self) -> str:
        return self.spec.profile if self.spec else "generic"

    @property
    def name(self) -> str:
        return self.spec.name if self.spec else self.path.stem

    def compatible_with(
        self,
        arch: str | None = None,
        os_name: str | None = None,
    ) -> bool:
        arch = arch or host_architecture()
        os_name = os_name or host_os()
        if self.platforms and os_name not in self.platforms:
            return False
        # No detectable arch info: let pedalboard try and report.
        return not self.architectures or arch in self.architectures

    def incompatibility_reason(
        self,
        arch: str | None = None,
        os_name: str | None = None,
    ) -> str | None:
        arch = arch or host_architecture()
        os_name = os_name or host_os()
        if self.compatible_with(arch, os_name):
            return None
        if self.platforms and os_name not in self.platforms:
            found_os = ", ".join(sorted(self.platforms)) or "unknown"
            return (
                f"{self.path.name} is built for {found_os} but this host is {os_name}. "
                "VST3 bundles cannot be loaded across operating systems."
            )
        found = ", ".join(sorted(self.architectures)) or "unknown"
        return (
            f"{self.path.name} is built for {found} but this host is {arch}. "
            "VST3 bundles cannot be loaded across architectures."
        )


def _make_discovered(path: Path, spec: PluginSpec | None) -> DiscoveredPlugin:
    return DiscoveredPlugin(
        path,
        spec,
        bundle_architectures(path),
        bundle_platforms(path),
    )


def discover_plugins(plugins_dir: Path) -> list[DiscoveredPlugin]:
    """Find VST3 bundles in a directory, newest catalog matches first."""
    if not plugins_dir.is_dir():
        return []
    found: list[DiscoveredPlugin] = []
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.name.lower().endswith(".vst3"):
            continue
        spec = BY_BUNDLE.get(entry.name.lower())
        if spec is None:
            # Fuzzy match: "Auburn Sounds Graillon 3.vst3" -> graillon
            lowered = entry.name.lower()
            for candidate in CATALOG:
                if candidate.slug in lowered:
                    spec = candidate
                    break
        found.append(_make_discovered(entry, spec))

    # Prefer plugins that can actually run here.
    found.sort(key=lambda p: (not p.compatible_with(), p.path.name))
    return found


def select_plugin(
    plugins_dir: Path,
    preferred: Path | None = None,
    *,
    require_compatible: bool = True,
) -> DiscoveredPlugin | None:
    """Pick the best usable plugin.

    Prefers an explicit path when it is architecture-compatible. Otherwise
    falls through to the first compatible bundle in the plugins directory so a
    preferred x86_64 Graillon does not block an aarch64 QPitch on the same host.
    """
    discovered = discover_plugins(plugins_dir)
    arch = host_architecture()
    os_name = host_os()

    if preferred is not None:
        preferred = preferred.resolve()
        for plugin in discovered:
            if plugin.path.resolve() == preferred or plugin.path.name == preferred.name:
                if not require_compatible or plugin.compatible_with(arch, os_name):
                    return plugin
                logger.info(
                    "Preferred plugin %s is not usable on %s/%s; looking for another VST3.",
                    plugin.path.name,
                    os_name,
                    arch,
                )
                break
        else:
            if preferred.exists():
                candidate = _make_discovered(preferred, BY_BUNDLE.get(preferred.name.lower()))
                if not require_compatible or candidate.compatible_with(arch, os_name):
                    return candidate

    for plugin in discovered:
        if plugin.compatible_with(arch, os_name):
            return plugin

    if require_compatible:
        return None
    return discovered[0] if discovered else None
