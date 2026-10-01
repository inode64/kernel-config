"""Read-only audit regressions: evidence is never a removal decision."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("kconfig_audit", Path(__file__).resolve().parents[1] / "lib/kconfig_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_help_mitigations_conditional_broken_and_quotes_remain_review_only(self):
        (self.root / "Kconfig").write_text('''
config NETFILTER_XT_MATCH_DCCP
    tristate '"dccp" match (DEPRECATED)'
config NFQUEUE
    bool "New target"
    help
      This replaces the obsolete QUEUE target.
config SAFE_NFS
    bool "Disable NFS UDP"
    help
      Say Y to prevent data corruption.
config TEST_SERVER
    bool "NFS server"
    help
      This is not for use in
      production.
config DRM_AMD_DC
    bool "AMD display"
    depends on BROKEN || \\
        X86_64
config ORDINARY
    bool "Ordinary driver"
''')
        config = self.root / ".config"
        config.write_text("CONFIG_DRM_AMD_DC=y\nCONFIG_NETFILTER_XT_MATCH_DCCP=m\n# CONFIG_SAFE_NFS is not set\n")
        original = config.read_bytes()
        report = audit.audit(self.root, config, "x86")
        items = {item["symbol"]: item for item in report["entries"]}
        self.assertEqual(set(items), {"NETFILTER_XT_MATCH_DCCP", "NFQUEUE", "SAFE_NFS", "TEST_SERVER", "DRM_AMD_DC"})
        self.assertTrue(all(item["disposition"] == "review-context" for item in items.values()))
        self.assertEqual(items["NETFILTER_XT_MATCH_DCCP"]["prompt"], '"dccp" match (DEPRECATED)')
        self.assertIn("X86_64", items["DRM_AMD_DC"]["evidence"][0]["text"])
        self.assertEqual(items["DRM_AMD_DC"]["value"], "y")
        self.assertEqual(config.read_bytes(), original)

    def test_architecture_filter_fixtures_and_symlink_cycles(self):
        (self.root / "Kconfig").touch()
        for arch in ("x86", "arm64"):
            directory = self.root / "arch" / arch
            directory.mkdir(parents=True)
            (directory / "Kconfig").write_text(f'config TEST_{arch}\n bool "Deprecated device"\n')
        fixtures = self.root / "scripts/kconfig/tests/test"
        fixtures.mkdir(parents=True)
        (fixtures / "Kconfig").write_text('config IGNORE\n bool "Deprecated fixture"\n')
        (self.root / "cycle").symlink_to(self.root, target_is_directory=True)
        native = audit.audit(self.root, arch="x86_64")
        self.assertEqual([item["symbol"] for item in native["entries"]], ["TEST_x86"])
        self.assertEqual(native["entries"][0]["value"], "unknown")
        all_arch = audit.audit(self.root, arch="all")
        self.assertEqual({item["symbol"] for item in all_arch["entries"]}, {"TEST_x86", "TEST_arm64"})

    def test_indented_help_does_not_invent_symbols_or_prompts(self):
        (self.root / "Kconfig").write_text('''config NORMAL
\tbool "Normal option"
\thelp
\t  Example of a deprecated option:
\t    config FAKE
\t      bool "Unsafe example"
config REAL
    def_bool n
    prompt 'Deprecated interface'
''')
        report = audit.audit(self.root, arch="all")
        self.assertEqual({item["symbol"] for item in report["entries"]}, {"NORMAL", "REAL"})
        real = next(item for item in report["entries"] if item["symbol"] == "REAL")
        self.assertEqual(real["type"], "bool")


if __name__ == "__main__":
    unittest.main()
