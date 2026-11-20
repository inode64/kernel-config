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
                           HIDRAW="y", INOTIFY_USER="y", SECCOMP_FILTER="y", TUN="y",
                           IP_MULTIPLE_TABLES="y", IPV6_MULTIPLE_TABLES="y", NF_TABLES_INET="y")
        # warp needs nf_tables and nft_reject_inet, built-in or modular.
        config = dict(re.findall(r"^CONFIG_(\w+)=(.*)$", self.config.read_text(), re.M))
        for symbol in ("NF_TABLES", "NFT_REJECT", "NFT_REJECT_INET"):
            self.assertIn(config.get(symbol, "n"), ("y", "m"), symbol)
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_python_profile_restores_cross_memory_attach(self):
        self.prepare_baseline(CROSS_MEMORY_ATTACH="n", DNOTIFY="n")
        self.assert_success(self.run_script("--strict", "--applications=python"))
        self.assert_values(CROSS_MEMORY_ATTACH="y", DNOTIFY="y")

    def test_real_hvo_default_follows_tree_version(self):
        self.prepare_baseline(HUGETLBFS="y", HUGETLB_PAGE_OPTIMIZE_VMEMMAP_DEFAULT_ON="y")
        makefile = (self.tree / "Makefile").read_text()
        version = tuple(int(re.search(rf"^{key} = (\d+)$", makefile, re.M).group(1))
                        for key in ("VERSION", "PATCHLEVEL"))
        self.assert_success(self.run_script("--strict"))
        self.assert_values(HUGETLB_PAGE_OPTIMIZE_VMEMMAP_DEFAULT_ON="y" if version >= (7, 2) else "n")

    def test_real_desktop_applications_restore_disabled_parents(self):
        self.prepare_baseline(DRM="n", SOUND="n", MEDIA_SUPPORT="n", USB="n",
                              HID="n", FUSE_FS="n", TUN="n")
        self.assert_success(self.run_script("--strict", "--applications=desktop,multimedia,rocm,nebula"))
        self.assert_values(DRM_AMDGPU="y", HSA_AMD="y", HSA_AMD_SVM="y", FUSE_FS="y",
                           SND_USB_AUDIO="y", USB_VIDEO_CLASS="y", HIDRAW="y",
                           SECCOMP_FILTER="y", TUN="y")

    def test_real_amd_host_with_qemu_and_nftables(self):
        self.prepare_baseline(KVM="m", KVM_AMD="m", NF_TABLES="m", NFT_NAT="m",
                              VHOST_NET="m", VHOST_VSOCK="n", VHOST_SCSI="n", VHOST_VDPA="n",
                              X86_ACPI_CPUFREQ="n")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=amd",
                                            "--host-type=baremetal", "--applications=qemu,firewalld"))
        self.assert_values(KVM_AMD="m", VHOST_NET="m", VHOST_VSOCK="y", VHOST="y",
                           VHOST_IOTLB="y", NF_TABLES="m", NFT_NAT="m",
                           X86_AMD_PSTATE="y", X86_ACPI_CPUFREQ="y")

    def assert_enabled(self, *symbols):
        config = dict(re.findall(r"^CONFIG_(\w+)=(.*)$", self.config.read_text(), re.M))
        for symbol in symbols:
            self.assertIn(config.get(symbol, "n"), ("y", "m"), symbol)

    def test_real_firehol_extensions_follow_a_modular_xtables(self):
        if "config NETFILTER_XTABLES_LEGACY" not in (self.tree / "net/netfilter/Kconfig").read_text():
            self.skipTest("requires a 6.17+ tree with NETFILTER_XTABLES_LEGACY")
        self.prepare_baseline(NETFILTER_XTABLES="m", NETFILTER_XTABLES_LEGACY="n", NFT_COMPAT="n",
                              NETFILTER_XT_TARGET_TCPMSS="n", NETFILTER_XT_MATCH_PHYSDEV="n")
        self.assert_success(self.run_script("--strict", "--prune-legacy", "--applications=firehol"))
        self.assert_values(NETFILTER_XTABLES_LEGACY="y", NETFILTER_XT_TARGET_TCPMSS="m",
                           NETFILTER_XT_MATCH_PHYSDEV="m")
        self.assert_enabled("IP_NF_TARGET_NETMAP", "IP_NF_TARGET_REDIRECT", "IP_NF_TARGET_TTL",
                            "IP6_NF_TARGET_HL", "IP_NF_FILTER", "IP_NF_NAT", "IP_NF_RAW")

    def test_real_nfs_server_next_to_a_modular_nfs_client(self):
        # SUNRPC, LOCKD and GRACE_PERIOD are hidden: NFSD=y raises them to y.
        self.prepare_baseline(NFS_FS="m", NFSD="n")
        self.assert_success(self.run_script("--strict", "--applications=nfs-server"))
        self.assert_values(NFSD="y", SUNRPC="y", LOCKD="y", GRACE_PERIOD="y", NFS_FS="m")

    def test_real_open_vswitch_next_to_a_modular_conntrack(self):
        self.prepare_baseline(NF_CONNTRACK="m", OPENVSWITCH="n", MPLS="n")
        self.assert_success(self.run_script("--strict", "--applications=openvswitch"))
        self.assert_values(OPENVSWITCH="m", MPLS="y")
        self.assert_enabled("VXLAN", "GENEVE", "NET_IPGRE_DEMUX", "NF_CONNTRACK_OVS")

    def test_real_atop_next_to_a_modular_connector(self):
        self.prepare_baseline(CONNECTOR="m")
        self.assert_success(self.run_script("--strict", "--applications=atop"))
        self.assert_values(CONNECTOR="y", PROC_EVENTS="y", TASKSTATS="y", TASK_DELAY_ACCT="y")

    def test_real_tunnel_profiles_leave_udp_tunnel_to_its_selectors(self):
        # GENEVE=m leaves NET_UDP_TUNNEL=m; docker then builds VXLAN in.
        self.prepare_baseline(VXLAN="n", GENEVE="m", OPENVSWITCH="m", NET_UDP_TUNNEL="m")
        self.assert_success(self.run_script("--strict", "--applications=docker,openvswitch,wireguard"))
        self.assert_values(VXLAN="y", NET_UDP_TUNNEL="y")

    def test_real_all_optimizations_keep_mpls_for_open_vswitch(self):
        self.prepare_baseline(OPENVSWITCH="m", MPLS="y")
        result = self.run_script("--strict", "--all-optimizations",
                                 "--applications=qemu,docker,openvswitch")
        self.assert_success(result)
        self.assertIn("Retaining CONFIG_MPLS: selected by enabled CONFIG_OPENVSWITCH", result.stdout)
        self.assert_values(OPENVSWITCH="m", MPLS="y", TIPC="n")

    def test_real_docker_with_legacy_gated_iptables(self):
        if "config NETFILTER_XTABLES_LEGACY" not in (self.tree / "net/netfilter/Kconfig").read_text():
            self.skipTest("requires a 6.17+ tree with NETFILTER_XTABLES_LEGACY")
        self.prepare_baseline(NETFILTER_XTABLES_LEGACY="y", IP_NF_IPTABLES_LEGACY="m",
                              IP_NF_NAT="m", IP_NF_RAW="m", NFT_COMPAT="n")
        self.assert_success(self.run_script("--strict", "--prune-legacy", "--applications=docker"))
        self.assert_values(NETFILTER_XTABLES_LEGACY="n", IP_NF_NAT="n", IP_NF_RAW="n")
        self.assert_enabled("NFT_COMPAT", "NFT_NAT", "NETFILTER_XT_MATCH_ADDRTYPE",
                            "NETFILTER_XT_MATCH_CONNTRACK", "NETFILTER_XT_NAT",
                            "NETFILTER_XT_TARGET_MASQUERADE")
        self.assert_success(self.run_script("--strict", "--prune-legacy",
                                            "--applications=docker,iptables-legacy"))
        self.assert_values(NETFILTER_XTABLES_LEGACY="y")
        self.assert_enabled("IP_NF_IPTABLES_LEGACY", "IP6_NF_IPTABLES_LEGACY", "IP_NF_FILTER",
                            "IP_NF_NAT", "IP6_NF_NAT", "IP_NF_RAW", "IP6_NF_RAW")

    def test_real_server_profile_enables_firmware_decoders(self):
        self.prepare_baseline(FW_LOADER="y", FW_LOADER_COMPRESS="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(FW_LOADER_COMPRESS="y", FW_LOADER_COMPRESS_XZ="y", FW_LOADER_COMPRESS_ZSTD="y")

    def test_real_server_profile_enables_memory_error_recovery(self):
        self.prepare_baseline(MEMORY_FAILURE="n", DEBUG_FS="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(MEMORY_FAILURE="y", ACPI_APEI_MEMORY_FAILURE="y", RAS_CEC="y")
        # --all-optimizations prunes DEBUG_FS, so RAS_CEC goes but recovery stays.
        result = self.run_script("--strict", "--all-optimizations")
        self.assert_success(result)
        self.assertIn("RAS_CEC needs DEBUG_FS", result.stdout)
        self.assert_values(MEMORY_FAILURE="y", ACPI_APEI_MEMORY_FAILURE="y", RAS_CEC="n", DEBUG_FS="n")

    def test_real_server_profile_disables_no_page_mapcount(self):
        self.prepare_baseline(TRANSPARENT_HUGEPAGE="y", NO_PAGE_MAPCOUNT="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(NO_PAGE_MAPCOUNT="n")

    def test_real_server_profile_disables_rt_group_scheduling(self):
        self.prepare_baseline(CGROUP_SCHED="y", RT_GROUP_SCHED="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(RT_GROUP_SCHED="n", CGROUP_SCHED="y", FAIR_GROUP_SCHED="y")

    def test_real_rasdaemon_keeps_trace_events_under_all_optimizations(self):
        # The pruned tracers come back at their default (y) once FTRACE returns,
        # unless the earlier disable requests are applied again. TCP_CONG_ADVANCED=n
        # makes the server profile refresh the config while FTRACE is still off,
        # which drops their "is not set" lines.
        self.prepare_baseline(MEMORY_FAILURE="n", PCIEAER="n", FTRACE="y", UPROBE_EVENTS="y",
                              EPROBE_EVENTS="y", TCP_CONG_ADVANCED="n")
        self.assert_success(self.run_script("--strict", "--all-optimizations", "--applications=rasdaemon"))
        self.assert_values(TRACING="y", EVENT_TRACING="y", FTRACE="y", ENABLE_DEFAULT_TRACERS="y",
                           FUNCTION_TRACER="n", DYNAMIC_FTRACE="n", KPROBES="n", DEBUG_FS="n",
                           UPROBE_EVENTS="n", EPROBE_EVENTS="n",
                           RAS="y", PCIEAER="y", ACPI_APEI_GHES="y", MEMORY_FAILURE="y", X86_MCE="y")
        self.assert_enabled("EDAC")

    def test_real_server_profile_keeps_lockup_and_hung_task_detection(self):
        for option in ("--optimization-profile=server", "--all-optimizations"):
            with self.subTest(option=option):
                self.config.write_bytes(self.original)
                self.prepare_baseline(DEBUG_KERNEL="n")
                self.assert_success(self.run_script("--strict", option))
                self.assert_values(DEBUG_KERNEL="y", SOFTLOCKUP_DETECTOR="y", DETECT_HUNG_TASK="y",
                                   DETECT_HUNG_TASK_BLOCKER="y", HARDLOCKUP_DETECTOR="n",
                                   DEBUG_MISC="n", X86_DEBUG_FPU="n", RCU_TRACE="n", DEBUG_PREEMPT="n")

    def test_real_server_profile_uses_lazy_preemption_when_none_is_arch_gated(self):
        preempt = (self.tree / "kernel/Kconfig.preempt").read_text()
        none = preempt.split("config PREEMPT_VOLUNTARY", 1)[0]
        if "config PREEMPT_NONE" not in none or "ARCH_NO_PREEMPT" not in none:
            self.skipTest("PREEMPT_NONE is not gated on ARCH_NO_PREEMPT")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(PREEMPT_LAZY="y", PREEMPT_NONE="n", PREEMPT_VOLUNTARY="n", PREEMPT="n",
                           PREEMPT_DYNAMIC="n")

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

    def test_real_debug_trace_pruning_keeps_x86_early_printk(self):
        self.prepare_baseline(EXPERT="y", EARLY_PRINTK="y")
        self.assert_success(self.run_script("--strict", "--prune-debug-trace",
                                            "--enable-symbols=DEBUG_INFO_NONE"))
        self.assert_values(EARLY_PRINTK="y")

    def test_real_hardening_pruning_selects_none_choices(self):
        self.prepare_baseline(INIT_STACK_ALL_ZERO="y")
        self.assert_success(self.run_script("--strict", "--prune-hardening"))
        self.assert_values(INIT_STACK_NONE="y", INIT_STACK_ALL_ZERO="n",
                           RANDSTRUCT_NONE="y", RANDSTRUCT_FULL="n")

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

    def test_real_profile_enables_crc_optimizations(self):
        kconfig = self.tree / "lib/crc/Kconfig"
        if not kconfig.is_file() or "config CRC_OPTIMIZATIONS" not in kconfig.read_text():
            self.skipTest("CRC_OPTIMIZATIONS is not in this tree")
        # The prompt exists only with EXPERT; otherwise olddefconfig forces default y.
        self.prepare_baseline(EXPERT="y", CRC_OPTIMIZATIONS="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(CRC_OPTIMIZATIONS="y")

    def test_real_profile_selects_crc32_slice_by_8(self):
        kconfig = self.tree / "lib/Kconfig"
        if not kconfig.is_file() or "config CRC32_SLICEBY8" not in kconfig.read_text():
            self.skipTest("CRC32 implementation choice is not in this tree")
        self.prepare_baseline(CRC32="y", CRC32_SLICEBY8="n", CRC32_SLICEBY4="n",
                              CRC32_BIT="n", CRC32_SARWATE="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(CRC32_SLICEBY8="y", CRC32_SARWATE="n", CRC32_SLICEBY4="n", CRC32_BIT="n")

    def test_real_profile_replaces_frame_pointer_unwinder_with_orc(self):
        debug = self.tree / "arch/x86/Kconfig.debug"
        if not debug.is_file() or "config UNWINDER_ORC" not in debug.read_text():
            self.skipTest("x86 ORC unwinder is not in this tree")
        if "CONFIG_X86_64=y" not in self.config.read_text():
            self.skipTest("ORC unwinder depends on X86_64")
        self.prepare_baseline(UNWINDER_ORC="n", UNWINDER_FRAME_POINTER="y")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(UNWINDER_ORC="y", UNWINDER_FRAME_POINTER="n")

    def test_real_server_profile_uses_idle_ticks_without_rcu_offload(self):
        # NO_HZ_FULL selects RCU_NOCB_CPU; with RCU_EXPERT left off, leaving
        # full dynticks is what turns the offload back off.
        self.prepare_baseline(NO_HZ_FULL="y", RCU_NOCB_CPU_DEFAULT_ALL="y", RCU_EXPERT="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(NO_HZ_IDLE="y", NO_HZ_FULL="n", HZ_PERIODIC="n",
                           RCU_NOCB_CPU_DEFAULT_ALL="n", RCU_NOCB_CPU="n",
                           TICK_CPU_ACCOUNTING="y", VIRT_CPU_ACCOUNTING_GEN="n")

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
