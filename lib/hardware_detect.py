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
    "pci": ("PCI",), "usb": ("USB_SUPPORT", "USB"),
    "scsi": ("BLOCK", "SCSI", "SCSI_LOWLEVEL"),
    "ata": ("BLOCK", "SCSI", "ATA"), "nvme": ("BLOCK",),
    "i2c": ("I2C",), "spi": ("SPI",), "hid": ("INPUT", "HID"),
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
}

PCI_DRIVERS = {
    "0x010802": ("BLOCK", "BLK_DEV_NVME"),
    "0x010601": ("BLOCK", "SCSI", "ATA", "SATA_AHCI"),
    "0x0c0300": ("USB_SUPPORT", "USB", "USB_UHCI_HCD"),
    "0x0c0310": ("USB_SUPPORT", "USB", "USB_OHCI_HCD", "USB_OHCI_HCD_PCI"),
    "0x0c0320": ("USB_SUPPORT", "USB", "USB_EHCI_HCD", "USB_EHCI_PCI"),
    "0x0c0330": ("USB_SUPPORT", "USB", "USB_XHCI_HCD", "USB_XHCI_PCI"),
}


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


def discover(host):
    aliases = Aliases(host)
    facts = set()
    devices = defaultdict(set)

    def add(kind, value, evidence):
        facts.add((kind, value, re.sub(r"[\x00-\x1f\x7f]", " ", evidence)))

    def symbols(names, evidence):
        for name in names:
            add("symbol", name, evidence)

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
            if interface_class == "08":
                symbols(("BLOCK", "SCSI", "USB_STORAGE"), path)
                if host.read(path + "/bInterfaceProtocol").lower() == "62":
                    symbols(("USB_UAS",), path)
            elif interface_class == "03":
                symbols(("INPUT", "HID", "USB_HID"), path)
        if "scsi" in subsystems:
            scsi_type = host.read(path + "/type")
            symbols({"0": ("BLK_DEV_SD",), "5": ("BLK_DEV_SR",)}.get(scsi_type, ()), path)
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

        module = host.link_name(path + "/driver/module")
        driver = host.link_name(path + "/driver")
        modalias = host.read(path + "/modalias")
        if not modalias:
            for line in host.read(path + "/uevent").splitlines():
                if line.startswith("MODALIAS="):
                    modalias = line.partition("=")[2]
        candidates = aliases.match(modalias) if modalias else set()
        if not module:
            if len(candidates) == 1:
                module = candidates.pop()
            elif len(candidates) > 1:
                add("notice", "ambiguous-alias", f"{path}: {' '.join(sorted(candidates))}")
            elif driver:
                module = driver
            elif modalias:
                add("notice", "unresolved-alias", f"{path}: {modalias}")
        if module and re.fullmatch(r"[A-Za-z0-9_-]+", module):
            module = module.replace("-", "_")
            add("builtin" if module in aliases.builtin else "module", module,
                f"{path} ({'bound driver' if driver else 'modalias'})")
    return sorted(facts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-root", default="/")
    parser.add_argument("--memory-ecc", action="store_true", help="report DRAM ECC evidence only")
    parser.add_argument("--serial-ports", action="store_true", help="report native UART evidence only")
    parser.add_argument("--kernel-srcdir", help="target kernel sources for UART hardware identifiers")
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
    for fact in discover(host):
        print("\t".join(fact))


if __name__ == "__main__":
    main()
