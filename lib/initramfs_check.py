#!/usr/bin/env python3
"""Read-only compression and root-coverage inspection. Never source configuration or run a builder.

Discovery is serialized once; validation uses the final olddefconfig result.
Image inspection checks stream framing/decoders, not bootability or signatures.
Root coverage reads /proc/self/mountinfo and sysfs to learn the md/LVM/LUKS
layers under / and checks that the generator (ugrd) lists the module that
assembles each layer and that the kernel provides its symbols.
"""

import argparse
import bz2
import json
import lzma
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import zlib


FORMATS = ("gzip", "bzip2", "lzma", "xz", "lzo", "lz4", "zstd")
MAX_IMAGE = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBERS = 256

# Root device coverage: generator module that assembles/opens each block layer
# of the root device, and the kernel symbols the layer needs (y or m is enough,
# the initramfs loads modules). Levels follow md/level in sysfs.
ROOT_LAYER_MODULES = {"md": "ugrd.fs.mdraid", "lvm": "ugrd.fs.lvm", "crypt": "ugrd.crypto.cryptsetup"}
ROOT_LAYER_KCONFIG = {"md": ("BLK_DEV_MD",), "lvm": ("BLK_DEV_DM",), "crypt": ("BLK_DEV_DM", "DM_CRYPT"),
                      "dm": ("BLK_DEV_DM",)}
RAID_LEVEL_KCONFIG = {"raid0": "MD_RAID0", "raid1": "MD_RAID1", "raid10": "MD_RAID10", "raid4": "MD_RAID456",
                      "raid5": "MD_RAID456", "raid6": "MD_RAID456", "linear": "MD_LINEAR"}
MAX_LAYER_DEPTH = 8


class Unknown(ValueError):
    """Insufficient evidence, distinct from a known incompatible image."""


def kernel_values(path):
    return dict(re.findall(r"^CONFIG_(\w+)=(.*)$", Path(path).read_text(), re.M))


def shell_literals(path, keys):
    """Accept literal assignments only. Shell expressions are never evaluated."""
    result = {}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if re.match(r"(?:source|\.)\s", stripped):
            raise Unknown(f"{path}:{number}: sourced configuration cannot be resolved safely")
        match = re.match(r"(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)=(.*)$", stripped)
        if not match:
            raise Unknown(f"{path}:{number}: shell commands/control flow cannot be resolved safely")
        if match[1] not in keys:
            if any(re.search(r"\b" + re.escape(key) + r"\s*=", stripped) for key in keys):
                raise Unknown(f"{path}:{number}: nonliteral assignment")
            continue
        raw = match[2]
        if match[1] == "GK_SHARE" and raw.strip() == '"${GK_SHARE:-/usr/share/genkernel}"':
            result[match[1]] = os.environ.get("GK_SHARE") or "/usr/share/genkernel"
            continue
        if any(char in raw for char in ("$", "`", ";", "\\")):
            raise Unknown(f"{path}:{number}: dynamic assignment for {match[1]}")
        words = shlex.split(raw, comments=True)
        if len(words) != 1:
            raise Unknown(f"{path}:{number}: ambiguous assignment for {match[1]}")
        result[match[1]] = words[0]
    return result


def normalize_format(value):
    if value is False or str(value).lower() in ("false", "no", "none"):
        return "none"
    if value is True or str(value).lower() == "true":
        return "xz"
    value = str(value).lower()
    return {"lzop": "lzo", "gz": "gzip", "bz2": "bzip2", "zst": "zstd"}.get(value, value)


def scan_cpio(data, offset):
    """Skip a newc/crc archive using lengths, never search for magic in contents."""
    start = offset
    for _ in range(1_000_000):
        header = data[offset:offset + 110]
        if len(header) != 110 or header[:6] not in (b"070701", b"070702"):
            raise ValueError("invalid/truncated CPIO header")
        try:
            fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        except ValueError as exc:
            raise ValueError("invalid CPIO length") from exc
        size, namesize = fields[6], fields[11]
        if not 1 <= namesize <= 4096:
            raise ValueError("invalid CPIO filename size")
        name_end = offset + 110 + namesize
        name = data[offset + 110:name_end]
        if len(name) != namesize or not name.endswith(b"\0"):
            raise ValueError("truncated CPIO filename")
        payload = start + ((name_end - start + 3) & ~3)
        if payload + size > len(data):
            raise ValueError("truncated CPIO payload")
        offset = start + ((payload + size - start + 3) & ~3)
        if name == b"TRAILER!!!\0":
            if size:
                raise ValueError("invalid CPIO trailer")
            return offset
        if offset == len(data):  # A trailer is optional in the kernel format.
            return offset
    raise Unknown("CPIO entry limit exceeded")


def zstd_frame_end(data, start):
    """Locate a standard ZSTD frame without allocating its expanded contents."""
    pos = start + 4
    if pos >= len(data):
        raise ValueError("truncated ZSTD header")
    descriptor = data[pos]
    pos += 1
    if descriptor & 0x18:
        raise ValueError("reserved ZSTD frame bits")
    single = bool(descriptor & 0x20)
    pos += 0 if single else 1  # window descriptor
    dictionary_bytes = (0, 1, 2, 4)[descriptor & 3]
    if pos + dictionary_bytes > len(data):
        raise ValueError("truncated ZSTD dictionary ID")
    if int.from_bytes(data[pos:pos + dictionary_bytes], "little"):
        raise ValueError("ZSTD external dictionaries are not supported for initramfs")
    pos += dictionary_bytes
    flag = descriptor >> 6
    pos += (1 if single else 0) if flag == 0 else (2, 4, 8)[flag - 1]
    while True:
        if pos + 3 > len(data):
            raise ValueError("truncated ZSTD block header")
        block = int.from_bytes(data[pos:pos + 3], "little")
        pos += 3
        kind, size = (block >> 1) & 3, block >> 3
        if kind == 3 or size > 128 * 1024:
            raise ValueError("invalid ZSTD block")
        pos += 1 if kind == 1 else size
        if pos > len(data):
            raise ValueError("truncated ZSTD block")
        if block & 1:
            pos += 4 if descriptor & 4 else 0
            if pos > len(data):
                raise ValueError("truncated ZSTD checksum")
            return pos


def lz4_frame_end(data, start):
    """Kernel legacy framing: size-prefixed blocks, EOF or zero terminator."""
    pos, blocks = start + 4, 0
    limit = (8 << 20) + (8 << 20) // 255 + 16
    while pos < len(data):
        if len(data) - pos < 4:
            if not any(data[pos:]):
                break
            raise ValueError("truncated legacy LZ4 block size")
        size = int.from_bytes(data[pos:pos + 4], "little")
        if not size:
            break
        pos += 4
        if size == 0x184C2102:  # concatenated legacy frame, accepted by Linux
            continue
        if size > limit or pos + size > len(data):
            raise ValueError("invalid/truncated legacy LZ4 block")
        blocks += 1
        pos += size
    if not blocks:
        raise ValueError("legacy LZ4 image has no blocks")
    return pos


def lzo_frame_end(data, start):
    """Parse the lzop framing/checksum layout accepted by decompress_unlzo.c."""
    pos = start + 9

    def take(count):
        nonlocal pos
        if pos + count > len(data):
            raise ValueError("truncated LZO frame")
        value = data[pos:pos + count]
        pos += count
        return value

    version = int.from_bytes(take(2), "big")
    take(4)  # library / extraction versions
    if take(1)[0] not in (1, 2, 3):
        raise ValueError("unsupported LZO method")
    if version >= 0x0940:
        take(1)  # level
    flags = int.from_bytes(take(4), "big")
    if flags & (0x800 | 0x40 | 0x2 | 0x200) or (flags & 0x101) not in (1, 0x100):
        raise ValueError("LZO flags/checksum layout unsupported by the kernel initramfs decoder")
    take(8 + (4 if version >= 0x0940 else 0))  # mode, mtime
    take(take(1)[0] + 4)  # filename and header checksum
    while True:
        expanded = int.from_bytes(take(4), "big")
        if expanded == 0:
            return pos
        size = int.from_bytes(take(4), "big")
        if expanded > 256 * 1024 or not 0 < size <= expanded:
            raise ValueError("invalid LZO block size")
        take(4)  # exactly one decompressed block checksum, as in Linux
        take(size)


def inspect_image(path):
    path = Path(path)
    if not path.is_file():
        raise Unknown(f"image is not a regular file: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise Unknown("image exceeds 128 MiB inspection limit")
    offset, expanded = 0, 0
    members = []
    while offset < len(data):
        if len(members) >= MAX_MEMBERS:
            raise Unknown("image member limit exceeded")
        if data[offset] == 0:
            offset = re.compile(b"\x00*").match(data, offset).end()
            continue
        start = offset
        head = data[offset:offset + 16]
        member = {"offset": offset}
        if head.startswith((b"070701", b"070702")):
            member["format"] = "none"
            offset = scan_cpio(data, offset)
        elif head.startswith(b"\x28\xb5\x2f\xfd"):
            member.update(format="zstd", validation="framing only; payload/checksum not verified")
            offset = zstd_frame_end(data, offset)
        elif head.startswith(b"\x04\x22\x4d\x18"):
            raise ValueError("modern LZ4 frame: the kernel initramfs decoder requires legacy LZ4")
        elif head.startswith(b"\x02\x21\x4c\x18"):
            member.update(format="lz4", validation="legacy framing only; payload not verified")
            offset = lz4_frame_end(data, offset)
        elif head.startswith(b"\x89LZO\x00\x0d\x0a\x1a\x0a"):
            member.update(format="lzo", validation="framing only; payload/checksums not verified")
            offset = lzo_frame_end(data, offset)
        else:
            if head.startswith(b"\xfd7zXZ\x00"):
                if len(head) < 12 or head[6] != 0 or head[7] not in (0, 1):
                    raise ValueError("XZ requires CRC32 or no integrity check; CRC64/SHA256 are unsupported")
                kind, decoder = "xz", lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_EXPANDED)
                member["check"] = "CRC32" if head[7] == 1 else "none"
            elif head.startswith(b"\x1f\x8b"):
                kind, decoder = "gzip", zlib.decompressobj(31)
            elif head.startswith(b"BZh"):
                kind, decoder = "bzip2", bz2.BZ2Decompressor()
            elif len(head) >= 13 and head[0] == 0x5d:
                kind, decoder = "lzma", lzma.LZMADecompressor(format=lzma.FORMAT_ALONE, memlimit=MAX_EXPANDED)
            else:
                raise Unknown(f"unrecognized image member at offset {offset} (UKI/wrappers are not supported)")
            member["format"] = kind
            try:
                output = decoder.decompress(data[offset:], MAX_EXPANDED - expanded + 1)
            except (lzma.LZMAError, zlib.error, OSError, EOFError) as exc:
                raise ValueError(f"invalid {kind} stream: {exc}") from exc
            expanded += len(output)
            if expanded > MAX_EXPANDED:
                raise Unknown("image exceeds 256 MiB expanded inspection limit")
            if not decoder.eof:
                raise ValueError(f"truncated {kind} stream")
            offset = len(data) - len(decoder.unused_data)
            # A compressed member may contain several uncompressed CPIO archives.
            pos = 0
            while pos < len(output):
                if output[pos] == 0:
                    pos = re.compile(b"\x00*").match(output, pos).end()
                else:
                    pos = scan_cpio(output, pos)
            member["validation"] = "decompression and CPIO framing"
        if offset <= start:
            raise ValueError("invalid image boundary")
        members.append(member)
    if not members:
        raise ValueError("empty initramfs image")
    return {"path": str(path), "members": members}


def ugrd_capabilities(command):
    """Inspect the builder's interpreter, without importing/running ugrd."""
    launcher = Path(command).resolve()
    if launcher.name.startswith("python-exec"):
        candidates = list(Path("/usr/lib/python-exec").glob("python*/ugrd"))
        if len(candidates) != 1:
            raise Unknown("cannot resolve ugrd's python-exec interpreter unambiguously")
        launcher = candidates[0]
    with launcher.open("rb") as stream:
        first = stream.readline(512).decode(errors="replace").strip()
    if not first.startswith("#!"):
        raise Unknown("ugrd launcher has no Python shebang")
    args = shlex.split(first[2:])
    if args and Path(args[0]).name == "env":
        args = args[1:]
    if len(args) != 1 or not re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(args[0]).name):
        raise Unknown("ugrd interpreter could not be determined")
    interpreter = shutil.which(args[0])
    if not interpreter:
        raise Unknown("ugrd Python interpreter is unavailable")
    probe = '''import importlib.metadata as m, importlib, json
from pathlib import Path
import tomllib
d=m.distribution("ugrd")
defaults=tomllib.loads(Path(d.locate_file("ugrd/fs/cpio.toml")).read_text())
def usable(name):
    try:
        return callable(getattr(importlib.import_module(name), "compress", None))
    except ImportError:
        return False
print(json.dumps({"version":d.version,"default":defaults.get("cpio_compression"),
                  "xz":usable("lzma"),"zstd":usable("zstandard")}))'''
    result = subprocess.run([interpreter, "-B", "-c", probe], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise Unknown("cannot inspect ugrd metadata/dependencies in its interpreter")
    return json.loads(result.stdout)


class Inspector:
    def __init__(self, root=Path("/"), which=shutil.which, probe=ugrd_capabilities):
        self.root, self.which, self.probe = Path(root), which, probe

    def path(self, name):
        return self.root / name.lstrip("/")

    def generator(self, requested):
        if requested != "auto":
            return requested, "explicit selection"
        # kernel-install's /etc file replaces the vendor file, rather than
        # being overlaid on it. Do not infer usage from leftover builder config.
        for name in ("/etc/kernel/install.conf", "/usr/lib/kernel/install.conf"):
            path = self.path(name)
            if path.exists():
                setting = shell_literals(path, {"initrd_generator"}).get("initrd_generator")
                if setting:
                    return setting, str(path)
                break
        available = [name for name in ("genkernel", "ugrd") if self.which(name)]
        if len(available) == 1:
            return available[0], "only installed supported generator (inferred)"
        raise Unknown("no unambiguous generator; select --initramfs-generator explicitly")

    def discover(self, generator="auto", config="", image="", compression="auto"):
        result = {"generator": generator, "source": "", "version": "unknown", "compression": None,
                  "required_formats": [], "dynamic_formats": [], "needs_initrd": bool(image),
                  "issues": [], "notes": [], "images": [], "root": None}
        try:
            selected, source = self.generator(generator)
            result.update(generator=selected, source=source)
            if selected == "none":
                if config or compression != "auto":
                    raise Unknown("producer config/compression requires a generator")
            elif selected not in ("genkernel", "ugrd"):
                raise Unknown(f"unsupported configured generator: {selected}")
            else:
                result["needs_initrd"] = True
                command = self.which(selected)
                if not command:
                    raise Unknown(f"configured generator is not installed: {selected}")
                if selected == "ugrd":
                    self.ugrd(result, command, config, compression)
                else:
                    self.genkernel(result, config, compression)
        except (Unknown, OSError, ValueError, subprocess.SubprocessError) as exc:
            result["issues"].append({"status": "unknown", "message": str(exc)})
        if result["compression"] in FORMATS:
            result["required_formats"].append(result["compression"])
        if image:
            try:
                inspected = inspect_image(image)
                result["images"].append(inspected)
                result["required_formats"].extend(m["format"] for m in inspected["members"] if m["format"] != "none")
            except Unknown as exc:
                result["issues"].append({"status": "unknown", "message": str(exc)})
            except (OSError, ValueError) as exc:
                result["issues"].append({"status": "incompatible", "message": str(exc)})
        result["required_formats"] = sorted(set(result["required_formats"]))
        return result

    def ugrd(self, result, command, config, compression):
        caps = self.probe(command)
        result["version"] = caps["version"]
        # Capabilities below are verified for ugrd 2.x/PyCPIO's xz/zstd writer.
        if not caps["version"].startswith("2."):
            raise Unknown(f"ugrd {caps['version']}: compression adapter supports 2.x; inspect image explicitly")
        path = Path(config) if config else self.path("/etc/ugrd/config.toml")
        settings = {}
        if path.exists():
            settings = tomllib.loads(path.read_text())
        elif config:
            raise Unknown(f"missing ugrd configuration: {path}")
        if any(key in settings for key in ("imports", "custom_parameters", "module_path")):
            raise Unknown("custom ugrd build hooks/parameters require manual compression verification")
        result["config"] = str(path)
        self.root_coverage(result, "ugrd", settings)
        value = settings.get("cpio_compression", caps["default"])
        if compression != "auto":
            value = compression
            result["notes"].append("Producer CLI compression override supplied; builder configuration was not edited")
        value = normalize_format(value)
        if value not in ("none", "xz", "zstd"):
            result["issues"].append({"status": "incompatible", "message": f"ugrd/PyCPIO does not support {value}"})
            return
        result["compression"] = value
        if value != "none" and not caps[value]:
            result["issues"].append({"status": "incompatible", "message": f"ugrd Python lacks {'zstandard' if value == 'zstd' else 'lzma'}; a compressor executable is not sufficient"})

    def genkernel(self, result, config, compression):
        share = self.path("/usr/share/genkernel")
        keys = {"COMPRESS_INITRD", "COMPRESS_INITRD_TYPE", "INTEGRATED_INITRAMFS", "GK_SHARE"}
        table = share / "defaults/compression_methods.sh"
        if not table.is_file():
            raise Unknown("genkernel compression capability table is unavailable")
        table_text = table.read_text()
        table_keys = set(re.findall(r"^(GKICM_\w+)\s*=", table_text, re.M))
        methods = shell_literals(table, table_keys)
        defaults_path = share / "defaults/config.sh"
        defaults = shell_literals(defaults_path, {"DEFAULT_COMPRESS_INITRD", "DEFAULT_COMPRESS_INITRD_TYPE"})
        path = Path(config) if config else self.path("/etc/genkernel.conf")
        settings = shell_literals(path, keys | table_keys) if path.exists() else {}
        if config and not path.exists():
            raise Unknown(f"missing genkernel configuration: {path}")
        if "GK_SHARE" in settings and settings["GK_SHARE"] != "/usr/share/genkernel":
            raise Unknown("custom GK_SHARE requires explicit capability verification")
        methods.update({k: v for k, v in settings.items() if k in table_keys})
        result["config"] = str(path)
        self.root_coverage(result, "genkernel", settings)
        # Version is informational; actual capabilities come from installed tables.
        for version_file in (share / "genkernel.sh", share / "genkernel"):
            if version_file.is_file():
                match = re.search(r'^GK_V=[\'"]?([0-9][\w.+-]*)', version_file.read_text(), re.M)
                if match:
                    result["version"] = match[1]
        if settings.get("INTEGRATED_INITRAMFS", "no").lower() not in ("no", "false", "0"):
            raise Unknown("integrated genkernel initramfs needs CONFIG_INITRAMFS_SOURCE/COMPRESSION review")
        enabled = settings.get("COMPRESS_INITRD", defaults.get("DEFAULT_COMPRESS_INITRD", ""))
        if enabled.lower() not in ("yes", "true", "1", "no", "false", "0"):
            raise Unknown("cannot resolve genkernel COMPRESS_INITRD")
        value = settings.get("COMPRESS_INITRD_TYPE", defaults.get("DEFAULT_COMPRESS_INITRD_TYPE", ""))
        value = normalize_format(value) if enabled.lower() in ("yes", "true", "1") else "none"
        if compression != "auto":
            value = compression
            result["notes"].append("Producer CLI compression override supplied; builder configuration was not edited")
        result["compression"] = value
        if value == "none":
            return
        supported, available = set(), set()
        for key, name in methods.items():
            if not key.endswith("_KOPTNAME"):
                continue
            fmt = normalize_format(name)
            command = shlex.split(methods.get(key.removesuffix("_KOPTNAME") + "_CMD", ""))
            if fmt not in FORMATS or not command:
                continue
            supported.add(fmt)
            if not self.which(command[0]):
                continue
            if fmt == "xz" and not any(arg in ("--check=crc32", "--check=none", "-Ccrc32", "-Cnone") for arg in command):
                if value in (fmt, "best", "fastest"):
                    raise Unknown("genkernel XZ command does not explicitly use CRC32/none")
                continue
            if fmt == "lz4" and "-l" not in command:
                if value in (fmt, "best", "fastest"):
                    raise Unknown("genkernel LZ4 command does not explicitly select legacy framing (-l)")
                continue
            available.add(fmt)
        if value in ("best", "fastest"):
            result["dynamic_formats"] = sorted(available)
            result["notes"].append(f"{value}: genkernel selects among installed compressors whose RD_* is enabled in the final config")
            if not available:
                result["issues"].append({"status": "incompatible", "message": "no available genkernel compressors"})
        elif value not in supported or value not in available:
            result["issues"].append({"status": "incompatible", "message": f"genkernel compressor unsupported or unavailable: {value}"})

    # Root device coverage. Everything is read through self.path() from
    # /proc and /sys; no blkid/mdadm/lvm subprocess is run.

    def read_attr(self, path):
        try:
            return path.read_text().strip()
        except OSError:
            return ""

    def root_source(self, result):
        """Root mount source from mountinfo, or None (with a note) when it is not a block device."""
        mountinfo = self.path("/proc/self/mountinfo")
        if not mountinfo.is_file():
            result["notes"].append("Root coverage not checked: /proc/self/mountinfo is unavailable")
            return None
        source = None
        for line in mountinfo.read_text().splitlines():
            fields, separator, tail = line.partition(" - ")
            fields, tail = fields.split(), tail.split()
            if separator and len(fields) >= 5 and fields[4] == "/" and len(tail) >= 2:
                source = tail[1]  # last record wins, like the kernel's view of /
        if source is None:
            result["notes"].append("Root coverage not checked: no / mount in /proc/self/mountinfo")
            return None
        if not source.startswith("/dev/"):
            result["notes"].append(f"Root coverage not checked: root source {source} is not a single block device (btrfs multi-device, zfs, nfs, rootfs)")
            return None
        return source

    def block_name(self, source):
        """sysfs block name for a /dev path, or None when it cannot be mapped (e.g. /dev/root)."""
        relative = source.removeprefix("/dev/")
        if relative.startswith("mapper/"):
            wanted = relative.removeprefix("mapper/")
            for entry in sorted(self.path("/sys/class/block").glob("dm-*/dm/name")):
                if self.read_attr(entry) == wanted:
                    return entry.parent.parent.name
            return None
        node = self.path(source)
        if node.is_symlink():  # /dev/md/NAME, /dev/disk/by-*/...
            return os.path.basename(os.readlink(node)) or None
        if "/" in relative or relative == "root":
            # /dev/root cannot be mapped without the device number; documented limitation.
            return None
        return relative

    def block_layers(self, name, depth=0, visited=None):
        """Ordered layers of a block device, top-down, following sysfs slaves/ and partition parents."""
        visited = set() if visited is None else visited
        if depth > MAX_LAYER_DEPTH or name in visited:
            return []
        visited.add(name)
        device = self.path("/sys/class/block") / name
        if not device.is_dir():
            return []
        layers = []
        if (device / "md").is_dir():
            layers.append({"type": "md", "name": name, "level": self.read_attr(device / "md/level"),
                           "metadata": self.read_attr(device / "md/metadata_version"),
                           "uuid": self.read_attr(device / "md/uuid")})
        elif (device / "dm/uuid").is_file():
            uuid = self.read_attr(device / "dm/uuid")
            kind = "lvm" if uuid.startswith("LVM-") else "crypt" if uuid.startswith("CRYPT-") else "dm"
            layers.append({"type": kind, "name": self.read_attr(device / "dm/name") or name})
        elif (device / "partition").is_file():
            parent = device.resolve().parent.name
            return layers + self.block_layers(parent, depth + 1, visited)
        slaves = device / "slaves"
        if slaves.is_dir():
            for slave in sorted(slaves.iterdir()):
                layers.extend(self.block_layers(slave.name, depth + 1, visited))
        return layers

    def other_md_arrays(self, exclude):
        """Active md arrays (md/level set) that are not part of the root device chain."""
        names = []
        for device in sorted(self.path("/sys/class/block").glob("md*")):
            if device.name not in exclude and (device / "md").is_dir() and self.read_attr(device / "md/level"):
                names.append(device.name)
        return names

    def mdadm_conf_lists(self, uuid, settings):
        """Whether the initramfs mdadm.conf will name the root array (ugrd copies the host file)."""
        for copy in settings.get("copies", {}).values():
            if isinstance(copy, dict) and copy.get("destination") == "/etc/mdadm.conf":
                return True
        wanted = re.sub(r"[^0-9a-f]", "", uuid.lower())  # sysfs prints %pU (dashes), mdadm uses colons
        if not wanted:
            return False
        conf = self.path("/etc/mdadm.conf")
        if not conf.is_file():
            return False
        for line in conf.read_text().splitlines():
            if re.match(r"\s*ARRAY\b", line):
                for match in re.finditer(r"\bUUID=([0-9a-fA-F:-]+)", line):
                    if re.sub(r"[^0-9a-f]", "", match[1].lower()) == wanted:
                        return True
        return False

    def root_coverage(self, result, generator, settings):
        source = self.root_source(result)
        if source is None:
            return
        name = self.block_name(source)
        if name is None:
            result["notes"].append(f"Root coverage not checked: {source} could not be mapped to a sysfs block device")
            return
        layers = self.block_layers(name)
        md_layers = [layer for layer in layers if layer["type"] == "md"]
        root = {"source": source, "layers": layers, "modules": {}, "mdadm_conf": "n/a",
                "other_md": self.other_md_arrays({layer["name"] for layer in md_layers}) if md_layers else []}
        result["root"] = root
        if generator != "ugrd":
            if layers:
                result["notes"].append(f"Root coverage is informational for genkernel: root {source} is {describe_layers(layers)}; "
                                       "make sure the matching --mdadm/--lvm/--luks options are used")
            return
        modules = settings.get("modules", [])
        modules = [modules] if isinstance(modules, str) else list(modules)
        config = result.get("config", "/etc/ugrd/config.toml")
        reasons = {"md": "ugrd 2.x's virtual-block autodetection is written for device-mapper nodes; a root "
                         "mounted directly from /dev/mdN stops at 'No device mapper name found' and never reaches "
                         "the linux_raid_member check that would enable the module, so the initramfs ships "
                         "without mdadm",
                   "lvm": "ugrd only adds it when autodetection resolves an LVM2_member slave of the root "
                          "device-mapper node; an explicit module entry does not depend on that detection",
                   "crypt": "ugrd only adds it when autodetection resolves a crypto_LUKS slave of the root "
                            "device-mapper node; an explicit module entry does not depend on that detection"}
        for layer in layers:
            module = ROOT_LAYER_MODULES.get(layer["type"])
            if not module:
                continue
            root["modules"][module] = module in modules
            if module not in modules:
                result["issues"].append({"status": "incompatible", "message": f"ugrd root coverage: {module} is not listed in modules of {config}; {reasons[layer['type']]}"})
        if md_layers and root["other_md"]:
            covered = self.mdadm_conf_lists(md_layers[0]["uuid"], settings)
            root["mdadm_conf"] = "covered" if covered else "not covered"
            if not covered:
                result["issues"].append({"status": "warning", "message": f"other md arrays present ({', '.join(root['other_md'])}); /etc/mdadm.conf (copied by ugrd.fs.mdraid) has no ARRAY entry for the root array, so mdadm --assemble --scan depends on scanning every member at boot (a slow USB member can leave another array half assembled)"})


def describe_layers(layers):
    """`lvm vg-root on crypt cryptroot on md raid1`; `plain partition` when there is no layer."""
    parts = []
    for layer in layers:
        if layer["type"] == "md":
            text = f"md {layer['level'] or 'unknown level'}"
            if layer.get("metadata"):
                text += f", metadata {layer['metadata']}"
            parts.append(text)
        else:
            parts.append(f"{layer['type']} {layer['name']}")
    return " on ".join(parts) or "plain partition"


def evaluate(evidence, values):
    issues = list(evidence["issues"])
    if evidence["needs_initrd"] and values.get("BLK_DEV_INITRD") != "y":
        issues.append({"status": "incompatible", "message": "CONFIG_BLK_DEV_INITRD must be y"})
    for fmt in evidence["required_formats"]:
        if values.get("RD_" + fmt.upper()) != "y":
            issues.append({"status": "incompatible", "message": f"CONFIG_RD_{fmt.upper()} must be built in (=y)"})
    candidates = [fmt for fmt in evidence["dynamic_formats"] if values.get("RD_" + fmt.upper()) == "y"]
    if evidence["dynamic_formats"] and not candidates:
        issues.append({"status": "incompatible", "message": "genkernel best/fastest has no compressor with a built-in decoder; choose a format explicitly"})
    root = evidence.get("root") or {}
    for layer in root.get("layers", []):
        symbols = list(ROOT_LAYER_KCONFIG.get(layer["type"], ()))
        if layer["type"] == "md" and layer.get("level") in RAID_LEVEL_KCONFIG:
            symbols.append(RAID_LEVEL_KCONFIG[layer["level"]])
        detail = layer.get("level") or layer.get("name") or layer["type"]
        for symbol in symbols:
            if values.get(symbol) not in ("y", "m"):
                issues.append({"status": "incompatible", "message": f"CONFIG_{symbol} must be y or m for the root device ({root['source']} {detail})"})
    status = "compatible" if evidence["needs_initrd"] else "not checked"
    blocking = [i for i in issues if i["status"] != "warning"]
    if blocking:
        status = "incompatible" if any(i["status"] == "incompatible" for i in blocking) else "unknown"
    return {**evidence, "status": status, "issues": issues, "eligible_formats": candidates}


def blocking_issues(issues):
    """Warnings are reported but never fail validation or strict mode."""
    return [issue for issue in issues if issue["status"] != "warning"]


def render(result, values):
    print(f"Initramfs compression: {result['status']}")
    print(f"  Generator: {result['generator']} {result['version']} ({result['source']})")
    print(f"  Producer format: {result['compression'] or 'unknown'}")
    root = result.get("root")
    if root:
        print(f"  Root device: {root['source']} ({describe_layers(root['layers'])})")
        for module, present in root["modules"].items():
            print(f"    {module}: {'listed' if present else 'missing'}")
        if root["other_md"]:
            print(f"    Other md arrays: {', '.join(root['other_md'])} (mdadm.conf: {root['mdadm_conf']})")
    for image in result["images"]:
        print(f"  Image: {image['path']}")
        for member in image["members"]:
            print(f"    offset={member['offset']} format={member['format']} {member.get('check', '')} {member.get('validation', '')}")
    if result["eligible_formats"]:
        print("  Eligible genkernel formats: " + ", ".join(result["eligible_formats"]))
    for prefix, title in (("KERNEL_", "Kernel image"), ("RD_", "Initramfs decoders"),
                          ("MODULE_COMPRESS_", "Modules"), ("FW_LOADER_COMPRESS_", "Firmware decoders")):
        formats = [fmt for fmt in FORMATS if values.get(prefix + fmt.upper()) == "y"]
        print(f"  {title}: {', '.join(formats) or 'none selected'}")
    for issue in result["issues"]:
        print(f"  {issue['status']}: {issue['message']}")
    for note in result["notes"]:
        print("  Note: " + note)
    print("  Scope: compression compatibility; no builder was run and bootability is not certified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("discover", "validate", "requirements"))
    parser.add_argument("--generator", default="auto", choices=("auto", "none", "genkernel", "ugrd"))
    parser.add_argument("--producer-config", default="")
    parser.add_argument("--image", default="")
    parser.add_argument("--compression", default="auto", choices=("auto", "none", "best", "fastest", *FORMATS))
    parser.add_argument("--kernel-config")
    parser.add_argument("--host-root", default="/", help="root of the host whose /etc, /proc and /sys are inspected (discover phase)")
    args = parser.parse_args()
    if args.phase == "discover":
        inspector = Inspector(Path(args.host_root))
        print(json.dumps(inspector.discover(args.generator, args.producer_config, args.image, args.compression)))
        return 0
    evidence = json.load(sys.stdin)
    if args.phase == "requirements":
        if blocking_issues(evidence["issues"]) or not evidence["needs_initrd"]:
            print("Cannot infer initramfs requirements; see compatibility report", file=sys.stderr)
            return 1
        print("BLK_DEV_INITRD")
        for fmt in evidence["required_formats"]:
            print("RD_" + fmt.upper())
        return 0
    values = kernel_values(args.kernel_config)
    result = evaluate(evidence, values)
    render(result, values)
    return 1 if blocking_issues(result["issues"]) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Initramfs inspection failed: {error}", file=sys.stderr)
        sys.exit(2)
