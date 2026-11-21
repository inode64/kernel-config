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
    args = parser.parse_args()
    for fact in discover(Host(args.host_root)):
        print("\t".join(fact))


if __name__ == "__main__":
    main()
