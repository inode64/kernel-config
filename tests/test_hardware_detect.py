"""Hardware discovery from isolated sysfs captures, without probing the host."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from hardware_detect import Host, discover, memory_ecc, serial_ports


def serial_host(root):
    """Complete native ACPI inventory with no UART and a USB ACM peripheral."""
    for directory in ("sys/class/tty/tty0", "sys/class/tty/ttyACM0", "sys/bus/pci/devices",
                      "sys/bus/pnp/devices", "sys/firmware/acpi/tables"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    for path, value in {
        "proc/cmdline": "root=/dev/test console=tty0\n",
        "proc/consoles": "tty0 -WU (EC p) 4:1\n",
        "proc/cpuinfo": "vendor_id : AuthenticAMD\nflags : sse2\n",
        "sys/class/dmi/id/sys_vendor": "Example\n",
        "sys/class/dmi/id/product_name": "Laptop\n",
        "sys/firmware/acpi/tables/DSDT": "fixture",
        "sys/bus/acpi/devices/PNP0500:00/status": "0\n",
    }.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)


def memory_table(correction=6, total=72, data=64, sizes=(0x4000, 0), handle=1, slots=None):
    """SMBIOS memory records, including empty sockets and a string area."""
    array = bytearray(0x17)
    array[:2] = bytes((16, len(array)))
    array[2:4] = handle.to_bytes(2, "little")
    array[5:7] = bytes((3, correction))
    array[13:15] = (len(sizes) if slots is None else slots).to_bytes(2, "little")
    records = bytes(array) + b"\0\0"
    for index, size in enumerate(sizes):
        dimm = bytearray(0x15)
        dimm[:2] = bytes((17, len(dimm)))
        dimm[2:4] = (handle + index + 1).to_bytes(2, "little")
        dimm[4:6] = handle.to_bytes(2, "little")
        dimm[8:10] = (total if size else 0xffff).to_bytes(2, "little")
        dimm[10:12] = (data if size else 0xffff).to_bytes(2, "little")
        dimm[12:14] = size.to_bytes(2, "little")
        records += bytes(dimm) + b"fixture DIMM\0\0"
    return records


class HardwareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hardware-capture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = Host(self.root)

    def write(self, path, value):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value)

    def link(self, path, target):
        destination = self.root / path.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(target)

    def facts(self):
        return {(kind, name) for kind, name, _ in discover(self.host)}

    def serial_fixture(self):
        serial_host(self.root)
        self.write("kernel/drivers/tty/serial/8250/pnp.c", '"PNP0500" "PNP0501" "AMDI0020"')
        return self.root / "kernel"

    def test_serial_absent_preserves_usb_and_ignores_empty_8250_slots(self):
        source = self.serial_fixture()
        self.write("sys/class/tty/ttyS0/type", "0\n")
        self.write("proc/tty/driver/serial", "0: uart:unknown port:00000000 irq:0\n")
        self.assertEqual(serial_ports(self.host, source)[0], "off")
        self.write("sys/class/tty/ttyS0/type", "4\n")
        self.assertEqual(serial_ports(self.host, source)[0], "on")

    def test_serial_positive_evidence_survives_missing_driver(self):
        source = self.serial_fixture()
        for path, value in {
            "proc/cmdline": "console=ttyS0,115200\n",
            "proc/tty/driver/serial": "0: uart:16550A port:000003F8 irq:4\n",
            "sys/bus/pci/devices/0000:01:00.0/class": "0x070002\n",
            "sys/bus/pnp/devices/00:01/id": "PNP0501\n",
            "sys/bus/acpi/devices/AMDI0020:00/status": "15\n",
            "sys/firmware/acpi/tables/SPCR": "fixture",
            "sys/firmware/acpi/tables/DBG2": "fixture",
        }.items():
            with self.subTest(path=path):
                serial_host(self.root)
                self.write(path, value)
                self.assertEqual(serial_ports(self.host, source)[0], "on")
                target = self.root / path
                target.unlink()
                if path.startswith("sys/bus/"):
                    target.parent.rmdir()

    def test_serial_console_debug_arguments_and_serdev_keep_support(self):
        source = self.serial_fixture()
        for cmdline in ("earlycon", "earlycon=uart8250,io,0x3f8", "earlyprintk=serial",
                        "kgdboc=ttyS0", "console=hvc0", "console=ttyUSB0"):
            self.write("proc/cmdline", cmdline)
            self.assertEqual(serial_ports(self.host, source)[0], "on", cmdline)
        serial_host(self.root)
        (self.root / "sys/bus/serial/devices/serial0-0").mkdir(parents=True)
        self.assertEqual(serial_ports(self.host, source)[0], "on")

    def test_serial_incomplete_inventory_and_guests_never_prune(self):
        source = self.serial_fixture()
        for path in ("proc/consoles", "proc/cmdline", "sys/class/dmi/id/product_name",
                     "sys/bus/pnp/devices", "sys/bus/pci/devices", "sys/class/tty"):
            with self.subTest(path=path):
                target = self.root / path
                backup = target.with_name(target.name + ".saved")
                target.rename(backup)
                self.assertEqual(serial_ports(self.host, source)[0], "unknown")
                backup.rename(target)
        self.write("proc/cpuinfo", "flags : hypervisor\n")
        self.assertEqual(serial_ports(self.host, source)[0], "unknown")
        serial_host(self.root)
        self.write("sys/class/dmi/id/product_name", "KVM\n")
        self.assertEqual(serial_ports(self.host, source)[0], "unknown")

    def test_serial_unknown_acpi_status_and_partial_sources_keep_support(self):
        source = self.serial_fixture()
        (self.root / "sys/bus/acpi/devices/PNP0500:00/status").unlink()
        self.assertEqual(serial_ports(self.host, source)[0], "on")
        serial_host(self.root)
        self.assertEqual(serial_ports(self.host, self.root / "missing")[0], "unknown")
        self.write("sys/bus/acpi/devices/EXAMPLE0:00/modalias", "acpi:EXAMPLE0:PNP0501:\n")
        self.write("sys/bus/acpi/devices/EXAMPLE0:00/status", "15\n")
        self.assertEqual(serial_ports(self.host, source)[0], "on")

    def set_memory(self, table):
        path = self.root / "sys/firmware/dmi/tables/DMI"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(table)

    def test_memory_ecc_requires_correction_type_and_dimm_check_bits(self):
        for correction in (5, 6):
            for total in (72, 80):
                with self.subTest(correction=correction, total=total):
                    self.set_memory(memory_table(correction=correction, total=total))
                    self.assertEqual(memory_ecc(self.host)[0], "on")
        self.set_memory(memory_table(correction=3, total=64))
        self.assertEqual(memory_ecc(self.host)[0], "off")

    def test_memory_missing_unknown_conflicting_and_empty_are_not_non_ecc(self):
        self.assertEqual(memory_ecc(self.host)[0], "unknown")
        for kwargs in ({"correction": 3}, {"total": 64}, {"total": 0xffff},
                       {"data": 0}, {"correction": 4}, {"correction": 2},
                       {"sizes": (0, 0)}, {"sizes": (0xffff,)},
                       {"correction": 3, "total": 64, "slots": 3}):
            with self.subTest(kwargs=kwargs):
                self.set_memory(memory_table(**kwargs))
                self.assertEqual(memory_ecc(self.host)[0], "unknown")

    def test_memory_corrupt_and_partial_tables_do_not_prune(self):
        valid = memory_table(correction=3, total=64)
        for table in (valid[:-1], valid[:10], b"\x10\x02\0\0", valid + b"\x11",
                      valid + valid, valid[0x19:]):
            with self.subTest(table=table[:8]):
                self.set_memory(table)
                self.assertEqual(memory_ecc(self.host)[0], "unknown")

    def test_memory_arrays_require_all_non_ecc_but_any_ecc_keeps_support(self):
        non_ecc = memory_table(correction=3, total=64)
        self.set_memory(non_ecc + memory_table(handle=10))
        self.assertEqual(memory_ecc(self.host)[0], "on")
        self.set_memory(non_ecc + memory_table(handle=10, correction=2))
        self.assertEqual(memory_ecc(self.host)[0], "unknown")
        self.set_memory(non_ecc + memory_table(handle=10, correction=3, total=64))
        self.assertEqual(memory_ecc(self.host)[0], "off")

    def test_active_edac_takes_precedence_over_firmware_and_missing_data(self):
        self.set_memory(memory_table(correction=3, total=64))
        path = "sys/devices/system/edac/mc/mc0/dimm0/dimm_edac_mode"
        self.write(path, "SECDED\n")
        state, evidence = memory_ecc(self.host)
        self.assertEqual(state, "on")
        self.assertIn("active EDAC", evidence)
        self.write(path, "Unknown\n")
        self.assertEqual(memory_ecc(self.host)[0], "unknown")
        self.write("sys/devices/system/edac/mc/mc0/csrow0/edac_mode", "S4ECD4ED\n")
        self.assertEqual(memory_ecc(self.host)[0], "on")

    def test_standard_controllers_disks_usb_and_allocated_msi(self):
        self.write("sys/bus/pci/devices/0000:01:00.0/class", "0x010802\n")
        self.write("sys/bus/pci/devices/0000:01:00.0/msi_irqs/32", "msix\n")
        self.write("sys/bus/pci/devices/0000:02:00.0/class", "0x0c0330\n")
        self.write("sys/bus/scsi/devices/0:0:0:0/type", "0\n")
        self.write("sys/bus/usb/devices/1-1:1.0/bInterfaceClass", "08\n")
        self.write("sys/bus/usb/devices/1-1:1.0/bInterfaceProtocol", "62\n")
        facts = self.facts()
        for name in ("PCI", "PCI_MSI", "BLK_DEV_NVME", "USB_XHCI_PCI", "SCSI", "BLK_DEV_SD", "USB_UAS"):
            self.assertIn(("symbol", name), facts)
        (self.root / "sys/bus/pci/devices/0000:01:00.0/msi_irqs/32").unlink()
        self.assertNotIn(("symbol", "PCI_MSI"), self.facts())

    def test_aliases_builtin_bound_drivers_and_ambiguity(self):
        self.write("proc/sys/kernel/osrelease", "test-kernel\n")
        self.write("lib/modules/test-kernel/modules.alias", "alias pci:v1234* first\nalias pci:v1234* second\nalias usb:xyz* usb_net\n")
        self.write("lib/modules/test-kernel/modules.builtin.modinfo", "tpm_crb.alias=acpi*:MSFT0101:*\0tpm_crb.license=GPL\0")
        self.write("sys/bus/pci/devices/one/modalias", "pci:v1234d5678\n")
        self.write("sys/bus/usb/devices/two/modalias", "usb:xyz0001\n")
        self.write("sys/bus/acpi/devices/three/uevent", "MODALIAS=acpi:MSFT0101:\n")
        self.link("sys/bus/pci/devices/four/driver", "/sys/bus/pci/drivers/bound")
        self.link("sys/bus/pci/drivers/bound/module", "/sys/module/real_module")
        facts = self.facts()
        for fact in (("notice", "ambiguous-alias"), ("module", "usb_net"), ("builtin", "tpm_crb"), ("module", "real_module")):
            self.assertIn(fact, facts)
        self.assertNotIn(("module", "first"), facts)
        self.assertNotIn(("module", "second"), facts)

    def test_captured_absolute_links_deduplicate_devices_and_never_escape(self):
        self.link("sys/bus/pci/devices/nic", "/sys/devices/nic")
        self.link("sys/class/net/eth0", "../../devices/nic")
        self.write("sys/devices/nic/class", "0x020000\n")
        self.link("sys/devices/nic/driver", "/sys/bus/pci/drivers/test_net")
        self.link("sys/bus/pci/drivers/test_net/module", "/sys/module/test_net")
        facts = discover(self.host)
        self.assertEqual(sum(kind == "module" and name == "test_net" for kind, name, _ in facts), 1)
        self.assertIn(("symbol", "NETDEVICES"), self.facts())
        self.assertEqual(self.host.path("/../../etc/passwd"), self.root / "etc/passwd")
        self.link("sys/loop", "/sys/loop")
        self.assertEqual(self.host.read("/sys/loop"), "")

    def test_block_layers_reuse_root_coverage_symbols(self):
        self.write("sys/class/block/md0/md/level", "raid6\n")
        self.write("sys/class/block/dm-0/dm/uuid", "CRYPT-LUKS2-test\n")
        self.write("sys/class/block/dm-1/dm/uuid", "LVM-test\n")
        facts = self.facts()
        for name in ("MD", "BLK_DEV_MD", "MD_RAID456", "BLK_DEV_DM", "DM_CRYPT"):
            self.assertIn(("symbol", name), facts)

    def test_empty_capture_does_not_read_live_host(self):
        self.assertEqual(discover(self.host), [])
        self.write("sys/class/drm/version", "drm 1.1.0\n")
        self.assertEqual(discover(self.host), [])

    def ahci(self, name="0000:00:17.0", *, bound=True):
        path = f"/sys/devices/pci0000:00/{name}"
        self.link(f"/sys/bus/pci/devices/{name}", path)
        self.write(path + "/class", "0x010601\n")
        self.write(path + "/modalias", "pci:ahci\n")
        if bound:
            self.link(path + "/driver", "/sys/bus/pci/drivers/ahci")
            if not (self.root / "sys/bus/pci/drivers/ahci/module").is_symlink():
                self.link("/sys/bus/pci/drivers/ahci/module", "/sys/module/ahci")
        return path

    def test_ahci_without_devices_is_not_added_by_class_driver_or_alias(self):
        self.write("proc/sys/kernel/osrelease", "test-kernel\n")
        self.write("lib/modules/test-kernel/modules.alias", "alias pci:ahci ahci\n")
        self.write("lib/modules/test-kernel/modules.builtin", "kernel/drivers/ata/ahci.ko\n")
        for bound in (False, True):
            with self.subTest(bound=bound):
                path = self.ahci(f"0000:00:{17 + int(bound)}.0", bound=bound)
                self.write(path + "/msi_irqs/32", "msi\n")
                for port, value in enumerate(("none", "unknown")):
                    device = path + f"/ata{port}/link{port}/dev{port}.0"
                    self.write(device + "/class", value)
                    self.link(f"/sys/class/ata_device/{int(bound)}-dev{port}.0", device)
                facts = self.facts()
                self.assertIn(("symbol", "PCI"), facts)
                self.assertIn(("symbol", "PCI_MSI"), facts)
                self.assertIn(("notice", "ahci-no-attached-device"), facts)
                for kind, name in (("symbol", "SATA_AHCI"), ("module", "ahci"),
                                   ("builtin", "ahci"), ("symbol", "ATA")):
                    self.assertNotIn((kind, name), facts)

    def test_ahci_with_unmounted_block_device_is_required(self):
        path = self.ahci()
        device = path + "/ata1/host0/target0:0:0/0:0:0:0/block/sda"
        self.write(device + "/dev", "8:0\n")
        self.link("/sys/class/block/sda", device)
        self.link("/sys/block/sda", device)
        for symbol in ("SATA_AHCI", "ATA", "SCSI", "BLOCK"):
            self.assertIn(("symbol", symbol), self.facts())
        self.assertIn(("module", "ahci"), self.facts())

    def test_ahci_with_scsi_device_does_not_require_a_block_driver(self):
        path = self.ahci()
        device = path + "/ata1/host0/target0:0:0/0:0:0:0"
        self.link("/sys/bus/scsi/devices/0:0:0:0", device)
        for scsi_type in ("0", "5"):
            with self.subTest(scsi_type=scsi_type):
                self.write(device + "/type", scsi_type)
                self.assertIn(("symbol", "SATA_AHCI"), self.facts())

    def test_ahci_with_identified_ata_device_does_not_require_scsi(self):
        path = self.ahci()
        device = path + "/ata1/link1/dev1.0"
        self.link("/sys/class/ata_device/dev1.0", device)
        for ata_class in ("ata", "atapi", "pmp", "semb", "zac"):
            with self.subTest(ata_class=ata_class):
                self.write(device + "/class", ata_class)
                self.assertIn(("symbol", "SATA_AHCI"), self.facts())

    def test_devices_on_other_controllers_do_not_enable_empty_ahci(self):
        path = self.ahci()
        device = path + "1/host0/target0:0:0/0:0:0:0/block/sda"
        self.write(device + "/dev", "8:0\n")
        self.link("/sys/class/block/sda", device)
        self.assertNotIn(("symbol", "SATA_AHCI"), self.facts())

    def test_multiple_ahci_controllers_keep_the_required_driver(self):
        empty = self.ahci()
        used = self.ahci("0000:01:00.0")
        device = used + "/ata1/host0/target0:0:0/0:0:0:0/block/sda"
        self.write(device + "/dev", "8:0\n")
        self.link("/sys/class/block/sda", device)
        facts = discover(self.host)
        evidence = [e for kind, name, e in facts if name in ("SATA_AHCI", "ahci")]
        self.assertTrue(evidence)
        self.assertTrue(all(used in e and empty not in e for e in evidence))

    def test_extended_pci_usb_scsi_and_dm_targets(self):
        self.write("sys/bus/pci/devices/0000:01:00.0/class", "0x0c0340\n")
        self.write("sys/bus/pci/devices/0000:02:00.0/class", "0x080501\n")
        self.write("sys/bus/pci/devices/0000:03:00.0/class", "0x040300\n")
        self.write("sys/bus/pci/devices/0000:04:00.0/class", "0x0c0010\n")
        for idx, (cls, sub, proto) in enumerate((
            ("01", "01", "00"),
            ("0e", "01", "00"),
            ("02", "02", "01"),
            ("02", "06", "00"),
            ("02", "0d", "00"),
            ("e0", "01", "01"),
        )):
            self.write(f"sys/bus/usb/devices/1-{idx}:1.0/bInterfaceClass", cls + "\n")
            self.write(f"sys/bus/usb/devices/1-{idx}:1.0/bInterfaceSubClass", sub + "\n")
            self.write(f"sys/bus/usb/devices/1-{idx}:1.0/bInterfaceProtocol", proto + "\n")
        self.write("sys/bus/scsi/devices/1:0:0:0/type", "1\n")
        for idx, uuid in enumerate((
            "mpath-36001405test",
            "INTEGRITY-test",
            "LVM-vg-tpool",
            "LVM-vg-cpool",
            "LVM-vg-cow",
            "LVM-vg-wcache",
            "LVM-vg-vdo",
            "LVM-vg-rimage_0",
        )):
            self.write(f"sys/class/block/dm-{idx}/dm/uuid", uuid + "\n")
        facts = self.facts()
        for name in ("USB4", "MMC_SDHCI_PCI", "MMC_BLOCK", "SND_HDA_INTEL", "FIREWIRE_OHCI"):
            self.assertIn(("symbol", name), facts)
        for name in ("SND_USB_AUDIO", "USB_VIDEO_CLASS", "USB_ACM", "USB_NET_CDCETHER",
                     "USB_NET_CDC_NCM", "BT_HCIBTUSB", "CHR_DEV_ST",
                     "DM_MULTIPATH", "DM_INTEGRITY", "DM_THIN_PROVISIONING", "DM_CACHE",
                     "DM_SNAPSHOT", "DM_WRITECACHE", "DM_VDO", "DM_RAID"):
            self.assertIn(("modsymbol", name), facts)

    def test_hwmon_i2c_mdio_pcie_services_and_core_drivers(self):
        self.write("proc/sys/kernel/osrelease", "test-kernel\n")
        self.write("lib/modules/test-kernel/modules.alias",
                   "alias mdio:0000000000011100110010000100???? realtek\n")
        self.write("sys/class/hwmon/hwmon0/name", "drivetemp\n")
        self.write("sys/class/hwmon/hwmon1/name", "nvme\n")
        self.write("sys/bus/i2c/devices/0-0050/name", "spd5118\n")
        self.write("sys/bus/i2c/devices/0-0050/modalias", "i2c:spd5118\n")
        self.write("sys/bus/i2c/devices/0-0018/name", "jc42\n")
        self.write("sys/bus/i2c/devices/0-0051/name", "ee1004\n")
        self.write("sys/bus/mdio_bus/devices/phy0/phy_id", "0x001cc840\n")
        self.link("sys/bus/mdio_bus/devices/phy0/driver", "/sys/bus/mdio_bus/drivers/Realtek Internal NBASE-T PHY")
        for idx, drv in enumerate(("pcie_pme", "aer", "dpc", "pciehp", "pcie_bwctrl")):
            self.link(f"sys/bus/pci_express/devices/0000:00:01.0:pcie00{idx}/driver",
                      f"/sys/bus/pci_express/drivers/{drv}")
        self.link("sys/bus/platform/devices/alarmtimer.0.auto/driver", "/sys/bus/platform/drivers/alarmtimer")
        self.link("sys/bus/faux/devices/microcode/driver", "/sys/bus/faux/drivers/faux_driver")
        facts = self.facts()
        for fact in (
            ("modsymbol", "SENSORS_DRIVETEMP"), ("symbol", "NVME_HWMON"),
            ("modsymbol", "SENSORS_SPD5118"), ("modsymbol", "SENSORS_JC42"),
            ("modsymbol", "EEPROM_EE1004"), ("symbol", "PHYLIB"), ("module", "realtek"),
            ("symbol", "PCIEPORTBUS"), ("symbol", "PCIE_PME"), ("symbol", "PCIEAER"),
            ("symbol", "PCIE_DPC"), ("symbol", "HOTPLUG_PCI_PCIE"),
        ):
            self.assertIn(fact, facts)
        for skipped in ("alarmtimer", "faux_driver", "pcie_pme", "pcie_bwctrl"):
            self.assertNotIn(("module", skipped), facts)
            self.assertNotIn(("builtin", skipped), facts)
        self.assertNotIn(("notice", "unresolved-alias"), facts)

    def test_acpi_filtering_input_multicast_hid_fallback_and_deduplication(self):
        self.write("proc/sys/kernel/osrelease", "test-kernel\n")
        self.write("lib/modules/test-kernel/modules.alias", "\n".join((
            "alias acpi*:MSFT0101:* tpm_crb",
            "alias acpi*:MSFT0101:* tpm_tis",
            "alias input:* evdev",
            "alias input:* input_leds",
            "alias input:* keyboard",
            "alias hid:* hid_generic",
            "alias hid:b0003g*v0000046Dp0000C52B hid_logitech_dj",
            "alias cpu:x86* kvm_amd",
            "alias cpu:x86* rapl",
        )) + "\n")
        # Disabled ACPI device is ignored.
        self.write("sys/bus/acpi/devices/DISABLED:00/status", "0\n")
        self.write("sys/bus/acpi/devices/DISABLED:00/modalias", "acpi:DISABLED:\n")
        # ACPI companion whose physical_node is already bound emits no bogus ambiguity.
        self.write("sys/bus/acpi/devices/MSFT0101:00/status", "15\n")
        self.write("sys/bus/acpi/devices/MSFT0101:00/modalias", "acpi:MSFT0101:\n")
        self.link("sys/devices/platform/MSFT0101:00/driver", "/sys/bus/platform/drivers/tpm_crb_acpi")
        self.link("sys/bus/platform/drivers/tpm_crb_acpi/module", "/sys/module/tpm_crb")
        self.link("sys/bus/acpi/devices/MSFT0101:00/physical_node", "/sys/devices/platform/MSFT0101:00")
        self.link("sys/bus/platform/devices/MSFT0101:00", "/sys/devices/platform/MSFT0101:00")
        # Structural ACPI resources and unbound PCI host/IOMMU bridges do not spam unresolved-alias.
        self.write("sys/bus/acpi/devices/PNP0C02:00/modalias", "acpi:PNP0C02:\n")
        self.write("sys/bus/platform/devices/PNP0103:00/modalias", "acpi:PNP0103:\n")
        self.write("sys/bus/pci/devices/0000:00:00.0/class", "0x060000\n")
        self.write("sys/bus/pci/devices/0000:00:00.0/modalias", "pci:v00001022d000014DA\n")
        self.write("sys/bus/pci/devices/0000:00:00.2/class", "0x080600\n")
        self.write("sys/bus/pci/devices/0000:00:00.2/modalias", "pci:v00001022d000014D9\n")
        # Input multicast resolves evdev + input_leds without ambiguous-alias.
        self.write("sys/class/input/input0/modalias", "input:b0003v046DpC548\n")
        # HID specific driver beats hid_generic fallback.
        self.write("sys/bus/hid/devices/0003:046D:C52B.0001/modalias", "hid:b0003g0001v0000046Dp0000C52B\n")
        # Repeated CPU ambiguous notices collapse to a single entry.
        for cpu in range(4):
            self.write(f"sys/bus/cpu/devices/cpu{cpu}/modalias", "cpu:x86test\n")
        facts = discover(self.host)
        pairs = {(k, v) for k, v, _ in facts}
        for fact in (("module", "tpm_crb"), ("module", "evdev"), ("module", "input_leds"),
                     ("module", "hid_logitech_dj")):
            self.assertIn(fact, pairs)
        self.assertNotIn(("notice", "unresolved-alias"), pairs)
        ambiguous = [e for k, v, e in facts if (k, v) == ("notice", "ambiguous-alias")]
        self.assertEqual(len(ambiguous), 1)
        self.assertIn("kvm_amd rapl", ambiguous[0])

    def test_mounted_filesystems_and_vfat_nls_from_mountinfo(self):
        self.write("proc/self/mountinfo", "\n".join((
            "26 2 8:1 / / rw,noatime - ext4 /dev/sda1 rw",
            "27 26 8:2 / /efi rw - vfat /dev/sda2 rw,codepage=850,iocharset=iso8859-15,utf8",
            "28 26 8:3 / /home rw - btrfs /dev/sda3 rw",
            "29 26 0:40 / /srv rw - nfs4 192.0.2.1:/srv rw,vers=4.2",
            "30 26 0:41 / /run/user/1000/doc rw - fuse.portal portal rw",
        )) + "\n")
        facts = self.facts()
        for fact in (
            ("symbol", "EXT4_FS"), ("symbol", "VFAT_FS"),
            ("symbol", "NLS"), ("modsymbol", "NLS_CODEPAGE_850"),
            ("modsymbol", "NLS_ISO8859_15"), ("modsymbol", "NLS_UTF8"),
            ("modsymbol", "BTRFS_FS"), ("symbol", "NETWORK_FILESYSTEMS"),
            ("modsymbol", "NFS_FS"), ("modsymbol", "NFS_V4"), ("symbol", "NFS_V4_2"),
            ("modsymbol", "FUSE_FS"),
        ):
            self.assertIn(fact, facts)

    def test_powercap_rapl_and_acpi_power_thermal_button_devices(self):
        # MSR RAPL package domain + subdomain (subdomain does not duplicate).
        (self.root / "sys/class/powercap/intel-rapl:0").mkdir(parents=True)
        (self.root / "sys/class/powercap/intel-rapl:0:0").mkdir(parents=True)
        # TPMI RAPL domain.
        self.link("sys/class/powercap/intel-rapl:1/device", "/sys/bus/auxiliary/devices/intel_vsec.tpmi-rapl.0")
        # MMIO RAPL domain.
        (self.root / "sys/class/powercap/intel-rapl-mmio:0").mkdir(parents=True)
        # Active ACPI AC, battery, fan, power button, and thermal zone.
        for dev in ("ACPI0003:00", "PNP0C0A:00", "PNP0C0B:00", "PNP0C0C:00", "LNXTHERM:00"):
            self.write(f"sys/bus/acpi/devices/{dev}/status", "15\n")
            self.write(f"sys/bus/acpi/devices/{dev}/modalias", f"acpi:{dev.split(':')[0]}:\n")
        # Disabled ACPI battery (status=0) must be skipped.
        self.write("sys/bus/acpi/devices/PNP0C0A:01/status", "0\n")
        self.write("sys/bus/acpi/devices/PNP0C0A:01/modalias", "acpi:PNP0C0A:\n")
        raw = discover(Host(self.root))
        facts = {(kind, value) for kind, value, _ in raw}
        for sym in ("POWERCAP", "PCI", "THERMAL", "ACPI", "ACPI_PROCESSOR", "INPUT"):
            self.assertIn(("symbol", sym), facts)
        for mod in ("INTEL_RAPL", "INTEL_RAPL_TPMI", "PROC_THERMAL_MMIO_RAPL",
                    "ACPI_AC", "ACPI_BATTERY", "ACPI_FAN", "ACPI_BUTTON", "ACPI_THERMAL"):
            self.assertIn(("modsymbol", mod), facts)
        self.assertFalse(any(kind == "notice" and value == "unresolved-alias" for kind, value, _ in raw))
        self.assertEqual(sum(1 for kind, value, _ in raw if (kind, value) == ("modsymbol", "INTEL_RAPL")), 1)
        self.assertEqual(sum(1 for kind, value, _ in raw if (kind, value) == ("modsymbol", "ACPI_BATTERY")), 1)


if __name__ == "__main__":
    unittest.main()
