"""Hardware discovery from isolated sysfs captures, without probing the host."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from hardware_detect import Host, discover


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


if __name__ == "__main__":
    unittest.main()
