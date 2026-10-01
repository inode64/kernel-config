"""Opt-in checks using a complete kernel tree and its real olddefconfig target.

Run with KERNEL_TEST_SRCDIR=/path/to/linux python3 -m unittest discover -s tests -v.
Build tools, generated includes, and the baseline config are copied to a temporary
tree. Other source paths are linked read-only by convention, never built/installed.
"""

import hashlib
import lzma
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from test_kernel_config import SCRIPT
from test_initramfs_check import cpio


@unittest.skipUnless(os.environ.get("KERNEL_TEST_SRCDIR"), "set KERNEL_TEST_SRCDIR for real Kconfig tests")
class KernelIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(os.environ["KERNEL_TEST_SRCDIR"]).resolve()
        cls.original = (cls.source / ".config").read_bytes()
        cls.temp = tempfile.TemporaryDirectory(prefix="kernel-config-integration-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.tree = Path(cls.temp.name) / "linux"
        cls.tree.mkdir()
        for child in cls.source.iterdir():
            if child.name.startswith("."):
                continue
            target = cls.tree / child.name
            if child.name in ("scripts", "include"):
                shutil.copytree(child, target, symlinks=True)
            else:
                target.symlink_to(child, target_is_directory=child.is_dir())
        cls.config = cls.tree / ".config"
        cls.env = os.environ.copy()
        for name in re.findall(r"^init_tunable ([A-Z_]+) ", SCRIPT.read_text(), re.M):
            cls.env.pop(name, None)
        for name in ("KBUILD_OUTPUT", "KSRCDIR", "CONFIG_FILE", "ALL_OPTIMIZATIONS"):
            cls.env.pop(name, None)
        cls.env["INITRAMFS_GENERATOR"] = "none"

    def setUp(self):
        self.config.write_bytes(self.original)

    def run_script(self, *args):
        result = subprocess.run(["bash", str(SCRIPT), str(self.tree), *args],
                                env=self.env, capture_output=True, text=True, timeout=180)
        self.assertEqual((self.source / ".config").read_bytes(), self.original)
        self.assertFalse(list(self.tree.glob(".kernel-config.*")))
        return result

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout[-8000:] + result.stderr[-8000:])

    def assert_values(self, **values):
        config = dict(re.findall(r"^CONFIG_(\w+)=(.*)$", self.config.read_text(), re.M))
        for symbol, value in values.items():
            self.assertEqual(config.get(symbol, "n"), value, symbol)

    def prepare_baseline(self, **values):
        for symbol, value in values.items():
            subprocess.run([str(self.tree / "scripts/config"), "--file", str(self.config),
                            "--enable" if value == "y" else "--module" if value == "m" else "--disable", symbol],
                           check=True, env=self.env, capture_output=True)
        result = subprocess.run(["make", "-C", str(self.tree), "olddefconfig"],
                                env=self.env, capture_output=True, text=True, timeout=180)
        self.assert_success(result)
        self.assert_values(**values)

    def test_real_extended_controls_and_idempotence(self):
        args = ("--strict", "--preemption=lazy", "--preempt-dynamic=on", "--tick-mode=full",
                "--thp=madvise", "--lru-gen=on", "--zswap=on", "--zswap-compressor=zstd",
                "--zram=module", "--zram-compressor=lz4", "--numa-balancing=on",
                "--kmalloc-partition=random", "--tcp-congestion=bbr", "--io-uring=on")
        self.assert_success(self.run_script(*args))
        self.assert_values(PREEMPT_LAZY="y", PREEMPT="n", PREEMPT_RT="n", PREEMPT_DYNAMIC="y",
                           NO_HZ_FULL="y", NO_HZ_IDLE="n", HZ_PERIODIC="n",
                           TRANSPARENT_HUGEPAGE="y", TRANSPARENT_HUGEPAGE_MADVISE="y",
                           LRU_GEN="y", LRU_GEN_ENABLED="y", ZSWAP="y", ZSWAP_DEFAULT_ON="y",
                           ZSWAP_COMPRESSOR_DEFAULT='"zstd"', ZRAM="m", ZRAM_BACKEND_LZ4="y",
                           ZRAM_DEF_COMP='"lz4"', NUMA="y", NUMA_MIGRATION="y",
                           NUMA_BALANCING="y", NUMA_BALANCING_DEFAULT_ENABLED="y",
                           KMALLOC_PARTITION_CACHES="y", KMALLOC_PARTITION_RANDOM="y",
                           TCP_CONG_BBR="y", DEFAULT_TCP_CONG='"bbr"', NET_SCH_FQ="y", IO_URING="y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_rt_model_and_io_uring_off(self):
        self.prepare_baseline(EXPERT="y")
        self.assert_success(self.run_script("--strict", "--preemption=rt", "--preempt-dynamic=off",
                                            "--tick-mode=idle", "--thp=off", "--numa-balancing=off",
                                            "--io-uring=off"))
        self.assert_values(PREEMPT_RT="y", PREEMPT="y", PREEMPT_LAZY="n", PREEMPT_DYNAMIC="n",
                           NO_HZ_IDLE="y", NO_HZ_FULL="n", TRANSPARENT_HUGEPAGE="n",
                           NUMA_BALANCING="n", IO_URING="n")

    def test_real_desktop_applications_keep_gpu_and_hotplug_modules(self):
        self.prepare_baseline(DRM="y", DRM_AMDGPU="m", FUSE_FS="m",
                              SND_USB_AUDIO="m", USB_VIDEO_CLASS="m")
        args = ("--strict", "--video-support=amd",
                "--applications=desktop,multimedia,rocm,nebula,warp")
        self.assert_success(self.run_script(*args))
        self.assert_values(DRM_AMDGPU="m", FUSE_FS="m", SND_USB_AUDIO="m",
                           USB_VIDEO_CLASS="m", HSA_AMD="y", HSA_AMD_SVM="y",
                           HIDRAW="y", INOTIFY_USER="y", SECCOMP_FILTER="y", TUN="y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_desktop_applications_restore_disabled_parents(self):
        self.prepare_baseline(DRM="n", SOUND="n", MEDIA_SUPPORT="n", USB="n",
                              HID="n", FUSE_FS="n", TUN="n")
        self.assert_success(self.run_script("--strict", "--applications=desktop,multimedia,rocm,nebula"))
        self.assert_values(DRM_AMDGPU="y", HSA_AMD="y", HSA_AMD_SVM="y", FUSE_FS="y",
                           SND_USB_AUDIO="y", USB_VIDEO_CLASS="y", HIDRAW="y",
                           SECCOMP_FILTER="y", TUN="y")

    def test_real_amd_host_with_qemu_and_nftables(self):
        self.prepare_baseline(KVM="m", KVM_AMD="m", NF_TABLES="m", NFT_NAT="m",
                              VHOST_NET="m", VHOST_VSOCK="n", VHOST_SCSI="n", VHOST_VDPA="n")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=amd",
                                            "--host-type=baremetal", "--applications=qemu,firewalld"))
        self.assert_values(KVM_AMD="m", VHOST_NET="m", VHOST_VSOCK="y", VHOST="y",
                           VHOST_IOTLB="y", NF_TABLES="m", NFT_NAT="m")

    def test_real_unavailable_preemption_is_rejected_without_commit(self):
        # Lazy-capable targets in 7.2 hide NONE and VOLUNTARY from the model choice.
        if "CONFIG_ARCH_HAS_PREEMPT_LAZY=y" not in self.config.read_text():
            self.skipTest("requires a lazy-preemption target")
        result = self.run_script("--strict", "--preemption=voluntary")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CONFIG_PREEMPT_VOLUNTARY", result.stdout + result.stderr)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_real_typed_kmalloc_respects_compiler_capability(self):
        self.prepare_baseline(KMALLOC_PARTITION_CACHES="y", KMALLOC_PARTITION_RANDOM="y")
        before = self.config.read_bytes()
        has_alloc_token = "CONFIG_CC_HAS_ALLOC_TOKEN=y" in before.decode().splitlines()
        result = self.run_script("--strict", "--kmalloc-partition=typed")
        if has_alloc_token:
            self.assert_success(result)
            self.assert_values(KMALLOC_PARTITION_TYPED="y", KMALLOC_PARTITION_RANDOM="n")
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("CONFIG_KMALLOC_PARTITION_TYPED", result.stdout + result.stderr)
            self.assertEqual(self.config.read_bytes(), before)

    def test_real_runtime_verification_pruning(self):
        self.prepare_baseline(FTRACE="y", RV="y", RV_REACTORS="y", RV_REACT_PRINTK="y")
        self.assert_success(self.run_script("--strict", "--prune-runtime-verification"))
        self.assert_values(RV="n", RV_REACTORS="n", RV_REACT_PRINTK="n")

    def test_real_pruning_respects_types_prompts_and_crypto_abi(self):
        before = dict(re.findall(r"^CONFIG_(\w+)=(.*)$", self.config.read_text(), re.M))
        self.assert_success(self.run_script("--strict", "--prune-debug-trace", "--prune-selftest",
                                            "--prune-legacy", "--prune-runtime-verification"))
        for symbol in ["CRYPTO_USER_API", "CRYPTO_USER_API_HASH", "CRYPTO_USER_API_SKCIPHER",
                       "CRYPTO_USER_API_AEAD", "CRYPTO_USER_API_RNG"]:
            self.assert_values(**{symbol: before.get(symbol, "n")})

    def test_real_mixed_case_audio_removal_preserves_strix_and_active_audio(self):
        disabled = ["SND_SOC_AMD_ACP3x", "SND_SOC_AMD_RENOIR", "SND_SOC_AMD_ACP5x",
                    "SND_SOC_AMD_ACP6x", "SND_SOC_AMD_ACP7X", "SND_SOC_SOF_AMD_RENOIR",
                    "SND_SOC_SOF_AMD_VANGOGH", "SND_SOC_SOF_AMD_REMBRANDT", "SND_SOC_SOF_AMD_ACP63"]
        before = dict(re.findall(r"^CONFIG_(\w+)=(.*)$", self.config.read_text(), re.M))
        self.assert_success(self.run_script("--strict", "--disable-symbols=" + ",".join(disabled)))
        self.assert_values(**dict.fromkeys(disabled, "n"))
        for symbol in ["SND_HDA_INTEL", "SND_USB_AUDIO", "SND_SOC_AMD_PS", "SND_SOC_SOF_AMD_ACP70"]:
            self.assert_values(**{symbol: before.get(symbol, "n")})

    def test_real_olddefconfig_dry_run(self):
        result = self.run_script("--dry-run", "--strict", "--sched-cache=off")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertIn("Dry-run complete", result.stdout)

    def test_real_preset_preserves_boot_decoders(self):
        result = self.run_script("--all-optimizations")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        after = self.config.read_text().splitlines()
        for line in self.original.decode().splitlines():
            if line.endswith("=y") and line.startswith(("CONFIG_RD_", "CONFIG_FW_LOADER_COMPRESS")):
                self.assertIn(line, after)

    def test_real_olddefconfig_commit_and_idempotence(self):
        result = self.run_script("--strict", "--sched-cache=off", "--initrd-compression=zstd")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        contents = self.config.read_text()
        self.assertIn("# CONFIG_SCHED_CACHE is not set", contents)
        self.assertIn("CONFIG_RD_ZSTD=y", contents)
        before = hashlib.sha256(self.config.read_bytes()).digest()
        result = self.run_script("--strict", "--sched-cache=off", "--initrd-compression=zstd")
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-5000:])
        self.assertEqual(hashlib.sha256(self.config.read_bytes()).digest(), before)

    def test_real_initramfs_auto_and_post_kconfig_validation(self):
        image = Path(self.temp.name) / "early-and-xz.img"
        image.write_bytes(cpio(b"microcode") + lzma.compress(cpio(), check=lzma.CHECK_CRC32))
        self.prepare_baseline(RD_XZ="n")
        args = ("--strict", "--initramfs-generator=none", "--initramfs-image", str(image))
        self.assert_success(self.run_script(*args, "--initrd-compression=auto"))
        self.assert_values(RD_XZ="y", BLK_DEV_INITRD="y")
        baseline = self.config.read_bytes()
        result = self.run_script(*args, "--disable-symbols=RD_XZ")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_bytes(), baseline)

    def test_real_uclamp_autogroup_and_desktop_policy(self):
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop", "--uclamp=off", "--autogroup=off"))
        self.assert_values(UCLAMP_TASK="n", SCHED_AUTOGROUP="n", TRANSPARENT_HUGEPAGE_MADVISE="y",
                           TRANSPARENT_HUGEPAGE_ALWAYS="n")
        self.assert_success(self.run_script("--strict", "--uclamp=on", "--autogroup=on"))
        self.assert_values(UCLAMP_TASK="y", SCHED_AUTOGROUP="y", CPU_FREQ_GOV_SCHEDUTIL="y")

    def test_real_risk_controls_nfs_profile_and_explicit_override(self):
        self.prepare_baseline(MODULE_FORCE_UNLOAD="y", NFSD_FLEXFILELAYOUT="y", NFS_DISABLE_UDP_SUPPORT="n")
        self.assert_success(self.run_script("--strict", "--prune-dangerous", "--applications=nfs-server",
                                            "--module-force-load=off", "--module-force-unload=off",
                                            "--nfs-udp=off", "--obsolete-crypto=off"))
        self.assert_values(MODULE_FORCE_LOAD="n", MODULE_FORCE_UNLOAD="n", NFSD_FLEXFILELAYOUT="n",
                           NFS_DISABLE_UDP_SUPPORT="y", CRYPTO_USER_API_ENABLE_OBSOLETE="n",
                           NFSD="y", NFSD_V4="y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script("--strict", "--module-force-load=off", "--module-force-unload=off",
                                            "--nfs-udp=off", "--obsolete-crypto=off"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script("--strict", "--prune-dangerous", "--module-force-unload=on",
                                            "--nfs-udp=on", "--enable-symbols=NFSD_FLEXFILELAYOUT"))
        self.assert_values(MODULE_FORCE_UNLOAD="y", NFSD_FLEXFILELAYOUT="y", NFS_DISABLE_UDP_SUPPORT="n")

    def test_real_quoted_deprecation_and_alias_migration(self):
        self.prepare_baseline(NETFILTER_XT_MATCH_DCCP="m", HID_THINGM="m", HID_LED="m")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_values(NETFILTER_XT_MATCH_DCCP="n", HID_THINGM="n", HID_LED="m", DRM_AMD_DC="y")

    def test_real_help_only_legacy_with_expert_enabled(self):
        self.prepare_baseline(EXPERT="y", SGETMASK_SYSCALL="y", GPIO_CDEV="y", GPIO_CDEV_V1="y",
                              SND_HDA_CTL_DEV_ID="y")
        self.assert_success(self.run_script("--strict", "--prune-legacy"))
        self.assert_values(SGETMASK_SYSCALL="n", GPIO_CDEV_V1="n", GPIO_CDEV="y", SND_HDA_CTL_DEV_ID="n")

    def test_real_server_tick_overrides_without_rcu_expert(self):
        for mode, symbol in (("idle", "NO_HZ_IDLE"), ("periodic", "HZ_PERIODIC")):
            with self.subTest(mode=mode):
                self.config.write_bytes(self.original)
                self.prepare_baseline(RCU_EXPERT="n")
                self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                                    f"--tick-mode={mode}"))
                self.assert_values(**{symbol: "y", "NO_HZ_FULL": "n", "RCU_NOCB_CPU": "n"})

    def test_real_profile_thp_off_and_both_rt_controls(self):
        for option in ("--thp=off", "--preemption=rt", "--preempt-mode=rt"):
            with self.subTest(option=option):
                self.config.write_bytes(self.original)
                self.prepare_baseline(EXPERT="y")
                self.assert_success(self.run_script("--strict", "--optimization-profile=server", option))
                self.assert_values(TRANSPARENT_HUGEPAGE="n", PERSISTENT_HUGE_ZERO_FOLIO="n")
                if option != "--thp=off":
                    self.assert_values(PREEMPT_RT="y", NUMA_BALANCING="n")

    def test_real_modern_numa_override_skips_legacy_requests(self):
        self.prepare_baseline(NUMA="n", NUMA_MIGRATION="n", NUMA_BALANCING="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop",
                                            "--numa-balancing-mode=on", "--numa-balancing=off"))
        self.assert_values(NUMA="n", NUMA_MIGRATION="n", NUMA_BALANCING="n")


if __name__ == "__main__":
    unittest.main()
