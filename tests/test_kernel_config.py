#!/usr/bin/env python3
"""End-to-end regressions with isolated config/make tools; never probe real modules."""

import os
import lzma
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from test_initramfs_check import cpio


SCRIPT = Path(__file__).resolve().parents[1] / "kernel-config.sh"

CONFIG_TOOL = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
if "--keep-case" in sys.argv:
    sys.argv.remove("--keep-case")
else:
    sys.argv[4] = sys.argv[4].upper()
p = Path(sys.argv[2])
op, sym = sys.argv[3:5]
if os.environ.get("FAIL_CONFIG") == sym:
    sys.exit(23)
key = "CONFIG_" + sym
value = "y" if op == "--enable" else "n" if op == "--disable" else "m" if op == "--module" else sys.argv[5]
lines = [line for line in p.read_text().splitlines()
         if not line.startswith(key + "=") and line != "# " + key + " is not set"]
lines.append("# " + key + " is not set" if value == "n" else key + "=" + value)
p.write_text("\n".join(lines) + "\n")
'''

MAKE_TOOL = r'''#!/usr/bin/env python3
import os
from pathlib import Path
import sys
import signal
with open(os.environ["MAKE_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
p = Path(next(arg.split("=", 1)[1] for arg in sys.argv[1:]
              if arg.startswith("KCONFIG_CONFIG=")))
Path(str(p) + ".old").write_text(p.read_text())
if os.environ.get("FAIL_MAKE"):
    p.write_text("CONFIG_PARTIAL=y\n")
    sys.exit(24)
for sym in os.environ.get("DROP_SYMBOLS", "").split():
    lines = [line for line in p.read_text().splitlines()
             if not line.startswith("CONFIG_" + sym + "=")
             and line != "# CONFIG_" + sym + " is not set"]
    p.write_text("\n".join(lines) + "\n# CONFIG_" + sym + " is not set\n")
if os.environ.get("CHANGE_ORIGINAL"):
    Path(os.environ["ORIGINAL_CONFIG"]).write_text("CONFIG_EDITED_EXTERNALLY=y\n")
if os.environ.get("DELETE_TEMP"):
    p.unlink()
if os.environ.get("SIGNAL_PARENT"):
    os.kill(os.getppid(), signal.SIGTERM)
'''


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kernel-config-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tree = self.root / "linux fixture"
        (self.tree / "scripts").mkdir(parents=True)
        (self.tree / "kernel").mkdir()
        (self.root / "bin").mkdir()
        (self.root / "tmp").mkdir()
        (self.tree / "Makefile").write_text("# test fixture\n")
        (self.tree / "scripts/Kconfig.include").write_text("")
        (self.tree / "kernel/Kconfig.preempt").write_text("")
        symbols = [
            "SMP", "SCHED_CACHE", "KASAN", "DAMON_DEBUG_SANITY", "DEBUG_KERNEL",
            "ARCH_PKEY_BITS", "BLK_DEV_INITRD", "RD_GZIP", "RD_XZ", "RD_ZSTD",
            "KERNEL_GZIP", "KERNEL_ZSTD", "FW_LOADER_COMPRESS",
            "FW_LOADER_COMPRESS_XZ", "FW_LOADER_COMPRESS_ZSTD", "ZSWAP",
            "ZSWAP_DEFAULT_ON", "ZSWAP_COMPRESSOR_DEFAULT_LZO",
            "ZSWAP_COMPRESSOR_DEFAULT_DEFLATE", "CGROUPS", "CGROUP_PIDS",
        ]
        (self.tree / "Kconfig").write_text("\n".join(
            f'config {sym}\n\tbool "' +
            ('Check sanity of DAMON code' if sym == 'DAMON_DEBUG_SANITY' else sym) + '"'
            for sym in symbols
        ))
        self.config = self.tree / ".config"
        self.baseline = (
            "CONFIG_SMP=y\nCONFIG_ARCH_PKEY_BITS=4\nCONFIG_KASAN=y\n"
            "CONFIG_DAMON_DEBUG_SANITY=y\nCONFIG_BLK_DEV_INITRD=y\n"
            "CONFIG_RD_GZIP=y\nCONFIG_RD_XZ=y\n# CONFIG_RD_ZSTD is not set\n"
            "CONFIG_KERNEL_GZIP=y\n# CONFIG_KERNEL_ZSTD is not set\n"
            "CONFIG_FW_LOADER_COMPRESS=y\nCONFIG_FW_LOADER_COMPRESS_XZ=y\n"
            "CONFIG_FW_LOADER_COMPRESS_ZSTD=y\n# CONFIG_CGROUPS is not set\n"
        )
        self.config.write_text(self.baseline)
        self.write_tool(self.tree / "scripts/config", CONFIG_TOOL)
        self.write_tool(self.root / "bin/make", MAKE_TOOL)
        self.env = os.environ.copy()
        # Isolate tests from any tuning inherited from the developer's shell.
        for name in re.findall(r"^init_tunable ([A-Z_]+) ", SCRIPT.read_text(), re.M):
            self.env.pop(name, None)
        for name in ("ALL_OPTIMIZATIONS", "KSRCDIR", "CONFIG_FILE", "KBUILD_OUTPUT"):
            self.env.pop(name, None)
        self.env.update(
            PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
            TMPDIR=str(self.root / "tmp"),
            MAKE_LOG=str(self.root / "make.log"),
            ORIGINAL_CONFIG=str(self.config),
            INITRAMFS_GENERATOR="none",
        )

    @staticmethod
    def write_tool(path, content):
        path.write_text(content)
        path.chmod(0o755)

    def run_script(self, *args, env=None, source=None):
        result = subprocess.run(
            ["bash", str(SCRIPT), str(source or self.tree), *args],
            cwd=self.root, env=self.env | (env or {}),
            text=True, capture_output=True, timeout=30,
        )
        self.assertFalse(list(self.tree.glob(".kernel-config.*")), result.stderr)
        self.assertFalse(list((self.root / "tmp").iterdir()), result.stderr)
        return result

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def assert_untouched(self):
        self.assertEqual(self.config.read_text(), self.baseline)
        self.assertFalse(list(self.tree.glob(".config.bak.*")))

    def add_symbols(self, *symbols):
        with (self.tree / "Kconfig").open("a") as stream:
            for sym in symbols:
                kind = "tristate" if sym == "ZRAM" else "bool"
                stream.write(f'\nconfig {sym}\n\t{kind} "{sym}"\n')

    def set_baseline_symbols(self, **values):
        lines = self.config.read_text().splitlines()
        for sym, value in values.items():
            lines = [line for line in lines if not line.startswith(f"CONFIG_{sym}=")
                     and line != f"# CONFIG_{sym} is not set"]
            lines.append(f"# CONFIG_{sym} is not set" if value == "n" else f"CONFIG_{sym}={value}")
        self.baseline = "\n".join(lines) + "\n"
        self.config.write_text(self.baseline)

    def assert_symbol(self, sym, value):
        expected = f"# CONFIG_{sym} is not set" if value == "n" else f"CONFIG_{sym}={value}"
        self.assertIn(expected, self.config.read_text().splitlines())

    def test_relative_source_and_config_paths_with_spaces(self):
        result = self.run_script("--config-file", ".config", "--sched-cache", "on",
                                 source=self.tree.name)
        self.assert_success(result)
        self.assertIn("CONFIG_SCHED_CACHE=y", self.config.read_text())

    def test_relative_source_default_config(self):
        self.assert_success(self.run_script("--sched-cache=on", source=self.tree.name))

    def test_check_is_read_only(self):
        self.assert_success(self.run_script("--check", "--sched-cache=on"))
        self.assert_untouched()
        self.assertFalse((self.root / "make.log").exists())

    def test_invalid_values_are_rejected_before_mutation(self):
        for option in ["applications", "host-type", "nr-cpus", "cpu-vendor-filter",
                       "video-support", "optimization-profile", "tpm-support",
                       "sched-cache", "kernel-compression", "initrd-compression",
                       "firmware-compression", "uefi-support", "numa-support"]:
            with self.subTest(option=option):
                result = self.run_script("--prune-sanitizers", f"--{option}=invalid")
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()
        self.assertFalse((self.root / "make.log").exists())

    def test_invalid_extended_options_and_conflicts_are_read_only(self):
        options = ["preemption", "preempt-dynamic", "tick-mode", "thp", "lru-gen", "zswap",
                   "zswap-compressor", "zram", "zram-compressor", "numa-balancing",
                   "kmalloc-partition", "tcp-congestion", "io-uring"]
        cases = [[f"--{option}=bad"] for option in options] + [
            ["--zswap=keep|on"], ["--prune-runtime-verification=bad"],
            ["--zswap=off", "--zswap-compressor=zstd"],
            ["--zram=off", "--zram-compressor=lz4"],
            ["--numa-support=off", "--numa-balancing=on"],
            ["--preemption=rt", "--thp=madvise"],
            ["--preemption=rt", "--numa-balancing=on"],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_script("--prune-sanitizers", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()
        self.assertFalse((self.root / "make.log").exists())

    def test_preemption_models_and_dynamic_are_independent(self):
        models = {"none": "PREEMPT_NONE", "voluntary": "PREEMPT_VOLUNTARY",
                  "full": "PREEMPT", "lazy": "PREEMPT_LAZY", "rt": "PREEMPT"}
        self.add_symbols(*set(models.values()), "PREEMPT_DYNAMIC", "PREEMPT_RT")
        for mode, selected in models.items():
            with self.subTest(mode=mode):
                self.config.write_text(self.baseline)
                result = self.run_script("--strict", f"--preemption={mode}", "--preempt-dynamic=on")
                self.assert_success(result)
                for sym in set(models.values()):
                    self.assert_symbol(sym, "y" if sym == selected else "n")
                self.assert_symbol("PREEMPT_DYNAMIC", "y")
                self.assert_symbol("PREEMPT_RT", "y" if mode == "rt" else "n")

    def test_tick_modes_are_exclusive(self):
        modes = {"periodic": "HZ_PERIODIC", "idle": "NO_HZ_IDLE", "full": "NO_HZ_FULL"}
        self.add_symbols(*modes.values())
        for mode, selected in modes.items():
            with self.subTest(mode=mode):
                self.assert_success(self.run_script("--strict", f"--tick-mode={mode}"))
                for sym in modes.values():
                    self.assert_symbol(sym, "y" if sym == selected else "n")

    def test_scheduler_profiles_choose_one_model_and_tick(self):
        models = ["PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT", "PREEMPT_LAZY"]
        ticks = ["HZ_PERIODIC", "NO_HZ_IDLE", "NO_HZ_FULL"]
        self.add_symbols(*models, *ticks, "PREEMPT_RT", "PREEMPT_DYNAMIC")
        self.set_baseline_symbols(PREEMPT_NONE="n", PREEMPT_DYNAMIC="n", EXPERT="y",
                                  ARCH_SUPPORTS_RT="y", HAVE_CONTEXT_TRACKING_USER="y",
                                  HAVE_VIRT_CPU_ACCOUNTING_GEN="y")
        for profile, model, tick in [("server", "PREEMPT_NONE", "NO_HZ_FULL"),
                                     ("desktop", "PREEMPT", "NO_HZ_IDLE"),
                                     ("realtime", "PREEMPT", "NO_HZ_FULL")]:
            with self.subTest(profile=profile):
                self.config.write_text(self.baseline)
                self.assert_success(self.run_script(f"--optimization-profile={profile}"))
                for sym in models:
                    self.assert_symbol(sym, "y" if sym == model else "n")
                for sym in ticks:
                    self.assert_symbol(sym, "y" if sym == tick else "n")
                self.assert_symbol("PREEMPT_DYNAMIC", "y" if profile == "desktop" else "n")
                self.assert_symbol("PREEMPT_RT", "y" if profile == "realtime" else "n")

    def test_explicit_controls_override_profile_in_strict_mode(self):
        self.add_symbols("PREEMPT_NONE", "PREEMPT_VOLUNTARY", "PREEMPT", "PREEMPT_LAZY",
                         "PREEMPT_DYNAMIC", "PREEMPT_RT", "NO_HZ_FULL", "NO_HZ_IDLE", "HZ_PERIODIC",
                         "TRANSPARENT_HUGEPAGE", "TRANSPARENT_HUGEPAGE_ALWAYS",
                         "TRANSPARENT_HUGEPAGE_MADVISE", "TRANSPARENT_HUGEPAGE_NEVER", "NUMA_BALANCING")
        result = self.run_script("--strict", "--optimization-profile=server", "--preemption=lazy",
                                 "--preempt-dynamic=on", "--tick-mode=periodic", "--thp=off",
                                 "--zswap=off", "--numa-balancing=off")
        self.assert_success(result)
        self.assert_symbol("PREEMPT_LAZY", "y")
        self.assert_symbol("PREEMPT_DYNAMIC", "y")
        self.assert_symbol("HZ_PERIODIC", "y")
        self.assert_symbol("TRANSPARENT_HUGEPAGE", "n")
        self.assert_symbol("ZSWAP", "n")
        self.assert_symbol("NUMA_BALANCING", "n")

    def test_realtime_profile_clears_disabled_children(self):
        self.add_symbols("PSI", "PSI_DEFAULT_DISABLED")
        result = self.run_script("--strict", "--all-optimizations", "--optimization-profile=realtime",
                                 env={"DROP_SYMBOLS": "PSI_DEFAULT_DISABLED ZSWAP_DEFAULT_ON ZSWAP_COMPRESSOR_DEFAULT_LZO"})
        self.assert_success(result)
        self.assert_symbol("PSI", "n")
        self.assert_symbol("PSI_DEFAULT_DISABLED", "n")
        self.assert_symbol("ZSWAP", "n")

    def test_keep_controls_preserve_baseline(self):
        self.add_symbols("PREEMPT", "PREEMPT_DYNAMIC", "NO_HZ_IDLE", "TRANSPARENT_HUGEPAGE",
                         "LRU_GEN", "ZRAM", "NUMA_BALANCING", "RANDOM_KMALLOC_CACHES", "IO_URING")
        self.set_baseline_symbols(PREEMPT="y", PREEMPT_DYNAMIC="n", NO_HZ_IDLE="y",
                                  TRANSPARENT_HUGEPAGE="y", LRU_GEN="y", ZRAM="m",
                                  NUMA_BALANCING="n", RANDOM_KMALLOC_CACHES="y", IO_URING="y")
        controls = ["preemption", "preempt-dynamic", "tick-mode", "thp", "lru-gen", "zswap",
                    "zswap-compressor", "zram", "zram-compressor", "numa-balancing",
                    "kmalloc-partition", "tcp-congestion", "io-uring"]
        self.assert_success(self.run_script("--strict", *(f"--{name}=keep" for name in controls)))
        self.assertEqual(self.config.read_text(), self.baseline)

    def test_thp_off_and_never_are_distinct(self):
        self.add_symbols("TRANSPARENT_HUGEPAGE", "TRANSPARENT_HUGEPAGE_ALWAYS",
                         "TRANSPARENT_HUGEPAGE_MADVISE", "TRANSPARENT_HUGEPAGE_NEVER")
        for mode in ["always", "madvise", "never", "off"]:
            with self.subTest(mode=mode):
                self.assert_success(self.run_script("--strict", f"--thp={mode}"))
                self.assert_symbol("TRANSPARENT_HUGEPAGE", "n" if mode == "off" else "y")
                if mode != "off":
                    for suffix in ["ALWAYS", "MADVISE", "NEVER"]:
                        self.assert_symbol(f"TRANSPARENT_HUGEPAGE_{suffix}", "y" if suffix == mode.upper() else "n")

    def test_lru_gen_support_and_default(self):
        self.add_symbols("LRU_GEN", "LRU_GEN_ENABLED")
        for mode, value in [("on", "y"), ("off", "n")]:
            self.assert_success(self.run_script("--strict", f"--lru-gen={mode}"))
            self.assert_symbol("LRU_GEN", value)
            self.assert_symbol("LRU_GEN_ENABLED", value)

    def test_zswap_compressors_replace_preset_choice(self):
        algorithms = ["lzo", "lz4", "lz4hc", "zstd", "deflate", "842"]
        self.add_symbols("SWAP", *(f"ZSWAP_COMPRESSOR_DEFAULT_{a.upper()}" for a in algorithms))
        for algorithm in algorithms:
            with self.subTest(algorithm=algorithm):
                self.assert_success(self.run_script("--strict", "--all-optimizations",
                                                     "--zswap=on", f"--zswap-compressor={algorithm}"))
                self.assert_symbol("ZSWAP", "y")
                self.assert_symbol("ZSWAP_DEFAULT_ON", "y")
                for other in algorithms:
                    self.assert_symbol(f"ZSWAP_COMPRESSOR_DEFAULT_{other.upper()}", "y" if algorithm == other else "n")

    def test_zram_module_and_compressor(self):
        self.add_symbols("ZRAM", "ZRAM_BACKEND_LZO", "ZRAM_BACKEND_ZSTD",
                         "ZRAM_DEF_COMP_LZORLE", "ZRAM_DEF_COMP_ZSTD")
        self.set_baseline_symbols(MODULES="y", ZRAM="m", ZRAM_DEF_COMP_LZORLE="y")
        self.assert_success(self.run_script("--strict", "--zram-compressor=zstd"))
        self.assert_symbol("ZRAM", "m")
        self.assert_symbol("ZRAM_BACKEND_ZSTD", "y")
        self.assert_symbol("ZRAM_DEF_COMP_ZSTD", "y")
        self.assert_symbol("ZRAM_DEF_COMP_LZORLE", "n")
        self.assert_success(self.run_script("--strict", "--zram=builtin", "--zram-compressor=lzo-rle"))
        self.assert_symbol("ZRAM", "y")
        self.assert_symbol("ZRAM_BACKEND_LZO", "y")
        self.assert_symbol("ZRAM_DEF_COMP_LZORLE", "y")

    def test_zram_module_requires_modules_without_enabling_them(self):
        self.add_symbols("ZRAM")
        result = self.run_script("--strict", "--zram=module")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires CONFIG_MODULES=y", result.stderr)
        self.assert_untouched()

    def test_numa_migration_mapping_across_versions(self):
        self.add_symbols("NUMA", "MIGRATION", "NUMA_BALANCING", "NUMA_BALANCING_DEFAULT_ENABLED")
        self.assert_success(self.run_script("--strict", "--numa-balancing=on"))
        self.assert_symbol("MIGRATION", "y")
        self.config.write_text(self.baseline)
        self.add_symbols("NUMA_MIGRATION")
        self.assert_success(self.run_script("--strict", "--numa-balancing=on"))
        self.assert_symbol("NUMA_MIGRATION", "y")
        self.assert_symbol("NUMA_BALANCING", "y")

    def test_kmalloc_legacy_and_new_mapping(self):
        self.add_symbols("RANDOM_KMALLOC_CACHES")
        self.assert_success(self.run_script("--strict", "--kmalloc-partition=random"))
        self.assert_symbol("RANDOM_KMALLOC_CACHES", "y")
        self.config.write_text(self.baseline)
        self.add_symbols("KMALLOC_PARTITION_CACHES", "KMALLOC_PARTITION_RANDOM", "KMALLOC_PARTITION_TYPED")
        self.assert_success(self.run_script("--strict", "--kmalloc-partition=typed"))
        self.assert_symbol("KMALLOC_PARTITION_CACHES", "y")
        self.assert_symbol("KMALLOC_PARTITION_TYPED", "y")
        self.assertNotIn("CONFIG_RANDOM_KMALLOC_CACHES=", self.config.read_text())

    def test_typed_kmalloc_on_old_kernel_fails_strictly(self):
        self.add_symbols("RANDOM_KMALLOC_CACHES")
        result = self.run_script("--strict", "--kmalloc-partition=typed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not supported", result.stderr)
        self.assert_untouched()

    def test_kconfig_rejects_typed_mode_without_compiler_capability(self):
        self.add_symbols("KMALLOC_PARTITION_CACHES", "KMALLOC_PARTITION_RANDOM", "KMALLOC_PARTITION_TYPED")
        result = self.run_script("--strict", "--kmalloc-partition=typed",
                                 env={"DROP_SYMBOLS": "KMALLOC_PARTITION_TYPED"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_tcp_algorithm_and_bbr_fq_support(self):
        self.add_symbols("NET", "INET", "TCP_CONG_ADVANCED", "TCP_CONG_CUBIC", "TCP_CONG_BBR",
                         "NET_SCHED", "NET_SCH_FQ", "DEFAULT_CUBIC", "DEFAULT_BBR", "DEFAULT_RENO")
        for algorithm in ["cubic", "bbr", "reno"]:
            with self.subTest(algorithm=algorithm):
                self.assert_success(self.run_script("--strict", f"--tcp-congestion={algorithm}"))
                for other in ["cubic", "bbr", "reno"]:
                    self.assert_symbol(f"DEFAULT_{other.upper()}", "y" if other == algorithm else "n")
                if algorithm == "bbr":
                    self.assert_symbol("NET_SCH_FQ", "y")
                    self.assert_symbol("TCP_CONG_BBR", "y")

    def test_io_uring_off_requires_expert(self):
        self.add_symbols("IO_URING")
        result = self.run_script("--strict", "--io-uring=off")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires CONFIG_EXPERT=y", result.stderr)
        self.assert_untouched()
        self.set_baseline_symbols(EXPERT="y")
        self.assert_success(self.run_script("--strict", "--io-uring=off"))
        self.assert_symbol("IO_URING", "n")

    def test_protected_choice_does_not_disable_siblings(self):
        self.add_symbols("NO_HZ_FULL", "NO_HZ_IDLE", "HZ_PERIODIC")
        self.set_baseline_symbols(NO_HZ_IDLE="y")
        result = self.run_script("--tick-mode=full", "--protected-config-symbols=NO_HZ_IDLE")
        self.assert_success(result)
        self.assertIn("conflicts with protected", result.stderr)
        self.assert_untouched()

    def test_expanded_pruning(self):
        # Prompts intentionally avoid the old discovery keywords.
        with (self.tree / "Kconfig").open("a") as stream:
            for sym, prompt in [("RV", "Runtime Verification"), ("SLUB_STATS", "Enable performance statistics"),
                                ("ZRAM_MEMORY_TRACKING", "Track zRam block status"),
                                ("IO_URING_MOCK_FILE", "Enable io_uring mock files (Experimental)")]:
                stream.write(f'\nconfig {sym}\n\tbool "{prompt}"\n')
        self.set_baseline_symbols(RV="y", SLUB_STATS="y", ZRAM_MEMORY_TRACKING="y", IO_URING_MOCK_FILE="y")
        for args, disabled in [(["--prune-runtime-verification"], ["RV"]),
                               (["--prune-observability"], ["RV"]),
                               (["--prune-debug-trace"], ["RV", "SLUB_STATS", "ZRAM_MEMORY_TRACKING"]),
                               (["--prune-selftest"], ["IO_URING_MOCK_FILE"])]:
            with self.subTest(args=args):
                self.config.write_text(self.baseline)
                self.assert_success(self.run_script("--strict", *args))
                for sym in disabled:
                    self.assert_symbol(sym, "n")

    def test_invalid_protected_symbol(self):
        result = self.run_script("--protected-config-symbols=FOO;BAR")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_mixed_case_symbols_survive_config_tool_cache_and_diff(self):
        self.add_symbols("SND_SOC_AMD_ACP3x")
        self.set_baseline_symbols(SND_SOC_AMD_ACP3x="m")
        result = self.run_script("--strict", "--dry-run", "--disable-symbols=CONFIG_SND_SOC_AMD_ACP3x")
        self.assert_success(result)
        self.assertIn("CONFIG_SND_SOC_AMD_ACP3x: m -> n", result.stdout)
        self.assertNotIn("CONFIG_SND_SOC_AMD_ACP3X", result.stdout)
        self.assert_untouched()
        self.assert_success(self.run_script("--strict", "--disable-symbols=SND_SOC_AMD_ACP3x"))
        self.assert_symbol("SND_SOC_AMD_ACP3x", "n")

    def test_protected_mixed_case_symbol_is_preserved(self):
        self.add_symbols("SND_SOC_AMD_ACP3x")
        self.set_baseline_symbols(SND_SOC_AMD_ACP3x="m")
        result = self.run_script("--strict", "--protected-config-symbols=SND_SOC_AMD_ACP3x",
                                 "--disable-symbols=SND_SOC_AMD_ACP3x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_SND_SOC_AMD_ACP3x requested=n final=m", result.stderr)
        self.assert_untouched()

    def test_explicit_symbol_lists_types_modules_and_conflicts(self):
        self.add_symbols("ZRAM", "IO_URING")
        self.set_baseline_symbols(MODULES="y")
        self.assert_success(self.run_script("--strict", "--module-symbols=ZRAM", "--enable-symbols=IO_URING"))
        self.assert_symbol("ZRAM", "m")
        self.assert_symbol("IO_URING", "y")
        for args in [("--disable-symbols=ZRAM", "--enable-symbols=ZRAM"),
                     ("--disable-symbols=ZRAM;id",), ("--strict", "--module-symbols=IO_URING")]:
            with self.subTest(args=args):
                before = self.config.read_bytes()
                result = self.run_script(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.config.read_bytes(), before)

    def test_pruning_skips_hidden_capabilities_integers_and_guarded_prompts(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('''
menu "Debugging"
config ARCH_HAS_DEBUG_VM_PGTABLE
    bool "Architecture debug capability"
config INTERNAL_DEBUG
    bool
    default y
config BOOTPARAM_HUNG_TASK_PANIC
    int "Debug panic threshold"
    default 0
config SLUB_DEBUG
    bool "SLUB debugging" if EXPERT
config RealDebug_x
    bool "Device debug"
endmenu
''')
        self.set_baseline_symbols(ARCH_HAS_DEBUG_VM_PGTABLE="y", INTERNAL_DEBUG="y",
                                  BOOTPARAM_HUNG_TASK_PANIC="3", SLUB_DEBUG="y", RealDebug_x="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        for sym in ("ARCH_HAS_DEBUG_VM_PGTABLE", "INTERNAL_DEBUG", "SLUB_DEBUG"):
            self.assert_symbol(sym, "y")
        self.assert_symbol("BOOTPARAM_HUNG_TASK_PANIC", "3")
        self.assert_symbol("RealDebug_x", "n")
        result = self.run_script("--strict", "--disable-symbols=BOOTPARAM_HUNG_TASK_PANIC")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a bool/tristate", result.stderr)

    def test_pruning_honors_expert_prompt_on_target_architecture(self):
        (self.tree / "arch/x86").mkdir(parents=True)
        (self.tree / "arch/arm64").mkdir(parents=True)
        (self.tree / "arch/x86/Kconfig").write_text('config EARLY_PRINTK\n bool "Early debug" if EXPERT\n')
        (self.tree / "arch/arm64/Kconfig").write_text('config EARLY_PRINTK\n bool "Early debug"\n')
        self.set_baseline_symbols(EARLY_PRINTK="y", EXPERT="n")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace", env={"ARCH": "x86"}))
        self.assert_symbol("EARLY_PRINTK", "y")
        self.set_baseline_symbols(EXPERT="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace", env={"ARCH": "x86"}))
        self.assert_symbol("EARLY_PRINTK", "n")

    def test_legacy_pruning_preserves_af_alg_application_abi(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('''
config CRYPTO_USER_API
    tristate
config CRYPTO_USER_API_SKCIPHER
    tristate "Symmetric key cipher algorithms (deprecated)"
config CRYPTO_USER_API_ENABLE_OBSOLETE
    bool "Obsolete cryptographic algorithms"
config OLD_DRIVER
    tristate "Obsolete device driver"
''')
        self.set_baseline_symbols(CRYPTO_USER_API="y", CRYPTO_USER_API_SKCIPHER="m",
                                  CRYPTO_USER_API_ENABLE_OBSOLETE="y", OLD_DRIVER="m")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("CRYPTO_USER_API", "y")
        self.assert_symbol("CRYPTO_USER_API_SKCIPHER", "m")
        self.assert_symbol("CRYPTO_USER_API_ENABLE_OBSOLETE", "y")
        self.assert_symbol("OLD_DRIVER", "n")

    def test_pruning_preserves_transitive_selects_from_retained_features(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('''
config ACTIVE_DRIVER
    bool "Required driver"
    select DEBUG_HELPER
config DEBUG_HELPER
    bool "Debug helper"
    select DEBUG_LEAF
config DEBUG_LEAF
    bool "Debug leaf"
config UNUSED_DEBUG_DRIVER
    bool "Debug driver"
    select UNUSED_DEBUG_HELPER
config UNUSED_DEBUG_HELPER
    bool "Debug helper"
''')
        self.set_baseline_symbols(ACTIVE_DRIVER="y", DEBUG_HELPER="y", DEBUG_LEAF="y",
                                  UNUSED_DEBUG_DRIVER="y", UNUSED_DEBUG_HELPER="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        for sym in ("ACTIVE_DRIVER", "DEBUG_HELPER", "DEBUG_LEAF"):
            self.assert_symbol(sym, "y")
        for sym in ("UNUSED_DEBUG_DRIVER", "UNUSED_DEBUG_HELPER"):
            self.assert_symbol(sym, "n")

    def test_incomplete_tree(self):
        (self.tree / "scripts/Kconfig.include").unlink()
        result = self.run_script("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Incomplete kernel tree", result.stderr)
        self.assert_untouched()

    def test_single_escaped_quotes_menus_and_def_bool_prompts(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write(r'''
config NETFILTER_XT_MATCH_DCCP
    tristate '"dccp" protocol match support (DEPRECATED)'
config QUOTED_LEGACY
    bool "Support \"old device\" (deprecated)"
config GUARDED_LEGACY
    bool 'Deprecated guarded ABI' if EXPERT
config EXPLICIT_PROMPT
    def_bool n
    prompt 'Deprecated compatibility API'
config HIDDEN_LEGACY
    def_bool y
menu 'Legacy devices'
config MENU_CHILD
    bool 'Supported device'
config HIDDEN_QUOTED_DEFAULT
    def_bool "y"
endmenu
config REPLACEMENT
    bool 'New interface'
    help
      This replaces the deprecated old interface.
config BEFORE_CHOICE
    def_bool n
    prompt 'Regular driver' if EXPERT
choice
    prompt 'Legacy ABI selection'
config CHOICE_ITEM
    bool 'Current ABI'
endchoice
''')
        self.set_baseline_symbols(NETFILTER_XT_MATCH_DCCP="m", QUOTED_LEGACY="y", GUARDED_LEGACY="y",
                                  EXPLICIT_PROMPT="y", HIDDEN_LEGACY="y", MENU_CHILD="y", REPLACEMENT="y",
                                  BEFORE_CHOICE="y", CHOICE_ITEM="y", HIDDEN_QUOTED_DEFAULT="y", EXPERT="n")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        for sym in ("NETFILTER_XT_MATCH_DCCP", "QUOTED_LEGACY", "EXPLICIT_PROMPT", "MENU_CHILD"):
            self.assert_symbol(sym, "n")
        for sym in ("GUARDED_LEGACY", "HIDDEN_LEGACY", "REPLACEMENT", "BEFORE_CHOICE", "CHOICE_ITEM", "HIDDEN_QUOTED_DEFAULT"):
            self.assert_symbol(sym, "y")

    def test_reviewed_help_only_rules_and_expert_visibility(self):
        symbols = ("GPIO_CDEV_V1", "SND_HDA_CTL_DEV_ID", "MODULE_FORCE_LOAD", "MODULE_FORCE_UNLOAD",
                   "NFSD_FLEXFILELAYOUT", "CXL_MEM_RAW_COMMANDS", "I2C_AT91_SLAVE_EXPERIMENTAL",
                   "CRYPTO_BENCHMARK", "MMC_TEST", "IOMMUFD_TEST", "HVC_UDBG")
        self.add_symbols(*symbols)
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('\nconfig SGETMASK_SYSCALL\n bool "sgetmask/ssetmask syscalls" if EXPERT\n')
        self.set_baseline_symbols(**dict.fromkeys(symbols, "y"), SGETMASK_SYSCALL="y", EXPERT="n")
        result = self.run_script("--strict", "--prune-legacy", "--prune-dangerous", "--prune-selftest")
        self.assert_success(result)
        for sym in symbols:
            self.assert_symbol(sym, "n")
        self.assert_symbol("SGETMASK_SYSCALL", "y")
        self.assertIn("Retaining CONFIG_SGETMASK_SYSCALL", result.stdout)

    def test_nfs_profile_does_not_request_flexfile_testing(self):
        self.add_symbols("NFSD", "NFSD_V4", "NFSD_FLEXFILELAYOUT")
        self.set_baseline_symbols(NFSD_FLEXFILELAYOUT="n")
        self.assert_success(self.run_script("--strict", "--applications=nfs-server"))
        self.assert_symbol("NFSD_FLEXFILELAYOUT", "n")
        self.set_baseline_symbols(NFSD_FLEXFILELAYOUT="y")
        self.assert_success(self.run_script("--strict", "--applications=nfs-server", "--prune-dangerous"))
        self.assert_symbol("NFSD_FLEXFILELAYOUT", "n")
        self.assert_success(self.run_script("--strict", "--applications=nfs-server", "--prune-dangerous",
                                            "--enable-symbols=NFSD_FLEXFILELAYOUT"))
        self.assert_symbol("NFSD_FLEXFILELAYOUT", "y")

    def test_risk_controls_keep_inversion_and_override_pruning(self):
        symbols = ("MODULES", "MODULE_UNLOAD", "MODULE_FORCE_LOAD", "MODULE_FORCE_UNLOAD",
                   "NFS_FS", "NFS_DISABLE_UDP_SUPPORT", "CRYPTO_USER_API_ENABLE_OBSOLETE", "CRYPTO_USER_API_HASH")
        self.add_symbols(*symbols)
        self.set_baseline_symbols(**dict.fromkeys(symbols, "y"))
        self.set_baseline_symbols(NFS_FS="m", NFS_DISABLE_UDP_SUPPORT="n")
        before = self.config.read_bytes()
        self.assert_success(self.run_script("--strict"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script("--strict", "--module-force-load=off", "--module-force-unload=off",
                                            "--nfs-udp=off", "--obsolete-crypto=off"))
        for sym in ("MODULE_FORCE_LOAD", "MODULE_FORCE_UNLOAD", "CRYPTO_USER_API_ENABLE_OBSOLETE"):
            self.assert_symbol(sym, "n")
        self.assert_symbol("NFS_DISABLE_UDP_SUPPORT", "y")
        self.assert_symbol("CRYPTO_USER_API_HASH", "y")
        self.assert_success(self.run_script("--strict", "--prune-dangerous", "--module-force-load=on",
                                            "--module-force-unload=on", "--nfs-udp=on"))
        self.assert_symbol("MODULE_FORCE_LOAD", "y")
        self.assert_symbol("MODULE_FORCE_UNLOAD", "y")
        self.assert_symbol("NFS_DISABLE_UDP_SUPPORT", "n")

    def test_risk_controls_reject_missing_parents_invalid_modes_and_protection(self):
        self.add_symbols("MODULE_FORCE_LOAD", "MODULE_FORCE_UNLOAD", "NFS_DISABLE_UDP_SUPPORT")
        for flag in ("--module-force-load=on", "--module-force-unload=on", "--nfs-udp=off", "--obsolete-crypto=off",
                     "--module-force-load=invalid", "--nfs-udp=auto"):
            with self.subTest(flag=flag):
                result = self.run_script("--strict", flag)
                self.assertNotEqual(result.returncode, 0)
                self.assert_untouched()
        self.set_baseline_symbols(MODULE_FORCE_UNLOAD="y")
        before = self.config.read_bytes()
        result = self.run_script("--strict", "--module-force-unload=off", "--protected-config-symbols=MODULE_FORCE_UNLOAD")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)

    def test_deprecated_alias_migration_preserves_replacement_and_protection(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('''
config HID_THINGM
    tristate "ThingM blink(1) USB RGB LED"
    select HID_LED
config HID_LED
    tristate "USB LED support"
''')
        self.set_baseline_symbols(HID_THINGM="m", HID_LED="y")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("HID_THINGM", "n")
        self.assert_symbol("HID_LED", "y")
        self.set_baseline_symbols(HID_THINGM="m", HID_LED="n")
        self.assert_success(self.run_script("--strict", "--prune-legacy", "--protected-config-symbols=HID_LED"))
        self.assert_symbol("HID_THINGM", "m")
        self.assert_symbol("HID_LED", "n")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("HID_THINGM", "n")
        self.assert_symbol("HID_LED", "m")

    def test_alias_selected_by_retained_driver_is_not_migrated(self):
        self.add_symbols("HID_THINGM", "HID_LED")
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('\nconfig PARENT\n bool "Required parent"\n select HID_THINGM\n')
        self.set_baseline_symbols(PARENT="y", HID_THINGM="y", HID_LED="y")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("HID_THINGM", "y")

    def test_audit_mode_accepts_incomplete_tree_without_make_or_writes(self):
        (self.tree / "Makefile").unlink()
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('\nconfig REVIEW_ME\n bool "Device"\n help\n   This is deprecated.\n')
        self.assert_success(result := self.run_script("--audit-kconfig", "--prune-legacy"))
        self.assertIn("CONFIG_REVIEW_ME", result.stdout)
        self.assert_untouched()
        self.assertFalse((self.root / "make.log").exists())

    def test_missing_config_tool_is_not_generated(self):
        (self.tree / "scripts/config").unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "make.log").exists())
        self.assert_untouched()

    def test_config_tool_failure_rolls_back(self):
        result = self.run_script("--prune-sanitizers", "--sched-cache=on",
                                 env={"FAIL_CONFIG": "SCHED_CACHE"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("scripts/config failed", result.stderr)
        self.assert_untouched()

    def test_olddefconfig_failure_rolls_back(self):
        result = self.run_script("--prune-sanitizers", env={"FAIL_MAKE": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_missing_normalized_config_is_fatal(self):
        result = self.run_script("--sched-cache=off", env={"DELETE_TEMP": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_sigterm_cleans_up_without_committing(self):
        result = self.run_script("--prune-sanitizers", env={"SIGNAL_PARENT": "1"})
        self.assertEqual(result.returncode, 143)
        self.assert_untouched()

    def test_dry_run_changes_and_cleanup(self):
        result = self.run_script("--dry-run", "--sched-cache=on")
        self.assert_success(result)
        self.assertIn("CONFIG_SCHED_CACHE: n -> y", result.stdout)
        self.assert_untouched()

    def test_dry_run_never_probes_modules(self):
        result = self.run_script("--dry-run", "--prune-unused-modules")
        self.assert_success(result)
        self.assertIn("dry-run: active module probing is skipped", result.stdout)
        calls = (self.root / "make.log").read_text().splitlines()
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith(" olddefconfig"))
        self.assert_untouched()

    def test_strict_rejects_dependency_reversal(self):
        result = self.run_script("--strict", "--sched-cache=on",
                                 env={"DROP_SYMBOLS": "SCHED_CACHE"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requested=y final=n", result.stderr)
        self.assert_untouched()

    def test_non_strict_reports_dependency_reversal(self):
        result = self.run_script("--sched-cache=on", env={"DROP_SYMBOLS": "SCHED_CACHE"})
        self.assert_success(result)
        self.assertIn("Unmet request", result.stderr)

    def test_strict_accepts_satisfied_request(self):
        self.assert_success(self.run_script("--strict", "--sched-cache=on"))

    def test_strict_protects_against_indirect_changes(self):
        result = self.run_script("--strict", env={"DROP_SYMBOLS": "ARCH_PKEY_BITS"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Protected symbol changed", result.stderr)
        self.assert_untouched()

    def test_protected_explicit_request_conflict(self):
        result = self.run_script("--strict", "--sched-cache=on",
                                 "--protected-config-symbols=SCHED_CACHE")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_absent_but_defined_children_can_be_enabled(self):
        result = self.run_script("--applications=docker")
        self.assert_success(result)
        self.assertIn("CONFIG_CGROUPS=y", self.config.read_text())
        self.assertIn("CONFIG_CGROUP_PIDS=y", self.config.read_text())

    def test_unsupported_control_does_not_invent_symbol(self):
        kconfig = self.tree / "Kconfig"
        kconfig.write_text(kconfig.read_text().replace(
            'config SCHED_CACHE\n\tbool "SCHED_CACHE"', 'config OTHER\n\tbool "OTHER"'))
        result = self.run_script("--strict", "--sched-cache=on")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not defined", result.stderr)
        self.assert_untouched()

    def test_kconfig_parser_fixtures_are_not_kernel_symbols(self):
        kconfig = self.tree / "Kconfig"
        kconfig.write_text(kconfig.read_text().replace("SCHED_CACHE", "OTHER"))
        tests = self.tree / "scripts/kconfig/tests/example"
        tests.mkdir(parents=True)
        (tests / "Kconfig").write_text('config SCHED_CACHE\n\tbool "Test"\n')
        result = self.run_script("--strict", "--sched-cache=on")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not defined", result.stderr)

    def test_damon_sanity_pruning(self):
        result = self.run_script("--prune-debug-trace")
        self.assert_success(result)
        self.assertIn("# CONFIG_DAMON_DEBUG_SANITY is not set", self.config.read_text())

    def test_compression_preset_preserves_boot_decoders(self):
        result = self.run_script("--all-optimizations")
        self.assert_success(result)
        after = self.config.read_text()
        for sym in ["RD_GZIP", "RD_XZ", "FW_LOADER_COMPRESS", "FW_LOADER_COMPRESS_XZ",
                    "FW_LOADER_COMPRESS_ZSTD", "KERNEL_ZSTD"]:
            self.assertIn(f"CONFIG_{sym}=y", after)

    def test_explicit_initrd_decoder_is_additive(self):
        self.assert_success(self.run_script("--initrd-compression=zstd"))
        for sym in ["RD_GZIP", "RD_XZ", "RD_ZSTD"]:
            self.assertIn(f"CONFIG_{sym}=y", self.config.read_text())

    def test_conflicting_initrd_options(self):
        result = self.run_script("--initrd-compression=zstd", "--initrd-support=off")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()

    def test_explicit_firmware_compression(self):
        self.assert_success(self.run_script("--firmware-compression=off"))
        self.assertIn("# CONFIG_FW_LOADER_COMPRESS is not set", self.config.read_text())

    def test_environment_and_cli_precedence(self):
        result = self.run_script("--sched-cache=off", env={"SCHED_CACHE": "on"})
        self.assert_success(result)
        self.assertIn("# CONFIG_SCHED_CACHE is not set", self.config.read_text())

    def test_permissions_and_backup_are_preserved(self):
        self.config.chmod(0o640)
        self.assert_success(self.run_script("--sched-cache=on"))
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)
        backups = list(self.tree.glob(".config.bak.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.baseline)

    def test_backups_are_unique_and_repeated_run_is_idempotent(self):
        self.assert_success(self.run_script("--sched-cache=on"))
        self.assert_success(self.run_script("--sched-cache=off"))
        backups = list(self.tree.glob(".config.bak.*"))
        self.assertEqual(len(backups), 2)
        before = self.config.read_bytes()
        self.assert_success(self.run_script("--sched-cache=off"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(len(list(self.tree.glob(".config.bak.*"))), 2)

    def test_concurrent_edit_is_preserved(self):
        result = self.run_script("--sched-cache=on", env={"CHANGE_ORIGINAL": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original config changed", result.stderr)
        self.assertEqual(self.config.read_text(), "CONFIG_EDITED_EXTERNALLY=y\n")
        self.assertFalse(list(self.tree.glob(".config.bak.*")))

    def test_symlink_target_is_updated_without_replacing_link(self):
        target = self.tree / "target.config"
        self.config.rename(target)
        self.config.symlink_to(target.name)
        self.assert_success(self.run_script("--sched-cache=on"))
        self.assertTrue(self.config.is_symlink())
        self.assertIn("CONFIG_SCHED_CACHE=y", target.read_text())


    def test_kernel_compression_choice_respects_protected_sibling(self):
        result = self.run_script("--strict", "--kernel-compression=zstd", "--protected-config-symbols=KERNEL_GZIP")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with protected CONFIG_KERNEL_GZIP", result.stderr)
        self.assert_untouched()

    def test_uclamp_autogroup_override_desktop_and_keep(self):
        self.add_symbols("UCLAMP_TASK", "SCHED_AUTOGROUP", "CPU_FREQ_GOV_SCHEDUTIL")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop", "--uclamp=off", "--autogroup=off"))
        self.assert_symbol("UCLAMP_TASK", "n")
        self.assert_symbol("SCHED_AUTOGROUP", "n")
        self.assert_success(self.run_script("--strict", "--uclamp=on", "--autogroup=on"))
        self.assert_symbol("UCLAMP_TASK", "y")
        self.assert_symbol("CPU_FREQ_GOV_SCHEDUTIL", "y")
        self.assert_symbol("SCHED_AUTOGROUP", "y")
        self.assert_success(self.run_script("--strict", "--uclamp=keep", "--autogroup=keep"))
        self.assert_symbol("UCLAMP_TASK", "y")
        self.assert_symbol("SCHED_AUTOGROUP", "y")

    def test_desktop_defaults_to_thp_madvise(self):
        self.add_symbols("TRANSPARENT_HUGEPAGE", "TRANSPARENT_HUGEPAGE_ALWAYS",
                         "TRANSPARENT_HUGEPAGE_MADVISE", "TRANSPARENT_HUGEPAGE_NEVER")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("TRANSPARENT_HUGEPAGE_MADVISE", "y")
        self.assert_symbol("TRANSPARENT_HUGEPAGE_ALWAYS", "n")

    def test_desktop_numa_balancing_requires_numa_and_migration(self):
        self.add_symbols("NUMA", "NUMA_BALANCING", "NUMA_BALANCING_DEFAULT_ENABLED", "MIGRATION", "NUMA_MIGRATION")
        self.set_baseline_symbols(NUMA="n", NUMA_BALANCING="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("NUMA", "n")
        self.assert_symbol("NUMA_BALANCING", "n")
        self.set_baseline_symbols(NUMA="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("NUMA_BALANCING", "y")
        self.assert_symbol("NUMA_MIGRATION", "y")

    def test_initramfs_check_rejects_missing_decoder_without_make(self):
        image = self.root / "image with spaces.img"
        image.write_bytes(lzma.compress(cpio(), check=lzma.CHECK_CRC32))
        self.set_baseline_symbols(RD_XZ="n")
        result = self.run_script("--check", "--strict", "--initramfs-image", image.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_RD_XZ must be built in", result.stdout)
        self.assert_untouched()
        self.assertFalse((self.root / "make.log").exists())

    def test_initramfs_auto_dry_run_and_commit_preserve_other_decoders(self):
        image = self.root / "initramfs"
        image.write_bytes(cpio(b"early microcode") + lzma.compress(cpio(), check=lzma.CHECK_CRC32))
        self.set_baseline_symbols(RD_XZ="n")
        args = ("--strict", "--initramfs-image", str(image), "--initrd-compression=auto")
        self.assert_success(self.run_script("--dry-run", *args))
        self.assert_untouched()
        self.assert_success(self.run_script(*args))
        self.assert_symbol("RD_XZ", "y")
        self.assert_symbol("RD_GZIP", "y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_final_initramfs_validation_catches_explicit_decoder_removal(self):
        image = self.root / "initramfs"
        image.write_bytes(lzma.compress(cpio(), check=lzma.CHECK_CRC32))
        result = self.run_script("--strict", "--initramfs-image", str(image), "--disable-symbols=RD_XZ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Strict initramfs validation failed", result.stderr)
        self.assert_untouched()

    def test_unknown_image_strict_rejects_nonstrict_warns(self):
        image = self.root / "initramfs"
        image.write_bytes(b"unknown format")
        result = self.run_script("--strict", "--initramfs-image", str(image), "--sched-cache=on")
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()
        result = self.run_script("--initramfs-image", str(image), "--sched-cache=on")
        self.assert_success(result)
        self.assertIn("compatibility is not established", result.stderr)

    def test_new_option_errors_do_not_change_config(self):
        for args in [("--uclamp=bad",), ("--autogroup=bad",), ("--initramfs-generator=bad",),
                     ("--initramfs-compression=bad",), ("--initrd-compression=auto",),
                     ("--initrd-compression=none", "--initrd-support=off")]:
            result = self.run_script(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assert_untouched()

    def test_validation_mode_strict_rolls_back_new_control_requests(self):
        result = self.run_script("--validation-mode=strict", "--sched-cache=on",
                                 env={"DROP_SYMBOLS": "SCHED_CACHE"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_SCHED_CACHE requested=y final=n", result.stderr)
        self.assert_untouched()

    def test_check_validates_legacy_controls_without_running_make(self):
        result = self.run_script("--check", "--timer-hz=500")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid TIMER_HZ", result.stderr)
        self.assertFalse((self.root / "make.log").exists())
        self.assert_untouched()

    def test_new_explicit_controls_override_legacy_control_requests(self):
        self.add_symbols("LRU_GEN", "LRU_GEN_ENABLED", "LRU_GEN_STATS")
        self.set_baseline_symbols(LRU_GEN="y", LRU_GEN_ENABLED="y", LRU_GEN_STATS="n")
        self.assert_success(self.run_script("--validation-mode=strict", "--sched-cache-mode=off",
                                            "--sched-cache=on", "--mglru-mode=on", "--lru-gen=off"))
        self.assert_symbol("SCHED_CACHE", "y")
        self.assert_symbol("LRU_GEN", "n")

    def test_profile_modules_are_preserved_until_explicit_builtin_request(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('\nconfig DRM_AMDGPU\n tristate "AMD GPU"\n')
        self.set_baseline_symbols(DRM_AMDGPU="m")
        self.assert_success(self.run_script("--strict", "--video-support=amd"))
        self.assert_symbol("DRM_AMDGPU", "m")
        self.assert_success(self.run_script("--strict", "--video-support=amd", "--enable-symbols=DRM_AMDGPU"))
        self.assert_symbol("DRM_AMDGPU", "y")

    def test_legacy_pruning_preserves_framebuffer_console(self):
        with (self.tree / "Kconfig").open("a") as stream:
            stream.write('\nconfig DRM_FBDEV_EMULATION\n bool "Legacy fbdev support"\n')
        self.set_baseline_symbols(DRM_FBDEV_EMULATION="y")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("DRM_FBDEV_EMULATION", "y")

    def test_modern_preemption_skips_unavailable_legacy_request(self):
        self.add_symbols("PREEMPT")
        self.assert_success(self.run_script("--strict", "--preempt-mode=rt", "--preemption=full"))
        self.assert_symbol("PREEMPT", "y")

    def test_modern_numa_override_skips_legacy_dependency_requests(self):
        self.add_symbols("NUMA_BALANCING", "NUMA_BALANCING_DEFAULT_ENABLED", "NUMA_MIGRATION")
        result = self.run_script("--strict", "--numa-balancing-mode=on", "--numa-balancing=off",
                                 env={"DROP_SYMBOLS": "NUMA_MIGRATION NUMA_BALANCING_DEFAULT_ENABLED"})
        self.assert_success(result)
        self.assert_symbol("NUMA_BALANCING", "n")

    def test_legacy_rt_and_numa_conflicts_are_checked_before_make(self):
        cases = [("--preempt-mode=rt", "--thp=madvise"),
                 ("--preempt-mode=rt", "--numa-balancing=on"),
                 ("--preempt-mode=rt", "--numa-balancing-mode=on"),
                 ("--numa-support=off", "--numa-balancing-mode=on")]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_script("--check", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("conflicts", result.stderr)
                self.assertFalse((self.root / "make.log").exists())
                self.assert_untouched()

    def test_profile_thp_dependents_are_superseded_by_off_or_rt(self):
        self.add_symbols("PREEMPT", "PREEMPT_RT", "TRANSPARENT_HUGEPAGE",
                         "TRANSPARENT_HUGEPAGE_MADVISE", "PERSISTENT_HUGE_ZERO_FOLIO")
        for option in ("--thp=off", "--preemption=rt", "--preempt-mode=rt"):
            with self.subTest(option=option):
                self.config.write_text(self.baseline)
                result = self.run_script("--strict", "--optimization-profile=server", option,
                                         env={"DROP_SYMBOLS": "PERSISTENT_HUGE_ZERO_FOLIO TRANSPARENT_HUGEPAGE_MADVISE"})
                self.assert_success(result)
                self.assert_symbol("TRANSPARENT_HUGEPAGE", "n")

    def test_tick_override_supersedes_profile_rcu_offload_requests(self):
        self.add_symbols("NO_HZ_FULL", "NO_HZ_IDLE", "HZ_PERIODIC", "RCU_NOCB_CPU",
                         "RCU_NOCB_CPU_DEFAULT_ALL", "RCU_NOCB_CPU_CB_BOOST")
        self.set_baseline_symbols(HAVE_CONTEXT_TRACKING_USER="y", HAVE_VIRT_CPU_ACCOUNTING_GEN="y",
                                  RCU_EXPERT="n")
        dropped = {"DROP_SYMBOLS": "RCU_NOCB_CPU RCU_NOCB_CPU_DEFAULT_ALL RCU_NOCB_CPU_CB_BOOST"}
        for mode in ("idle", "periodic"):
            with self.subTest(mode=mode):
                self.config.write_text(self.baseline)
                self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                                    f"--tick-mode={mode}", env=dropped))
        self.config.write_text(self.baseline)
        result = self.run_script("--strict", "--optimization-profile=server", "--tick-mode=idle",
                                 "--enable-symbols=RCU_NOCB_CPU", env=dropped)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_RCU_NOCB_CPU requested=y final=n", result.stderr)
        self.assertEqual(self.config.read_text(), self.baseline)


class ModuleRestorationTests(unittest.TestCase):
    def test_failed_module_listing_does_not_unload_anything(self):
        source = SCRIPT.read_text()
        functions = "\n".join(re.search(r"^" + name + r"\(\) \{\n.*?^\}", source,
                                        re.M | re.S)[0] for name in
                              ["read_loaded_modules", "restore_loaded_modules_to_initial_state"])
        shell = functions + '''
capture_loaded_modules() { return 1; }
modprobe() { echo UNEXPECTED_PROBE; }
baseline=(initial_module)
restore_loaded_modules_to_initial_state baseline
'''
        result = subprocess.run(["bash", "-c", shell], text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("UNEXPECTED_PROBE", result.stdout)

    def test_failed_load_still_restores_dependencies(self):
        function = re.search(r"^probe_unloaded_module_candidate\(\) \{\n.*?^\}",
                             SCRIPT.read_text(), re.M | re.S)[0]
        for restore_status, expected in [(0, 0), (1, 2)]:
            with self.subTest(restore_status=restore_status):
                shell = function + f'''
modprobe() {{ return 1; }}
restore_loaded_modules_to_initial_state() {{ echo RESTORED; return {restore_status}; }}
probe_unloaded_module_candidate fake_module baseline
'''
                result = subprocess.run(["bash", "-c", shell], text=True, capture_output=True)
                self.assertEqual(result.returncode, expected)
                self.assertIn("RESTORED", result.stdout)


if __name__ == "__main__":
    unittest.main()
