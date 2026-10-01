"""Compression evidence tests with synthetic archives and isolated builder metadata."""

import bz2
import gzip
import importlib.util
import lzma
from pathlib import Path
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


class CompressionTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
