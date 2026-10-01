"""Optional discovery regressions against local Kconfig reference snapshots."""

import re
import subprocess
import unittest

from test_kernel_config import SCRIPT


SNAPSHOTS = sorted(path for path in SCRIPT.parent.glob("linux-*") if (path / "Kconfig").is_file())


@unittest.skipUnless(SNAPSHOTS, "no local Kconfig snapshots")
class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = SCRIPT.read_text()
        names = ["find_kconfig_files", "discover_kconfig_symbols_by_pattern",
                 "discover_debug_trace_kconfig_symbols", "discover_selftest_kconfig_symbols",
                 "discover_coverage_kconfig_symbols", "discover_fault_injection_kconfig_symbols",
                 "discover_legacy_kconfig_symbols", "discover_dangerous_kconfig_symbols",
                 "load_defined_symbols"]
        cls.functions = "\n".join(re.search(r"^" + name + r"\(\) \{\n.*?^\}", source,
                                            re.M | re.S)[0] for name in names)

    def discover(self, snapshot, category):
        result = subprocess.run(
            ["bash", "-c", self.functions + '\nKSRCDIR="$1"\n'
             + f"discover_{category}_kconfig_symbols", "_", str(snapshot)],
            text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return set(result.stdout.splitlines())

    def test_control_symbol_inventory_across_reference_versions(self):
        for snapshot in SNAPSHOTS:
            with self.subTest(version=snapshot.name):
                result = subprocess.run(
                    ["bash", "-euo", "pipefail", "-c", self.functions
                     + '\nKSRCDIR="$1"\ndeclare -A _DEFINED_SYMBOLS=()\n'
                     + 'load_defined_symbols\nprintf "%s\\n" "${!_DEFINED_SYMBOLS[@]}"',
                     "_", str(snapshot)], text=True, capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                symbols = set(result.stdout.splitlines())
                self.assertTrue({"TRANSPARENT_HUGEPAGE_NEVER", "LRU_GEN_ENABLED",
                                 "ZSWAP_COMPRESSOR_DEFAULT_ZSTD", "ZRAM_BACKEND_LZO",
                                 "ZRAM_DEF_COMP_LZORLE", "NUMA_BALANCING_DEFAULT_ENABLED",
                                 "TCP_CONG_BBR", "DEFAULT_RENO", "IO_URING", "RV"} <= symbols)
                if snapshot.name == "linux-7.2.8":
                    self.assertTrue({"KMALLOC_PARTITION_CACHES", "KMALLOC_PARTITION_RANDOM",
                                     "KMALLOC_PARTITION_TYPED", "NUMA_MIGRATION",
                                     "IO_URING_MOCK_FILE"} <= symbols)
                elif snapshot.name in {"linux-6.18.18", "linux-6.19.8", "linux-7.0-rc4"}:
                    self.assertIn("RANDOM_KMALLOC_CACHES", symbols)
                    self.assertNotIn("KMALLOC_PARTITION_CACHES", symbols)
                    self.assertNotIn("NUMA_MIGRATION", symbols)

    def test_selftests_across_reference_versions(self):
        for snapshot in SNAPSHOTS:
            with self.subTest(version=snapshot.name):
                self.assertIn("DAMON_KUNIT_TEST", self.discover(snapshot, "selftest"))

    def test_quoted_deprecated_prompts_and_false_positives(self):
        for snapshot in SNAPSHOTS:
            with self.subTest(version=snapshot.name):
                legacy = self.discover(snapshot, "legacy")
                self.assertTrue({"NETFILTER_XT_TARGET_NOTRACK", "NETFILTER_XT_MATCH_DCCP"} <= legacy)
                self.assertFalse({"NETFILTER_XT_TARGET_NFQUEUE", "NETFILTER_NETLINK_LOG", "TCM_USER2",
                                  "DRM_AMD_DC", "SND_SOC_SOF_BAYTRAIL", "CRYPTO_USER_API_HASH",
                                  "CRYPTO_USER_API_ENABLE_OBSOLETE", "COMPAT_VDSO"} & legacy)

    def test_new_72_controls_are_discovered(self):
        snapshot = SCRIPT.parent / "linux-7.2.8"
        if not snapshot.is_dir():
            self.skipTest("linux-7.2.8 reference snapshot is not installed")
        for category, expected in [
            ("debug_trace", "TRUSTED_KEYS_DEBUG"),
            ("selftest", "AF_RXRPC_KUNIT_TEST"),
            ("coverage", "GCOV_PROFILE_NETFILTER"),
            ("fault_injection", "BLK_ERROR_INJECTION"),
        ]:
            with self.subTest(category=category):
                self.assertIn(expected, self.discover(snapshot, category))


if __name__ == "__main__":
    unittest.main()
