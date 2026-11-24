#!/usr/bin/env python3
"""Read-only sysfs discovery; emit candidates, never load or probe modules."""

import argparse
import os
import re
from collections import defaultdict, deque
from fnmatch import fnmatchcase
from pathlib import Path

from initramfs_check import RAID_LEVEL_KCONFIG, ROOT_LAYER_KCONFIG


class Host:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, name):
        # Captured sysfs can contain absolute /sys links. Resolve them inside
        # the capture, never against the build host's live /sys.
        pending = deque(Path(name).parts)
        parts = []
        links = 0
        while pending:
            part = pending.popleft()
            if part in ("/", ".", ""):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                continue
            candidate = self.root.joinpath(*parts, part)
            if candidate.is_symlink():
                links += 1
                if links > 40:
                    raise OSError("sysfs symlink loop")
                target = Path(os.readlink(candidate))
                if target.is_absolute():
                    parts = []
                    if target.is_relative_to(self.root):
                        target = target.relative_to(self.root)
                pending.extendleft(reversed(target.parts))
            else:
                parts.append(part)
        return self.root.joinpath(*parts)

    def read(self, name):
        try:
            return self.path(name).read_text(errors="replace").strip()
        except OSError:
            return ""

    def children(self, name):
        try:
            return sorted(p.name for p in self.path(name).iterdir())
        except OSError:
            return []

    def link_name(self, name):
        try:
            # A driver/module link itself is evidence even in a partial capture.
            parent, base = name.rsplit("/", 1)
            return Path(os.readlink(self.path(parent) / base)).name
        except OSError:
            return ""


class Aliases:
    def __init__(self, host):
        self.patterns = defaultdict(list)
        self.builtin = set()
        release = host.read("/proc/sys/kernel/osrelease")
        if not release and host.root == Path("/"):
            release = os.uname().release
        if not re.fullmatch(r"[A-Za-z0-9_.+~-]+", release):
            return
        directory = f"/lib/modules/{release}"
        for line in host.read(directory + "/modules.alias").splitlines():
            fields = line.split()
            if len(fields) == 3 and fields[0] == "alias":
                self.add(fields[1], fields[2])
        for entry in host.read(directory + "/modules.builtin").splitlines():
            self.builtin.add(Path(entry).name.removesuffix(".ko").replace("-", "_"))
        # modules.builtin.modinfo uses NUL-separated module.field=value records.
        for entry in host.read(directory + "/modules.builtin.modinfo").split("\0"):
            if ".alias=" in entry:
                module, pattern = entry.split(".alias=", 1)
                self.builtin.add(module.replace("-", "_"))
                self.add(pattern, module)

    def add(self, pattern, module):
        if re.fullmatch(r"[A-Za-z0-9_-]+", module):
            prefix = pattern.split(":", 1)[0]
            key = prefix if re.fullmatch(r"[A-Za-z0-9_]+", prefix) else "*"
            self.patterns[key].append((pattern, module.replace("-", "_")))

    def match(self, modalias):
        patterns = self.patterns[modalias.split(":", 1)[0]] + self.patterns["*"]
        return {module for pattern, module in patterns
                if fnmatchcase(modalias, pattern)}


BUS_PARENTS = {
    "pci": ("PCI",), "pci_express": ("PCI", "PCIEPORTBUS"),
    "usb": ("USB_SUPPORT", "USB"),
    "scsi": ("BLOCK", "SCSI", "SCSI_LOWLEVEL"),
    "ata": ("BLOCK", "SCSI", "ATA"), "nvme": ("BLOCK",),
    "i2c": ("I2C",), "i3c": ("I3C",), "spi": ("SPI",),
    "hid": ("INPUT", "HID"), "serio": ("INPUT", "SERIO"),
    "hdaudio": ("SOUND", "SND", "SND_HDA"),
    "mdio_bus": ("NET", "NETDEVICES", "PHYLIB"),
    "wmi": ("ACPI", "ACPI_WMI"), "vmbus": ("HYPERV",),
    "virtio": ("VIRTIO_MENU",), "mmc": ("MMC",),
    "thunderbolt": ("USB4",), "firewire": ("FIREWIRE",),
}
CLASS_PARENTS = {
    "block": ("BLOCK",), "net": ("NET", "NETDEVICES"),
    "input": ("INPUT",), "sound": ("SOUND", "SND"), "drm": ("DRM",),
    "hwmon": ("HWMON",), "watchdog": ("WATCHDOG",), "rtc": ("RTC_CLASS",),
    "tpm": ("TCG_TPM",), "gpio": ("GPIOLIB",), "pwm": ("PWM",),
    "bluetooth": ("BT",), "ieee80211": ("WIRELESS", "WLAN", "CFG80211"),
    "infiniband": ("INFINIBAND",), "dma": ("DMADEVICES",),
    "powercap": ("POWERCAP",), "thermal": ("THERMAL",),
    "video4linux": ("MEDIA_SUPPORT", "VIDEO_DEV"),
    "leds": ("NEW_LEDS", "LEDS_CLASS"), "nvme": ("BLOCK",),
    "ptp": ("PTP_1588_CLOCK",),
}

PCI_DRIVERS = {
    "0x010802": ("BLOCK", "BLK_DEV_NVME"),
    "0x010601": ("BLOCK", "SCSI", "ATA", "SATA_AHCI"),
    "0x040300": ("SOUND", "SND", "SND_HDA_INTEL"),
    "0x040380": ("SOUND", "SND", "SND_HDA_INTEL"),
    "0x080500": ("MMC", "MMC_SDHCI", "MMC_SDHCI_PCI", "MMC_BLOCK"),
    "0x080501": ("MMC", "MMC_SDHCI", "MMC_SDHCI_PCI", "MMC_BLOCK"),
    "0x0c0010": ("FIREWIRE", "FIREWIRE_OHCI"),
    "0x0c0300": ("USB_SUPPORT", "USB", "USB_UHCI_HCD"),
    "0x0c0310": ("USB_SUPPORT", "USB", "USB_OHCI_HCD", "USB_OHCI_HCD_PCI"),
    "0x0c0320": ("USB_SUPPORT", "USB", "USB_EHCI_HCD", "USB_EHCI_PCI"),
    "0x0c0330": ("USB_SUPPORT", "USB", "USB_XHCI_HCD", "USB_XHCI_PCI"),
    "0x0c0340": ("USB4",),
}

# PCI Express port service drivers live in drivers/pci/pcie/ under object names
# (pme.o, bwctrl.o, aer.o, dpc.o) that do not match their runtime driver names.
PCIE_SERVICE_DRIVERS = {
    "pcie_pme": ("PCIEPORTBUS", "PCIE_PME"),
    "aer": ("PCIEPORTBUS", "PCIEAER"),
    "dpc": ("PCIEPORTBUS", "PCIE_DPC"),
    "pciehp": ("PCIEPORTBUS", "HOTPLUG_PCI", "HOTPLUG_PCI_PCIE"),
    "pcie_bwctrl": ("PCIEPORTBUS",),
}

# Always-built framework drivers with no standalone module Kconfig option.
CORE_BUILTIN_DRIVERS = frozenset({"alarmtimer", "faux_driver"})

# Structural ACPI firmware containers/resources that never bind a standalone driver.
STRUCTURAL_ACPI_IDS = frozenset({
    "ACPI0010", "LNXSYSTM", "LNXSYBUS", "LNXPOWER",
    "PNP0000", "PNP0100", "PNP0103", "PNP0200",
    "PNP0A03", "PNP0A05", "PNP0A06", "PNP0A08",
    "PNP0C01", "PNP0C02", "PNP0C0F",
})

# Well-known I2C memory/SPD sensors instantiated by i2c-smbus even when their
# driver is absent from the running kernel.
I2C_KNOWN_DRIVERS = {
    "spd5118": (("I2C", "HWMON"), ("SENSORS_SPD5118",)),
    "jc42": (("I2C", "HWMON"), ("SENSORS_JC42",)),
    "ee1004": (("I2C",), ("EEPROM_EE1004",)),
}

# Standard ACPI platform devices that may remain unbound or map to short
# generic module names (ac, battery, button, fan, thermal).
ACPI_KNOWN_DRIVERS = {
    "ACPI0003": (("ACPI",), ("ACPI_AC",)),
    "PNP0C0A": (("ACPI",), ("ACPI_BATTERY",)),
    "PNP0C0B": (("ACPI", "THERMAL"), ("ACPI_FAN",)),
    "PNP0C0C": (("ACPI", "INPUT"), ("ACPI_BUTTON",)),
    "PNP0C0D": (("ACPI", "INPUT"), ("ACPI_BUTTON",)),
    "PNP0C0E": (("ACPI", "INPUT"), ("ACPI_BUTTON",)),
    "LNXPWRBN": (("ACPI", "INPUT"), ("ACPI_BUTTON",)),
    "LNXSLPBN": (("ACPI", "INPUT"), ("ACPI_BUTTON",)),
    "LNXTHERM": (("ACPI", "ACPI_PROCESSOR", "THERMAL"), ("ACPI_THERMAL",)),
    # arch/x86 registers a "pcspkr" platform device on every boot. PNP0800 is
    # the firmware node that actually describes a PC speaker.
    "PNP0800": (("INPUT",), ("INPUT_PCSPKR",)),
}

# Platform devices created on every boot, independent of the hardware.
SYNTHETIC_PLATFORM_DEVICES = frozenset({"pcspkr"})

# Local and network filesystems detected from /proc/self/mountinfo.
LOCAL_FS_KCONFIG = {
    "ext4": ("EXT4_FS",),
    "xfs": ("XFS_FS",),
    "btrfs": ("BTRFS_FS",),
    "f2fs": ("F2FS_FS",),
    "vfat": ("VFAT_FS",),
    "jfs": ("JFS_FS",),
}
SECONDARY_FS_KCONFIG = {
    "exfat": ((), ("EXFAT_FS",)),
    "ntfs3": ((), ("NTFS3_FS",)),
    "iso9660": ((), ("ISO9660_FS",)),
    "udf": ((), ("UDF_FS",)),
    "squashfs": ((), ("SQUASHFS",)),
    "erofs": ((), ("EROFS_FS",)),
    "overlay": ((), ("OVERLAY_FS",)),
    "fuse": ((), ("FUSE_FS",)),
    "nfs": (("NETWORK_FILESYSTEMS",), ("NFS_FS",)),
    "nfs4": (("NETWORK_FILESYSTEMS",), ("NFS_FS", "NFS_V4")),
    "cifs": (("NETWORK_FILESYSTEMS",), ("CIFS",)),
    "smb3": (("NETWORK_FILESYSTEMS",), ("CIFS",)),
    "ceph": (("NETWORK_FILESYSTEMS",), ("CEPH_FS",)),
}
BOOT_MOUNTPOINTS = frozenset({"/", "/boot", "/efi", "/boot/efi"})

GENERIC_DMI_BOARDS = frozenset({
    "default string", "to be filled by o.e.m.", "system product name",
    "base board product name", "none", "n/a", "not applicable",
    "not specified", "unknown", "standard pc",
})

SOURCE_SCAN_DIRS = {
    "acpi": (
        "drivers/i2c", "drivers/gpio", "drivers/hwmon", "drivers/pinctrl",
        "drivers/platform/x86", "drivers/thermal", "drivers/hid",
        "drivers/char/tpm", "drivers/spi", "drivers/input", "drivers/tty",
    ),
    "i2c": (
        "drivers/hwmon", "drivers/i2c", "drivers/rtc",
        "drivers/misc/eeprom", "drivers/char/tpm", "drivers/input",
    ),
    "wmi": (
        "drivers/platform/x86", "drivers/hwmon",
    ),
    "dmi_hwmon": (
        "drivers/hwmon",
    ),
    "platform": (
        "drivers/gpio", "drivers/input", "drivers/platform",
        "drivers/power", "drivers/rtc",
    ),
}


class SourceResolver:
    """Resolve unbound ACPI/I2C/WMI/DMI hardware against target kernel sources."""

    def __init__(self, kernel_srcdir):
        self.root = Path(kernel_srcdir)
        self._dir_files = {}
        self._kbuild_cache = {}

    def _iter_c_files(self, rel_dirs):
        for rel_dir in rel_dirs:
            cached = self._dir_files.get(rel_dir)
            if cached is None:
                directory = self.root / rel_dir
                cached = []
                if directory.is_dir():
                    for cfile in sorted(directory.rglob("*.c")):
                        # A built tree leaves *.mod.c next to the sources. Those
                        # generated aliases are not part of the target Kbuild map.
                        if cfile.name.endswith(".mod.c"):
                            continue
                        try:
                            cached.append((cfile, cfile.read_text(errors="replace")))
                        except OSError:
                            continue
                self._dir_files[rel_dir] = cached
            yield from cached

    def _kbuild_map(self, directory):
        cached = self._kbuild_cache.get(directory)
        if cached is not None:
            return cached
        direct_objs = set()
        comp_sources = defaultdict(set)
        for name in ("Kbuild", "Makefile"):
            mf = directory / name
            if not mf.is_file():
                continue
            try:
                text = mf.read_text(errors="replace")
            except OSError:
                continue
            text = re.sub(r"\\\n", " ", text)
            for raw_line in text.splitlines():
                line = raw_line.split("#", 1)[0].strip()
                if not line:
                    continue
                m = re.match(r"^obj-\$\(CONFIG_[A-Za-z0-9_]+\)\s*[-+?:]?=\s*(.+)$", line)
                if m:
                    for item in m.group(1).split():
                        if item.endswith(".o") and "/" not in item:
                            direct_objs.add(item)
                    continue
                m = re.match(
                    r"^([A-Za-z0-9_-]+)-(?:y|objs|\$\(CONFIG_[A-Za-z0-9_]+\))\s*[-+?:]?=\s*(.+)$",
                    line,
                )
                if m:
                    target = m.group(1)
                    for item in m.group(2).split():
                        if item.endswith(".o") and "/" not in item:
                            comp_sources[item].add(target)
        comp_map = {
            src: {t for t in targets if f"{t}.o" in direct_objs}
            for src, targets in comp_sources.items()
        }
        cached = (direct_objs, comp_map)
        self._kbuild_cache[directory] = cached
        return cached

    def _modules_for_source(self, cfile):
        direct_objs, comp_map = self._kbuild_map(cfile.parent)
        obj = f"{cfile.stem}.o"
        if obj in direct_objs:
            return {cfile.stem.replace("-", "_")}
        return {t.replace("-", "_") for t in comp_map.get(obj, ())}

    def match_modalias(self, modalias):
        if modalias.startswith("acpi:"):
            ids = [
                part for part in modalias[5:].split(":")
                if part and part not in STRUCTURAL_ACPI_IDS
                and re.fullmatch(r"[A-Za-z0-9_]{4,16}", part)
            ]
            for ident in ids:
                needle = f'"{ident}"'
                modules = set()
                for cfile, text in self._iter_c_files(SOURCE_SCAN_DIRS["acpi"]):
                    if needle in text and "acpi_device_id" in text and "MODULE_DEVICE_TABLE" in text:
                        modules.update(self._modules_for_source(cfile))
                if modules:
                    return sorted(modules)
            return []
        if modalias.startswith("i2c:"):
            ident = modalias[4:]
            if not re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", ident):
                return []
            pattern = re.compile(rf'\{{\s*"{re.escape(ident)}"\s*,')
            modules = set()
            for cfile, text in self._iter_c_files(SOURCE_SCAN_DIRS["i2c"]):
                if "i2c_device_id" in text and "MODULE_DEVICE_TABLE" in text and pattern.search(text):
                    modules.update(self._modules_for_source(cfile))
            return sorted(modules)
        if modalias.startswith("wmi:"):
            guid = modalias[4:]
            if not re.fullmatch(r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}", guid):
                return []
            pattern = re.compile(rf'"{re.escape(guid)}"', re.IGNORECASE)
            modules = set()
            for cfile, text in self._iter_c_files(SOURCE_SCAN_DIRS["wmi"]):
                if (any(marker in text for marker in ("wmi_device_id", "wmi_driver", "MODULE_ALIAS"))
                        and pattern.search(text)):
                    modules.update(self._modules_for_source(cfile))
            return sorted(modules)
        return []

    def match_platform(self, name):
        """Return (module, source) pairs for a platform: modalias name."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", name):
            return []
        alias = f'MODULE_ALIAS("platform:{name}")'
        id_pattern = re.compile(rf'\.name\s*=\s*"{re.escape(name)}"')
        found = {}
        for cfile, text in self._iter_c_files(SOURCE_SCAN_DIRS["platform"]):
            if "MODULE_DEVICE_TABLE" not in text and "MODULE_ALIAS" not in text:
                continue
            matched = alias in text or (
                "platform_device_id" in text and "MODULE_DEVICE_TABLE" in text
                and id_pattern.search(text))
            if not matched:
                continue
            for module in self._modules_for_source(cfile):
                found.setdefault(module, cfile)
        return sorted(found.items())

    def autoload_block(self, cfile, module):
        """Return why a matched module must not be enabled automatically.

        ``debug-only`` drivers depend on DEBUG_FS. ``not-x86`` drivers depend
        on !X86, so an x86 host cannot bind them even when firmware publishes
        the device.
        """
        symbol = ""
        for makefile_name in ("Kbuild", "Makefile"):
            makefile = cfile.parent / makefile_name
            try:
                text = makefile.read_text(errors="replace")
            except OSError:
                continue
            text = re.sub(r"\\\n", " ", text)
            for raw_line in text.splitlines():
                line = raw_line.split("#", 1)[0].strip()
                match = re.match(r"^obj-\$\(CONFIG_([A-Za-z0-9_]+)\)\s*[-+?:]?=\s*(.+)$", line)
                if not match:
                    continue
                for item in match.group(2).split():
                    if item.endswith(".o") and "/" not in item \
                            and Path(item).stem.replace("-", "_") == module:
                        symbol = match.group(1)
        if not symbol:
            return ""
        for directory in (cfile.parent, cfile.parent.parent):
            kconfig = directory / "Kconfig"
            try:
                text = kconfig.read_text(errors="replace")
            except OSError:
                continue
            match = re.search(
                rf"^config {re.escape(symbol)}\b\n(.*?)(?=^config |\Z)",
                text, re.M | re.S)
            if not match:
                continue
            head = match.group(1).split("\nhelp", 1)[0]
            depends = "\n".join(
                line for line in head.splitlines()
                if re.match(r"\s*(depends on|select)\b", line))
            if re.search(r"\bDEBUG_FS\b", depends):
                return "debug-only"
            if re.search(r"!X86\b", depends):
                return "not-x86"
        return ""

    def apd_x86_ids(self):
        """ACPI IDs turned into platform devices by CONFIG_X86_AMD_PLATFORM_DEVICE."""
        path = self.root / "drivers/acpi/acpi_apd.c"
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return set()
        match = re.search(
            r"#ifdef CONFIG_X86_AMD_PLATFORM_DEVICE\n(.*?)#endif", text, re.S)
        if not match:
            return set()
        return set(re.findall(r'"([A-Z0-9]{4,16})"', match.group(1)))

    def match_bus_ids(self, modaliases):
        """Classify PCI and USB ids against drivers/ and sound/.

        Returns modalias -> ("matched", [modules]), ("unsupported", []) when
        no table contains the id, or ("unmapped", []) when a table mentions it
        but Kbuild does not name a module.
        """
        queries = []
        for modalias in modaliases:
            if modalias.startswith("pci:"):
                match = re.fullmatch(r"pci:v([0-9A-Fa-f]{8})d([0-9A-Fa-f]{8}).*", modalias)
                if not match:
                    continue
                vendor, device = int(match.group(1), 16), int(match.group(2), 16)
                pattern = re.compile(rf"0x{vendor:04x}\s*,\s*0x{device:04x}\b", re.I)
                queries.append((modalias, "pci", pattern, False))
            elif modalias.startswith("usb:"):
                match = re.fullmatch(r"usb:v([0-9A-Fa-f]{4})p([0-9A-Fa-f]{4}).*", modalias)
                if not match:
                    continue
                vendor, product = int(match.group(1), 16), int(match.group(2), 16)
                pattern = re.compile(rf"0x{vendor:04x}\s*,\s*0x{product:04x}\b", re.I)
                interface = re.search(r"ic([0-9A-Fa-f]{2})", modalias)
                billboard = bool(interface and int(interface.group(1), 16) == 0x11)
                queries.append((modalias, "usb", pattern, billboard))
        found = {modalias: set() for modalias, *_rest in queries}
        saw = {modalias: False for modalias, *_rest in queries}
        if queries:
            for top in ("drivers", "sound"):
                root = self.root / top
                if not root.is_dir():
                    continue
                for cfile in root.rglob("*.c"):
                    if cfile.name.endswith(".mod.c") or "tests" in cfile.parts:
                        continue
                    try:
                        text = cfile.read_text(errors="replace")
                    except OSError:
                        continue
                    has_pci = "pci_device_id" in text
                    has_usb = "usb_device_id" in text
                    if not has_pci and not has_usb:
                        continue
                    modules = None
                    for modalias, kind, pattern, billboard in queries:
                        if kind == "pci" and not has_pci:
                            continue
                        if kind == "usb" and not has_usb:
                            continue
                        hit = pattern.search(text) is not None
                        if kind == "usb" and billboard and "USB_CLASS_BILLBOARD" in text:
                            hit = True
                        if not hit:
                            continue
                        saw[modalias] = True
                        if modules is None:
                            modules = self._modules_for_source(cfile)
                        found[modalias].update(modules)
        classified = {}
        for modalias, *_rest in queries:
            if found[modalias]:
                classified[modalias] = ("matched", sorted(found[modalias]))
            elif saw[modalias]:
                classified[modalias] = ("unmapped", [])
            else:
                classified[modalias] = ("unsupported", [])
        return classified

    def match_dmi_hwmon(self, board_name):
        board_name = board_name.strip()
        if len(board_name) < 4 or board_name.lower() in GENERIC_DMI_BOARDS:
            return []
        needle = f'"{board_name}"'
        modules = set()
        for cfile, text in self._iter_c_files(SOURCE_SCAN_DIRS["dmi_hwmon"]):
            if "DMI_BOARD_NAME" in text and needle in text:
                modules.update(self._modules_for_source(cfile))
        return sorted(modules)


def serial_ports(host, kernel_srcdir):
    """Detect native UARTs; USB serial and virtual terminals are separate stacks."""
    try:
        cmdline = host.path("/proc/cmdline").read_text()
        consoles = host.path("/proc/consoles").read_text().splitlines()
        for arg in cmdline.split():
            key, _, value = arg.partition("=")
            if key in {"earlycon", "earlyprintk", "kgdboc", "kgdboc_earlycon"}:
                return "on", f"kernel command line: {arg}"
            if key == "console" and value and not re.fullmatch(r"tty\d+(?:,.*)?|null", value):
                return "on", f"kernel command line: {arg}"
        if not consoles:
            return "unknown", "active console inventory unavailable"
        for line in consoles:
            fields = line.split()
            if not fields:
                return "unknown", "incomplete active console inventory"
            if not re.fullmatch(r"tty\d+|ttynull", fields[0]):
                return "on", f"active console: {fields[0]}"

        ttys = list(host.path("/sys/class/tty").iterdir())
        if not ttys:
            return "unknown", "TTY inventory unavailable"
        for tty in ttys:
            name = tty.name
            if re.fullmatch(r"tty\d+|tty|console|ptmx|ttynull|tty(?:USB|ACM)\d+", name):
                continue
            # 8250 registers empty slots too; PORT_UNKNOWN (0) is not a UART.
            if re.fullmatch(r"ttyS\d+", name) and host.read(f"/sys/class/tty/{name}/type") == "0":
                continue
            return "on", f"registered non-USB tty: {name}"
        serial = host.read("/proc/tty/driver/serial")
        if re.search(r"\buart:(?!unknown\b)\S+", serial):
            return "on", "UART in /proc/tty/driver/serial"
        if host.children("/sys/bus/serial/devices"):
            return "on", "registered serdev device"

        tables = {p.name for p in host.path("/sys/firmware/acpi/tables").iterdir()}
        if tables & {"SPCR", "DBG2"}:
            return "on", "ACPI serial console/debug table"
        if "DSDT" not in tables or host.path("/sys/firmware/devicetree/base").exists():
            return "unknown", "native UART pruning requires an ACPI platform"
        cpuinfo = host.path("/proc/cpuinfo").read_text()
        identity = " ".join(host.path(f"/sys/class/dmi/id/{name}").read_text()
                            for name in ("sys_vendor", "product_name"))
        if (not cpuinfo.strip() or not identity.strip()
                or re.search(r"\bhypervisor\b", cpuinfo)
                or re.search(r"QEMU|KVM|VMware|Virtual|Xen|Bochs|bhyve", identity, re.I)
                or host.read("/sys/hypervisor/type")):
            return "unknown", "virtual machine or incomplete platform identity"

        for device in host.path("/sys/bus/pci/devices").iterdir():
            pci_class = host.path(f"/sys/bus/pci/devices/{device.name}/class").read_text().strip()
            if not re.fullmatch(r"0x[0-9a-fA-F]{6}", pci_class):
                return "unknown", "incomplete PCI class inventory"
            if pci_class.lower().startswith("0x07"):
                return "on", f"PCI communication controller: {device.name}"

        # Use the target kernel's identifiers, including unbound PNP/ACPI UARTs.
        # Looking only at loaded modules would miss hardware disabled in .config.
        sources = list((Path(kernel_srcdir) / "drivers/tty/serial").rglob("*.c"))
        ids = {identifier for source in sources
               for identifier in re.findall(r'"([A-Z0-9]{7,8})"', source.read_text())}
        if not {"PNP0500", "PNP0501"} <= ids:
            return "unknown", "target UART source identifiers unavailable"
        for device in host.path("/sys/bus/pnp/devices").iterdir():
            identifiers = host.path(f"/sys/bus/pnp/devices/{device.name}/id").read_text().split()
            if not identifiers:
                return "unknown", "incomplete PNP inventory"
            if ids.intersection(identifiers):
                return "on", f"PNP UART: {device.name}"
        acpi = list(host.path("/sys/bus/acpi/devices").iterdir())
        if not acpi:
            return "unknown", "ACPI device inventory unavailable"
        for device in acpi:
            identifiers = {device.name.split(":")[0],
                           *host.read(f"/sys/bus/acpi/devices/{device.name}/modalias").split(":")}
            if not ids.intersection(identifiers):
                continue
            status = host.read(f"/sys/bus/acpi/devices/{device.name}/status")
            # Only _STA=0 establishes that the firmware has disabled this UART.
            if status != "0":
                return "on", f"ACPI UART: {device.name} (status={status or 'unknown'})"
        return "off", "no native UART in tty, PCI, PNP or ACPI; USB serial support retained"
    except (OSError, ValueError) as error:
        return "unknown", f"incomplete UART inventory: {error}"

def memory_ecc(host):
    """Return on/off/unknown; missing EDAC drivers never prove non-ECC RAM."""
    controllers = [name for name in host.children("/sys/devices/system/edac/mc")
                   if re.fullmatch(r"mc\d+", name)]
    for controller in controllers:
        directory = f"/sys/devices/system/edac/mc/{controller}"
        for device in host.children(directory):
            if not re.fullmatch(r"(?:dimm|rank|csrow)\d+", device):
                continue
            for attr in ("dimm_edac_mode", "edac_mode"):
                path = f"{directory}/{device}/{attr}"
                mode = host.read(path)
                if mode in {"EC", "SECDED", "S2ECD2ED", "S4ECD4ED", "S8ECD8ED", "S16ECD16ED"}:
                    return "on", f"{path}={mode} (active EDAC)"

    # SMBIOS types 16/17 describe the array's correction type and each DIMM's
    # total/data widths. Parse the raw table to avoid dmidecode and locale rules.
    try:
        table = host.path("/sys/firmware/dmi/tables/DMI").read_bytes()
    except OSError:
        return "unknown", "SMBIOS memory table unavailable; no active ECC evidence"
    arrays, devices, handles = {}, [], set()
    offset = 0
    while offset < len(table):
        if len(table) - offset < 4:
            return "unknown", "truncated SMBIOS header"
        kind, length = table[offset:offset + 2]
        end = table.find(b"\0\0", offset + length)
        if length < 4 or offset + length > len(table) or end < 0:
            return "unknown", "incomplete SMBIOS record"
        record = table[offset:offset + length]
        handle = int.from_bytes(record[2:4], "little")
        if handle in handles:
            return "unknown", "duplicate SMBIOS handle"
        handles.add(handle)
        if kind == 127:
            break
        if kind == 16:
            if length < 0x0f:
                return "unknown", "incomplete SMBIOS memory array"
            arrays[handle] = record
        elif kind == 17:
            if length < 0x15:
                return "unknown", "incomplete SMBIOS memory device"
            devices.append(record)
        offset = end + 2

    states = []
    slots = defaultdict(int)
    for device in devices:
        handle = int.from_bytes(device[4:6], "little")
        array = arrays.get(handle)
        if array is None:
            states.append("unknown")
            continue
        if array[5] != 3:  # System memory; video/flash arrays do not describe DRAM ECC.
            continue
        slots[handle] += 1
        size = int.from_bytes(device[12:14], "little")
        if size == 0:
            continue
        total = int.from_bytes(device[8:10], "little")
        data = int.from_bytes(device[10:12], "little")
        if size == 0xffff or total in (0, 0xffff) or data in (0, 0xffff):
            states.append("unknown")
        elif array[6] in (5, 6) and total > data:
            states.append("on")
        elif array[6] == 3 and total == data:
            states.append("off")
        else:
            states.append("unknown")
    for handle, array in arrays.items():
        if array[5] == 3 and slots[handle] != int.from_bytes(array[13:15], "little"):
            states.append("unknown")
    if "on" in states:
        return "on", "SMBIOS types 16/17 report ECC and DIMM check bits"
    if states and set(states) == {"off"} and not controllers:
        return "off", "SMBIOS: all populated system DIMMs have no ECC and equal total/data widths"
    return "unknown", "ECC data missing, conflicting, or an EDAC controller lacks a readable ECC mode"


def _discover_mounts(host, symbols, modsymbols):
    """Detect active local/network filesystems and VFAT NLS tables from mountinfo."""
    mountinfo = host.read("/proc/self/mountinfo")
    if not mountinfo:
        return
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields, tail = left.split(), right.split()
        if len(fields) < 5 or len(tail) < 2:
            continue
        mountpoint, fstype = fields[4], tail[0]
        opts = tail[2].split(",") if len(tail) >= 3 else []
        evidence = f"/proc/self/mountinfo {mountpoint} ({fstype})"
        if fstype in LOCAL_FS_KCONFIG:
            emit = symbols if mountpoint in BOOT_MOUNTPOINTS else modsymbols
            emit(LOCAL_FS_KCONFIG[fstype], evidence)
            if fstype == "vfat":
                nls = set()
                for opt in opts:
                    if opt == "utf8" or opt == "iocharset=utf8":
                        nls.add("NLS_UTF8")
                    elif opt == "iocharset=ascii":
                        nls.add("NLS_ASCII")
                    elif m := re.fullmatch(r"codepage=(\d+)", opt):
                        nls.add(f"NLS_CODEPAGE_{m[1]}")
                    elif m := re.fullmatch(r"iocharset=iso8859-(\d+)", opt):
                        nls.add(f"NLS_ISO8859_{m[1]}")
                    elif m := re.fullmatch(r"iocharset=cp(\d+)", opt):
                        nls.add(f"NLS_CODEPAGE_{m[1]}")
                if nls:
                    symbols(("NLS",), evidence)
                    modsymbols(sorted(nls), evidence)
        elif fstype in SECONDARY_FS_KCONFIG or fstype.startswith("fuse."):
            key = "fuse" if fstype.startswith("fuse.") else fstype
            parents, mods = SECONDARY_FS_KCONFIG[key]
            symbols(parents, evidence)
            modsymbols(mods, evidence)
            # LTS kernels require NFS_V4_1 for both 4.1 and 4.2 mounts.
            if fstype == "nfs4" and {"vers=4.1", "vers=4.2"}.intersection(opts):
                symbols(("NFS_V4_1",), evidence)
            if fstype == "nfs4" and "vers=4.2" in opts:
                symbols(("NFS_V4_2",), f"{evidence} vers=4.2")


def _acpi_physical_node_bound(host, path):
    for child in host.children(path):
        if (child == "physical_node" or child.startswith("physical_node")) and host.link_name(f"{path}/{child}/driver"):
            return True
    return False


def host_is_x86(host):
    cpuinfo = host.read("/proc/cpuinfo")
    if re.search(r"vendor_id\s*:\s*(GenuineIntel|AuthenticAMD|HygonGenuine)\b", cpuinfo):
        return True
    if host.root == Path("/"):
        return os.uname().machine in {"x86_64", "i386", "i686", "x86"}
    return False


def _firmware_id_searched(modalias):
    """True when match_modalias looked for this ACPI, I2C or WMI id."""
    if modalias.startswith("acpi:"):
        return any(
            part and part not in STRUCTURAL_ACPI_IDS
            and re.fullmatch(r"[A-Za-z0-9_]{4,16}", part)
            for part in modalias[5:].split(":"))
    if modalias.startswith("i2c:"):
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]{2,32}", modalias[4:]))
    if modalias.startswith("wmi:"):
        return bool(re.fullmatch(
            r"[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}", modalias[4:]))
    return False


def _bus_modalias(modalias):
    return bool(re.fullmatch(r"pci:v[0-9A-Fa-f]{8}d[0-9A-Fa-f]{8}.*", modalias)
                or re.fullmatch(r"usb:v[0-9A-Fa-f]{4}p[0-9A-Fa-f]{4}.*", modalias))


def _is_structural_acpi_modalias(modalias):
    if not modalias.startswith("acpi:"):
        return False
    ids = [part for part in modalias[5:].split(":") if part]
    return bool(ids) and all(item in STRUCTURAL_ACPI_IDS for item in ids)


def discover(host, kernel_srcdir=None):
    aliases = Aliases(host)
    source_resolver = SourceResolver(kernel_srcdir) if kernel_srcdir else None
    facts = set()
    devices = defaultdict(set)
    seen_ambiguous = set()
    seen_unresolved = set()
    emitted_modules = set()
    pending_bus = []

    def add(kind, value, evidence):
        facts.add((kind, value, re.sub(r"[\x00-\x1f\x7f]", " ", evidence)))

    def symbols(names, evidence):
        for name in names:
            add("symbol", name, evidence)

    def modsymbols(names, evidence):
        for name in names:
            add("modsymbol", name, evidence)

    def emit_module(name, evidence):
        name = name.replace("-", "_")
        if name in CORE_BUILTIN_DRIVERS:
            return
        if re.fullmatch(r"[A-Za-z0-9_]+", name):
            emitted_modules.add(name)
            add("builtin" if name in aliases.builtin else "module", name, evidence)

    for namespace in ("bus", "class", "subsystem"):
        for subsystem in host.children(f"/sys/{namespace}"):
            directory = f"/sys/{namespace}/{subsystem}"
            if namespace != "class":
                directory += "/devices"
            for name in host.children(directory):
                devices[f"{directory}/{name}"].add(subsystem)
    for name in host.children("/sys/block"):
        devices[f"/sys/block/{name}"].add("block")

    resolved = defaultdict(set)
    for path, subsystems in devices.items():
        try:
            device = host.path(path)
            if device.is_dir():
                resolved["/" + str(device.relative_to(host.root))].update(subsystems)
        except OSError:
            add("notice", "unreadable", path)
    # Empty AHCI ports still have a bound driver and matching module aliases.
    # Require a device below this controller, not merely an AHCI PCI function.
    # SCSI/ATA evidence also covers optical drives without media and devices
    # whose block driver is missing. Mount state does not determine necessity.
    storage_paths = {
        path for path, subsystems in resolved.items()
        if "block" in subsystems
        or ("scsi" in subsystems and host.read(path + "/type").isdigit())
        or ("ata_device" in subsystems and host.read(path + "/class")
            in {"ata", "atapi", "pmp", "semb", "zac"})
    }
    ahci_without_devices = {
        path for path, subsystems in resolved.items()
        if "pci" in subsystems and host.read(path + "/class").lower() == "0x010601"
        and not any(device.startswith(path + "/") for device in storage_paths)
    }
    for path, subsystems in sorted(resolved.items()):
        if "acpi" in subsystems and host.read(path + "/status") == "0":
            continue
        if any(path == controller or path.startswith(controller + "/")
               for controller in ahci_without_devices):
            if path in ahci_without_devices:
                symbols(BUS_PARENTS["pci"], path)
                if host.children(path + "/msi_irqs"):
                    symbols(("PCI_MSI",), path + "/msi_irqs (allocated interrupts)")
                add("notice", "ahci-no-attached-device",
                    f"{path}: no attached block/SCSI/ATA device observed; AHCI not added. "
                    "Unbound or incomplete sysfs cannot prove empty ports; use "
                    "--enable-symbols=SATA_AHCI for hotplug/recovery support")
            continue
        for subsystem in sorted(subsystems):
            symbols(BUS_PARENTS.get(subsystem, ()), path)
            symbols(CLASS_PARENTS.get(subsystem, ()), path)

        handled = False
        pci_class = ""
        if "pci" in subsystems:
            pci_class = host.read(path + "/class").lower()
            symbols(PCI_DRIVERS.get(pci_class, ()), f"{path} class={pci_class}")
            if pci_class.startswith("0x02"):
                symbols(("NET", "NETDEVICES"), path)
            if pci_class.startswith("0x0200"):
                symbols(("ETHERNET",), path)
            if host.children(path + "/msi_irqs"):
                symbols(("PCI_MSI",), path + "/msi_irqs (allocated interrupts)")
        if "usb" in subsystems:
            interface_class = host.read(path + "/bInterfaceClass").lower()
            interface_subclass = host.read(path + "/bInterfaceSubClass").lower()
            interface_proto = host.read(path + "/bInterfaceProtocol").lower()
            if interface_class == "08":
                symbols(("BLOCK", "SCSI", "USB_STORAGE"), path)
                if interface_proto == "62":
                    symbols(("USB_UAS",), path)
            elif interface_class == "03":
                symbols(("INPUT", "HID", "USB_HID"), path)
            elif interface_class == "01":
                symbols(("SOUND", "SND", "SND_USB"), path)
                modsymbols(("SND_USB_AUDIO",), f"{path} usb class=01 (audio)")
            elif interface_class == "0e":
                symbols(("MEDIA_SUPPORT", "MEDIA_CAMERA_SUPPORT", "MEDIA_USB_SUPPORT", "VIDEO_DEV"), path)
                modsymbols(("USB_VIDEO_CLASS",), f"{path} usb class=0e (UVC video)")
            elif interface_class == "02":
                if interface_subclass == "02":
                    modsymbols(("USB_ACM",), f"{path} usb class=02/02 (CDC ACM)")
                elif interface_subclass == "06":
                    symbols(("NET", "NETDEVICES", "USB_NET_DRIVERS", "USB_USBNET"), path)
                    modsymbols(("USB_NET_CDCETHER",), f"{path} usb class=02/06 (CDC Ethernet)")
                elif interface_subclass == "0d":
                    symbols(("NET", "NETDEVICES", "USB_NET_DRIVERS", "USB_USBNET"), path)
                    modsymbols(("USB_NET_CDC_NCM",), f"{path} usb class=02/0d (CDC NCM)")
            elif interface_class == "e0" and interface_subclass == "01" and interface_proto == "01":
                symbols(("NET", "BT"), path)
                modsymbols(("BT_HCIBTUSB",), f"{path} usb class=e0/01/01 (Bluetooth HCI)")
        if "scsi" in subsystems:
            scsi_type = host.read(path + "/type")
            symbols({"0": ("BLK_DEV_SD",), "5": ("BLK_DEV_SR",),
                     "14": ("BLK_DEV_SD",), "20": ("BLK_DEV_SD",)}.get(scsi_type, ()), path)
            if scsi_type == "1":
                modsymbols(("CHR_DEV_ST",), f"{path} scsi type=1 (tape)")
        if "block" in subsystems:
            level = host.read(path + "/md/level")
            uuid = host.read(path + "/dm/uuid")
            if level:
                symbols(("MD", *ROOT_LAYER_KCONFIG["md"]), path)
                if level in RAID_LEVEL_KCONFIG:
                    symbols((RAID_LEVEL_KCONFIG[level],), f"{path} md/level={level}")
                else:
                    add("notice", "unknown-raid-level", f"{path}: {level}")
            if host.children(path + "/dm"):
                layer = "crypt" if uuid.startswith("CRYPT-") else "lvm" if uuid.startswith("LVM-") else "dm"
                symbols(("MD", *ROOT_LAYER_KCONFIG[layer]), path)
                if uuid.startswith("mpath-"):
                    modsymbols(("DM_MULTIPATH",), f"{path} dm/uuid=mpath")
                elif uuid.startswith("INTEGRITY-"):
                    modsymbols(("DM_INTEGRITY",), f"{path} dm/uuid=INTEGRITY")
                elif uuid.startswith("LVM-"):
                    if uuid.endswith(("-tpool", "-tdata", "-tmeta")):
                        modsymbols(("DM_THIN_PROVISIONING",), f"{path} dm/uuid=LVM thin")
                    elif uuid.endswith(("-cpool", "-cdata", "-cmeta")):
                        modsymbols(("DM_CACHE",), f"{path} dm/uuid=LVM cache")
                    elif uuid.endswith(("-cow", "-real")):
                        modsymbols(("DM_SNAPSHOT",), f"{path} dm/uuid=LVM snapshot")
                    elif uuid.endswith(("-wcache", "-wcorig")):
                        modsymbols(("DM_WRITECACHE",), f"{path} dm/uuid=LVM writecache")
                    elif uuid.endswith(("-vdo", "-vpool", "-vdata")):
                        modsymbols(("DM_VDO",), f"{path} dm/uuid=LVM VDO")
                    elif re.search(r"-(?:rimage|rmeta)_\d+$", uuid):
                        modsymbols(("DM_RAID",), f"{path} dm/uuid=LVM RAID")
        if "hwmon" in subsystems:
            hwmon_name = host.read(path + "/name")
            if hwmon_name == "drivetemp":
                symbols(("HWMON", "SCSI", "ATA"), path)
                modsymbols(("SENSORS_DRIVETEMP",), f"{path} hwmon=drivetemp")
            elif hwmon_name == "nvme":
                symbols(("HWMON", "NVME_HWMON"), f"{path} hwmon=nvme")
        if "powercap" in subsystems:
            pc_name = Path(path).name
            if re.fullmatch(r"intel-rapl:\d+", pc_name):
                pc_dev = host.link_name(path + "/device")
                if "tpmi" in pc_dev:
                    symbols(("POWERCAP",), path)
                    modsymbols(("INTEL_RAPL_TPMI",), f"{path} powercap=intel-rapl-tpmi")
                else:
                    symbols(("POWERCAP", "PCI"), path)
                    modsymbols(("INTEL_RAPL",), f"{path} powercap=intel-rapl")
            elif re.fullmatch(r"intel-rapl-mmio:\d+", pc_name):
                symbols(("POWERCAP", "THERMAL"), path)
                modsymbols(("PROC_THERMAL_MMIO_RAPL",), f"{path} powercap=intel-rapl-mmio")

        module = host.link_name(path + "/driver/module")
        driver = host.link_name(path + "/driver")
        modalias = host.read(path + "/modalias")
        if not modalias:
            for line in host.read(path + "/uevent").splitlines():
                if line.startswith("MODALIAS="):
                    modalias = line.partition("=")[2]
        if "pci_express" in subsystems and driver in PCIE_SERVICE_DRIVERS:
            symbols(PCIE_SERVICE_DRIVERS[driver], f"{path} (bound {driver})")
            handled = True
        # A platform node under a PCI bridge can carry an ACPI modalias without
        # also appearing on the ACPI bus. The HID is still the driver key.
        if "acpi" in subsystems or modalias.startswith("acpi:"):
            acpi_hid = host.read(path + "/hid") or Path(path).name.split(":", 1)[0]
            if acpi_hid not in ACPI_KNOWN_DRIVERS and modalias.startswith("acpi:"):
                for part in modalias[5:].split(":"):
                    if part in ACPI_KNOWN_DRIVERS:
                        acpi_hid = part
                        break
            if acpi_hid in ACPI_KNOWN_DRIVERS:
                parents, mods = ACPI_KNOWN_DRIVERS[acpi_hid]
                symbols(parents, path)
                modsymbols(mods, f"{path} acpi:{acpi_hid}")
                handled = True
        if "i2c" in subsystems and not module and not driver:
            i2c_name = host.read(path + "/name").lower()
            if not i2c_name and modalias.startswith("i2c:"):
                i2c_name = modalias[4:].lower()
            if i2c_name in I2C_KNOWN_DRIVERS:
                parents, mods = I2C_KNOWN_DRIVERS[i2c_name]
                symbols(parents, path)
                modsymbols(mods, f"{path} i2c:{i2c_name}")
                handled = True
        if not handled and "mdio_bus" in subsystems and not module:
            phy_id = host.read(path + "/phy_id")
            if re.fullmatch(r"0x[0-9a-fA-F]{1,8}", phy_id) and not modalias:
                modalias = f"mdio:{int(phy_id, 16):032b}"
        if handled:
            continue
        if "acpi" in subsystems and not driver and not module and _acpi_physical_node_bound(host, path):
            continue

        candidates = aliases.match(modalias) if modalias else set()
        if not module:
            if len(candidates) == 1:
                module = candidates.pop()
            elif len(candidates) > 1:
                if modalias.startswith("input:"):
                    # Input handlers attach simultaneously rather than exclusively.
                    for handler in ("evdev", "input_leds"):
                        if handler in candidates:
                            emit_module(handler, f"{path} (input handler)")
                    continue
                if modalias.startswith("hid:") and "hid_generic" in candidates:
                    specific = candidates - {"hid_generic"}
                    if len(specific) == 1:
                        module = specific.pop()
                    else:
                        candidates = specific
                if not module:
                    key = ("cpu" if modalias.startswith("cpu:") else modalias, tuple(sorted(candidates)))
                    if key not in seen_ambiguous:
                        seen_ambiguous.add(key)
                        add("notice", "ambiguous-alias", f"{path}: {' '.join(sorted(candidates))}")
            elif driver:
                module = driver
            elif modalias:
                if _is_structural_acpi_modalias(modalias) or (
                    "pci" in subsystems and pci_class in {"0x060000", "0x080600"}
                ):
                    continue
                if modalias.startswith("platform:"):
                    platform_name = modalias.split(":", 1)[1]
                    if platform_name in SYNTHETIC_PLATFORM_DEVICES \
                            or platform_name.replace("-", "_") in emitted_modules:
                        continue
                    if source_resolver:
                        platform_matches = source_resolver.match_platform(platform_name)
                        if len(platform_matches) == 1:
                            platform_module, platform_source = platform_matches[0]
                            block = source_resolver.autoload_block(platform_source, platform_module)
                            evidence = f"{path} (kernel source match for {modalias})"
                            if block == "debug-only":
                                add("notice", "debug-only",
                                    f"{evidence}: {platform_module} depends on DEBUG_FS")
                            elif block == "not-x86" and host_is_x86(host):
                                add("notice", "unsupported",
                                    f"{evidence}: {platform_module} depends on !X86")
                            else:
                                emit_module(platform_module, evidence)
                            continue
                        if len(platform_matches) > 1:
                            names = " ".join(module for module, _ in platform_matches)
                            key = (modalias, tuple(module for module, _ in platform_matches))
                            if key not in seen_ambiguous:
                                seen_ambiguous.add(key)
                                add("notice", "ambiguous-source-match",
                                    f"{path}: {modalias} -> {names}")
                            continue
                        if modalias not in seen_unresolved:
                            seen_unresolved.add(modalias)
                            add("notice", "unsupported",
                                f"{path}: {modalias} (no driver in the target kernel)")
                        continue
                if source_resolver:
                    src_matches = source_resolver.match_modalias(modalias)
                    if len(src_matches) == 1:
                        emit_module(src_matches[0], f"{path} (kernel source match for {modalias})")
                        if modalias.startswith("acpi:"):
                            hid = next((part for part in modalias[5:].split(":") if part), "")
                            if hid in source_resolver.apd_x86_ids():
                                symbols(("X86_AMD_PLATFORM_DEVICE",),
                                        f"{path} acpi:{hid} (AMD ACPI platform device)")
                        continue
                    if len(src_matches) > 1:
                        key = (modalias, tuple(src_matches))
                        if key not in seen_ambiguous:
                            seen_ambiguous.add(key)
                            add("notice", "ambiguous-source-match",
                                f"{path}: {modalias} -> {' '.join(src_matches)}")
                        continue
                    if not src_matches and _firmware_id_searched(modalias):
                        if modalias not in seen_unresolved:
                            seen_unresolved.add(modalias)
                            add("notice", "unsupported",
                                f"{path}: {modalias} (no driver in the target kernel)")
                        continue
                    if _bus_modalias(modalias):
                        pending_bus.append((path, modalias))
                        continue
                if modalias not in seen_unresolved:
                    seen_unresolved.add(modalias)
                    add("notice", "unresolved-alias", f"{path}: {modalias}")
        if module:
            emit_module(module, f"{path} ({'bound driver' if driver else 'modalias'})")

    if source_resolver and pending_bus:
        first_path = {}
        for path, modalias in pending_bus:
            first_path.setdefault(modalias, path)
        for modalias, (status, modules) in source_resolver.match_bus_ids(first_path).items():
            path = first_path[modalias]
            if status == "matched" and len(modules) == 1:
                emit_module(modules[0], f"{path} (kernel source match for {modalias})")
            elif status == "matched":
                add("notice", "ambiguous-source-match",
                    f"{path}: {modalias} -> {' '.join(modules)}")
            elif status == "unsupported":
                add("notice", "unsupported",
                    f"{path}: {modalias} (no driver in the target kernel)")
            else:
                add("notice", "unresolved-alias", f"{path}: {modalias}")

    if source_resolver:
        board_name = host.read("/sys/class/dmi/id/board_name")
        if board_name:
            dmi_matches = source_resolver.match_dmi_hwmon(board_name)
            if len(dmi_matches) == 1 and dmi_matches[0] not in emitted_modules:
                emit_module(dmi_matches[0], f"/sys/class/dmi/id/board_name={board_name} (hwmon DMI table)")
            elif len(dmi_matches) > 1:
                unseen = [m for m in dmi_matches if m not in emitted_modules]
                if unseen:
                    add("notice", "ambiguous-source-match",
                        f"/sys/class/dmi/id/board_name={board_name} -> {' '.join(dmi_matches)}")

    _discover_mounts(host, symbols, modsymbols)
    return sorted(facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", default="/")
    parser.add_argument("--memory-ecc", action="store_true", help="report DRAM ECC evidence only")
    parser.add_argument("--serial-ports", action="store_true", help="report native UART evidence only")
    parser.add_argument("--kernel-srcdir", help="target kernel sources for UART and unbound device resolution")
    args = parser.parse_args()
    host = Host(args.host_root)
    if args.serial_ports:
        if not args.kernel_srcdir:
            parser.error("--serial-ports requires --kernel-srcdir")
        print("\t".join(serial_ports(host, args.kernel_srcdir)))
        return
    if args.memory_ecc:
        print("\t".join(memory_ecc(host)))
        return
    for fact in discover(host, kernel_srcdir=args.kernel_srcdir):
        print("\t".join(fact))


if __name__ == "__main__":
    main()
