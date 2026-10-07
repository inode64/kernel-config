"""Compression evidence tests with synthetic archives and isolated builder metadata."""

import bz2
import contextlib
import gzip
import importlib.util
import io
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / "lib/initramfs_check.py"
spec = importlib.util.spec_from_file_location("initramfs_check", HELPER)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def cpio_entry(name, payload=b""):
    name = name.encode() + b"\0"
    fields = [1, 0o100644, 0, 0, 1, 0, len(payload), 0, 0, 0, 0, len(name), 0]
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    data = header + name
    data += b"\0" * (-len(data) % 4)
    data += payload
    return data + b"\0" * (-len(data) % 4)


def cpio(payload=b"hello"):
    return cpio_entry("init", payload) + cpio_entry("TRAILER!!!")


def zstd_raw(payload):
    # Standard frame, single segment, two-byte content size, one raw last block.
    assert 256 <= len(payload) < 65792
    return (b"\x28\xb5\x2f\xfd\x60" + (len(payload) - 256).to_bytes(2, "little")
            + ((len(payload) << 3) | 1).to_bytes(3, "little") + payload)


MD_UUID_SYSFS = "c3f2a1b0-1234-5678-9abc-def012345678"  # %pU form printed by md/uuid
MD_UUID_MDADM = "c3f2a1b0:12345678:9abcdef0:12345678"  # same bytes as mdadm.conf writes them


class InspectorCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tools = {"ugrd", "xz", "zstd", "gzip", "lz4", "lzop", "bzip2", "lzma"}
        self.caps = dict(version="2.2.0", default="xz", xz=True, zstd=True)
        self.inspector = check.Inspector(self.root, which=lambda name: "/bin/" + name if name in self.tools else None,
                                         probe=lambda command: self.caps)
        self.image = self.root / "image with spaces.img"

    def write(self, name, contents):
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def ugrd(self, contents="", compression="auto"):
        path = self.write("etc/ugrd/config.toml", contents)
        return self.inspector.discover("ugrd", str(path), compression=compression)

    def genkernel(self, contents="", compression="auto"):
        self.tools.add("genkernel")
        self.write("usr/share/genkernel/defaults/config.sh", 'DEFAULT_COMPRESS_INITRD=yes\nDEFAULT_COMPRESS_INITRD_TYPE=best\n')
        self.write("usr/share/genkernel/defaults/compression_methods.sh", '''GKICM_GZ_KOPTNAME="GZIP"
GKICM_GZ_CMD="gzip -9"
GKICM_XZ_KOPTNAME="XZ"
GKICM_XZ_CMD="xz --check=crc32 -9"
GKICM_LZO_KOPTNAME="LZO"
GKICM_LZO_CMD="lzop -9"
GKICM_LZ4_KOPTNAME="LZ4"
GKICM_LZ4_CMD="lz4 -l -9"
''')
        path = self.write("etc/genkernel.conf", contents)
        return self.inspector.discover("genkernel", str(path), compression=compression)

    def evaluate(self, evidence, **values):
        return check.evaluate(evidence, {"BLK_DEV_INITRD": "y", "RD_XZ": "y", **values})

    def inspect(self, data):
        self.image.write_bytes(data)
        return check.inspect_image(self.image)

    def fake_root_device(self, source, layers, other_md=()):
        """Write mountinfo and the sysfs tree for a root device.

        ``layers`` is top-down; each is ``(name, kind, attrs)`` with kind in
        md/lvm/crypt/disk/part. ``part`` entries become the usual sysfs
        symlink into their parent's device directory (``attrs["parent"]``).
        """
        self.write("proc/self/mountinfo", f"26 2 9:127 / / rw,noatime shared:1 - ext4 {source} rw\n")
        previous = None
        for name, kind, attrs in layers:
            device = self.root / "sys/class/block" / name
            if kind == "part":
                target = self.root / "sys/devices/virtual/block" / attrs["parent"] / name
                target.mkdir(parents=True)
                (target / "partition").write_text("1\n")
                device.parent.mkdir(parents=True, exist_ok=True)
                device.symlink_to(target)
            else:
                device.mkdir(parents=True)
            if kind == "md":
                self.write(f"sys/class/block/{name}/md/level", attrs.get("level", "raid5") + "\n")
                self.write(f"sys/class/block/{name}/md/metadata_version", attrs.get("metadata", "0.90") + "\n")
                self.write(f"sys/class/block/{name}/md/uuid", attrs.get("uuid", MD_UUID_SYSFS) + "\n")
            elif kind in ("lvm", "crypt"):
                prefix = "LVM-" if kind == "lvm" else "CRYPT-LUKS2-"
                self.write(f"sys/class/block/{name}/dm/uuid", prefix + "0123456789abcdef\n")
                self.write(f"sys/class/block/{name}/dm/name", attrs["name"] + "\n")
            if previous is not None:
                (previous / "slaves" / name).mkdir(parents=True)
            previous = device
        for name in other_md:
            self.write(f"sys/class/block/{name}/md/level", "raid1\n")
            self.write(f"sys/class/block/{name}/md/uuid", "00000000-0000-0000-0000-00000000" + name[-4:].zfill(4) + "\n")

    def kernel_md(self, **values):
        return {"BLK_DEV_MD": "y", "MD_RAID456": "m", **values}


class CompressionTests(InspectorCase):
    def test_ugrd_defaults_and_python_dependencies(self):
        result = self.evaluate(self.ugrd())
        self.assertEqual(result["compression"], "xz")
        self.assertEqual(result["status"], "compatible")
        self.caps["zstd"] = False  # executable still exists
        result = self.evaluate(self.ugrd('cpio_compression = "zstd"'), RD_ZSTD="y")
        self.assertEqual(result["status"], "incompatible")
        self.assertIn("zstandard", result["issues"][0]["message"])

    def test_ugrd_rejects_other_formats_and_unknown_versions(self):
        self.assertEqual(self.evaluate(self.ugrd('cpio_compression="lz4"'))["status"], "incompatible")
        self.caps["version"] = "3.0.0"
        self.assertEqual(self.evaluate(self.ugrd())["status"], "unknown")

    def test_ugrd_no_compression_and_cli_override(self):
        result = self.evaluate(self.ugrd('cpio_compression=false'), RD_XZ="n")
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["required_formats"], [])
        result = self.evaluate(self.ugrd('cpio_compression="xz"', compression="zstd"), RD_ZSTD="y")
        self.assertEqual(result["compression"], "zstd")
        self.assertEqual(result["status"], "compatible")
        self.assertIn('"xz"', (self.root / "etc/ugrd/config.toml").read_text())

    def test_ugrd_invalid_toml_and_custom_hooks_are_unknown(self):
        for contents in ('cpio_compression=[', '[imports]\nx="y"'):
            with self.subTest(contents=contents):
                self.assertEqual(self.evaluate(self.ugrd(contents))["status"], "unknown")

    def test_decoders_and_initrd_support_must_be_built_in(self):
        for values in ({"RD_XZ": "n"}, {"RD_XZ": "m"}, {"BLK_DEV_INITRD": "n"}):
            with self.subTest(values=values):
                self.assertEqual(self.evaluate(self.ugrd(), **values)["status"], "incompatible")

    def test_kernel_image_compression_is_independent(self):
        self.assertEqual(self.evaluate(self.ugrd(), KERNEL_ZSTD="y", KERNEL_XZ="n")["status"], "compatible")

    def test_stale_config_does_not_select_genkernel(self):
        self.write("etc/genkernel.conf", 'COMPRESS_INITRD_TYPE="gzip"')
        self.assertEqual(self.inspector.generator("auto")[0], "ugrd")

    def test_kernel_install_precedence_and_ambiguous_tools(self):
        self.tools.add("genkernel")
        with self.assertRaises(check.Unknown):
            self.inspector.generator("auto")
        self.write("usr/lib/kernel/install.conf", "initrd_generator=genkernel")
        self.write("etc/kernel/install.conf", "initrd_generator=ugrd")
        self.assertEqual(self.inspector.generator("auto")[0], "ugrd")
        self.assertEqual(self.inspector.generator("genkernel")[0], "genkernel")

    def test_configured_generator_missing_or_unsupported(self):
        for name in ("genkernel", "dracut"):
            self.write("etc/kernel/install.conf", "initrd_generator=" + name)
            result = self.evaluate(self.inspector.discover())
            self.assertEqual(result["status"], "unknown")

    def test_no_generator_is_explicit_skip(self):
        self.assertEqual(self.evaluate(self.inspector.discover("none"))["status"], "not checked")
        self.assertEqual(self.evaluate(self.inspector.discover("none", compression="xz"))["status"], "unknown")

    def test_genkernel_best_fastest_use_final_decoders(self):
        for mode in ("best", "fastest"):
            evidence = self.genkernel(f'COMPRESS_INITRD_TYPE="{mode}"')
            result = self.evaluate(evidence, RD_XZ="n", RD_GZIP="y")
            self.assertEqual(result["status"], "compatible")
            self.assertEqual(result["eligible_formats"], ["gzip"])
            self.assertEqual(self.evaluate(evidence, RD_XZ="n")["status"], "incompatible")

    def test_genkernel_lzop_alias_and_missing_compressor(self):
        evidence = self.genkernel('COMPRESS_INITRD_TYPE="lzop"')
        self.assertEqual(self.evaluate(evidence, RD_LZO="y")["status"], "compatible")
        self.assertEqual(evidence["required_formats"], ["lzo"])
        self.tools.remove("lzop")
        evidence = self.genkernel('COMPRESS_INITRD_TYPE="lzop"')
        self.assertEqual(self.evaluate(evidence, RD_LZO="y")["status"], "incompatible")

    def test_genkernel_uncompressed_and_integrated(self):
        result = self.evaluate(self.genkernel('COMPRESS_INITRD="no"'), RD_XZ="n")
        self.assertEqual(result["status"], "compatible")
        result = self.evaluate(self.genkernel('INTEGRATED_INITRAMFS="yes"'))
        self.assertEqual(result["status"], "unknown")

    def test_genkernel_bad_command_flags(self):
        for fmt, line in (("xz", 'GKICM_XZ_CMD="xz -9"'), ("lz4", 'GKICM_LZ4_CMD="lz4 -9"')):
            result = self.evaluate(self.genkernel(f'COMPRESS_INITRD_TYPE="{fmt}"\n' + line), RD_LZ4="y")
            self.assertEqual(result["status"], "unknown")

    def test_genkernel_standard_share_and_literal_assignments(self):
        result = self.evaluate(self.genkernel('GK_SHARE="${GK_SHARE:-/usr/share/genkernel}"\nCOMPRESS_INITRD_TYPE="xz" # comment'))
        self.assertEqual(result["status"], "compatible")

    def test_shell_code_is_not_executed(self):
        marker = self.root / "must-not-exist"
        for line in (f'COMPRESS_INITRD_TYPE="$(touch {marker})"',
                     f'source {marker}', 'if true; then COMPRESS_INITRD_TYPE=xz; fi',
                     'if false; then\nCOMPRESS_INITRD_TYPE=gzip\nfi'):
            result = self.evaluate(self.genkernel(line))
            self.assertEqual(result["status"], "unknown")
            self.assertFalse(marker.exists())

    def test_missing_explicit_config_is_unknown(self):
        result = self.evaluate(self.inspector.discover("ugrd", str(self.root / "missing")))
        self.assertEqual(result["status"], "unknown")

    def test_supported_streams_and_no_compression(self):
        data = cpio()
        for fmt, packed in (("none", data), ("gzip", gzip.compress(data)),
                            ("bzip2", bz2.compress(data)), ("lzma", lzma.compress(data, format=lzma.FORMAT_ALONE)),
                            ("xz", lzma.compress(data, check=lzma.CHECK_CRC32)),
                            ("xz", lzma.compress(data, check=lzma.CHECK_NONE))):
            with self.subTest(fmt=fmt):
                result = self.inspect(packed)
                self.assertEqual([m["format"] for m in result["members"]], [fmt])

    def test_early_microcode_and_multiple_compressed_members(self):
        data = cpio(b"microcode") + b"\0" * 512
        data += lzma.compress(cpio(), check=lzma.CHECK_CRC32) + gzip.compress(cpio())
        result = self.inspect(data)
        self.assertEqual([m["format"] for m in result["members"]], ["none", "xz", "gzip"])
        evidence = self.inspector.discover("none", image=str(self.image))
        self.assertEqual(self.evaluate(evidence, RD_GZIP="n")["status"], "incompatible")
        self.assertEqual(self.evaluate(evidence, RD_GZIP="y")["status"], "compatible")

    def test_magic_inside_payload_is_not_a_stream_boundary(self):
        result = self.inspect(cpio(b"\xfd7zXZ\x00fake"))
        self.assertEqual(len(result["members"]), 1)

    def test_zstd_framing_and_concatenation(self):
        data = cpio(b"x" * 32)
        result = self.inspect(zstd_raw(data) + gzip.compress(data))
        self.assertEqual([m["format"] for m in result["members"]], ["zstd", "gzip"])
        with self.assertRaises(ValueError):
            self.inspect(zstd_raw(data)[:-1])

    def test_unsupported_xz_check_and_corrupt_images(self):
        for data in (lzma.compress(cpio(), check=lzma.CHECK_CRC64), gzip.compress(cpio())[:-5],
                     b"070701", b"\x04\x22\x4d\x18", b"\x02\x21\x4c\x18", b"\x89LZO\x00\r\n\x1a\n", b"", b"\0" * 512):
            with self.subTest(prefix=data[:8]):
                self.image.write_bytes(data)
                result = self.evaluate(self.inspector.discover("none", image=str(self.image)))
                self.assertEqual(result["status"], "incompatible")

    def test_unknown_images_are_not_certified(self):
        for data in (b"MZfake UKI", b"unrecognized"):
            self.image.write_bytes(data)
            self.assertEqual(self.evaluate(self.inspector.discover("none", image=str(self.image)))["status"], "unknown")

    def test_legacy_lz4_blocks_padding_and_concatenation(self):
        frame = b"\x02\x21\x4c\x18\x03\x00\x00\x00abc"
        result = self.inspect(cpio() + frame + b"\0" * 4 + gzip.compress(cpio()))
        self.assertEqual([m["format"] for m in result["members"]], ["none", "lz4", "gzip"])
        with self.assertRaises(ValueError):
            self.inspect(frame[:-1])

    def test_lzo_blocks_and_concatenation(self):
        header = (b"\x89LZO\0\r\n\x1a\n" + b"\x10\x30\x20\xa0\x09\x40\x01\x09"
                  + (1).to_bytes(4, "big") + b"\0" * 12 + b"\0" + b"\0" * 4)
        frame = header + (3).to_bytes(4, "big") * 2 + b"\0" * 4 + b"abc" + b"\0" * 4
        result = self.inspect(frame + gzip.compress(cpio()))
        self.assertEqual([m["format"] for m in result["members"]], ["lzo", "gzip"])
        with self.assertRaises(ValueError):
            self.inspect(frame[:-1])

    def test_producer_and_image_formats_are_both_required(self):
        self.image.write_bytes(gzip.compress(cpio()))
        evidence = self.inspector.discover("ugrd", image=str(self.image))
        self.assertEqual(evidence["required_formats"], ["gzip", "xz"])
        self.assertEqual(self.evaluate(evidence)["status"], "incompatible")


class RootCoverageTests(InspectorCase):
    MD_ROOT = [("md127", "md", {"level": "raid5", "metadata": "0.90"}), ("sda2", "disk", {}), ("sdb2", "disk", {})]

    def test_md_root_requires_mdraid_module(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT)
        result = self.evaluate(self.ugrd(), **self.kernel_md())
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(len(result["issues"]), 1)
        self.assertIn("ugrd.fs.mdraid", result["issues"][0]["message"])
        self.assertIn("etc/ugrd/config.toml", result["issues"][0]["message"])
        self.assertFalse(result["root"]["modules"]["ugrd.fs.mdraid"])
        result = self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), **self.kernel_md())
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["root"]["modules"]["ugrd.fs.mdraid"])
        self.assertEqual(result["root"]["layers"][0]["metadata"], "0.90")
        self.assertEqual(result["root"]["source"], "/dev/md127")
        self.assertEqual(result["root"]["other_md"], [])
        self.assertEqual(result["root"]["mdadm_conf"], "n/a")

    def test_md_root_needs_raid_level_symbols(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT)
        evidence = self.ugrd('modules = ["ugrd.fs.mdraid"]')
        result = self.evaluate(evidence, BLK_DEV_MD="y")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual([i["message"] for i in result["issues"] if "CONFIG_MD_RAID456" in i["message"]],
                         ["CONFIG_MD_RAID456 must be y or m for the root device (/dev/md127 raid5)"])
        self.assertEqual(self.evaluate(evidence, MD_RAID456="y")["status"], "incompatible")  # BLK_DEV_MD missing
        self.assertEqual(self.evaluate(evidence, BLK_DEV_MD="m", MD_RAID456="y")["status"], "compatible")

    def test_other_md_arrays_warn_unless_mdadm_conf_names_root(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT, other_md=("md1", "md2"))
        result = self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), **self.kernel_md())
        self.assertEqual(result["status"], "compatible")
        self.assertEqual([i["status"] for i in result["issues"]], ["warning"])
        self.assertIn("other md arrays present (md1, md2)", result["issues"][0]["message"])
        self.assertEqual(result["root"]["other_md"], ["md1", "md2"])
        self.assertEqual(result["root"]["mdadm_conf"], "not covered")
        self.write("etc/mdadm.conf", f"DEVICE partitions\nARRAY /dev/md1 UUID=11111111:22222222:33333333:44444444\nARRAY /dev/md127 UUID={MD_UUID_MDADM}\n")
        result = self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), **self.kernel_md())
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["root"]["mdadm_conf"], "covered")
        self.write("etc/mdadm.conf", "ARRAY /dev/md1 UUID=11111111:22222222:33333333:44444444\n")
        self.assertEqual(len(self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), **self.kernel_md())["issues"]), 1)
        config = 'modules = ["ugrd.fs.mdraid"]\n[copies.mdadm]\nsource = "/root/mdadm-initramfs.conf"\ndestination = "/etc/mdadm.conf"\n'
        result = self.evaluate(self.ugrd(config), **self.kernel_md())
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["root"]["mdadm_conf"], "covered")

    def test_inactive_md_devices_are_not_other_arrays(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT)
        (self.root / "sys/class/block/md0/md").mkdir(parents=True)  # autodetected empty md0, no level/uuid
        result = self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), **self.kernel_md())
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["root"]["other_md"], [])

    def test_lvm_over_luks_requires_both_modules_and_symbols(self):
        layers = [("dm-1", "lvm", {"name": "vg-root"}), ("dm-0", "crypt", {"name": "cryptroot"}),
                  ("sda2", "part", {"parent": "sda"}), ("sda", "disk", {})]
        self.fake_root_device("/dev/mapper/vg-root", layers)
        result = self.evaluate(self.ugrd(), BLK_DEV_DM="y", DM_CRYPT="m")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual(sorted(i["status"] for i in result["issues"]), ["incompatible", "incompatible"])
        self.assertEqual(result["root"]["modules"], {"ugrd.fs.lvm": False, "ugrd.crypto.cryptsetup": False})
        self.assertEqual([(l["type"], l["name"]) for l in result["root"]["layers"]], [("lvm", "vg-root"), ("crypt", "cryptroot")])
        evidence = self.ugrd('modules = ["ugrd.fs.lvm", "ugrd.crypto.cryptsetup"]')
        result = self.evaluate(evidence, BLK_DEV_DM="y", DM_CRYPT="m")
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["issues"], [])
        result = self.evaluate(evidence, BLK_DEV_DM="y")
        self.assertEqual([i["message"] for i in result["issues"]],
                         ["CONFIG_DM_CRYPT must be y or m for the root device (/dev/mapper/vg-root cryptroot)"])

    def test_plain_partition_has_no_requirements(self):
        self.fake_root_device("/dev/nvme0n1p2", [("nvme0n1p2", "part", {"parent": "nvme0n1"}), ("nvme0n1", "disk", {})])
        result = self.evaluate(self.ugrd())
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["root"], {"source": "/dev/nvme0n1p2", "layers": [], "modules": {}, "other_md": [], "mdadm_conf": "n/a"})

    def test_md_partition_and_md_symlink_resolve_to_the_array(self):
        self.fake_root_device("/dev/md/root", [("md127p1", "part", {"parent": "md127"}),
                                              ("md127", "md", {"level": "raid1", "metadata": "1.2"}), ("sda", "disk", {})])
        (self.root / "dev/md").mkdir(parents=True)
        (self.root / "dev/md/root").symlink_to("../md127p1")
        result = self.evaluate(self.ugrd(), BLK_DEV_MD="y", MD_RAID1="y")
        self.assertEqual(result["status"], "incompatible")
        self.assertEqual([l["type"] for l in result["root"]["layers"]], ["md"])
        self.assertEqual(self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), BLK_DEV_MD="y", MD_RAID1="y")["status"], "compatible")
        self.assertEqual(self.evaluate(self.ugrd('modules = ["ugrd.fs.mdraid"]'), BLK_DEV_MD="y")["status"], "incompatible")

    def test_unknown_root_sources_are_notes_not_failures(self):
        result = self.evaluate(self.ugrd())  # no mountinfo at all
        self.assertEqual(result["status"], "compatible")
        self.assertIsNone(result["root"])
        self.assertTrue(any("mountinfo" in note for note in result["notes"]))
        self.write("proc/self/mountinfo", "26 2 0:30 / / rw shared:1 - zfs rpool/ROOT/gentoo rw\n")
        result = self.evaluate(self.ugrd())
        self.assertEqual(result["status"], "compatible")
        self.assertIsNone(result["root"])
        self.assertTrue(any("rpool/ROOT/gentoo" in note for note in result["notes"]))
        self.write("proc/self/mountinfo", "26 2 8:2 / / rw shared:1 - ext4 /dev/root rw\n")
        result = self.evaluate(self.ugrd())
        self.assertEqual(result["status"], "compatible")
        self.assertTrue(any("/dev/root" in note for note in result["notes"]))

    def test_genkernel_root_coverage_is_informational(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT, other_md=("md1",))
        result = self.evaluate(self.genkernel('COMPRESS_INITRD_TYPE="xz"'), **self.kernel_md())
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["root"]["layers"][0]["level"], "raid5")
        self.assertTrue(any("genkernel" in note and "md raid5" in note for note in result["notes"]))
        self.assertEqual(self.evaluate(self.genkernel('COMPRESS_INITRD_TYPE="xz"'), BLK_DEV_MD="y")["status"], "incompatible")

    def test_render_shows_root_device_and_modules(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT, other_md=("md1", "md2"))
        values = {"BLK_DEV_INITRD": "y", "RD_XZ": "y", **self.kernel_md()}
        result = check.evaluate(self.ugrd(), values)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            check.render(result, values)
        text = output.getvalue()
        self.assertIn("  Root device: /dev/md127 (md raid5, metadata 0.90)\n", text)
        self.assertIn("    ugrd.fs.mdraid: missing\n", text)
        self.assertIn("    Other md arrays: md1, md2 (mdadm.conf: not covered)\n", text)
        self.assertIn("  warning: other md arrays present", text)
        stacked = [{"type": "lvm", "name": "vg-root"}, {"type": "crypt", "name": "cryptroot"},
                   {"type": "md", "name": "md0", "level": "raid1", "metadata": "1.2", "uuid": ""}]
        self.assertEqual(check.describe_layers(stacked), "lvm vg-root on crypt cryptroot on md raid1, metadata 1.2")
        self.assertEqual(check.describe_layers([]), "plain partition")

    def test_validate_exit_code_ignores_warnings(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT, other_md=("md1",))
        evidence = self.ugrd('modules = ["ugrd.fs.mdraid"]')
        self.assertEqual([i["status"] for i in evidence["issues"]], ["warning"])
        kernel = self.write("kernel.config", "CONFIG_BLK_DEV_INITRD=y\nCONFIG_RD_XZ=y\nCONFIG_BLK_DEV_MD=y\nCONFIG_MD_RAID456=m\n")
        run = subprocess.run([sys.executable, str(HELPER), "validate", "--kernel-config", str(kernel)],
                             input=json.dumps(evidence), capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Initramfs compression: compatible", run.stdout)
        self.assertIn("warning: other md arrays present", run.stdout)
        run = subprocess.run([sys.executable, str(HELPER), "requirements"], input=json.dumps(evidence), capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.split(), ["BLK_DEV_INITRD", "RD_XZ"])
        kernel = self.write("kernel.config", "CONFIG_BLK_DEV_INITRD=y\nCONFIG_RD_XZ=y\nCONFIG_BLK_DEV_MD=y\n")
        run = subprocess.run([sys.executable, str(HELPER), "validate", "--kernel-config", str(kernel)],
                             input=json.dumps(evidence), capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertIn("CONFIG_MD_RAID456", run.stdout)

    def test_discover_cli_accepts_host_root(self):
        self.fake_root_device("/dev/md127", self.MD_ROOT)
        self.write("etc/kernel/install.conf", "initrd_generator=none\n")
        run = subprocess.run([sys.executable, str(HELPER), "discover", "--host-root", str(self.root)],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["generator"], "none")


if __name__ == "__main__":
    unittest.main()
