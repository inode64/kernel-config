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
from test_hardware_detect import memory_table, serial_host


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

    def use_native_host(self):
        host = Path(tempfile.mkdtemp(prefix="native-host-", dir=self.temp.name))
        self.addCleanup(shutil.rmtree, host)
        (host / "proc").mkdir()
        (host / "proc/cpuinfo").write_text("processor : 0\nflags : aes cx16\n")
        (host / "proc/cmdline").write_text("\n")
        unit = host / "sys/class/iommu/dmar0/intel-iommu"
        unit.mkdir(parents=True)
        (unit / "cap").write_text("800000000000000\n")
        (unit / "ecap").write_text("8\n")
        self.env = self.env | {"KC_HOST_ROOT": str(host)}
        return host

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

    def test_real_memory_ecc_drivers_follow_dimm_evidence_and_keep_mce(self):
        host = self.use_native_host()
        (host / "proc/cpuinfo").write_text("vendor_id : AuthenticAMD\ncpu family : 25\nmodel : 97\n")
        table = host / "sys/firmware/dmi/tables/DMI"
        table.parent.mkdir(parents=True)
        table.write_bytes(memory_table(correction=3, total=64))
        self.prepare_baseline(RAS="y", EDAC="y", X86_MCE="y", X86_MCE_AMD="y",
                              EDAC_DECODE_MCE="m", EDAC_AMD64="m", AMD_ATL="m", MEMORY_FAILURE="y")
        self.assert_success(self.run_script("--strict", "--native-arch", "--applications=rasdaemon"))
        self.assert_values(EDAC_AMD64="n", AMD_ATL="n", RAS_CEC="n", EDAC_DECODE_MCE="m",
                           EDAC="y", X86_MCE_AMD="y", MEMORY_FAILURE="y", MEMORY_HOTPLUG="y")
        repeat = self.run_script("--strict", "--native-arch", "--applications=rasdaemon", "--dry-run")
        self.assert_success(repeat)
        self.assertIn("No changes.", repeat.stdout)
        table.write_bytes(memory_table())
        self.assert_success(self.run_script("--strict", "--platform-drivers=auto"))
        self.assert_values(EDAC_AMD64="m", AMD_ATL="m", EDAC_DECODE_MCE="m")
        self.assert_success(self.run_script("--strict", "--platform-drivers=auto",
                                            "--disable-symbols=EDAC_AMD64,AMD_ATL"))
        self.assert_values(EDAC_AMD64="n", AMD_ATL="n", EDAC_DECODE_MCE="m")

    def test_real_serial_pruning_preserves_usb_ps2_and_explicit_console(self):
        host = self.use_native_host()
        serial_host(host)
        baseline = dict(SERIAL_8250="y", SERIAL_8250_CONSOLE="y", SERIAL_8250_PNP="y",
                        SERIO="y", SERIO_I8042="y", SERIO_SERPORT="m", USB_ACM="m",
                        USB_SERIAL="m", USB_SERIAL_FTDI_SIO="m", SERIAL_MULTI_INSTANTIATE="m")
        self.prepare_baseline(**baseline)
        self.assert_success(self.run_script("--strict", "--serial-support=auto"))
        self.assert_values(**(baseline | dict(SERIAL_8250="n", SERIAL_8250_CONSOLE="n", SERIAL_8250_PNP="n")))
        repeat = self.run_script("--strict", "--serial-support=auto", "--dry-run")
        self.assert_success(repeat)
        self.assertIn("No changes.", repeat.stdout)
        for option in ("--enable-symbols=SERIAL_8250_CONSOLE",
                       "--protected-config-symbols=SERIAL_8250_CONSOLE"):
            self.prepare_baseline(**baseline)
            self.assert_success(self.run_script("--strict", "--serial-support=auto", option))
            self.assert_values(**baseline)

    def test_real_extended_controls_and_idempotence(self):
        args = ("--strict", "--preemption=lazy", "--preempt-dynamic=on", "--tick-mode=full",
                "--thp=madvise", "--lru-gen=on", "--zswap=on", "--zswap-compressor=zstd",
                "--zram=module", "--zram-compressor=lz4", "--numa-balancing=on",
                "--kmalloc-partition=random", "--tcp-congestion=bbr", "--default-qdisc=fq",
                "--io-uring=on")
        self.assert_success(self.run_script(*args))
        self.assert_values(PREEMPT_LAZY="y", PREEMPT="n", PREEMPT_RT="n", PREEMPT_DYNAMIC="y",
                           NO_HZ_FULL="y", NO_HZ_IDLE="n", HZ_PERIODIC="n",
                           TRANSPARENT_HUGEPAGE="y", TRANSPARENT_HUGEPAGE_MADVISE="y",
                           LRU_GEN="y", LRU_GEN_ENABLED="y", ZSWAP="y", ZSWAP_DEFAULT_ON="y",
                           ZSWAP_COMPRESSOR_DEFAULT='"zstd"', ZRAM="m", ZRAM_BACKEND_LZ4="y",
                           ZRAM_DEF_COMP='"lz4"', NUMA="y", NUMA_MIGRATION="y",
                           NUMA_BALANCING="y", NUMA_BALANCING_DEFAULT_ENABLED="y",
                           KMALLOC_PARTITION_CACHES="y", KMALLOC_PARTITION_RANDOM="y",
                           TCP_CONG_BBR="y", DEFAULT_TCP_CONG='"bbr"', NET_SCH_FQ="y",
                           NET_SCH_DEFAULT="y", DEFAULT_FQ="y", DEFAULT_NET_SCH='"fq"',
                           IO_URING="y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_default_qdisc_from_disabled_net_sched_and_fq_pie(self):
        self.prepare_baseline(NET_SCHED="n")
        self.assert_success(self.run_script("--strict", "--default-qdisc=fq_codel"))
        self.assert_values(NET_SCHED="y", NET_SCH_DEFAULT="y", NET_SCH_FQ_CODEL="y",
                           DEFAULT_FQ_CODEL="y", DEFAULT_PFIFO_FAST="n",
                           DEFAULT_NET_SCH='"fq_codel"')
        self.prepare_baseline(NET_SCH_PIE="n")
        self.assert_success(self.run_script("--strict", "--default-qdisc=fq_pie"))
        self.assert_values(NET_SCH_PIE="y", NET_SCH_FQ_PIE="y", DEFAULT_FQ_PIE="y",
                           DEFAULT_FQ_CODEL="n", DEFAULT_NET_SCH='"fq_pie"')
        self.assert_success(self.run_script("--strict", "--default-qdisc=pfifo_fast"))
        self.assert_values(DEFAULT_PFIFO_FAST="y", DEFAULT_FQ_PIE="n",
                           DEFAULT_NET_SCH='"pfifo_fast"')

    def test_real_rt_model_and_io_uring_off(self):
        self.prepare_baseline(EXPERT="y")
        self.assert_success(self.run_script("--strict", "--preemption=rt", "--preempt-dynamic=off",
                                            "--tick-mode=idle", "--thp=off", "--numa-balancing=off",
                                            "--io-uring=off"))
        self.assert_values(PREEMPT_RT="y", PREEMPT="y", PREEMPT_LAZY="n", PREEMPT_DYNAMIC="n",
                           NO_HZ_IDLE="y", NO_HZ_FULL="n", TRANSPARENT_HUGEPAGE="n",
                           NUMA_BALANCING="n", IO_URING="n")

    def test_real_realtime_rcu_follows_final_preemption_and_tick(self):
        cases = (
            ("n", (), "n", "y"),
            ("y", (), "y", "y"),
            ("y", ("--preemption=full", "--tick-mode=idle"), "n", "n"),
            ("y", ("--preemption=full", "--tick-mode=periodic"), "n", "n"),
        )
        for expert, overrides, rt, offload in cases:
            with self.subTest(expert=expert, overrides=overrides):
                self.config.write_bytes(self.original)
                self.prepare_baseline(EXPERT=expert, RCU_EXPERT="n", PREEMPT_RT="n")
                args = ("--strict", "--optimization-profile=realtime", *overrides)
                self.assert_success(self.run_script(*args))
                self.assert_values(EXPERT=expert, RCU_EXPERT="n", PREEMPT_RT=rt, PREEMPT="y",
                                   RCU_BOOST=rt, RCU_NOCB_CPU=offload, RCU_NOCB_CPU_CB_BOOST=rt)
                before = self.config.read_bytes()
                self.assert_success(self.run_script(*args))
                self.assertEqual(self.config.read_bytes(), before)

    def test_real_realtime_rcu_respects_explicit_and_protected_boost(self):
        for override in ("--disable-symbols=RCU_BOOST", "--protected-config-symbols=RCU_BOOST"):
            with self.subTest(override=override):
                self.config.write_bytes(self.original)
                # Expose the prompt so the protected n is present in the baseline.
                self.prepare_baseline(EXPERT="y", RCU_EXPERT="y", PREEMPT="y",
                                      PREEMPT_RT="n", RCU_BOOST="n")
                self.assert_success(self.run_script("--strict", "--optimization-profile=realtime", override))
                self.assert_values(PREEMPT_RT="y", RCU_BOOST="n", RCU_NOCB_CPU="y",
                                   RCU_NOCB_CPU_CB_BOOST="n")

        self.prepare_baseline(EXPERT="n", RCU_EXPERT="n", PREEMPT_RT="n")
        before = self.config.read_bytes()
        result = self.run_script("--strict", "--optimization-profile=realtime",
                                 "--enable-symbols=RCU_BOOST")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_RCU_BOOST requested=y final=n", result.stderr)
        self.assertEqual(self.config.read_bytes(), before)

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
        self.assert_values(KVM_AMD="m", VHOST_NET="m", VHOST_VSOCK="m", VHOST="m",
                           VHOST_IOTLB="m", NF_TABLES="m", NFT_NAT="m",
                           X86_AMD_PSTATE="y", X86_ACPI_CPUFREQ="y", INTEL_IDLE="n")
        self.assert_success(self.run_script("--strict", "--applications=qemu",
                                            "--enable-symbols=VHOST_VSOCK"))
        self.assert_values(VHOST_VSOCK="y", VHOST="y", VHOST_IOTLB="y")
        self.assert_success(self.run_script("--strict", "--applications=qemu"))
        self.assert_values(VHOST_VSOCK="y", VHOST="y", VHOST_IOTLB="y")

    def test_real_qemu_with_modular_optional_dependencies(self):
        self.prepare_baseline(MODULES="y", IOMMUFD="m", MACVLAN="m", MACVTAP="m",
                              TUN="y", VSOCKETS="m", VHOST_MENU="y", VFIO="n",
                              VHOST_NET="n", VHOST_VSOCK="n")
        self.assert_values(TAP="m")
        args = ("--strict", "--applications=qemu")
        self.assert_success(self.run_script(*args))
        self.assert_values(IOMMUFD="m", TAP="m", VFIO="m", VHOST_NET="m",
                           VSOCKETS="m", VHOST_VSOCK="m")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args, "--disable-symbols=VFIO,VHOST_NET,VHOST_VSOCK"))
        self.assert_values(VFIO="n", VHOST_NET="n", VHOST_VSOCK="n")

    def test_real_intel_cpu_vendor_filter_enables_intel_idle(self):
        self.prepare_baseline(CPU_IDLE="y", INTEL_IDLE="n", X86_ACPI_CPUFREQ="n")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=intel"))
        self.assert_values(X86_INTEL_PSTATE="y", X86_ACPI_CPUFREQ="y", INTEL_IDLE="y")
        self.assert_success(self.run_script("--strict", "--cpu-vendor-filter=intel",
                                            "--disable-symbols=INTEL_IDLE"))
        self.assert_values(INTEL_IDLE="n")

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

    def test_real_legacy_firewall_targets_without_compatibility_wrappers(self):
        for profile in ("iptables-legacy", "docker,iptables-legacy", "firehol"):
            with self.subTest(profile=profile):
                self.config.write_bytes(self.original)
                self.prepare_baseline(NETFILTER_XTABLES="m", NETFILTER_XT_TARGET_MASQUERADE="n",
                                      NETFILTER_XT_TARGET_HL="n", IP_NF_TARGET_MASQUERADE="n",
                                      IP_NF_TARGET_TTL="n")
                self.assert_success(self.run_script(
                    "--strict", "--prune-legacy", f"--applications={profile}",
                    "--disable-symbols=IP_NF_TARGET_MASQUERADE,IP_NF_TARGET_TTL"))
                self.assert_values(NETFILTER_XT_TARGET_MASQUERADE="m", NETFILTER_XT_TARGET_HL="m",
                                   IP_NF_TARGET_MASQUERADE="n", IP_NF_TARGET_TTL="n")
                self.assert_enabled("IP_NF_FILTER", "IP_NF_NAT", "IP_NF_MANGLE", "IP_NF_RAW")

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

    def test_real_server_profile_enables_net_sched_for_qdiscs(self):
        self.prepare_baseline(NET_SCHED="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(NET_SCHED="y", NET_SCH_FQ="y", NET_SCH_FQ_CODEL="y")

    def test_real_multipath_profile_restores_selectors(self):
        self.prepare_baseline(MODULES="y", MD="y", BLK_DEV_DM="y", DM_MULTIPATH="y",
                              DM_MULTIPATH_QL="n", DM_MULTIPATH_ST="n",
                              DM_MULTIPATH_HST="n", DM_MULTIPATH_IOA="n", DM_UEVENT="n")
        args = ("--strict", "--all-optimizations", "--applications=multipath,rasdaemon")
        self.assert_success(self.run_script(*args))
        self.assert_values(BLK_DEV_DM="y", DM_MULTIPATH="y", DM_UEVENT="y",
                           DM_MULTIPATH_QL="m", DM_MULTIPATH_ST="m",
                           DM_MULTIPATH_HST="m", DM_MULTIPATH_IOA="m")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_multipath_profile_enables_modular_stack(self):
        self.prepare_baseline(MODULES="y", MD="n")
        self.assert_success(self.run_script("--strict", "--applications=multipath"))
        self.assert_values(MD="y", BLK_DEV_DM="m", DM_MULTIPATH="m", DM_UEVENT="y",
                           DM_MULTIPATH_QL="m", DM_MULTIPATH_ST="m",
                           DM_MULTIPATH_HST="m", DM_MULTIPATH_IOA="m")

    def test_real_rasdaemon_keeps_trace_events_under_all_optimizations(self):
        # The pruned tracers come back at their default (y) once FTRACE returns,
        # unless the earlier disable requests are applied again. TCP_CONG_ADVANCED=n
        # makes the server profile refresh the config while FTRACE is still off,
        # which drops their "is not set" lines.
        self.prepare_baseline(MEMORY_FAILURE="n", MEMORY_HOTPLUG="n", MEMORY_HOTREMOVE="n",
                              PCIEAER="n", FTRACE="y", UPROBE_EVENTS="y",
                              EPROBE_EVENTS="y", TCP_CONG_ADVANCED="n",
                              DEBUG_FS="y", DEBUG_FS_ALLOW_NONE="y")
        self.assert_success(self.run_script("--strict", "--all-optimizations", "--applications=rasdaemon"))
        self.assert_values(TRACING="y", EVENT_TRACING="y", FTRACE="y", ENABLE_DEFAULT_TRACERS="y",
                           FUNCTION_TRACER="n", DYNAMIC_FTRACE="n", KPROBES="n", DEBUG_FS="y",
                           DEBUG_FS_ALLOW_ALL="y",
                           UPROBE_EVENTS="n", EPROBE_EVENTS="n",
                           RAS="y", PCIEAER="y", ACPI_APEI_GHES="y", MEMORY_FAILURE="y", X86_MCE="y",
                           MEMORY_HOTPLUG="y", MEMORY_HOTREMOVE="n")
        self.assert_enabled("EDAC")
        if "config TRACEFS_AUTOMOUNT_DEPRECATED" in (self.tree / "kernel/trace/Kconfig").read_text():
            self.assert_values(TRACEFS_AUTOMOUNT_DEPRECATED="y")
            self.assert_success(self.run_script("--strict", "--all-optimizations", "--applications=rasdaemon",
                                                "--disable-symbols=TRACEFS_AUTOMOUNT_DEPRECATED"))
            self.assert_values(TRACEFS_AUTOMOUNT_DEPRECATED="n", DEBUG_FS="y", DEBUG_FS_ALLOW_ALL="y",
                               TRACING="y", EVENT_TRACING="y", RAS="y", MEMORY_FAILURE="y")

    def test_real_rasdaemon_lets_pruning_drop_debugfs_without_a_consumer(self):
        # AMD CPU (no ACPI EXTLOG) and rasdaemon 1.0 (tracefs): nothing restores
        # the debugfs that --all-optimizations prunes; the trace events stay.
        host = self.use_native_host()
        (host / "proc/cpuinfo").write_text(
            "vendor_id : AuthenticAMD\ncpu family : 25\nmodel : 97\nflags : mce mca\n")
        table = host / "sys/firmware/dmi/tables/DMI"
        table.parent.mkdir(parents=True)
        table.write_bytes(memory_table(correction=3, total=64))
        (host / "sys/firmware/acpi/tables").mkdir(parents=True)
        (host / "sys/firmware/acpi/tables/MCFG").write_bytes(b"")
        self.env = self.env | {"KC_RASDAEMON_VERSION": "rasdaemon 1.0.0"}
        self.prepare_baseline(DEBUG_FS="y", DEBUG_FS_ALLOW_ALL="y", FTRACE="y")
        result = self.run_script("--strict", "--all-optimizations", "--native-arch", "--applications=rasdaemon")
        self.assert_success(result)
        self.assertIn("rasdaemon does not request DEBUG_FS, DEBUG_FS_ALLOW_ALL", result.stdout)
        self.assert_values(DEBUG_FS="n", TRACING="y", EVENT_TRACING="y", FTRACE="y",
                           ENABLE_DEFAULT_TRACERS="y", RAS="y", PCIEAER="y", X86_MCE="y")
        repeat = self.run_script("--strict", "--all-optimizations", "--native-arch",
                                 "--applications=rasdaemon", "--dry-run")
        self.assert_success(repeat)
        self.assertIn("No changes.", repeat.stdout)

    def test_real_explicit_debugfs_keeps_pruned_children_off(self):
        # Another consumer (PCP's kvm PMDA) asks for debugfs explicitly. The
        # refresh while DEBUG_FS is pruned drops BLK_DEBUG_FS's "is not set"
        # line, so restoring DEBUG_FS would bring it back at its default (y).
        self.prepare_baseline(DEBUG_FS="y", DEBUG_FS_ALLOW_ALL="y", BLK_DEBUG_FS="y",
                              TCP_CONG_ADVANCED="n")
        args = ("--strict", "--all-optimizations", "--enable-symbols=DEBUG_FS,DEBUG_FS_ALLOW_ALL")
        self.assert_success(self.run_script(*args))
        self.assert_values(DEBUG_FS="y", DEBUG_FS_ALLOW_ALL="y", BLK_DEBUG_FS="n")
        repeat = self.run_script(*args, "--dry-run")
        self.assert_success(repeat)
        self.assertIn("No changes.", repeat.stdout)

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
        # The *_BUILD symbols precede the model choice and share its prefixes.
        none = re.search(r"^config PREEMPT_NONE\n(.*?)(?=^config |\Z)", preempt, re.M | re.S)
        if none is None or "ARCH_NO_PREEMPT" not in none.group(1):
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
        self.assert_success(self.run_script("--strict", "--prune-debug-trace"))
        self.assert_values(EARLY_PRINTK="y", DEBUG_INFO_NONE="y")

    def test_real_observability_coverage_and_debug_pruning_strict(self):
        self.prepare_baseline(FTRACE="y", DEBUG_KERNEL="n")
        self.assert_success(self.run_script("--strict", "--prune-observability"))
        self.assert_values(FTRACE="n", BRANCH_PROFILE_NONE="n")
        self.config.write_bytes(self.original)
        self.prepare_baseline(FTRACE="n", DEBUG_KERNEL="n")
        self.assert_success(self.run_script("--strict", "--prune-coverage"))
        self.assert_values(DEBUG_INFO_NONE="n", BRANCH_PROFILE_NONE="n")
        self.config.write_bytes(self.original)
        self.prepare_baseline(DEBUG_KERNEL="y", DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT="y")
        self.assert_success(self.run_script(
            "--strict", "--prune-debug-trace", "--protected-config-symbols=DEBUG_KERNEL",
        ))
        self.assert_values(DEBUG_KERNEL="y", DEBUG_INFO_NONE="y",
                           DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT="n")

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

    def test_real_uclamp_groups_and_overrides(self):
        self.prepare_baseline(CGROUPS="y", CGROUP_SCHED="y", CPU_FREQ="y",
                              CPU_FREQ_GOV_SCHEDUTIL="y", UCLAMP_TASK="y", UCLAMP_TASK_GROUP="n",
                              SCHED_AUTOGROUP="n")
        baseline = self.config.read_bytes()
        args = ("--strict", "--optimization-profile=desktop")
        self.assert_success(self.run_script(*args))
        self.assert_values(UCLAMP_TASK="y", UCLAMP_TASK_GROUP="y")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)
        for override in ("--uclamp=off", "--disable-symbols=UCLAMP_TASK_GROUP",
                         "--protected-config-symbols=UCLAMP_TASK_GROUP"):
            with self.subTest(override=override):
                self.config.write_bytes(baseline)
                self.assert_success(self.run_script(*args, override))
                self.assert_values(UCLAMP_TASK_GROUP="n")
        self.config.write_bytes(baseline)
        self.assert_success(self.run_script("--strict", "--uclamp=on"))
        self.assert_values(UCLAMP_TASK_GROUP="y")
        self.assert_success(self.run_script("--strict", "--uclamp=on", "--disable-symbols=CGROUP_SCHED"))
        self.assert_values(CGROUP_SCHED="n", UCLAMP_TASK_GROUP="n")
        self.prepare_baseline(EXPERT="y", GENTOO_LINUX="n", CGROUPS="n")
        self.assert_success(self.run_script("--strict", "--uclamp=on"))
        self.assert_values(CGROUPS="n", UCLAMP_TASK="y", UCLAMP_TASK_GROUP="n")

    def test_real_uclamp_enables_cpufreq_and_respects_protection(self):
        # SCHED_MC_PRIO selects CPU_FREQ on x86, hiding the missing parent.
        self.prepare_baseline(SCHED_MC_PRIO="n", CPU_FREQ="n")
        before = self.config.read_bytes()
        args = ("--strict", "--uclamp=on")
        result = self.run_script(*args, "--protected-config-symbols=CPU_FREQ")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_CPU_FREQ requested=y final=n", result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args, "--dry-run"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args))
        self.assert_values(CPU_FREQ="y", CPU_FREQ_GOV_SCHEDUTIL="y", UCLAMP_TASK="y",
                           SCHED_MC_PRIO="n")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)

    def test_real_profiles_restore_cgroup_dependencies(self):
        for profile in ("server", "desktop", "realtime"):
            with self.subTest(profile=profile):
                self.config.write_bytes(self.original)
                # These selectors otherwise keep CGROUPS on in Gentoo/desktop baselines.
                self.prepare_baseline(EXPERT="y", GENTOO_LINUX="n", SCHED_AUTOGROUP="n",
                                      CGROUPS="n", IOSCHED_BFQ="m")
                args = ("--strict", f"--optimization-profile={profile}")
                self.assert_success(self.run_script(*args))
                self.assert_values(CGROUPS="y", CGROUP_SCHED="y", FAIR_GROUP_SCHED="y")
                if profile == "desktop":
                    self.assert_values(BLK_CGROUP="y", IOSCHED_BFQ="m", BFQ_GROUP_IOSCHED="y")
                before = self.config.read_bytes()
                self.assert_success(self.run_script(*args))
                self.assertEqual(self.config.read_bytes(), before)

    def test_real_desktop_restores_bfq_io_controller(self):
        self.prepare_baseline(BLK_CGROUP="n", IOSCHED_BFQ="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=desktop"))
        self.assert_values(BLK_CGROUP="y", IOSCHED_BFQ="y", BFQ_GROUP_IOSCHED="y")

    def test_real_profiles_enable_zswap_from_swap_disabled_baseline(self):
        for profile, compressor in (("server", "zstd"), ("desktop", "lz4")):
            with self.subTest(profile=profile):
                self.config.write_bytes(self.original)
                self.prepare_baseline(SWAP="n", ZRAM="m", ZSMALLOC="m")
                args = ("--strict", f"--optimization-profile={profile}")
                self.assert_success(self.run_script(*args))
                self.assert_values(SWAP="y", ZSWAP="y", ZSWAP_DEFAULT_ON="y", ZSMALLOC="y", ZRAM="m",
                                   ZSWAP_SHRINKER_DEFAULT_ON="y",
                                   ZSWAP_COMPRESSOR_DEFAULT=f'"{compressor}"')
                before = self.config.read_bytes()
                self.assert_success(self.run_script(*args))
                self.assertEqual(self.config.read_bytes(), before)
                self.assert_success(self.run_script(*args, "--zswap=off"))
                self.assert_values(SWAP="y", ZSWAP="n", ZSWAP_DEFAULT_ON="n")

    def test_real_zswap_profile_respects_protected_swap_disabled(self):
        self.prepare_baseline(SWAP="n")
        before = self.config.read_bytes()
        result = self.run_script("--strict", "--optimization-profile=server",
                                 "--protected-config-symbols=SWAP")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_ZSWAP requested=y final=n", result.stderr)
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--protected-config-symbols=SWAP", "--zswap=off"))
        self.assert_values(SWAP="n", ZSWAP="n")

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

    def test_real_profile_disables_base_small_and_enables_raid6_benchmark(self):
        self.prepare_baseline(EXPERT="y", BASE_SMALL="y", BLK_DEV_MD="y",
                              MD_RAID456="m", RAID6_PQ_BENCHMARK="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(BASE_SMALL="n", RAID6_PQ="m", RAID6_PQ_BENCHMARK="y")
        # Without RAID6_PQ enabled, RAID6_PQ_BENCHMARK must not be requested.
        self.config.write_bytes(self.original)
        self.prepare_baseline(BLK_DEV_MD="n", BTRFS_FS="n", DM_RAID="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(RAID6_PQ="n", RAID6_PQ_BENCHMARK="n")

    def test_real_profile_enables_bpf_jit_and_always_on(self):
        self.prepare_baseline(BPF_SYSCALL="y", BPF_JIT="n", BPF_JIT_ALWAYS_ON="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(BPF_JIT="y", BPF_JIT_ALWAYS_ON="y", BPF_JIT_DEFAULT_ON="y")
        # Explicit opt-out of BPF_JIT_ALWAYS_ON still keeps BPF_JIT enabled.
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--disable-symbols=BPF_JIT_ALWAYS_ON"))
        self.assert_values(BPF_JIT="y", BPF_JIT_ALWAYS_ON="n")

    def test_real_native_arch_devices_and_overrides(self):
        host = self.use_native_host()
        # No IOMMU or CPU facilities are claimed by this device-only capture.
        shutil.rmtree(host / "sys/class/iommu")
        for name, pci_class, module in (("nvme", "0x010802", "nvme"), ("usb", "0x0c0330", "xhci_pci"),
                                         ("net", "0x020000", "igb")):
            device = host / f"sys/bus/pci/devices/{name}"
            device.mkdir(parents=True)
            (device / "class").write_text(pci_class + "\n")
            driver = host / f"sys/bus/pci/drivers/{name}"
            driver.mkdir(parents=True)
            (driver / "module").symlink_to(f"/sys/module/{module}")
            (device / "driver").symlink_to(f"/sys/bus/pci/drivers/{name}")
        (device / "msi_irqs").mkdir()
        (device / "msi_irqs/32").write_text("msix\n")
        scsi = host / "sys/bus/scsi/devices/0:0:0:0"
        scsi.mkdir(parents=True)
        (scsi / "type").write_text("0\n")
        tpm = host / "sys/class/tpm/tpm0"
        tpm.mkdir(parents=True)
        (tpm / "tpm_version_major").write_text("2\n")
        driver = host / "sys/bus/platform/drivers/tpm_crb"
        driver.mkdir(parents=True)
        (driver / "module").symlink_to("/sys/module/tpm_crb")
        (tpm / "driver").symlink_to("/sys/bus/platform/drivers/tpm_crb")
        self.prepare_baseline(BLK_DEV_NVME="n", USB_SUPPORT="n", NET_VENDOR_INTEL="n", IGB="n", TCG_TPM="n")
        before = self.config.read_bytes()
        args = ("--strict", "--nativer-arch", "--native-cpu=none", "--platform-drivers=none")
        self.assert_success(self.run_script(*args, "--dry-run"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args))
        self.assert_values(PCI="y", PCI_MSI="y", BLK_DEV_NVME="y", USB_SUPPORT="y", USB="y",
                           USB_XHCI_HCD="y", USB_XHCI_PCI="y", NET_VENDOR_INTEL="y", IGB="m",
                           TCG_TPM="y", TCG_CRB="m")
        result = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), result)
        self.config.write_bytes(before)
        self.assert_success(self.run_script(*args, "--disable-symbols=IGB,USB_SUPPORT", "--tpm-support=off"))
        self.assert_values(IGB="n", USB_SUPPORT="n", USB_XHCI_PCI="n", TCG_TPM="n", TCG_CRB="n")

    def test_real_native_ahci_requires_attached_devices_and_honors_overrides(self):
        host = self.use_native_host()
        shutil.rmtree(host / "sys/class/iommu")
        path = "/sys/devices/pci0000:00/0000:00:17.0"
        device = host / path.lstrip("/")
        device.mkdir(parents=True)
        (device / "class").write_text("0x010601\n")
        bus = host / "sys/bus/pci/devices"
        bus.mkdir(parents=True)
        (bus / "0000:00:17.0").symlink_to(path)
        driver = host / "sys/bus/pci/drivers/ahci"
        driver.mkdir(parents=True)
        (driver / "module").symlink_to("/sys/module/ahci")
        (device / "driver").symlink_to("/sys/bus/pci/drivers/ahci")
        args = ("--strict", "--native-arch", "--native-cpu=none", "--platform-drivers=none")
        self.prepare_baseline(ATA="y", SATA_AHCI="n")
        result = self.run_script(*args)
        self.assert_success(result)
        self.assertIn("ahci-no-attached-device", result.stdout)
        self.assert_values(SATA_AHCI="n")
        self.assert_success(self.run_script(*args, "--enable-symbols=SATA_AHCI"))
        self.assert_values(SATA_AHCI="y", SATA_HOST="y")
        for mode in ("y", "m"):
            with self.subTest(baseline=mode):
                self.prepare_baseline(SATA_AHCI=mode)
                self.assert_success(self.run_script(*args))
                self.assert_values(SATA_AHCI=mode)

        scsi = device / "ata1/host0/target0:0:0/0:0:0:0"
        scsi.mkdir(parents=True)
        (scsi / "type").write_text("0\n")
        scsi_bus = host / "sys/bus/scsi/devices"
        scsi_bus.mkdir(parents=True)
        (scsi_bus / "0:0:0:0").symlink_to("/" + str(scsi.relative_to(host)))
        self.prepare_baseline(SATA_AHCI="n")
        self.assert_success(self.run_script(*args))
        self.assert_values(SATA_AHCI="y", SATA_HOST="y")
        self.assert_success(self.run_script(*args, "--disable-symbols=SATA_AHCI"))
        self.assert_values(SATA_AHCI="n")
        self.assert_success(self.run_script(*args, "--protected-config-symbols=SATA_AHCI"))
        self.assert_values(SATA_AHCI="n")

    def test_real_native_cpu_features(self):
        host = self.use_native_host()
        (host / "proc/cpuinfo").write_text("processor : 0\nvendor_id : GenuineIntel\nflags : x2apic fred mce mca\n")
        freq = host / "sys/devices/system/cpu/cpufreq/policy0"
        freq.mkdir(parents=True)
        (freq / "scaling_driver").write_text("intel_pstate\n")
        idle = host / "sys/devices/system/cpu/cpuidle"
        idle.mkdir()
        (idle / "current_driver").write_text("intel_idle\n")
        self.prepare_baseline(EXPERT="y", GENTOO_LINUX="n", CPU_SUP_INTEL="y", SCHED_MC_PRIO="n", ACPI_PROCESSOR="n",
                              CPU_FREQ="n", CPU_IDLE="n", IOMMU_SUPPORT="y", IRQ_REMAP="y",
                              X86_X2APIC="n", X86_FRED="n", X86_MCE="n", HYPERVISOR_GUEST="y")
        before = self.config.read_bytes()
        args = ("--strict", "--native-cpu=auto")
        self.assert_success(self.run_script(*args, "--dry-run"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args))
        self.assert_values(X86_X2APIC="y", X86_FRED="y", X86_MCE="y", X86_MCE_INTEL="y",
                           CPU_FREQ="y", CPU_IDLE="y", INTEL_IDLE="y", X86_INTEL_PSTATE="y")
        self.config.write_bytes(before)
        self.assert_success(self.run_script(*args, "--disable-symbols=X86_X2APIC,X86_FRED,X86_MCE,CPU_FREQ,CPU_IDLE"))
        self.assert_values(X86_X2APIC="n", X86_FRED="n", X86_MCE="n", CPU_FREQ="n", CPU_IDLE="n")
        self.config.write_bytes(before)
        (host / "proc/cpuinfo").write_text("processor : 0\nflags : aes\n")
        self.assert_success(self.run_script(*args, "--optimization-profile=server"))
        self.assert_values(X86_X2APIC="n", X86_FRED="n")

    def test_real_native_aes_acceleration_and_overrides(self):
        self.use_native_host()
        modern = "config CRYPTO_LIB_AES_XTS" in (self.tree / "lib/crypto/Kconfig").read_text()
        self.prepare_baseline(MODULES="y", CRYPTO="y", CRYPTO_AES="y", CRYPTO_AES_NI_INTEL="n",
                              DM_CRYPT="n", X86_NATIVE_CPU="n")
        args = ("--strict", "--optimization-profile=server", "--applications=cryptsetup")
        self.assert_success(self.run_script(*args))
        self.assert_values(CRYPTO_AES_NI_INTEL="n", X86_NATIVE_CPU="n")
        args += ("--native-cpu=auto",)
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args, "--dry-run"))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args))
        self.assert_values(CRYPTO_AES="y", CRYPTO_AES_NI_INTEL="n" if modern else "m",
                           DM_CRYPT="y", X86_NATIVE_CPU="n")
        before = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), before)
        self.assert_success(self.run_script(*args, "--enable-symbols=CRYPTO_AES_NI_INTEL"))
        self.assert_values(CRYPTO_AES_NI_INTEL="y")
        self.assert_success(self.run_script(*args, "--disable-symbols=CRYPTO_AES_NI_INTEL"))
        self.assert_values(CRYPTO_AES_NI_INTEL="n")
        self.assert_success(self.run_script(*args, "--protected-config-symbols=CRYPTO_AES_NI_INTEL"))
        self.assert_values(CRYPTO_AES_NI_INTEL="n")
        self.assert_success(self.run_script("--strict", "--native-cpu=on"))
        self.assert_values(X86_NATIVE_CPU="y", CRYPTO_AES_NI_INTEL="n" if modern else "m")
        self.assert_success(self.run_script("--strict", "--native-cpu=off"))
        self.assert_values(X86_NATIVE_CPU="n", CRYPTO_AES_NI_INTEL="n" if modern else "m")

    def test_real_profile_enables_network_scaling(self):
        self.prepare_baseline(PCPU_DEV_REFCNT="n", BQL="n", RPS="n", RFS_ACCEL="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(PCPU_DEV_REFCNT="y", BQL="y", RPS="y", RFS_ACCEL="y")

    def test_real_native_posted_msi_profiles_and_iommu_overrides(self):
        host = self.use_native_host()
        # AMD_IOMMU selects PCI_MSI, which would prevent testing its removal.
        self.prepare_baseline(IOMMU_SUPPORT="y", INTEL_IOMMU="y", IRQ_REMAP="y",
                              HYPERVISOR_GUEST="y", X86_POSTED_MSI="n", AMD_IOMMU="n")
        baseline = self.config.read_bytes()
        for profile in ("server", "desktop"):
            with self.subTest(profile=profile):
                self.config.write_bytes(baseline)
                args = ("--strict", f"--optimization-profile={profile}")
                self.assert_success(self.run_script(*args))
                self.assert_values(X86_POSTED_MSI="n")
                args += ("--native-cpu=auto",)
                self.assert_success(self.run_script(*args))
                self.assert_values(X86_POSTED_MSI="y")
                before = self.config.read_bytes()
                self.assert_success(self.run_script(*args))
                self.assertEqual(self.config.read_bytes(), before)
        for override in ("--iommu-support=off", "--disable-symbols=IRQ_REMAP",
                         "--disable-symbols=X86_POSTED_MSI", "--protected-config-symbols=X86_POSTED_MSI",
                         "--disable-symbols=INTEL_IOMMU", "--disable-symbols=PCI_MSI"):
            with self.subTest(override=override):
                self.config.write_bytes(baseline)
                self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                                    "--native-cpu=auto", override))
                self.assert_values(X86_POSTED_MSI="n")
        self.config.write_bytes(baseline)
        (host / "sys/class/iommu/dmar0/intel-iommu/cap").write_text("0\n")
        result = self.run_script("--strict", "--native-cpu=auto")
        self.assert_success(result)
        self.assertIn("lacks interrupt remapping or posted-interrupt support", result.stdout)
        self.assert_values(X86_POSTED_MSI="n")

    def test_real_x86_x2apic_respects_iommu_and_baremetal_or_guest(self):
        self.prepare_baseline(IOMMU_SUPPORT="y", IRQ_REMAP="y", X86_X2APIC="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server"))
        self.assert_values(X86_X2APIC="y")
        self.config.write_bytes(self.original)
        self.prepare_baseline(IOMMU_SUPPORT="y", IRQ_REMAP="y", X86_X2APIC="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--host-type=baremetal", "--iommu-support=off"))
        self.assert_values(IOMMU_SUPPORT="n", X86_X2APIC="n")
        self.config.write_bytes(self.original)
        self.prepare_baseline(HYPERVISOR_GUEST="y", X86_X2APIC="n")
        self.assert_success(self.run_script("--strict", "--optimization-profile=server",
                                            "--iommu-support=off"))
        self.assert_values(HYPERVISOR_GUEST="y", IOMMU_SUPPORT="n", X86_X2APIC="y")

    def test_real_guest_host_types_pass_strict_mode(self):
        for host_type, expected in [
            ("qemu", {"KVM_GUEST": "y", "VIRTIO_MENU": "y", "VIRTIO_PCI": "y",
                      "SCSI_VIRTIO": "y", "HALTPOLL_CPUIDLE": "y",
                      "CPU_IDLE_GOV_HALTPOLL": "y", "X86_X2APIC": "y"}),
            ("vmware", {"VMWARE_VMCI": "y", "VMWARE_PVSCSI": "y", "VMXNET3": "y",
                        "HALTPOLL_CPUIDLE": "n", "X86_X2APIC": "y"}),
            ("hyperv", {"HYPERV": "y", "HYPERV_VMBUS": "y", "HYPERV_STORAGE": "y",
                        "HYPERV_KEYBOARD": "y", "HALTPOLL_CPUIDLE": "n", "X86_X2APIC": "y"}),
            ("virtualbox", {"VIRT_DRIVERS": "y", "VBOXGUEST": "y", "VBOXSF_FS": "y",
                            "HALTPOLL_CPUIDLE": "n", "X86_X2APIC": "y"}),
        ]:
            with self.subTest(host_type=host_type):
                self.config.write_bytes(self.original)
                self.assert_success(self.run_script("--strict", f"--host-type={host_type}"))
                self.assert_values(**expected)

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

    def test_real_native_hardware_detection_improvements(self):
        host = self.use_native_host()
        shutil.rmtree(host / "sys/class/iommu")
        (host / "proc/cpuinfo").write_text(
            "processor : 0\nvendor_id : AuthenticAMD\ncpu family : 25\nmodel : 97\nflags : umip pku mce mca\n"
        )
        dmi = host / "sys/class/dmi/id"
        dmi.mkdir(parents=True)
        (dmi / "sys_vendor").write_text("Example\n")
        (dmi / "product_name").write_text("Workstation\n")
        for idx in range(2):
            pol = host / f"sys/devices/system/cpu/cpufreq/policy{idx}"
            pol.mkdir(parents=True)
            (pol / "scaling_driver").write_text("amd-pstate-epp\n")
        smbus = host / "sys/bus/pci/devices/0000:00:14.0"
        smbus.mkdir(parents=True)
        (smbus / "vendor").write_text("0x1022\n")
        (smbus / "device").write_text("0x790b\n")
        (smbus / "class").write_text("0x0c0500\n")
        bmc = host / "sys/bus/pci/devices/0000:05:00.0"
        bmc.mkdir(parents=True)
        (bmc / "vendor").write_text("0x1a03\n")
        (bmc / "device").write_text("0x2000\n")
        (bmc / "class").write_text("0x030000\n")
        i2c = host / "sys/bus/i2c/devices/0-0050"
        i2c.mkdir(parents=True)
        (i2c / "name").write_text("spd5118\n")
        hwmon = host / "sys/class/hwmon/hwmon0"
        hwmon.mkdir(parents=True)
        (hwmon / "name").write_text("drivetemp\n")
        (host / "proc/self").mkdir(parents=True)
        (host / "proc/self/mountinfo").write_text(
            "26 2 8:1 / / rw - ext4 /dev/sda1 rw\n"
            "27 26 8:2 / /efi rw - vfat /dev/sda2 rw,codepage=850,iocharset=iso8859-15\n"
        )
        self.prepare_baseline(
            MODULES="y", BLK_DEV_INITRD="y", WATCHDOG="n", SP5100_TCO="n",
            I2C_PIIX4="n", SENSORS_SPD5118="n", SENSORS_DRIVETEMP="n",
            DRM="y", DRM_AST="n", DRM_I915="m", DRM_NOUVEAU="m",
            X86_INTEL_MEMORY_PROTECTION_KEYS="n",
            HYPERVISOR_GUEST="y", PARAVIRT="y", KVM_GUEST="y",
            VFAT_FS="n", NLS_CODEPAGE_850="n", NLS_ISO8859_15="n",
        )
        args = ("--strict", "--native-arch", "--host-type=auto", "--video-support=auto")
        result = self.run_script(*args)
        self.assert_success(result)
        self.assertEqual(result.stdout.count("active CPU frequency driver: amd-pstate-epp"), 1)
        self.assert_values(
            KVM_GUEST="n", DRM_AST="y", DRM_I915="n", DRM_NOUVEAU="n",
            I2C_PIIX4="m", SP5100_TCO="m", SENSORS_SPD5118="m", SENSORS_DRIVETEMP="m",
            X86_INTEL_MEMORY_PROTECTION_KEYS="y",
            EXT4_FS="y", VFAT_FS="y", NLS_CODEPAGE_850="m", NLS_ISO8859_15="m",
        )
        # Idempotence on second run.
        after = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), after)

    def test_real_kernel_source_fallback_tpm_powercap_and_strict_pruning(self):
        host = self.use_native_host()
        shutil.rmtree(host / "sys/class/iommu")
        (host / "proc/cpuinfo").write_text(
            "processor : 0\nvendor_id : AuthenticAMD\ncpu family : 25\nmodel : 97\nflags : mce mca\n"
        )
        dmi = host / "sys/class/dmi/id"
        dmi.mkdir(parents=True)
        (dmi / "sys_vendor").write_text("ASUS\n")
        (dmi / "product_name").write_text("Workstation\n")
        (dmi / "board_name").write_text("PRIME X670-P WIFI\n")
        # Unbound ACPI GPIO, WMI BMOF, and active ACPI power button.
        for dev, mod in (
            ("AMDIF031:01", "acpi:AMDIF031:"),
            ("PNP0C0C:00", "acpi:PNP0C0C:"),
        ):
            p = host / f"sys/bus/acpi/devices/{dev}"
            p.mkdir(parents=True)
            (p / "status").write_text("15\n")
            (p / "modalias").write_text(mod + "\n")
        wmi = host / "sys/bus/wmi/devices/05901221"
        wmi.mkdir(parents=True)
        (wmi / "modalias").write_text("wmi:05901221-D566-11D1-B2F0-00A0C9062910\n")
        # Active powercap RAPL domain.
        (host / "sys/class/powercap/intel-rapl:0").mkdir(parents=True)
        # TPM 2.0 on CRB transport only.
        tpm_dev = host / "sys/class/tpm/tpm0/device"
        tpm_dev.mkdir(parents=True)
        (tpm_dev.parent / "tpm_version_major").write_text("2\n")
        (tpm_dev / "driver").symlink_to("/sys/bus/acpi/drivers/tpm_crb_acpi")

        self.prepare_baseline(
            MODULES="y", BLK_DEV_INITRD="y",
            FTRACE="y", BRANCH_PROFILE_NONE="y",
            GPIOLIB="y", GPIO_AMDPT="n",
            ACPI_WMI="y", X86_PLATFORM_DEVICES="y", WMI_BMOF="n",
            HWMON="y", SENSORS_NCT6775="n",
            POWERCAP="n", INTEL_RAPL="n",
            ACPI_BUTTON="n",
            TCG_TPM="n", TCG_CRB="n", TCG_TIS="n", TCG_VTPM_PROXY="n",
        )
        args = (
            "--strict", "--all-optimizations", "--native-arch",
            "--tpm-support=auto",
        )
        result = self.run_script(*args)
        self.assert_success(result)
        self.assert_values(
            FTRACE="n",
            GPIO_AMDPT="m",
            WMI_BMOF="m",
            SENSORS_NCT6775="m",
            POWERCAP="y",
            INTEL_RAPL="m",
            ACPI_BUTTON="m",
            TCG_TPM="y",
            TCG_CRB="y",
            TCG_TIS="n",
            TCG_VTPM_PROXY="n",
        )
        # Idempotence on second run.
        after = self.config.read_bytes()
        self.assert_success(self.run_script(*args))
        self.assertEqual(self.config.read_bytes(), after)


if __name__ == "__main__":
    unittest.main()
