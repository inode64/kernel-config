#!/usr/bin/env python3
"""End-to-end regressions with isolated config/make tools; never probe real modules."""

import os
import lzma
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import textwrap
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
for sym in os.environ.get("REMOVE_SYMBOLS", "").split():
    # Like DROP_SYMBOLS, but the symbol vanishes entirely (its prompt disappeared).
    lines = [line for line in p.read_text().splitlines()
             if not line.startswith("CONFIG_" + sym + "=")
             and line != "# CONFIG_" + sym + " is not set"]
    p.write_text("\n".join(lines) + "\n")
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
        for name in ("ALL_OPTIMIZATIONS", "KSRCDIR", "CONFIG_FILE", "KBUILD_OUTPUT", "KC_HOST_ROOT"):
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

    def add_symbols(self, *symbols, kind=None):
        with (self.tree / "Kconfig").open("a") as stream:
            for sym in symbols:
                sym_kind = kind or ("tristate" if sym == "ZRAM" else "bool")
                stream.write(f'\nconfig {sym}\n\t{sym_kind} "{sym}"\n')

    def add_kconfig(self, text, path="Kconfig"):
        target = self.tree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as stream:
            stream.write("\n" + textwrap.dedent(text))

    def make_host_root(self, *, cpuinfo=None, cmdline=None, sys_vendor=None, acpi=(), dmi_entries=(),
                       pci=(), iommu=(), iommu_caps=(), acpi_tables=(), efi=False, dma=(), nodes=()):
        """Build a synthetic /sys and /proc for the auto detectors (KC_HOST_ROOT)."""
        host = self.root / "host"
        host.mkdir(exist_ok=True)
        if cpuinfo is not None:
            (host / "proc").mkdir(exist_ok=True)
            (host / "proc/cpuinfo").write_text(cpuinfo)
        if cmdline is not None:
            (host / "proc").mkdir(exist_ok=True)
            (host / "proc/cmdline").write_text(cmdline)
        if sys_vendor is not None:
            (host / "sys/class/dmi/id").mkdir(parents=True, exist_ok=True)
            (host / "sys/class/dmi/id/sys_vendor").write_text(sys_vendor + "\n")
        for hid in acpi:
            (host / "sys/bus/acpi/devices" / hid).mkdir(parents=True, exist_ok=True)
        for entry in dmi_entries:
            (host / "sys/firmware/dmi/entries" / entry).mkdir(parents=True, exist_ok=True)
        for bdf, vendor, device, cls in pci:
            dev = host / "sys/bus/pci/devices" / bdf
            dev.mkdir(parents=True, exist_ok=True)
            for name, value in (("vendor", vendor), ("device", device), ("class", cls)):
                (dev / name).write_text(value + "\n")
        for unit in iommu:
            (host / "sys/class/iommu" / unit).mkdir(parents=True, exist_ok=True)
        for unit, cap, ecap in iommu_caps:
            directory = host / "sys/class/iommu" / unit / "intel-iommu"
            directory.mkdir(parents=True, exist_ok=True)
            for name, value in (("cap", cap), ("ecap", ecap)):
                if value is not None:
                    (directory / name).write_text(value + "\n")
        for table in acpi_tables:
            (host / "sys/firmware/acpi/tables").mkdir(parents=True, exist_ok=True)
            (host / "sys/firmware/acpi/tables" / table).write_bytes(b"")
        if efi:
            (host / "sys/firmware/efi").mkdir(parents=True, exist_ok=True)
        for channel in dma:
            (host / "sys/class/dma" / channel).mkdir(parents=True, exist_ok=True)
        for node in nodes:
            (host / "sys/devices/system/node" / node).mkdir(parents=True, exist_ok=True)
        return {"KC_HOST_ROOT": str(host)}

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
                   "kmalloc-partition", "tcp-congestion", "default-qdisc", "io-uring"]
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
        # HAVE_* are architecture capabilities. Only realtime treats them as a
        # reason to select full dynticks; server stays on idle ticks.
        for profile, model, tick in [("server", "PREEMPT_NONE", "NO_HZ_IDLE"),
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

    def test_server_profile_skips_preemption_models_with_unmet_dependencies(self):
        # Linux 7.4 gates PREEMPT_NONE on ARCH_NO_PREEMPT and drops voluntary
        # preemption where the lazy model exists. A migrated config still lists
        # the old symbols as "is not set".
        self.add_kconfig('''
            config ARCH_NO_PREEMPT
            \tbool

            config ARCH_HAS_PREEMPT_LAZY
            \tbool

            config PREEMPT_NONE
            \tbool "No Forced Preemption (Server)"
            \tdepends on !PREEMPT_RT
            \tdepends on ARCH_NO_PREEMPT

            config PREEMPT_VOLUNTARY
            \tbool "Voluntary Kernel Preemption (Desktop)"
            \tdepends on !ARCH_HAS_PREEMPT_LAZY
            \tdepends on !ARCH_NO_PREEMPT

            config PREEMPT_LAZY
            \tbool "Scheduler controlled preemption model"
            \tdepends on !ARCH_NO_PREEMPT
            \tdepends on ARCH_HAS_PREEMPT_LAZY

            config PREEMPT
            \tbool "Preemptible Kernel (Low-Latency Desktop)"
            \tdepends on !ARCH_NO_PREEMPT

            config PREEMPT_RT
            \tbool "Fully Preemptible Kernel (Real-Time)"
            \tdepends on EXPERT
        ''')
        self.add_symbols("PREEMPT_DYNAMIC")
        self.set_baseline_symbols(
            ARCH_HAS_PREEMPT_LAZY="y", PREEMPT="y", PREEMPT_NONE="n",
            PREEMPT_VOLUNTARY="n", PREEMPT_LAZY="n", PREEMPT_DYNAMIC="y", EXPERT="n",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("PREEMPT_LAZY", "y")
        self.assert_symbol("PREEMPT_NONE", "n")
        self.assert_symbol("PREEMPT_VOLUNTARY", "n")
        self.assert_symbol("PREEMPT", "n")
        self.assert_symbol("PREEMPT_DYNAMIC", "n")

        # An architecture that cannot preempt still gets the throughput model.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(ARCH_NO_PREEMPT="y", ARCH_HAS_PREEMPT_LAZY="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("PREEMPT_NONE", "y")
        self.assert_symbol("PREEMPT_LAZY", "n")

    def test_server_profile_uses_idle_ticks_and_drops_rcu_offload_default(self):
        # A 64-bit SMP baseline advertises full-dynticks capabilities and may
        # still offload every CPU. The server profile must not keep that.
        self.add_symbols(
            "NO_HZ_FULL", "NO_HZ_IDLE", "HZ_PERIODIC",
            "RCU_NOCB_CPU", "RCU_NOCB_CPU_DEFAULT_ALL",
            "TICK_CPU_ACCOUNTING", "VIRT_CPU_ACCOUNTING_GEN", "VIRT_CPU_ACCOUNTING_NATIVE",
        )
        self.set_baseline_symbols(
            HAVE_CONTEXT_TRACKING_USER="y", HAVE_VIRT_CPU_ACCOUNTING_GEN="y",
            RCU_EXPERT="n", RCU_NOCB_CPU="y", RCU_NOCB_CPU_DEFAULT_ALL="y",
            NO_HZ_FULL="y", NO_HZ_IDLE="n", HZ_PERIODIC="n",
            TICK_CPU_ACCOUNTING="n", VIRT_CPU_ACCOUNTING_GEN="y",
            VIRT_CPU_ACCOUNTING_NATIVE="n",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("NO_HZ_IDLE", "y")
        self.assert_symbol("NO_HZ_FULL", "n")
        self.assert_symbol("HZ_PERIODIC", "n")
        self.assert_symbol("RCU_NOCB_CPU_DEFAULT_ALL", "n")
        self.assert_symbol("TICK_CPU_ACCOUNTING", "y")
        self.assert_symbol("VIRT_CPU_ACCOUNTING_GEN", "n")
        self.assert_symbol("VIRT_CPU_ACCOUNTING_NATIVE", "n")

    def test_profiles_replace_frame_pointer_unwinder_with_orc(self):
        self.add_kconfig('''
            config X86_64
            \tbool

            config UNWINDER_ORC
            \tbool "ORC unwinder"
            \tdepends on X86_64

            config UNWINDER_FRAME_POINTER
            \tbool "Frame pointer unwinder"

            config UNWINDER_GUESS
            \tbool "Guess unwinder"
            \tdepends on EXPERT
        ''')
        self.set_baseline_symbols(
            X86_64="y", UNWINDER_FRAME_POINTER="y", UNWINDER_ORC="n", UNWINDER_GUESS="n",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("UNWINDER_ORC", "y")
        self.assert_symbol("UNWINDER_FRAME_POINTER", "n")
        self.assert_symbol("UNWINDER_GUESS", "n")

        # x86_32 and any target where ORC's dependency is unmet stay put.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(X86_64="n", UNWINDER_FRAME_POINTER="y", UNWINDER_ORC="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("UNWINDER_FRAME_POINTER", "y")
        self.assert_symbol("UNWINDER_ORC", "n")

        # The guess unwinder is the EXPERT choice with no runtime overhead.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(
            X86_64="y", UNWINDER_GUESS="y", UNWINDER_FRAME_POINTER="n", UNWINDER_ORC="n", EXPERT="y",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("UNWINDER_GUESS", "y")
        self.assert_symbol("UNWINDER_ORC", "n")
        self.assert_symbol("UNWINDER_FRAME_POINTER", "n")

        # A protected frame-pointer unwinder, or an explicit enable, wins.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(
            X86_64="y", UNWINDER_FRAME_POINTER="y", UNWINDER_ORC="n", UNWINDER_GUESS="n",
        )
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=server",
            "--protected-config-symbols=UNWINDER_FRAME_POINTER",
        ))
        self.assert_symbol("UNWINDER_FRAME_POINTER", "y")
        self.assert_symbol("UNWINDER_ORC", "n")
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=server",
            "--enable-symbols=UNWINDER_FRAME_POINTER",
        ))
        self.assert_symbol("UNWINDER_FRAME_POINTER", "y")
        self.assert_symbol("UNWINDER_ORC", "n")

    def test_profiles_enable_fast_crc(self):
        self.add_kconfig('''
            config CRC_OPTIMIZATIONS
            \tbool "Enable optimized CRC implementations" if EXPERT
            \tdepends on !UML

            config CRC32
            \ttristate "CRC32/CRC32c functions"

            config CRC32_SLICEBY8
            \tbool "Slice by 8 bytes"

            config CRC32_SLICEBY4
            \tbool "Slice by 4 bytes"

            config CRC32_SARWATE
            \tbool "Sarwate's Algorithm"

            config CRC32_BIT
            \tbool "Classic Algorithm"
        ''')
        self.set_baseline_symbols(
            CRC_OPTIMIZATIONS="n", CRC32="y", UML="n",
            CRC32_SARWATE="y", CRC32_SLICEBY8="n", CRC32_SLICEBY4="n", CRC32_BIT="n",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("CRC_OPTIMIZATIONS", "y")
        self.assert_symbol("CRC32_SLICEBY8", "y")
        self.assert_symbol("CRC32_SARWATE", "n")
        self.assert_symbol("CRC32_SLICEBY4", "n")
        self.assert_symbol("CRC32_BIT", "n")

        # The 6.12 choice depends on CRC32. Leave a slower member when it is off.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(CRC32="n", CRC32_SARWATE="y", CRC32_SLICEBY8="n", CRC_OPTIMIZATIONS="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("CRC_OPTIMIZATIONS", "y")
        self.assert_symbol("CRC32", "n")
        self.assert_symbol("CRC32_SARWATE", "y")
        self.assert_symbol("CRC32_SLICEBY8", "n")

        # An explicit choice wins, and UML cannot select CRC_OPTIMIZATIONS.
        self.config.write_text(self.baseline)
        self.set_baseline_symbols(
            CRC32="y", CRC32_SARWATE="y", CRC32_SLICEBY8="n", CRC_OPTIMIZATIONS="n", UML="y",
        )
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=server", "--enable-symbols=CRC32_SARWATE",
        ))
        self.assert_symbol("CRC32_SARWATE", "y")
        self.assert_symbol("CRC32_SLICEBY8", "n")
        self.assert_symbol("CRC_OPTIMIZATIONS", "n")

    def test_crc_tuning_skips_absent_symbols(self):
        result = self.run_script("--strict", "--optimization-profile=realtime")
        self.assert_success(result)
        self.assertNotIn("CRC_OPTIMIZATIONS", result.stdout)
        self.assertNotIn("CRC32_SLICEBY8", result.stdout)

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
                    "kmalloc-partition", "tcp-congestion", "default-qdisc", "io-uring"]
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

    def test_compression_preset_enables_swap_without_an_optimization_profile(self):
        self.add_symbols("SWAP")
        self.set_baseline_symbols(SWAP="n")
        self.assert_success(self.run_script("--strict", "--all-optimizations",
                                            "--optimization-profile=none"))
        self.assert_symbol("SWAP", "y")
        self.assert_symbol("ZSWAP", "y")
        self.assert_symbol("ZSWAP_DEFAULT_ON", "y")

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

    def test_default_qdisc_control_and_dependencies(self):
        qdiscs = ["pfifo_fast", "fq", "fq_codel", "fq_pie", "codel", "sfq"]
        self.add_symbols(
            "NET", "NET_SCHED", "NET_SCH_DEFAULT", "NET_SCH_PIE",
            *(f"NET_SCH_{q.upper()}" for q in qdiscs if q != "pfifo_fast"),
            *(f"DEFAULT_{q.upper()}" for q in qdiscs),
        )
        for qdisc in qdiscs:
            with self.subTest(qdisc=qdisc):
                self.config.write_text(self.baseline)
                self.assert_success(self.run_script("--strict", f"--default-qdisc={qdisc}"))
                self.assert_symbol("NET", "y")
                self.assert_symbol("NET_SCHED", "y")
                self.assert_symbol("NET_SCH_DEFAULT", "y")
                for other in qdiscs:
                    self.assert_symbol(f"DEFAULT_{other.upper()}", "y" if other == qdisc else "n")
                if qdisc != "pfifo_fast":
                    self.assert_symbol(f"NET_SCH_{qdisc.upper()}", "y")
                if qdisc == "fq_pie":
                    self.assert_symbol("NET_SCH_PIE", "y")
        # Protected sibling choice blocks switching under --strict.
        self.set_baseline_symbols(DEFAULT_PFIFO_FAST="y")
        before = self.config.read_bytes()
        backups = sorted(self.tree.glob(".config.bak.*"))
        result = self.run_script(
            "--strict", "--default-qdisc=fq_codel",
            "--protected-config-symbols=DEFAULT_PFIFO_FAST",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with protected CONFIG_DEFAULT_PFIFO_FAST", result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(sorted(self.tree.glob(".config.bak.*")), backups)

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

    def test_pattern_discovery_ignores_foreign_architecture_prompts(self):
        self.add_kconfig('''
            config EARLY_PRINTK
            \tbool "Early printk" if EXPERT
        ''', path="arch/x86/Kconfig.debug")
        self.add_kconfig('''
            config EARLY_PRINTK
            \tbool "Activate early kernel debugging"
        ''', path="arch/nios2/Kconfig.debug")
        self.add_kconfig('''
            config CPU_BIG_ENDIAN
            \tbool "Build big-endian kernel (DEPRECATED)"
        ''', path="arch/arm/mm/Kconfig")
        self.add_kconfig('''
            config CPU_BIG_ENDIAN
            \tbool "Build big endian kernel"
        ''', path="arch/powerpc/Kconfig")
        self.set_baseline_symbols(EARLY_PRINTK="y", EXPERT="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace", env={"ARCH": "x86"}))
        self.assert_symbol("EARLY_PRINTK", "y")
        self.set_baseline_symbols(CPU_BIG_ENDIAN="y")
        self.assert_success(self.run_script("--strict", "--prune-legacy", env={"ARCH": "powerpc"}))
        self.assert_symbol("CPU_BIG_ENDIAN", "y")

    def test_hardening_pruning_selects_none_choices_and_disables_runtime_toggles(self):
        self.add_kconfig('''
            menu "Kernel hardening options"
            config INIT_STACK_NONE
            \tbool "no automatic stack variable initialization (weakest)"
            config INIT_STACK_ALL_ZERO
            \tbool "zero-init everything (strongest and safest)"
            config INIT_STACK_ALL_PATTERN
            \tbool "pattern-init everything (strongest)"
            config RANDSTRUCT_NONE
            \tbool "Disable structure layout randomization"
            config RANDSTRUCT_FULL
            \tbool "Fully randomize structure layout"
            config RANDSTRUCT_PERFORMANCE
            \tbool "Limit randomization of structure layout to cache-lines"
            config KSTACK_ERASE
            \tbool "Poison kernel stack before returning from syscalls"
            config KSTACK_ERASE_RUNTIME_DISABLE
            \tbool "Allow runtime disabling of kernel stack erasing"
            endmenu
        ''')
        self.set_baseline_symbols(
            INIT_STACK_NONE="n", INIT_STACK_ALL_ZERO="y", INIT_STACK_ALL_PATTERN="n",
            RANDSTRUCT_NONE="n", RANDSTRUCT_FULL="y", RANDSTRUCT_PERFORMANCE="n",
            KSTACK_ERASE="y", KSTACK_ERASE_RUNTIME_DISABLE="n",
        )
        self.assert_success(self.run_script("--strict", "--prune-hardening"))
        self.assert_symbol("INIT_STACK_NONE", "y")
        self.assert_symbol("INIT_STACK_ALL_ZERO", "n")
        self.assert_symbol("RANDSTRUCT_NONE", "y")
        self.assert_symbol("RANDSTRUCT_FULL", "n")
        self.assert_symbol("KSTACK_ERASE", "n")
        self.assert_symbol("KSTACK_ERASE_RUNTIME_DISABLE", "n")
        # A protected hardening choice member prevents selecting the NONE sibling.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script(
            "--strict", "--prune-hardening", "--protected-config-symbols=INIT_STACK_ALL_ZERO",
        ))
        self.assert_symbol("INIT_STACK_ALL_ZERO", "y")
        self.assert_symbol("INIT_STACK_NONE", "n")
        self.assert_symbol("RANDSTRUCT_NONE", "y")

    def test_inverse_disable_excludes_legacy_spi_nor_swp_and_keeps_crypto_tests(self):
        self.add_kconfig('''
            config MTD_SPI_NOR_SWP_DISABLE
            \tbool "Disable SWP on any flashes (legacy behavior)"
            config CRYPTO_MANAGER_DISABLE_TESTS
            \tbool "Disable run-time self tests"
        ''')
        self.set_baseline_symbols(MTD_SPI_NOR_SWP_DISABLE="y", CRYPTO_MANAGER_DISABLE_TESTS="n")
        self.assert_success(self.run_script("--strict", "--prune-legacy", "--prune-selftest"))
        self.assert_symbol("MTD_SPI_NOR_SWP_DISABLE", "n")
        self.assert_symbol("CRYPTO_MANAGER_DISABLE_TESTS", "y")

    def test_debug_coverage_observability_pruning_respects_choice_parents(self):
        self.add_kconfig('''
            menu "Compile-time checks and compiler options"
            config DEBUG_INFO_NONE
            \tbool "Disable debug information"
            config DEBUG_INFO_DWARF5
            \tbool "Generate DWARF Version 5 debuginfo"
            endmenu
            config FTRACE
            \tbool "Tracers"
            config BRANCH_PROFILE_NONE
            \tbool "No branch profiling"
            config PROFILE_ALL_BRANCHES
            \tbool "Profile all if conditionals"
        ''')
        # When DEBUG_KERNEL and FTRACE are off, neither NONE choice should be requested.
        self.set_baseline_symbols(
            DEBUG_KERNEL="n", DEBUG_INFO_NONE="n", DEBUG_INFO_DWARF5="n",
            FTRACE="n", BRANCH_PROFILE_NONE="n", PROFILE_ALL_BRANCHES="n",
        )
        self.assert_success(self.run_script("--strict", "--prune-coverage",
                                            env={"REMOVE_SYMBOLS": "DEBUG_INFO_NONE BRANCH_PROFILE_NONE"}))
        self.assert_success(self.run_script("--strict", "--prune-observability",
                                            env={"REMOVE_SYMBOLS": "BRANCH_PROFILE_NONE"}))
        # When DEBUG_KERNEL stays on (protected), PRUNE_DEBUG_TRACE selects DEBUG_INFO_NONE.
        self.set_baseline_symbols(
            DEBUG_KERNEL="y", DEBUG_INFO_NONE="n", DEBUG_INFO_DWARF5="y",
            FTRACE="y", BRANCH_PROFILE_NONE="n", PROFILE_ALL_BRANCHES="y",
        )
        self.assert_success(self.run_script(
            "--strict", "--prune-debug-trace", "--protected-config-symbols=DEBUG_KERNEL",
        ))
        self.assert_symbol("DEBUG_KERNEL", "y")
        self.assert_symbol("DEBUG_INFO_NONE", "y")
        self.assert_symbol("DEBUG_INFO_DWARF5", "n")
        # When FTRACE stays on (protected), PRUNE_OBSERVABILITY selects BRANCH_PROFILE_NONE.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script(
            "--strict", "--prune-observability", "--protected-config-symbols=FTRACE",
        ))
        self.assert_symbol("FTRACE", "y")
        self.assert_symbol("BRANCH_PROFILE_NONE", "y")
        self.assert_symbol("PROFILE_ALL_BRANCHES", "n")

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

    def test_desktop_apps_preserve_modular_drivers_and_explicit_overrides(self):
        self.add_symbols("DRM", "DRM_AMDGPU", "HSA_AMD", "HSA_AMD_SVM",
                         "FUSE_FS", "SND_USB_AUDIO", "USB_VIDEO_CLASS", "HIDRAW",
                         "INOTIFY_USER", "USER_NS", "SECCOMP", "TUN", "WIREGUARD")
        drivers = dict.fromkeys(("DRM_AMDGPU", "FUSE_FS", "SND_USB_AUDIO", "USB_VIDEO_CLASS"), "m")
        self.set_baseline_symbols(**drivers, WIREGUARD="n", HIDRAW="n")
        args = ("--strict", "--video-support=amd",
                "--applications=desktop,multimedia,rocm,nebula,warp")
        self.assert_success(self.run_script(*args))
        for symbol in drivers:
            self.assert_symbol(symbol, "m")
        for symbol in ("HSA_AMD", "HSA_AMD_SVM", "HIDRAW", "INOTIFY_USER", "USER_NS", "SECCOMP", "TUN"):
            self.assert_symbol(symbol, "y")
        self.assert_symbol("WIREGUARD", "n")
        self.assert_success(self.run_script(*args, "--disable-symbols=HIDRAW"))
        self.assert_symbol("HIDRAW", "n")

    def test_warp_profile_enables_policy_routing_and_nftables(self):
        self.add_symbols("TUN", "IP_MULTIPLE_TABLES", "IPV6_MULTIPLE_TABLES",
                         "NF_TABLES", "NF_TABLES_INET", "NFT_REJECT_INET")
        self.set_baseline_symbols(TUN="n", IP_MULTIPLE_TABLES="n", IPV6_MULTIPLE_TABLES="n",
                                  NF_TABLES="n", NF_TABLES_INET="n", NFT_REJECT_INET="n")
        self.assert_success(self.run_script("--strict", "--applications=nebula"))
        self.assert_symbol("TUN", "y")
        self.assert_symbol("IPV6_MULTIPLE_TABLES", "n")
        self.assert_success(self.run_script("--strict", "--applications=warp"))
        for symbol in ("TUN", "IP_MULTIPLE_TABLES", "IPV6_MULTIPLE_TABLES",
                       "NF_TABLES", "NF_TABLES_INET", "NFT_REJECT_INET"):
            self.assert_symbol(symbol, "y")

    def test_python_profile_enables_remote_debugging_syscalls(self):
        self.add_symbols("CROSS_MEMORY_ATTACH", "DNOTIFY")
        self.set_baseline_symbols(CROSS_MEMORY_ATTACH="n", DNOTIFY="n")
        self.assert_success(self.run_script("--strict", "--applications=python"))
        self.assert_symbol("CROSS_MEMORY_ATTACH", "y")
        self.assert_symbol("DNOTIFY", "y")
        self.assert_success(self.run_script("--strict", "--applications=python",
                                            "--disable-symbols=DNOTIFY"))
        self.assert_symbol("DNOTIFY", "n")

    def write_tree_version(self, version, patchlevel):
        (self.tree / "Makefile").write_text(
            f"VERSION = {version}\nPATCHLEVEL = {patchlevel}\nSUBLEVEL = 0\n")

    def test_hvo_default_is_disabled_before_7_2(self):
        sym = "HUGETLB_PAGE_OPTIMIZE_VMEMMAP_DEFAULT_ON"
        self.add_symbols(sym)
        self.set_baseline_symbols(**{sym: "y"})
        # The fixture Makefile has no version, which counts as affected.
        for version, expected in ((None, "n"), ((6, 18), "n"), ((7, 1), "n"),
                                  ((7, 2), "y"), ((8, 0), "y")):
            with self.subTest(version=version):
                self.config.write_text(self.baseline)
                if version:
                    self.write_tree_version(*version)
                result = self.run_script("--strict")
                self.assert_success(result)
                self.assert_symbol(sym, expected)
                self.assertEqual("Disabling HVO by default" in result.stdout, expected == "n")

    def test_hvo_default_explicit_request_requires_7_2(self):
        sym = "HUGETLB_PAGE_OPTIMIZE_VMEMMAP_DEFAULT_ON"
        self.add_symbols(sym)
        self.set_baseline_symbols(**{sym: "n"})
        self.write_tree_version(6, 18)
        result = self.run_script("--strict", f"--enable-symbols={sym}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"CONFIG_{sym}=y is only allowed from Linux 7.2 (tree: 6.18)", result.stderr)
        self.assert_untouched()
        result = self.run_script(f"--enable-symbols={sym}")
        self.assert_success(result)
        self.assertIn("only allowed from Linux 7.2", result.stderr)
        self.assert_untouched()
        self.write_tree_version(7, 2)
        self.assert_success(self.run_script("--strict", f"--enable-symbols={sym}"))
        self.assert_symbol(sym, "y")

    def test_hvo_default_protected_before_7_2_is_kept_but_rejected(self):
        sym = "HUGETLB_PAGE_OPTIMIZE_VMEMMAP_DEFAULT_ON"
        self.add_symbols(sym)
        self.set_baseline_symbols(**{sym: "y"})
        self.write_tree_version(6, 18)
        protect = f"--protected-config-symbols={sym}"
        for args in ((protect,), (protect, f"--enable-symbols={sym}")):
            with self.subTest(args=args):
                result = self.run_script("--strict", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stderr.count("only allowed from Linux 7.2 (tree: 6.18)"), 1)
                self.assertNotIn("Disabling HVO by default", result.stdout)
                self.assert_untouched()
        result = self.run_script(protect)
        self.assert_success(result)
        self.assertIn(f"protected CONFIG_{sym}=y is only allowed from Linux 7.2", result.stderr)
        self.assert_symbol(sym, "y")
        self.write_tree_version(7, 2)
        result = self.run_script("--strict", protect)
        self.assert_success(result)
        self.assertNotIn("only allowed from Linux 7.2", result.stderr)

    def test_new_application_profiles_respect_protection_and_none(self):
        self.add_symbols("HIDRAW", "TUN")
        self.set_baseline_symbols(HIDRAW="n", TUN="n")
        self.assert_success(self.run_script("--strict", "--applications=multimedia,nebula",
                                            "--protected-config-symbols=HIDRAW,TUN"))
        self.assert_symbol("HIDRAW", "n")
        self.assert_symbol("TUN", "n")
        for profiles in ("none,desktop", "rocm,none"):
            self.assertNotEqual(self.run_script(f"--applications={profiles}").returncode, 0)

    def test_firewalld_nat_does_not_force_legacy_tables_on_new_kernels(self):
        self.add_symbols("IP_NF_NAT", "IP6_NF_NAT", "NF_TABLES", "NFT_NAT")
        self.assert_success(self.run_script("--strict", "--applications=firewalld"))
        self.assert_symbol("IP_NF_NAT", "y")
        self.add_symbols("NETFILTER_XTABLES_LEGACY")
        self.set_baseline_symbols(IP_NF_NAT="n", IP6_NF_NAT="n", NETFILTER_XTABLES_LEGACY="n")
        self.assert_success(self.run_script("--strict", "--applications=firewalld"))
        for symbol in ("IP_NF_NAT", "IP6_NF_NAT", "NETFILTER_XTABLES_LEGACY"):
            self.assert_symbol(symbol, "n")
        self.assert_symbol("NFT_NAT", "y")

    def test_docker_uses_nft_compat_unless_the_legacy_profile_is_added(self):
        nft = ("NFT_COMPAT", "NETFILTER_XT_MATCH_ADDRTYPE", "NETFILTER_XT_MATCH_CONNTRACK",
               "NETFILTER_XT_NAT", "NETFILTER_XT_TARGET_MASQUERADE")
        legacy = ("NETFILTER_XTABLES_LEGACY", "IP_NF_IPTABLES_LEGACY", "IP6_NF_IPTABLES_LEGACY",
                  "IP_NF_FILTER", "IP_NF_NAT", "IP6_NF_NAT", "IP_NF_RAW", "IP6_NF_RAW")
        self.add_symbols(*nft, *legacy)
        self.set_baseline_symbols(**dict.fromkeys(nft + legacy, "n"))
        result = self.run_script("--strict", "--prune-legacy", "--applications=docker")
        self.assert_success(result)
        self.assertIn("add the iptables-legacy profile", result.stdout)
        for symbol in nft:
            self.assert_symbol(symbol, "y")
        for symbol in legacy:
            self.assert_symbol(symbol, "n")
        result = self.run_script("--strict", "--prune-legacy", "--applications=docker,iptables-legacy")
        self.assert_success(result)
        self.assertNotIn("add the iptables-legacy profile", result.stdout)
        for symbol in legacy:
            self.assert_symbol(symbol, "y")

    def test_tunnel_profiles_do_not_pin_the_hidden_udp_tunnel(self):
        self.add_kconfig('''
            config NET_UDP_TUNNEL
            \ttristate
        ''')
        self.add_symbols("OPENVSWITCH", "VXLAN", "WIREGUARD", kind="tristate")
        self.add_symbols("MODULES")
        self.set_baseline_symbols(MODULES="y", NET_UDP_TUNNEL="m", VXLAN="m")
        result = self.run_script("--strict", "--applications=openvswitch,wireguard")
        self.assert_success(result)
        self.assertNotIn("CONFIG_NET_UDP_TUNNEL", result.stdout)
        self.assert_symbol("VXLAN", "m")

    def test_fixed_prune_lists_keep_symbols_selected_by_enabled_features(self):
        self.add_kconfig('''
            config OPENVSWITCH
            \ttristate "Open vSwitch"
            \tselect MPLS
            config MPLS
            \tbool "MultiProtocol Label Switching"
            config TIPC
            \ttristate "The TIPC Protocol"
        ''')
        self.set_baseline_symbols(OPENVSWITCH="m", MPLS="y", TIPC="m")
        result = self.run_script("--strict", "--prune-unused-net")
        self.assert_success(result)
        self.assertIn("Retaining CONFIG_MPLS: selected by enabled CONFIG_OPENVSWITCH", result.stdout)
        self.assert_symbol("MPLS", "y")
        self.assert_symbol("TIPC", "n")
        # A profile that enables Open vSwitch also overrides the earlier prune.
        self.set_baseline_symbols(OPENVSWITCH="n", MPLS="y")
        self.assert_success(self.run_script("--strict", "--prune-unused-net",
                                            "--applications=openvswitch"))
        self.assert_symbol("OPENVSWITCH", "y")
        self.assert_symbol("MPLS", "y")

    def test_debug_trace_pruning_keeps_the_iptables_raw_table(self):
        self.add_kconfig('''
            config IP_NF_RAW
            \ttristate  'raw table support (required for NOTRACK/TRACE)'
            config NETFILTER_XT_TARGET_TRACE
            \ttristate  '"TRACE" target support'
        ''')
        self.set_baseline_symbols(IP_NF_RAW="m", NETFILTER_XT_TARGET_TRACE="m")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        self.assert_symbol("IP_NF_RAW", "m")
        self.assert_symbol("NETFILTER_XT_TARGET_TRACE", "n")

    def test_debug_trace_pruning_removes_dm_block_manager_locking(self):
        # Its prompt ("Block manager locking") does not say debug; the fixed list catches it.
        self.add_kconfig('''
            config DM_DEBUG_BLOCK_MANAGER_LOCKING
            \tbool "Block manager locking"
        ''')
        self.set_baseline_symbols(DM_DEBUG_BLOCK_MANAGER_LOCKING="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        self.assert_symbol("DM_DEBUG_BLOCK_MANAGER_LOCKING", "n")

    def test_tpm_on_leaves_tpm2_hmac_to_the_baseline(self):
        self.add_symbols("TCG_TPM", "HW_RANDOM_TPM", "TCG_TIS_CORE", "TCG_TIS", "TCG_CRB",
                         "TCG_VTPM_PROXY", "TCG_TPM2_HMAC")
        env = self.make_host_root()
        tpm = Path(env["KC_HOST_ROOT"]) / "sys/class/tpm/tpm0"
        tpm.mkdir(parents=True)
        (tpm / "tpm_version_major").write_text("2\n")
        for baseline in ("n", "y"):
            with self.subTest(baseline=baseline):
                self.set_baseline_symbols(TCG_CRB="n", TCG_TPM2_HMAC=baseline)
                result = self.run_script("--strict", "--tpm-support=on", env=env)
                self.assert_success(result)
                self.assert_symbol("TCG_CRB", "y")
                self.assert_symbol("TCG_TPM2_HMAC", baseline)
                self.assertNotIn("CONFIG_TCG_TPM2_HMAC", result.stdout)

    def test_server_profile_enables_compressed_firmware(self):
        self.add_symbols("FW_LOADER")
        self.set_baseline_symbols(FW_LOADER="y", FW_LOADER_COMPRESS="n",
                                  FW_LOADER_COMPRESS_XZ="n", FW_LOADER_COMPRESS_ZSTD="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for symbol in ("FW_LOADER_COMPRESS", "FW_LOADER_COMPRESS_XZ", "FW_LOADER_COMPRESS_ZSTD"):
            self.assert_symbol(symbol, "y")
        self.set_baseline_symbols(FW_LOADER_COMPRESS="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--firmware-compression=off"))
        self.assert_symbol("FW_LOADER_COMPRESS", "n")
        # Without a firmware loader there is nothing to decompress.
        self.set_baseline_symbols(FW_LOADER="n", FW_LOADER_COMPRESS="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("FW_LOADER_COMPRESS", "n")

    def test_server_profile_enables_memory_error_recovery(self):
        recovery = ("MEMORY_FAILURE", "ACPI_APEI_MEMORY_FAILURE", "RAS_CEC")
        self.add_symbols("ARCH_SUPPORTS_MEMORY_FAILURE", "ACPI_APEI", "X86_MCE", "DEBUG_FS", *recovery)
        base = dict(ARCH_SUPPORTS_MEMORY_FAILURE="y", ACPI_APEI="y", X86_MCE="y", DEBUG_FS="y",
                    **dict.fromkeys(recovery, "n"))
        self.set_baseline_symbols(**base)
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for symbol in recovery:
            self.assert_symbol(symbol, "y")
        # RAS_CEC depends on DEBUG_FS; the rest does not.
        self.set_baseline_symbols(**base | {"DEBUG_FS": "n"})
        result = self.run_script("--strict", "--optimization-profile=server")
        self.assert_success(result)
        self.assertIn("RAS_CEC needs DEBUG_FS", result.stdout)
        self.assert_symbol("MEMORY_FAILURE", "y")
        self.assert_symbol("RAS_CEC", "n")
        # Architectures without memory-failure support are left alone.
        self.set_baseline_symbols(**base | {"ARCH_SUPPORTS_MEMORY_FAILURE": "n"})
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for symbol in recovery:
            self.assert_symbol(symbol, "n")

    def test_server_profile_disables_experimental_no_page_mapcount(self):
        self.add_symbols("NO_PAGE_MAPCOUNT")
        self.set_baseline_symbols(NO_PAGE_MAPCOUNT="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("NO_PAGE_MAPCOUNT", "y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("NO_PAGE_MAPCOUNT", "n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--enable-symbols=NO_PAGE_MAPCOUNT"))
        self.assert_symbol("NO_PAGE_MAPCOUNT", "y")

    def test_server_profile_disables_rt_group_scheduling(self):
        self.add_symbols("RT_GROUP_SCHED")
        self.set_baseline_symbols(RT_GROUP_SCHED="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("RT_GROUP_SCHED", "n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--enable-symbols=RT_GROUP_SCHED"))
        self.assert_symbol("RT_GROUP_SCHED", "y")

    def test_server_profile_enables_net_sched_before_qdiscs(self):
        self.add_symbols("NET", "NET_SCHED", "NET_SCH_FQ", "NET_SCH_FQ_CODEL",
                         "TCP_CONG_ADVANCED", "TCP_CONG_BBR")
        self.set_baseline_symbols(NET="y", NET_SCHED="n", NET_SCH_FQ="n", NET_SCH_FQ_CODEL="n",
                                  TCP_CONG_ADVANCED="n", TCP_CONG_BBR="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for sym in ("NET_SCHED", "NET_SCH_FQ", "NET_SCH_FQ_CODEL", "TCP_CONG_ADVANCED", "TCP_CONG_BBR"):
            self.assert_symbol(sym, "y")
        # Without NET, none of the network algorithms or qdiscs are requested.
        self.set_baseline_symbols(NET="n", NET_SCHED="n", NET_SCH_FQ="n", NET_SCH_FQ_CODEL="n",
                                  TCP_CONG_ADVANCED="n", TCP_CONG_BBR="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for sym in ("NET_SCHED", "NET_SCH_FQ", "NET_SCH_FQ_CODEL", "TCP_CONG_ADVANCED", "TCP_CONG_BBR"):
            self.assert_symbol(sym, "n")

    def test_all_optimization_profiles_disable_sched_alt(self):
        self.add_symbols("SCHED_ALT")
        for profile in ("server", "desktop", "realtime"):
            with self.subTest(profile=profile):
                self.set_baseline_symbols(SCHED_ALT="y")
                self.assert_success(self.run_script("--strict", f"--optimization-profile={profile}"))
                self.assert_symbol("SCHED_ALT", "n")
        # Protecting SCHED_ALT keeps it across profiles.
        self.set_baseline_symbols(SCHED_ALT="y")
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=desktop", "--protected-config-symbols=SCHED_ALT",
        ))
        self.assert_symbol("SCHED_ALT", "y")

    def test_profiles_enable_bpf_jit_when_dependencies_are_met(self):
        self.add_kconfig('''
            config BPF
            \tbool
            config HAVE_CBPF_JIT
            \tbool
            config HAVE_EBPF_JIT
            \tbool
            config BPF_SYSCALL
            \tbool "Enable bpf() system call"
            config BPF_JIT
            \tbool "Enable BPF Just In Time compiler"
            \tdepends on BPF
            \tdepends on HAVE_CBPF_JIT || HAVE_EBPF_JIT
            config BPF_JIT_ALWAYS_ON
            \tbool "Permanently enable BPF JIT and remove BPF interpreter"
            \tdepends on BPF_SYSCALL && HAVE_EBPF_JIT && BPF_JIT
        ''')
        self.set_baseline_symbols(BPF="y", HAVE_EBPF_JIT="y", BPF_SYSCALL="y",
                                  BPF_JIT="n", BPF_JIT_ALWAYS_ON="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("BPF_JIT", "y")
        self.assert_symbol("BPF_JIT_ALWAYS_ON", "y")
        # Without BPF_SYSCALL, classic BPF JIT is still enabled without BPF_JIT_ALWAYS_ON.
        self.set_baseline_symbols(BPF="y", HAVE_EBPF_JIT="y", BPF_SYSCALL="n",
                                  BPF_JIT="n", BPF_JIT_ALWAYS_ON="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("BPF_JIT", "y")
        self.assert_symbol("BPF_JIT_ALWAYS_ON", "n")
        # Explicit --disable-symbols=BPF_JIT suppresses both without strict conflict.
        self.set_baseline_symbols(BPF="y", HAVE_EBPF_JIT="y", BPF_SYSCALL="y",
                                  BPF_JIT="y", BPF_JIT_ALWAYS_ON="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--disable-symbols=BPF_JIT"))
        self.assert_symbol("BPF_JIT", "n")
        # Explicit --disable-symbols=BPF_JIT_ALWAYS_ON keeps BPF_JIT on.
        self.set_baseline_symbols(BPF="y", HAVE_EBPF_JIT="y", BPF_SYSCALL="y",
                                  BPF_JIT="n", BPF_JIT_ALWAYS_ON="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--disable-symbols=BPF_JIT_ALWAYS_ON"))
        self.assert_symbol("BPF_JIT", "y")
        self.assert_symbol("BPF_JIT_ALWAYS_ON", "n")
        # Protected BPF_JIT=n prevents enabling BPF_JIT_ALWAYS_ON.
        self.set_baseline_symbols(BPF="y", HAVE_EBPF_JIT="y", BPF_SYSCALL="y",
                                  BPF_JIT="n", BPF_JIT_ALWAYS_ON="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--protected-config-symbols=BPF_JIT"))
        self.assert_symbol("BPF_JIT", "n")
        self.assert_symbol("BPF_JIT_ALWAYS_ON", "n")

    def test_profiles_enable_network_scaling_when_net_and_deps_are_met(self):
        self.add_kconfig('''
            config NET
            \tbool "Networking support"
            config SYSFS
            \tbool "sysfs file system support"
            config PCPU_DEV_REFCNT
            \tbool "Use percpu variables to maintain network device refcount"
            \tdepends on SMP
            config BQL
            \tbool "Enable Byte Queue Limits"
            \tdepends on SYSFS
            config RPS
            \tbool "Receive packet steering"
            \tdepends on SMP && SYSFS
            config RFS_ACCEL
            \tbool "Hardware acceleration of RFS"
            \tdepends on RPS
        ''')
        self.set_baseline_symbols(NET="y", SMP="y", SYSFS="y",
                                  PCPU_DEV_REFCNT="n", BQL="n", RPS="n", RFS_ACCEL="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for sym in ("PCPU_DEV_REFCNT", "BQL", "RPS", "RFS_ACCEL"):
            self.assert_symbol(sym, "y")
        # NET=n skips all four.
        self.set_baseline_symbols(NET="n", SMP="y", SYSFS="y",
                                  PCPU_DEV_REFCNT="n", BQL="n", RPS="n", RFS_ACCEL="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        for sym in ("PCPU_DEV_REFCNT", "BQL", "RPS", "RFS_ACCEL"):
            self.assert_symbol(sym, "n")
        # Disabling or protecting RPS off skips RFS_ACCEL too.
        self.set_baseline_symbols(NET="y", SMP="y", SYSFS="y",
                                  PCPU_DEV_REFCNT="n", BQL="n", RPS="n", RFS_ACCEL="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop",
                                            "--disable-symbols=RPS"))
        self.assert_symbol("PCPU_DEV_REFCNT", "y")
        self.assert_symbol("BQL", "y")
        self.assert_symbol("RPS", "n")
        self.assert_symbol("RFS_ACCEL", "n")

    def test_x86_x2apic_follows_iommu_and_guest_prerequisites(self):
        self.add_kconfig('''
            config X86
            \tbool
            config X86_64
            \tbool
            config X86_LOCAL_APIC
            \tbool
            config IOMMU_SUPPORT
            \tbool "IOMMU Hardware Support"
            config IRQ_REMAP
            \tbool "Support for Interrupt Remapping"
            config HYPERVISOR_GUEST
            \tbool "Linux guest support"
            config PARAVIRT
            \tbool "Enable paravirtualization code"
            config KVM_GUEST
            \tbool "KVM Guest support"
            config X86_X2APIC
            \tbool "x2APIC interrupt controller architecture support"
            \tdepends on X86_LOCAL_APIC && X86_64 && (IRQ_REMAP || HYPERVISOR_GUEST)
        ''')
        # Bare-metal with IOMMU+IRQ_REMAP enables X86_X2APIC under a profile.
        self.set_baseline_symbols(X86="y", X86_64="y", X86_LOCAL_APIC="y",
                                  IOMMU_SUPPORT="y", IRQ_REMAP="y",
                                  HYPERVISOR_GUEST="n", X86_X2APIC="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("X86_X2APIC", "y")
        # Turning off IOMMU on bare metal must not request X86_X2APIC.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--host-type=baremetal", "--iommu-support=off"))
        self.assert_symbol("X86_X2APIC", "n")
        # A KVM guest enables X86_X2APIC via HYPERVISOR_GUEST even with IOMMU off.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--host-type=qemu", "--iommu-support=off"))
        self.assert_symbol("X86_X2APIC", "y")

    def test_guest_host_types_enable_parents_drop_hidden_and_follow_modular_deps(self):
        self.add_kconfig('''
            config HYPERVISOR_GUEST
            \tbool "Linux guest support"
            config PARAVIRT
            \tbool "Enable paravirtualization code"
            config PARAVIRT_XXL
            \tbool
            config KVM_GUEST
            \tbool "KVM Guest support"
            config VIRTIO_MENU
            \tbool "Virtio drivers"
            config VIRTIO
            \ttristate
            config VIRTIO_PCI
            \ttristate "PCI driver for virtio devices"
            config SCSI
            \ttristate "SCSI device support"
            config SCSI_LOWLEVEL
            \tbool "SCSI low-level drivers"
            config SCSI_VIRTIO
            \ttristate "virtio-scsi support"
            config FUSE_FS
            \ttristate "FUSE support"
            config VIRTIO_FS
            \ttristate "Virtio Filesystem"
            config VIRT_DRIVERS
            \tbool "Virtualization drivers"
            config VBOXGUEST
            \ttristate "Virtual Box Guest integration support"
            config VBOXSF_FS
            \ttristate "VirtualBox guest shared folder support"
        ''')
        self.set_baseline_symbols(
            HYPERVISOR_GUEST="n", PARAVIRT="n", PARAVIRT_XXL="n", KVM_GUEST="n",
            VIRTIO_MENU="n", VIRTIO="n", VIRTIO_PCI="n", SCSI="y", SCSI_LOWLEVEL="n",
            SCSI_VIRTIO="n", FUSE_FS="m", VIRTIO_FS="n", VIRT_DRIVERS="n",
            VBOXGUEST="n", VBOXSF_FS="n",
        )
        result = self.run_script("--strict", "--host-type=qemu")
        self.assert_success(result)
        self.assertNotIn("Enabling: CONFIG_PARAVIRT_XXL", result.stdout)
        self.assertNotIn("Enabling: CONFIG_VIRTIO\n", result.stdout)
        self.assert_symbol("VIRTIO_MENU", "y")
        self.assert_symbol("SCSI_LOWLEVEL", "y")
        self.assert_symbol("SCSI_VIRTIO", "y")
        self.assert_symbol("VIRTIO_FS", "m")
        # VirtualBox enables VIRT_DRIVERS; baremetal leaves shared parents untouched.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--host-type=virtualbox"))
        self.assert_symbol("VIRT_DRIVERS", "y")
        self.assert_symbol("VBOXGUEST", "y")
        self.assert_symbol("VBOXSF_FS", "y")
        self.set_baseline_symbols(SCSI_LOWLEVEL="y", VIRT_DRIVERS="y")
        self.assert_success(self.run_script("--strict", "--host-type=baremetal"))
        self.assert_symbol("SCSI_LOWLEVEL", "y")
        self.assert_symbol("VIRT_DRIVERS", "y")

    def test_qemu_guest_enables_haltpoll_cpuidle_when_cpu_idle_and_x86(self):
        self.add_kconfig('''
            config X86
            \tbool
            config CPU_IDLE
            \tbool "CPU idle PM support"
            config HYPERVISOR_GUEST
            \tbool "Linux guest support"
            config PARAVIRT
            \tbool "Enable paravirtualization code"
            config KVM_GUEST
            \tbool "KVM Guest support"
            config CPU_IDLE_GOV_HALTPOLL
            \tbool "Haltpoll governor (for virtualized systems)"
            \tdepends on KVM_GUEST
            config HALTPOLL_CPUIDLE
            \ttristate "Halt poll cpuidle driver"
            \tdepends on X86 && KVM_GUEST
        ''')
        self.set_baseline_symbols(
            X86="y", CPU_IDLE="y", HYPERVISOR_GUEST="n", PARAVIRT="n",
            KVM_GUEST="n", HALTPOLL_CPUIDLE="n", CPU_IDLE_GOV_HALTPOLL="n",
        )
        self.assert_success(self.run_script("--strict", "--host-type=qemu"))
        self.assert_symbol("HALTPOLL_CPUIDLE", "y")
        # CPU_IDLE=n leaves HALTPOLL_CPUIDLE untouched without failing strict mode.
        self.set_baseline_symbols(CPU_IDLE="n", HALTPOLL_CPUIDLE="n")
        self.assert_success(self.run_script("--strict", "--host-type=qemu"))
        self.assert_symbol("HALTPOLL_CPUIDLE", "n")
        # Baremetal prunes both haltpoll options.
        self.set_baseline_symbols(CPU_IDLE="y", HALTPOLL_CPUIDLE="y", CPU_IDLE_GOV_HALTPOLL="y")
        self.assert_success(self.run_script("--strict", "--host-type=baremetal"))
        self.assert_symbol("HALTPOLL_CPUIDLE", "n")
        self.assert_symbol("CPU_IDLE_GOV_HALTPOLL", "n")

    def test_intel_cpu_vendor_filter_enables_intel_idle_when_deps_met(self):
        self.add_kconfig('''
            config X86
            \tbool
            config CPU_IDLE
            \tbool "CPU idle PM support"
            config CPU_SUP_INTEL
            \tbool "Support Intel processors"
            config X86_INTEL_PSTATE
            \tbool "Intel P state control"
            config X86_ACPI_CPUFREQ
            \ttristate "ACPI Processor P-States driver"
        ''')
        self.add_kconfig('''
            config INTEL_IDLE
            \tbool "Cpuidle Driver for Intel Processors"
            \tdepends on CPU_IDLE
            \tdepends on X86
            \tdepends on CPU_SUP_INTEL
        ''', path="drivers/idle/Kconfig")
        self.set_baseline_symbols(
            X86="y", CPU_IDLE="y", CPU_SUP_INTEL="y",
            X86_INTEL_PSTATE="n", X86_ACPI_CPUFREQ="n", INTEL_IDLE="n",
        )
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=intel"))
        self.assert_symbol("X86_INTEL_PSTATE", "y")
        self.assert_symbol("X86_ACPI_CPUFREQ", "y")
        self.assert_symbol("INTEL_IDLE", "y")
        # Respects explicit opt-out and missing CPU_IDLE.
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script(
            "--strict", "--cpu-vendor-filter=intel", "--disable-symbols=INTEL_IDLE",
        ))
        self.assert_symbol("INTEL_IDLE", "n")
        self.set_baseline_symbols(CPU_IDLE="n", INTEL_IDLE="n")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=intel"))
        self.assert_symbol("INTEL_IDLE", "n")
        # AMD filter prunes INTEL_IDLE.
        self.set_baseline_symbols(CPU_IDLE="y", INTEL_IDLE="y")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=amd"))
        self.assert_symbol("INTEL_IDLE", "n")

    def test_profiles_tune_slub_cpu_partial_base_small_and_raid6_benchmark(self):
        self.add_kconfig('''
            config EXPERT
            \tbool "Configure standard kernel features (expert users)"
            config BASE_SMALL
            \tbool "Enable smaller-sized data structures for core" if EXPERT
            config SLUB_TINY
            \tbool "Configure SLUB for minimal memory footprint"
            config SLUB_CPU_PARTIAL
            \tbool "Enable per cpu partial caches"
            \tdepends on SMP && !SLUB_TINY
            config RAID6_PQ
            \ttristate
            config RAID6_PQ_BENCHMARK
            \tbool "Automatically choose fastest RAID6 PQ functions"
            \tdepends on RAID6_PQ
        ''')
        # server and desktop enable SLUB_CPU_PARTIAL (6.12-6.18), disable BASE_SMALL,
        # and enable RAID6_PQ_BENCHMARK when RAID6_PQ is enabled (y or m).
        for profile in ("server", "desktop"):
            with self.subTest(profile=profile):
                self.set_baseline_symbols(
                    SMP="y", EXPERT="y", BASE_SMALL="y", SLUB_TINY="y",
                    SLUB_CPU_PARTIAL="n", RAID6_PQ="m", RAID6_PQ_BENCHMARK="n",
                )
                self.assert_success(self.run_script("--strict", f"--optimization-profile={profile}"))
                self.assert_symbol("SLUB_TINY", "n")
                self.assert_symbol("SLUB_CPU_PARTIAL", "y")
                self.assert_symbol("BASE_SMALL", "n")
                self.assert_symbol("RAID6_PQ_BENCHMARK", "y")
        # realtime disables SLUB_CPU_PARTIAL, still disables BASE_SMALL and keeps RAID6 benchmark.
        self.set_baseline_symbols(
            SMP="y", EXPERT="y", BASE_SMALL="y", SLUB_TINY="n",
            SLUB_CPU_PARTIAL="y", RAID6_PQ="y", RAID6_PQ_BENCHMARK="n",
        )
        self.assert_success(self.run_script("--strict", "--optimization-profile=realtime"))
        self.assert_symbol("SLUB_CPU_PARTIAL", "n")
        self.assert_symbol("BASE_SMALL", "n")
        self.assert_symbol("RAID6_PQ_BENCHMARK", "y")
        # Protected SLUB_TINY=y, EXPERT=n, RAID6_PQ=n, and explicit overrides are respected.
        self.set_baseline_symbols(
            SMP="y", EXPERT="n", BASE_SMALL="y", SLUB_TINY="y",
            SLUB_CPU_PARTIAL="n", RAID6_PQ="n", RAID6_PQ_BENCHMARK="n",
        )
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=server",
            "--protected-config-symbols=SLUB_TINY",
        ))
        self.assert_symbol("SLUB_TINY", "y")
        self.assert_symbol("SLUB_CPU_PARTIAL", "n")
        self.assert_symbol("BASE_SMALL", "y")
        self.assert_symbol("RAID6_PQ_BENCHMARK", "n")
        self.set_baseline_symbols(
            SMP="y", EXPERT="y", BASE_SMALL="y", SLUB_TINY="n",
            SLUB_CPU_PARTIAL="n", RAID6_PQ="m", RAID6_PQ_BENCHMARK="n",
        )
        self.assert_success(self.run_script(
            "--strict", "--optimization-profile=server",
            "--enable-symbols=BASE_SMALL",
            "--disable-symbols=SLUB_CPU_PARTIAL,RAID6_PQ_BENCHMARK",
        ))
        self.assert_symbol("BASE_SMALL", "y")
        self.assert_symbol("SLUB_CPU_PARTIAL", "n")
        self.assert_symbol("RAID6_PQ_BENCHMARK", "n")

    def test_rasdaemon_profile_keeps_trace_events_after_observability_pruning(self):
        wanted = ("RAS", "EDAC", "ACPI_APEI", "ACPI_APEI_GHES", "PCIEPORTBUS", "PCIEAER", "FTRACE",
                  "ENABLE_DEFAULT_TRACERS", "TRACING", "TRACEPOINTS", "EVENT_TRACING", "X86_MCE",
                  "MEMORY_FAILURE", "DEBUG_FS", "DEBUG_FS_ALLOW_ALL", "TRACEFS_AUTOMOUNT_DEPRECATED")
        self.add_symbols("X86", "ARCH_SUPPORTS_MEMORY_FAILURE", "FUNCTION_TRACER", *wanted)
        self.set_baseline_symbols(X86="y", ARCH_SUPPORTS_MEMORY_FAILURE="y", FUNCTION_TRACER="y",
                                  **dict.fromkeys(wanted, "n"))
        self.assert_success(self.run_script("--strict", "--prune-observability",
                                            "--applications=rasdaemon"))
        for symbol in wanted:
            self.assert_symbol(symbol, "y")
        self.assert_symbol("FUNCTION_TRACER", "n")

    def test_server_profile_keeps_lockup_and_hung_task_detection(self):
        detectors = ("DEBUG_KERNEL", "LOCKUP_DETECTOR", "SOFTLOCKUP_DETECTOR", "DETECT_HUNG_TASK",
                     "DETECT_HUNG_TASK_BLOCKER")
        side_effects = ("DEBUG_MISC", "X86_DEBUG_FPU", "RCU_TRACE")
        self.add_symbols(*detectors, *side_effects)
        self.set_baseline_symbols(**dict.fromkeys(detectors, "n"), **dict.fromkeys(side_effects, "y"))
        # The debug/trace prune lists every one of them; the server profile runs later.
        self.assert_success(self.run_script("--strict", "--prune-debug-trace", "--optimization-profile=server"))
        for symbol in detectors:
            self.assert_symbol(symbol, "y")
        for symbol in side_effects:
            self.assert_symbol(symbol, "n")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        for symbol in detectors[:4]:  # the fixed list; the blocker dump is found by menu in real trees
            self.assert_symbol(symbol, "n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--disable-symbols=SOFTLOCKUP_DETECTOR,DETECT_HUNG_TASK"))
        self.assert_symbol("SOFTLOCKUP_DETECTOR", "n")
        self.assert_symbol("DETECT_HUNG_TASK", "n")
        # A baseline that already debugs keeps its own debug choices.
        self.set_baseline_symbols(DEBUG_KERNEL="y", DEBUG_MISC="y", SOFTLOCKUP_DETECTOR="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_symbol("SOFTLOCKUP_DETECTOR", "y")
        self.assert_symbol("DEBUG_MISC", "y")

    def test_profiles_leave_promptless_symbols_to_their_selectors(self):
        self.add_kconfig('''
            config SUNRPC
            \ttristate
            config NFSD
            \ttristate "NFS server support"
        ''')
        self.set_baseline_symbols(SUNRPC="m", NFSD="n")
        # An earlier explicit request for the hidden symbol is dropped as well.
        result = self.run_script("--strict", "--applications=nfs-server")
        self.assert_success(result)
        self.assertIn("no prompt, left to the options that select them:", result.stdout)
        self.assertNotIn("Enabling: CONFIG_SUNRPC", result.stdout)
        self.assert_symbol("SUNRPC", "m")
        self.assert_symbol("NFSD", "y")

    def test_atop_builds_the_connector_in_for_proc_events(self):
        self.add_symbols("MODULES", "TASKSTATS", "PROC_EVENTS")
        self.add_symbols("CONNECTOR", kind="tristate")
        self.set_baseline_symbols(MODULES="y", CONNECTOR="m", PROC_EVENTS="n")
        self.assert_success(self.run_script("--strict", "--applications=atop"))
        self.assert_symbol("CONNECTOR", "y")
        self.assert_symbol("PROC_EVENTS", "y")

    def test_desktop_enables_preempt_dynamic_on_7_4_lazy_targets(self):
        # 7.4 dropped HAVE_PREEMPT_DYNAMIC; ARCH_HAS_PREEMPT_LAZY gates PREEMPT_DYNAMIC.
        self.add_symbols("ARCH_HAS_PREEMPT_LAZY", "PREEMPT_DYNAMIC")
        self.set_baseline_symbols(ARCH_HAS_PREEMPT_LAZY="y")
        lines = [l for l in self.config.read_text().splitlines() if "PREEMPT_DYNAMIC" not in l]
        self.baseline = "\n".join(lines) + "\n"
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_symbol("PREEMPT_DYNAMIC", "y")

    def test_legacy_pruning_moves_sysfb_simplefb_to_efidrm_and_vesadrm(self):
        self.add_kconfig('''
            config SYSFB_SIMPLEFB
            \tbool "Mark VGA/VBE/EFI FB as generic system framebuffer (deprecated)"
        ''')
        self.add_symbols("X86", "EFI", "MODULES", "FB_SIMPLE")
        self.add_symbols("DRM_SIMPLEDRM", "DRM_EFIDRM", "DRM_VESADRM", kind="tristate")
        base = dict(X86="y", EFI="y", MODULES="y", SYSFB_SIMPLEFB="y", DRM_SIMPLEDRM="y",
                    DRM_EFIDRM="n", DRM_VESADRM="n", FB_SIMPLE="n")
        # 7.2 does not deprecate it yet: nothing changes.
        self.write_tree_version(7, 2)
        self.set_baseline_symbols(**base)
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("SYSFB_SIMPLEFB", "y")
        self.write_tree_version(7, 4)
        for simpledrm in ("y", "m"):
            with self.subTest(simpledrm=simpledrm):
                self.set_baseline_symbols(**base | {"DRM_SIMPLEDRM": simpledrm})
                result = self.run_script("--strict", "--prune-legacy")
                self.assert_success(result)
                self.assertIn("Migrating CONFIG_SYSFB_SIMPLEFB to CONFIG_DRM_EFIDRM CONFIG_DRM_VESADRM", result.stdout)
                self.assert_symbol("SYSFB_SIMPLEFB", "n")
                self.assert_symbol("DRM_EFIDRM", simpledrm)
                self.assert_symbol("DRM_VESADRM", simpledrm)
        # The fbdev simplefb console still needs it; with no user it just goes.
        self.set_baseline_symbols(**base | {"DRM_SIMPLEDRM": "n", "FB_SIMPLE": "y"})
        result = self.run_script("--strict", "--prune-legacy")
        self.assert_success(result)
        self.assertIn("Retaining CONFIG_SYSFB_SIMPLEFB", result.stdout)
        self.assert_symbol("SYSFB_SIMPLEFB", "y")
        self.set_baseline_symbols(**base | {"DRM_SIMPLEDRM": "n", "FB_SIMPLE": "n"})
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_symbol("SYSFB_SIMPLEFB", "n")
        self.assert_symbol("DRM_EFIDRM", "n")

    def test_cephfs_inline_data_is_kept_and_its_absence_reported(self):
        self.add_kconfig('''
            config CEPH_FS_INLINE_DATA
            \tbool "Enable CephFS inline data support (deprecated)"
        ''')
        self.add_symbols("CEPH_LIB", "CEPH_FS")
        # A filesystem that still holds inline data needs it: legacy pruning keeps it.
        self.set_baseline_symbols(CEPH_LIB="y", CEPH_FS="y", CEPH_FS_INLINE_DATA="y")
        result = self.run_script("--strict", "--prune-legacy", "--applications=ceph")
        self.assert_success(result)
        self.assert_symbol("CEPH_FS_INLINE_DATA", "y")
        self.assertNotIn("CEPH_FS_INLINE_DATA is off", result.stdout)
        self.set_baseline_symbols(CEPH_FS_INLINE_DATA="n")
        result = self.run_script("--strict", "--applications=ceph")
        self.assert_success(result)
        self.assertIn("CEPH_FS_INLINE_DATA is off", result.stdout)
        self.assert_symbol("CEPH_FS_INLINE_DATA", "n")

    def test_firehol_requests_the_xtables_its_rules_use(self):
        syms = ("NETFILTER_XTABLES_LEGACY", "IP_NF_IPTABLES_LEGACY", "NETFILTER_XT_TARGET_TCPMSS",
                "NETFILTER_XT_TARGET_NETMAP", "NETFILTER_XT_TARGET_CT", "NETFILTER_XT_MATCH_PHYSDEV",
                "NF_NAT_FTP", "NFT_NAT")
        self.add_symbols(*syms)
        self.set_baseline_symbols(**dict.fromkeys(syms, "n"))
        self.assert_success(self.run_script("--strict", "--prune-legacy", "--applications=firehol"))
        for symbol in syms:
            self.assert_symbol(symbol, "y")

    def test_nfs_server_reminds_about_nfsdcld_without_enabling_legacy_tracking(self):
        self.add_symbols("NFSD", "NFSD_V4", "NFSD_LEGACY_CLIENT_TRACKING")
        self.set_baseline_symbols(NFSD="y", NFSD_V4="y", NFSD_LEGACY_CLIENT_TRACKING="n")
        result = self.run_script("--strict", "--applications=nfs-server")
        self.assert_success(result)
        self.assertIn("nfsdcld", result.stdout)
        self.assert_symbol("NFSD_LEGACY_CLIENT_TRACKING", "n")
        self.assert_success(self.run_script("--strict", "--applications=nfs-server",
                                            "--enable-symbols=NFSD_LEGACY_CLIENT_TRACKING"))
        self.assert_symbol("NFSD_LEGACY_CLIENT_TRACKING", "y")

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
        self.add_symbols("UCLAMP_TASK", "SCHED_AUTOGROUP", "CPU_FREQ", "CPU_FREQ_GOV_SCHEDUTIL")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop", "--uclamp=off", "--autogroup=off"))
        self.assert_symbol("UCLAMP_TASK", "n")
        self.assert_symbol("SCHED_AUTOGROUP", "n")
        self.assert_success(self.run_script("--strict", "--uclamp=on", "--autogroup=on"))
        self.assert_symbol("UCLAMP_TASK", "y")
        self.assert_symbol("CPU_FREQ", "y")
        self.assert_symbol("CPU_FREQ_GOV_SCHEDUTIL", "y")
        self.assert_symbol("SCHED_AUTOGROUP", "y")
        self.assert_success(self.run_script("--strict", "--uclamp=keep", "--autogroup=keep"))
        self.assert_symbol("UCLAMP_TASK", "y")
        self.assert_symbol("SCHED_AUTOGROUP", "y")

    def test_native_aes_acceleration_for_supported_trees(self):
        self.add_symbols("X86", "MODULES", "KMSAN", "X86_NATIVE_CPU")
        env = self.make_host_root(cpuinfo="processor : 0\nflags : aes cx16\n")
        self.add_symbols("CRYPTO", "CRYPTO_AES", kind="tristate")
        self.add_kconfig('''
            config CRYPTO_AES_NI_INTEL
            \ttristate "AES-NI/VAES"
            \tdepends on X86
        ''', path="arch/x86/crypto/Kconfig")
        cases = (
            ((), {}, "m"),
            ((), {"CRYPTO": "m", "CRYPTO_AES": "m"}, "m"),
            ((), {"MODULES": "n"}, "y"),
            ((), {"CRYPTO_AES_NI_INTEL": "y"}, "y"),
            ((), {"CRYPTO_AES_NI_INTEL": "m"}, "m"),
            (("--enable-symbols=CRYPTO_AES_NI_INTEL",), {"CRYPTO_AES_NI_INTEL": "m"}, "y"),
            (("--disable-symbols=CRYPTO_AES_NI_INTEL",), {}, "n"),
            (("--protected-config-symbols=CRYPTO_AES_NI_INTEL",), {}, "n"),
            (("--optimization-profile=none",), {}, "m"),
            (("--native-cpu=none",), {}, "n"),
            (("--native-cpu=off",), {}, "n"),
            (("--native-cpu=off",), {"CRYPTO_AES_NI_INTEL": "m"}, "m"),
            (("--native-cpu=on",), {}, "m"),
            ((), {"X86": "n"}, "n"),
            ((), {"KMSAN": "y"}, "n"),
            ((), {"CRYPTO": "n", "CRYPTO_AES": "n"}, "n"),
            ((), {"CRYPTO_AES": "n"}, "n"),
        )
        for args, overrides, expected in cases:
            with self.subTest(args=args, overrides=overrides):
                self.set_baseline_symbols(**dict(X86="y", MODULES="y", KMSAN="n", CRYPTO="y",
                                                X86_NATIVE_CPU="n", CRYPTO_AES="y", CRYPTO_AES_NI_INTEL="n") | overrides)
                self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                                    "--native-cpu=auto", *args, env=env))
                self.assert_symbol("CRYPTO_AES_NI_INTEL", expected)
                self.assert_symbol("X86_NATIVE_CPU", "y" if "--native-cpu=on" in args else "n")
        self.add_symbols("CRYPTO_LIB_AES_XTS", kind="tristate")
        self.set_baseline_symbols(X86="y", MODULES="y", CRYPTO="y", CRYPTO_AES="y",
                                  CRYPTO_AES_NI_INTEL="n")
        self.assert_success(self.run_script("--strict", "--native-cpu=auto", env=env))
        self.assert_symbol("CRYPTO_AES_NI_INTEL", "n")

    def test_native_aes_requires_evidence_from_every_cpu(self):
        self.add_symbols("X86", "MODULES", "CRYPTO", "CRYPTO_AES")
        self.add_symbols("CRYPTO_AES_NI_INTEL", kind="tristate")
        cases = (
            ("", "n"),
            ("processor : 0\nflags : vaes aes_extra\n", "n"),
            ("processor : 0\nFeatures : aes\n", "n"),
            ("processor : 0\nflags : aes\n\nprocessor : 1\nflags : cx16\n", "n"),
            ("processor : 0\nflags : aes\n\nprocessor : 1\n", "n"),
            ("processor : 0\nflags : aes\n\nprocessor : 1\nflags : aes cx16\n", "m"),
        )
        for cpuinfo, expected in cases:
            with self.subTest(cpuinfo=cpuinfo):
                env = self.make_host_root(cpuinfo=cpuinfo)
                self.set_baseline_symbols(X86="y", MODULES="y", CRYPTO="y", CRYPTO_AES="y",
                                          CRYPTO_AES_NI_INTEL="n")
                result = self.run_script("--strict", "--native-cpu=auto", env=env)
                self.assert_success(result)
                self.assert_symbol("CRYPTO_AES_NI_INTEL", expected)
                if expected == "n":
                    self.assertIn("CPU flag aes is not confirmed", result.stdout)
        self.set_baseline_symbols(CRYPTO_AES_NI_INTEL="n")
        self.assert_success(self.run_script("--strict", "--native-cpu=auto",
                                            env=env | {"CROSS_COMPILE": "x86_64-linux-gnu-"}))
        self.assert_symbol("CRYPTO_AES_NI_INTEL", "n")
        (self.root / "host/proc/cpuinfo").unlink()
        self.assert_success(self.run_script("--strict", "--native-cpu=auto", env=env))
        self.assert_symbol("CRYPTO_AES_NI_INTEL", "n")

    def test_native_posted_msi_follows_profiles_and_final_iommu_settings(self):
        env = self.make_host_root(cpuinfo="processor : 0\nflags : cx16\n", cmdline="\n",
                                  iommu_caps=(("dmar0", "800000000000000", "8"),))
        self.add_symbols("X86", "X86_64", "IOMMU_SUPPORT", "INTEL_IOMMU", "PCI_MSI", "ACPI", "X86_IO_APIC")
        self.add_kconfig('''
            config IRQ_REMAP
            \tbool "Interrupt remapping"
            \tdepends on X86_64 && X86_IO_APIC && PCI_MSI && ACPI
        ''', path="drivers/iommu/Kconfig")
        self.add_kconfig('''
            config X86_POSTED_MSI
            \tbool "MSI delivery by posted interrupts"
            \tdepends on X86_64 && IRQ_REMAP
        ''', path="arch/x86/Kconfig")
        cases = (
            ("server", (), {}, "y"),
            ("desktop", (), {}, "y"),
            ("none", (), {}, "y"),
            ("realtime", (), {}, "y"),
            ("realtime", (), {"X86_POSTED_MSI": "y"}, "y"),
            ("server", ("--native-cpu=none",), {}, "n"),
            ("desktop", ("--native-cpu=none",), {}, "n"),
            ("server", ("--iommu-support=off",), {}, "n"),
            ("server", ("--disable-symbols=IRQ_REMAP",), {}, "n"),
            ("server", ("--disable-symbols=PCI_MSI",), {}, "n"),
            ("server", ("--disable-symbols=ACPI",), {}, "n"),
            ("server", ("--disable-symbols=X86_POSTED_MSI",), {}, "n"),
            ("server", ("--protected-config-symbols=X86_POSTED_MSI",), {}, "n"),
            ("server", (), {"INTEL_IOMMU": "n"}, "n"),
            ("server", (), {"X86_64": "n"}, "n"),
            ("server", (), {"IOMMU_SUPPORT": "n"}, "n"),
        )
        for profile, args, overrides, expected in cases:
            with self.subTest(profile=profile, args=args, overrides=overrides):
                self.set_baseline_symbols(**dict(X86="y", X86_64="y", IOMMU_SUPPORT="y",
                                                PCI_MSI="y", ACPI="y", X86_IO_APIC="y",
                                                INTEL_IOMMU="y", IRQ_REMAP="y", X86_POSTED_MSI="n") | overrides)
                self.assert_success(self.run_script("--strict", f"--optimization-profile={profile}",
                                                    "--native-cpu=auto", *args, env=env))
                self.assert_symbol("X86_POSTED_MSI", expected)

    def test_native_posted_msi_requires_all_iommu_capabilities(self):
        self.add_symbols("X86", "X86_64", "IOMMU_SUPPORT", "INTEL_IOMMU", "IRQ_REMAP", "X86_POSTED_MSI")
        good = ("dmar0", "800000000000000", "8")
        cases = (
            ((good,), "cx16", "", "y"),
            ((good, ("dmar1", "ffffffffffffffff", "ffffffffffffffff")), "cx16", "", "y"),
            ((good, ("dmar1", "0", "8")), "cx16", "", "n"),
            ((good, ("dmar1", "800000000000000", "0")), "cx16", "", "n"),
            ((("dmar0", "800000000000000", None),), "cx16", "", "n"),
            ((("dmar0", "not-hex", "8"),), "cx16", "", "n"),
            ((("dmar0", "18000000000000000", "8"),), "cx16", "", "n"),
            ((), "cx16", "", "n"),
            ((good,), "aes", "", "n"),
            ((good,), "cx16", "intremap=posted_msi", "y"),
            ((good,), "cx16", "intremap=on,nopost", "n"),
            ((good,), "cx16", "intremap=off", "n"),
            ((good,), "cx16", "nointremap", "n"),
            ((good,), "cx16", "intel_iommu=off", "n"),
            ((good,), "cx16", "pci=nomsi", "n"),
        )
        for units, flags, cmdline, expected in cases:
            with self.subTest(units=units, flags=flags, cmdline=cmdline):
                shutil.rmtree(self.root / "host", ignore_errors=True)
                env = self.make_host_root(cpuinfo=f"processor : 0\nflags : {flags}\n",
                                          cmdline=cmdline + "\n", iommu_caps=units, acpi_tables=("DMAR",))
                self.set_baseline_symbols(X86="y", X86_64="y", IOMMU_SUPPORT="y", INTEL_IOMMU="y",
                                          IRQ_REMAP="y", X86_POSTED_MSI="n")
                result = self.run_script("--strict", "--native-cpu=auto", env=env)
                self.assert_success(result)
                self.assert_symbol("X86_POSTED_MSI", expected)
                self.assertIn("runtime use still requires" if expected == "y" else "posted MSI skipped", result.stdout)
        self.set_baseline_symbols(X86_POSTED_MSI="n")
        (self.root / "host/proc/cmdline").unlink()
        self.assert_success(self.run_script("--strict", "--native-cpu=auto", env=env))
        self.assert_symbol("X86_POSTED_MSI", "n")

    def test_native_arch_alias_mapping_overrides_and_unavailable_candidates(self):
        self.add_symbols("PCI", "PCI_MSI", "NET", "NETDEVICES", "ETHERNET", "NET_VENDOR_TEST", "MODULES")
        self.add_symbols("TEST_NIC", "TEST_UNRELATED", kind="tristate")
        self.add_kconfig('config TEST_HIDDEN\n\ttristate\n')
        source = self.tree / "drivers/net/ethernet/test"
        source.mkdir(parents=True)
        (source / "Makefile").write_text("obj-$(CONFIG_TEST_NIC) += test-nic.o\nobj-$(CONFIG_TEST_HIDDEN) += test-nic.o\n")
        (source / "Kconfig").write_text('config NET_VENDOR_TEST\n\tbool "Test vendor"\n')
        self.set_baseline_symbols(PCI="n", PCI_MSI="n", NET="n", NETDEVICES="n", ETHERNET="n",
                                  NET_VENDOR_TEST="n", TEST_NIC="n", TEST_UNRELATED="m", MODULES="y")
        env = self.make_host_root()
        host = Path(env["KC_HOST_ROOT"])
        device = host / "sys/bus/pci/devices/nic"
        device.mkdir(parents=True)
        (device / "class").write_text("0x020000\n")
        (device / "msi_irqs").mkdir()
        (device / "msi_irqs/32").write_text("msix\n")
        driver = host / "sys/bus/pci/drivers/test"
        driver.mkdir(parents=True)
        (driver / "module").symlink_to("/sys/module/test_nic")
        (device / "driver").symlink_to("/sys/bus/pci/drivers/test")
        for flag in ("--native-arch", "--nativer-arch", "NATIVE_ARCH=true"):
            with self.subTest(flag=flag):
                self.config.write_text(self.baseline)
                backups = set(self.tree.glob(".config.bak.*"))
                result = self.run_script("--strict", flag, "--dry-run", env=env)
                self.assert_success(result)
                self.assertEqual(self.config.read_text(), self.baseline)
                self.assertEqual(set(self.tree.glob(".config.bak.*")), backups)
                self.assert_success(self.run_script("--strict", flag, env=env))
                for sym in ("PCI", "PCI_MSI", "NET", "NETDEVICES", "ETHERNET", "NET_VENDOR_TEST"):
                    self.assert_symbol(sym, "y")
                self.assert_symbol("TEST_NIC", "m")
                self.assert_symbol("TEST_UNRELATED", "m")
                self.assertNotIn("CONFIG_TEST_HIDDEN=", self.config.read_text())
        self.config.write_text(self.baseline)
        result = self.run_script("--strict", "--native-arch", env=env | {"DROP_SYMBOLS": "TEST_NIC"})
        self.assert_success(result)
        self.assert_symbol("TEST_NIC", "n")
        self.assertIn("CONFIG_TEST_NIC unavailable with current dependencies", result.stdout)
        for override in ("--disable-symbols=TEST_NIC", "--protected-config-symbols=TEST_NIC"):
            self.config.write_text(self.baseline)
            self.assert_success(self.run_script("--strict", "--native-arch", override, env=env))
            self.assert_symbol("TEST_NIC", "n")
        self.config.write_text(self.baseline)
        backups = set(self.tree.glob(".config.bak.*"))
        self.assert_success(self.run_script("--native-arch", env=env | {"CROSS_COMPILE": "test-"}))
        self.assertEqual(self.config.read_text(), self.baseline)
        self.assertEqual(set(self.tree.glob(".config.bak.*")), backups)
        self.assert_success(self.run_script("--nativer-arch=false", env=env))
        self.assertEqual(self.config.read_text(), self.baseline)
        self.assertEqual(set(self.tree.glob(".config.bak.*")), backups)

    def test_native_arch_driver_modes_select_conflicts_and_rollback(self):
        self.add_symbols("MODULES")
        self.add_kconfig('''
            config TEST_DRIVER
            \ttristate "Driver"
            \tselect TEST_INTERMEDIATE
            config TEST_INTERMEDIATE
            \ttristate "Intermediate"
            \tselect TEST_REQUIRED
            config TEST_REQUIRED
            \ttristate "Required"
        ''')
        with (self.tree / "Makefile").open("a") as stream:
            stream.write("obj-$(CONFIG_TEST_DRIVER) += test-driver.o\n")
        self.set_baseline_symbols(MODULES="y", BLK_DEV_INITRD="n", TEST_DRIVER="n", TEST_REQUIRED="n")
        env = self.make_host_root()
        host = Path(env["KC_HOST_ROOT"])
        device = host / "sys/bus/platform/devices/test/driver"
        device.mkdir(parents=True)
        (device / "module").symlink_to("/sys/module/test_driver")
        for override in ("--disable-symbols=TEST_REQUIRED", "--protected-config-symbols=TEST_REQUIRED", "--module-symbols=TEST_REQUIRED"):
            self.config.write_text(self.baseline)
            self.assert_success(self.run_script("--strict", "--native-arch", override, env=env))
            self.assert_symbol("TEST_DRIVER", "n")
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--native-arch", env=env))
        self.assert_symbol("TEST_DRIVER", "y")
        self.set_baseline_symbols(TEST_DRIVER="m")
        self.assert_success(self.run_script("--strict", "--native-arch", env=env))
        self.assert_symbol("TEST_DRIVER", "m")
        before = self.config.read_bytes()
        backups = set(self.tree.glob(".config.bak.*"))
        result = self.run_script("--native-arch", env=env | {"FAIL_MAKE": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(set(self.tree.glob(".config.bak.*")), backups)

    def test_native_arch_reuses_detectors_without_pruning_absent_hardware(self):
        self.add_symbols("EFI", "EFI_STUB", "EFI_PARTITION", "EFIVAR_FS", "DMADEVICES", "NUMA", "TCG_TPM")
        self.set_baseline_symbols(EFI="y", DMADEVICES="y", NUMA="y", TCG_TPM="y")
        env = self.make_host_root()
        self.assert_success(self.run_script("--strict", "--native-arch", env=env))
        for sym in ("EFI", "DMADEVICES", "NUMA", "TCG_TPM"):
            self.assert_symbol(sym, "y")
        self.set_baseline_symbols(EFI="n", DMADEVICES="n", NUMA="n", TCG_TPM="n")
        env = self.make_host_root(efi=True, nodes=("node0", "node1"), dma=("dma0",))
        tpm = Path(env["KC_HOST_ROOT"]) / "sys/class/tpm/tpm0"
        tpm.mkdir(parents=True)
        (tpm / "tpm_version_major").write_text("2\n")
        self.assert_success(self.run_script("--strict", "--native-arch", env=env))
        for sym in ("EFI", "DMADEVICES", "NUMA", "TCG_TPM"):
            self.assert_symbol(sym, "y")
        self.config.write_text(self.baseline)
        result = self.run_script("--strict", "--native-arch", env=env | {"DROP_SYMBOLS": "NUMA"})
        self.assert_success(result)
        self.assert_symbol("NUMA", "n")
        self.assertIn("CONFIG_NUMA unavailable with current dependencies", result.stdout)
        self.config.write_text(self.baseline)
        self.assert_success(self.run_script("--strict", "--uefi-support=none", "--native-arch", "--tpm-support=none",
                                            env=env | {"DMA_ENGINE_SUPPORT": "none", "NUMA_SUPPORT": "none"}))
        for sym in ("EFI", "DMADEVICES", "NUMA", "TCG_TPM"):
            self.assert_symbol(sym, "n")

    def test_native_cpu_features_require_cpu_and_driver_evidence(self):
        self.add_symbols("X86", "X86_64", "X86_LOCAL_APIC", "HYPERVISOR_GUEST", "IOMMU_SUPPORT", "IRQ_REMAP",
                         "X86_MCE", "X86_MCE_INTEL", "X86_FRED", "CPU_FREQ", "CPU_IDLE", "INTEL_IDLE",
                         "X86_INTEL_PSTATE", "X86_AMD_PSTATE", "X86_ACPI_CPUFREQ")
        self.add_kconfig('''
            config X86_X2APIC
            \tbool "x2APIC"
            \tdepends on X86_64 && X86_LOCAL_APIC && (IRQ_REMAP || HYPERVISOR_GUEST)
        ''')
        env = self.make_host_root(cpuinfo="processor : 0\nvendor_id : GenuineIntel\nflags : x2apic fred mce mca\n")
        freq = self.root / "host/sys/devices/system/cpu/cpufreq/policy0"
        freq.mkdir(parents=True)
        (freq / "scaling_driver").write_text("intel_pstate\n")
        idle = self.root / "host/sys/devices/system/cpu/cpuidle"
        idle.mkdir()
        (idle / "current_driver").write_text("intel_idle\n")
        self.set_baseline_symbols(X86="y", X86_64="y", X86_LOCAL_APIC="y", IRQ_REMAP="y", IOMMU_SUPPORT="y",
                                  HYPERVISOR_GUEST="n", X86_X2APIC="n", X86_FRED="n", X86_MCE="n",
                                  X86_MCE_INTEL="n", CPU_FREQ="n", CPU_IDLE="n", INTEL_IDLE="n", X86_INTEL_PSTATE="n")
        original = self.config.read_bytes()
        args = ("--strict", "--native-cpu=auto")
        self.assert_success(self.run_script(*args, env=env))
        for sym in ("X86_X2APIC", "X86_FRED", "X86_MCE", "X86_MCE_INTEL", "CPU_FREQ", "CPU_IDLE", "INTEL_IDLE", "X86_INTEL_PSTATE"):
            self.assert_symbol(sym, "y")
        self.config.write_bytes(original)
        self.assert_success(self.run_script(*args, "--disable-symbols=X86_X2APIC,X86_FRED,CPU_FREQ,CPU_IDLE,X86_MCE", env=env))
        for sym in ("X86_X2APIC", "X86_FRED", "CPU_FREQ", "CPU_IDLE", "X86_MCE", "X86_MCE_INTEL", "INTEL_IDLE", "X86_INTEL_PSTATE"):
            self.assert_symbol(sym, "n")
        self.config.write_bytes(original)
        (self.root / "host/proc/cpuinfo").write_text("processor : 0\nflags : aes\n")
        (freq / "scaling_driver").unlink()
        (idle / "current_driver").unlink()
        self.assert_success(self.run_script(*args, "--optimization-profile=server", env=env))
        for sym in ("X86_X2APIC", "X86_FRED", "X86_MCE", "INTEL_IDLE", "X86_INTEL_PSTATE"):
            self.assert_symbol(sym, "n")

    def test_native_compile_mode_keeps_toolchain_validation(self):
        self.add_symbols("X86", "CC_HAS_MARCH_NATIVE")
        self.add_kconfig('''
            config X86_NATIVE_CPU
            \tbool "Build for the local CPU"
            \tdepends on CC_HAS_MARCH_NATIVE
        ''')
        env = self.make_host_root()
        self.set_baseline_symbols(X86="y", CC_HAS_MARCH_NATIVE="n", X86_NATIVE_CPU="n")
        result = self.run_script("--strict", "--native-cpu=on", env=env | {"DROP_SYMBOLS": "X86_NATIVE_CPU"})
        self.assertNotEqual(result.returncode, 0)
        self.assert_untouched()
        self.set_baseline_symbols(CC_HAS_MARCH_NATIVE="y")
        result = self.run_script("--strict", "--native-cpu=on", env=env | {"CROSS_COMPILE": "x86_64-linux-gnu-"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot infer the target CPU", result.stderr)
        self.assert_untouched()

    def test_uclamp_groups_follow_profile_dependencies_and_overrides(self):
        self.add_symbols("CPU_FREQ", "CPU_FREQ_GOV_SCHEDUTIL", "CGROUP_SCHED")
        self.add_kconfig('''
            config UCLAMP_TASK
            \tbool "Utilization clamping"
            \tdepends on CPU_FREQ_GOV_SCHEDUTIL
            config UCLAMP_TASK_GROUP
            \tbool "Utilization clamping per group of tasks"
            \tdepends on CGROUP_SCHED && UCLAMP_TASK
        ''')
        cases = (
            (("--optimization-profile=desktop",), {}, "y"),
            (("--uclamp=on",), {}, "y"),
            (("--uclamp=keep",), {}, "n"),
            (("--optimization-profile=server",), {}, "n"),
            (("--optimization-profile=desktop", "--uclamp=off"), {}, "n"),
            (("--optimization-profile=desktop", "--disable-symbols=UCLAMP_TASK_GROUP"), {}, "n"),
            (("--optimization-profile=desktop", "--protected-config-symbols=UCLAMP_TASK_GROUP"), {}, "n"),
            (("--optimization-profile=desktop", "--disable-symbols=CGROUP_SCHED"), {}, "n"),
            (("--uclamp=on",), {"CGROUPS": "n"}, "n"),
            (("--uclamp=on",), {"CGROUP_SCHED": "n"}, "n"),
        )
        for args, overrides, expected in cases:
            with self.subTest(args=args, overrides=overrides):
                self.set_baseline_symbols(**dict(CGROUPS="y", CGROUP_SCHED="y", CPU_FREQ="y",
                                                UCLAMP_TASK="n", UCLAMP_TASK_GROUP="n") | overrides)
                self.assert_success(self.run_script("--strict", *args))
                self.assert_symbol("UCLAMP_TASK_GROUP", expected)

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


    # --- host-root injection and detector regressions ---

    INTEL_CPUINFO = "processor\t: 0\nvendor_id\t: GenuineIntel\ncpu family\t: 6\nmodel\t\t: 58\n\n"
    AMD_CPUINFO = "processor\t: 0\nvendor_id\t: AuthenticAMD\ncpu family\t: 25\nmodel\t\t: 33\n\n"

    def test_host_root_redirects_auto_detectors(self):
        self.add_symbols("EFI", "EFI_STUB", "EFI_PARTITION", "DMADEVICES", "NUMA")
        self.set_baseline_symbols(EFI="n", DMADEVICES="y", NUMA="n")
        env = self.make_host_root(efi=True, nodes=("node0", "node1"))
        result = self.run_script("--uefi-support=auto", "--dma-engine-support=auto",
                                 "--numa-support=auto", env=env)
        self.assert_success(result)
        self.assert_symbol("EFI", "y")
        self.assert_symbol("DMADEVICES", "n")
        self.assert_symbol("NUMA", "y")
        # A second synthetic host without those devices prunes them again, whatever the real host has.
        shutil.rmtree(self.root / "host")
        env = self.make_host_root(dma=("dma0chan0",))
        self.assert_success(self.run_script("--uefi-support=auto", "--dma-engine-support=auto",
                                            "--numa-support=auto", env=env))
        self.assert_symbol("EFI", "n")
        self.assert_symbol("DMADEVICES", "y")
        self.assert_symbol("NUMA", "n")

    def test_iommu_auto_follows_host_iommu_evidence(self):
        self.add_symbols("X86", "IOMMU_SUPPORT", "IOMMUFD", "IOMMU_DMA", "INTEL_IOMMU", "AMD_IOMMU")
        self.set_baseline_symbols(X86="y", IOMMU_SUPPORT="y", INTEL_IOMMU="y", AMD_IOMMU="n")
        # A Celeron without VT-d: no /sys/class/iommu entries and no ACPI DMAR table.
        env = self.make_host_root(cpuinfo=self.INTEL_CPUINFO)
        result = self.run_script("--strict", "--iommu-support=auto", env=env)
        self.assert_success(result)
        self.assertIn("Applying IOMMU support profile: off", result.stdout)
        self.assert_symbol("IOMMU_SUPPORT", "n")
        self.assert_symbol("INTEL_IOMMU", "n")
        # Firmware advertises VT-d even though the running kernel has no IOMMU driver.
        self.set_baseline_symbols(IOMMU_SUPPORT="n", INTEL_IOMMU="n")
        env = self.make_host_root(cpuinfo=self.INTEL_CPUINFO, acpi_tables=("DMAR",))
        self.assert_success(self.run_script("--strict", "--iommu-support=auto", env=env))
        self.assert_symbol("IOMMU_SUPPORT", "y")
        self.assert_symbol("INTEL_IOMMU", "y")
        self.assert_symbol("AMD_IOMMU", "n")
        # An active AMD IOMMU unit.
        shutil.rmtree(self.root / "host")
        self.set_baseline_symbols(IOMMU_SUPPORT="n", INTEL_IOMMU="n")
        env = self.make_host_root(cpuinfo=self.AMD_CPUINFO, iommu=("ivhd0",))
        self.assert_success(self.run_script("--strict", "--iommu-support=auto", env=env))
        self.assert_symbol("AMD_IOMMU", "y")
        self.assert_symbol("INTEL_IOMMU", "n")
        # Explicit on still wins without any evidence.
        shutil.rmtree(self.root / "host")
        self.set_baseline_symbols(IOMMU_SUPPORT="n", INTEL_IOMMU="n", AMD_IOMMU="n")
        env = self.make_host_root(cpuinfo=self.INTEL_CPUINFO)
        self.assert_success(self.run_script("--strict", "--iommu-support=on", env=env))
        self.assert_symbol("INTEL_IOMMU", "y")

    def test_generic_arch_kconfig_symbols_are_toggles(self):
        self.add_kconfig('''
            config ARCH_SUPPORTS_SCHED_MC
            \tbool

            config SCHED_MC
            \tbool "Multi-Core Cache (MC) scheduler support"
            \tdepends on ARCH_SUPPORTS_SCHED_MC
        ''', path="arch/Kconfig")
        self.add_kconfig('''
            config SCHED_MC_PRIO
            \tbool "CPU core priorities scheduler support"
            \tdepends on SCHED_MC
        ''', path="arch/x86/Kconfig")
        self.add_kconfig('''
            config ARM64_ONLY
            \tbool "Only visible on arm64"
        ''', path="arch/arm64/Kconfig")
        self.set_baseline_symbols(ARCH_SUPPORTS_SCHED_MC="y", SCHED_MC="n", SCHED_MC_PRIO="n")
        result = self.run_script("--strict", "--optimization-profile=server", env={"ARCH": "x86"})
        self.assert_success(result)
        self.assertNotIn("Skipping non-toggle symbol: CONFIG_SCHED_MC", result.stdout)
        self.assert_symbol("SCHED_MC", "y")
        self.assert_symbol("SCHED_MC_PRIO", "y")
        # Other architectures stay excluded from the metadata.
        result = self.run_script("--disable-symbols=ARM64_ONLY", env={"ARCH": "x86"})
        self.assert_success(result)
        self.assertIn("CONFIG_ARM64_ONLY is not a bool/tristate symbol", result.stderr)

    def test_protected_disabled_symbol_may_lose_its_prompt(self):
        self.set_baseline_symbols(KASAN="n")
        result = self.run_script("--strict", "--sched-cache=on", "--protected-config-symbols=KASAN",
                                 env={"REMOVE_SYMBOLS": "KASAN"})
        self.assert_success(result)
        self.assertNotIn("Protected symbol changed", result.stderr)
        self.assertNotIn("CONFIG_KASAN", self.config.read_text())

    def test_protected_absent_symbol_may_gain_disabled_entry(self):
        # CGROUP_PIDS is defined in Kconfig but absent from the baseline config.
        result = self.run_script("--strict", "--sched-cache=on",
                                 "--protected-config-symbols=CGROUP_PIDS",
                                 env={"DROP_SYMBOLS": "CGROUP_PIDS"})
        self.assert_success(result)
        self.assertNotIn("Protected symbol changed", result.stderr)

    def test_unmet_request_lists_failed_dependencies(self):
        self.add_symbols("HAS_IOMEM", "EDAC_SUPPORT", "RAS")
        self.add_kconfig('''
            config EDAC
            \ttristate "EDAC (Error Detection And Correction) reporting"
            \tdepends on HAS_IOMEM && EDAC_SUPPORT && RAS
        ''')
        self.set_baseline_symbols(HAS_IOMEM="y", EDAC_SUPPORT="y", RAS="n")
        result = self.run_script("--strict", "--enable-symbols=EDAC", env={"DROP_SYMBOLS": "EDAC"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_EDAC requested=y final=n", result.stderr)
        self.assertIn("depends on HAS_IOMEM && EDAC_SUPPORT && RAS; unmet: RAS=n", result.stderr)
        self.assertNotIn("HAS_IOMEM=", result.stderr)
        self.assert_untouched()

    def test_unmet_dependency_hint_handles_alternatives_negation_and_comparisons(self):
        self.add_symbols("A", "B", "C", "FOO", "BAR")
        self.add_kconfig('''
            config ALT
            \tbool "alternatives"
            \tdepends on (A || B) && !C

            config CMP
            \tbool "comparison"
            \tdepends on FOO=y && BAR
        ''')
        self.set_baseline_symbols(A="n", B="n", C="y", FOO="n", BAR="n")
        result = self.run_script("--enable-symbols=ALT,CMP", env={"DROP_SYMBOLS": "ALT CMP"})
        self.assert_success(result)
        self.assertIn("unmet: A=n, B=n, C=y", result.stderr)
        # The comparison FOO=y is not evaluated; only the bare symbol term is reported.
        self.assertIn("depends on FOO=y && BAR; unmet: BAR=n)", result.stderr)
        # A satisfied alternative removes the hint for that line.
        self.set_baseline_symbols(B="y", C="n")
        result = self.run_script("--enable-symbols=ALT", env={"DROP_SYMBOLS": "ALT"})
        self.assert_success(result)
        self.assertIn("CONFIG_ALT requested=y final=n", result.stderr)
        self.assertNotIn("unmet:", result.stderr)

    def test_apply_prints_changes_before_backup(self):
        result = self.run_script("--sched-cache=on")
        self.assert_success(result)
        self.assertIn("CONFIG_SCHED_CACHE: n -> y", result.stdout)
        self.assertLess(result.stdout.index("CONFIG_SCHED_CACHE: n -> y"), result.stdout.index("Backup:"))
        result = self.run_script("--sched-cache=on")
        self.assert_success(result)
        self.assertEqual(result.stdout.count("No changes."), 1)


    # --- platform drivers (PLATFORM_DRIVERS=auto) ---

    def test_platform_drivers_ipmi_power_meter_and_hpe_as_modules(self):
        self.add_symbols("IPMI_HANDLER", "IPMI_DEVICE_INTERFACE", "IPMI_SI", "ACPI_IPMI",
                         "SENSORS_ACPI_POWER", "HP_ILO", "HP_WATCHDOG", "HWMON", kind="tristate")
        self.add_symbols("MODULES", "WATCHDOG", "PCI")
        self.set_baseline_symbols(MODULES="y", PCI="y", HWMON="y")
        env = self.make_host_root(sys_vendor="HPE", acpi=("IPI0001:00", "ACPI000D:00"),
                                  pci=[("0000:01:00.2", "0x103c", "0x3306", "0x088000")])
        result = self.run_script("--dry-run", "--strict", "--platform-drivers=auto", env=env)
        self.assert_success(result)
        self.assertIn("IPI0001:00 -> CONFIG_IPMI_SI=m", result.stdout)
        self.assertIn("ACPI000D:00 -> CONFIG_SENSORS_ACPI_POWER=m", result.stdout)
        self.assertIn("sys_vendor=HPE (PCI 0000:01:00.2 103c:3306) -> CONFIG_HP_WATCHDOG=m", result.stdout)
        self.assertIn("CONFIG_ACPI_IPMI: n -> m", result.stdout)
        self.assertIn("CONFIG_WATCHDOG: n -> y", result.stdout)
        self.assertIn("Validation passed", result.stdout)
        self.assert_untouched()
        result = self.run_script("--dry-run", "--strict", "--native-arch", "--disable-symbols=IPMI_SI", env=env)
        self.assert_success(result)
        self.assertIn("CONFIG_ACPI_IPMI: n -> m", result.stdout)
        self.assertIn("CONFIG_HP_WATCHDOG: n -> m", result.stdout)
        self.assertNotIn("CONFIG_IPMI_SI: n -> m", result.stdout)
        self.assert_untouched()
        result = self.run_script("--platform-drivers=auto", "--protected-config-symbols=IPMI_SI", env=env)
        self.assert_success(result)
        self.assertIn("Skipping protected symbol: CONFIG_IPMI_SI", result.stdout)
        # Parents are enabled first (built in unless the baseline already had them as modules).
        self.assert_symbol("IPMI_HANDLER", "y")
        self.assert_symbol("HP_ILO", "m")
        self.assertNotIn("CONFIG_IPMI_SI=", self.config.read_text())

    def test_platform_drivers_intel_edac_pch_and_missing_symbols(self):
        self.add_symbols("RAS", "EDAC", "X86_MCE", "X86_MCE_INTEL", "X86", "X86_64", "I2C", "HWMON",
                         "WATCHDOG", "MODULES")
        self.add_symbols("I2C_I801", "SENSORS_CORETEMP", "ITCO_WDT", kind="tristate")
        self.set_baseline_symbols(X86="y", X86_64="y", MODULES="y")
        env = self.make_host_root(cpuinfo=self.INTEL_CPUINFO,
                                  pci=[("0000:00:00.0", "0x8086", "0x1918", "0x060000"),
                                       ("0000:00:1f.3", "0x8086", "0xa123", "0x0c0500"),
                                       ("0000:00:1f.0", "0x8086", "0xa145", "0x060100")])
        result = self.run_script("--strict", "--platform-drivers=auto", env=env)
        self.assert_success(result)
        self.assertIn("PCI 0000:00:00.0 8086:1918 (host bridge) -> CONFIG_EDAC_IE31200 not available in this tree",
                      result.stdout)
        self.assert_symbol("RAS", "y")
        self.assert_symbol("EDAC", "y")
        self.assert_symbol("X86_MCE", "y")
        self.assert_symbol("I2C_I801", "m")
        self.assert_symbol("SENSORS_CORETEMP", "m")
        self.assert_symbol("ITCO_WDT", "m")
        # Two olddefconfig passes: the parent refresh and the final one.
        calls = (self.root / "make.log").read_text().splitlines()
        self.assertEqual(len(calls), 2)
        self.set_baseline_symbols(MODULES="n")
        self.assert_success(self.run_script("--strict", "--platform-drivers=auto", env=env))
        self.assert_symbol("I2C_I801", "y")
        self.assert_symbol("ITCO_WDT", "y")

    def test_platform_drivers_amd_dell_vendor_filter_default_and_invalid_value(self):
        self.add_symbols("RAS", "EDAC", "X86_MCE", "X86_MCE_AMD", "X86", "HWMON", "MODULES")
        self.add_symbols("EDAC_AMD64", "EDAC_DECODE_MCE", "SENSORS_K10TEMP", "DCDBAS", "DELL_SMBIOS",
                         kind="tristate")
        self.set_baseline_symbols(X86="y", MODULES="y")
        env = self.make_host_root(cpuinfo=self.AMD_CPUINFO, sys_vendor="Dell Inc.", acpi=("IPI0001:00",))
        result = self.run_script("--strict", "--platform-drivers=auto", env=env)
        self.assert_success(result)
        self.assertIn("cpuinfo family 0x19 (AMD) -> CONFIG_EDAC_AMD64=m", result.stdout)
        self.assertIn("IPI0001:00 -> CONFIG_IPMI_SI not available in this tree", result.stdout)
        for sym in ("EDAC_AMD64", "EDAC_DECODE_MCE", "SENSORS_K10TEMP", "DCDBAS", "DELL_SMBIOS"):
            self.assert_symbol(sym, "m")
        # The CPU vendor follows an explicit --cpu-vendor-filter, so the phases never contradict.
        self.set_baseline_symbols(EDAC_AMD64="n", EDAC_DECODE_MCE="n", SENSORS_K10TEMP="n")
        result = self.run_script("--platform-drivers=auto", "--cpu-vendor-filter=intel", env=env)
        self.assert_success(result)
        self.assertNotIn("CONFIG_EDAC_AMD64=m", result.stdout)
        self.assert_symbol("EDAC_AMD64", "n")
        # Default none is inert; an invalid value is rejected before anything runs.
        self.set_baseline_symbols()
        result = self.run_script("--dry-run", env=env)
        self.assert_success(result)
        self.assertNotIn("platform drivers profile", result.stdout)
        backups = sorted(self.tree.glob(".config.bak.*"))
        result = self.run_script("--platform-drivers=maybe", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid PLATFORM_DRIVERS", result.stderr)
        self.assertEqual(self.config.read_text(), self.baseline)
        self.assertEqual(sorted(self.tree.glob(".config.bak.*")), backups)

    def test_platform_drivers_without_evidence_is_inert(self):
        env = self.make_host_root(cpuinfo=self.INTEL_CPUINFO)
        result = self.run_script("--dry-run", "--strict", "--platform-drivers=auto", env=env)
        self.assert_success(result)
        self.assertIn("no platform driver evidence found on this host", result.stdout)
        self.assertIn("No changes.", result.stdout)


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
