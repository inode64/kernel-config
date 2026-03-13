# kernel-config

`kernel-config.sh` is a pragmatic Bash script for pruning and tuning a Linux kernel `.config` file.

It is designed for people who already build custom kernels and want a repeatable way to:

- remove debug, test, legacy, or unused subsystems
- tune scheduler defaults for server, desktop, or realtime use
- keep only the CPU, GPU, firmware, and platform support they actually need
- enable kernel features commonly required by specific applications
- auto-detect some host characteristics and translate them into kernel config changes

The script edits an existing kernel configuration using `scripts/config`, creates a timestamped backup, and finishes with `make olddefconfig` to normalize dependencies.

## What It Does

The script works on an existing kernel source tree and `.config` file.

High-level flow:

1. Resolve the kernel source directory and target `.config`.
2. Create a backup of the current config.
3. Enable or disable symbols through `scripts/config`.
4. Optionally auto-detect host traits such as CPU vendor, boot mode, TPM, NUMA, CPU count, and others.
5. Run `make olddefconfig`.

It is not a full kernel config generator. It assumes you already have a baseline config and want to refine it.

## Requirements

- Bash
- a Linux kernel source tree
- a writable `.config`
- `make`
- `scripts/config` in the kernel tree

If `scripts/config` is missing, the script tries to generate it with:

```bash
make -s scripts
```

Some auto-detection features use host tools if available:

- `xfs_info` for XFS feature detection
- `findmnt` for mounted XFS discovery

## Quick Start

```bash
cp /usr/src/linux/.config /usr/src/linux/.config.orig
./kernel-config.sh /usr/src/linux
make -C /usr/src/linux olddefconfig
make -C /usr/src/linux -j"$(nproc)"
```

You can also point to a specific config file:

```bash
./kernel-config.sh --kernel-srcdir /usr/src/linux --config-file /usr/src/linux/.config
```

## Usage

```text
./kernel-config.sh [OPTIONS] [KERNEL_SRCDIR] [CONFIG_FILE] [VAR=VALUE...]
```

Supported input styles:

- command-line flags such as `--video-support intel`
- environment variables such as `VIDEO_SUPPORT=intel`
- trailing `VAR=VALUE` assignments
- positional `KERNEL_SRCDIR` and `CONFIG_FILE`

Precedence:

1. Environment variables set defaults.
2. CLI flags override environment variables.
3. Trailing `VAR=VALUE` arguments also override earlier defaults.

Boolean flags accept `0` or `1`. If passed without a value, they imply `1`.

## Main Parameters

### Core tuning

- `ALL_OPTIMIZATIONS=0|1`
  Enables the script's optimization preset. It does not enable hardening pruning.

- `OPTIMIZATION_PROFILE=none|server|desktop|realtime`
  Adjusts defaults such as scheduler/preemption/HZ choices.

- `KEEP_OBSERVABILITY=0|1`
  Keeps or prunes tracing, perf, debugfs, and related observability features.

- `PRUNE_LEGACY=0|1`
  Disables old compatibility options and symbols marked legacy/deprecated in Kconfig.

- `PRUNE_DEBUG_TRACE=0|1`
  Disables debug and trace options discovered from Kconfig prompts.

- `PRUNE_HARDENING=0|1`
  Disables hardening and mitigation symbols discovered from Kconfig prompts.

- `PRUNE_SELFTEST=0|1`
  Disables selftests and test-only options discovered from Kconfig prompts.

- `PRUNE_SANITIZERS=0|1`
  Disables sanitizer-related options such as KASAN, KCSAN, UBSAN, KCOV, and KMEMLEAK.

- `PRUNE_COVERAGE=0|1`
  Disables gcov coverage and profiling options.

- `PRUNE_FAULT_INJECTION=0|1`
  Disables fault-injection and forced-failure testing options.

### Platform and hardware filters

- `CPU_VENDOR_FILTER=none|auto|amd|intel`
  On x86, prunes options specific to the other CPU vendor.

- `VIDEO_SUPPORT=none|auto|amd|intel|nvidia|nouveau`
  Keeps only the selected display driver stack.

  Notes:
  `nvidia` means a proprietary NVIDIA setup, so the script prunes Nouveau and the in-tree AMD/Intel stacks.

- `UEFI_SUPPORT=none|auto|on|off`
  Keeps or prunes common EFI/UEFI kernel support.

- `INITRD_SUPPORT=none|auto|on|off`
  Keeps or prunes `CONFIG_BLK_DEV_INITRD`.

- `TPM_SUPPORT=none|auto|on|off`
  Keeps or prunes TPM support and attempts to detect TPM `1.2` or `2.0`.

- `DMA_ENGINE_SUPPORT=none|auto|on|off`
  Keeps or prunes `CONFIG_DMADEVICES` based on whether the running host exposes `dmaengine` devices.

- `IOMMU_SUPPORT=none|auto|on|off`
  Keeps or prunes generic IOMMU support and selects `AMD_IOMMU` or `INTEL_IOMMU` from the resolved CPU vendor.

- `NUMA_SUPPORT=none|auto|on|off`
  Keeps or prunes `CONFIG_NUMA` based on currently visible NUMA nodes.

- `NR_CPUS=none|auto|<integer>`
  Sets `CONFIG_NR_CPUS` to a fixed value or to the detected host CPU count. If the config defines a valid `NR_CPUS` range, the value is clamped into that range.

### Virtualization and workload hints

- `HOST_TYPE=native|qemu|vmware|hyperv|virtualbox`
  Tunes guest/virtualization drivers for the selected host type.

- `APPLICATIONS=<comma-separated-list>`
  Enables kernel features commonly required by selected applications.

  Supported profiles:
  `samba`, `firehol`, `firewalld`, `openvswitch`, `ceph`, `nfs-client`, `nfs-server`, `openvpn`, `wireguard`, `docker`, `qemu`, `atop`, `bmon`, `btop`, `htop`, `iotop-c`, `cryptsetup`

  Example:

```bash
APPLICATIONS="docker,firewalld,wireguard" ./kernel-config.sh /usr/src/linux
```

### Additional pruning toggles

- `KEEP_BPF=0|1`
- `KEEP_COMPAT32=0|1`
- `PRUNE_UNUSED_NET=0|1`
- `PRUNE_OLD_HW=0|1`
- `PRUNE_X86_OLD_PLATFORMS=0|1`
- `KEEP_LEGACY_ATA=0|1`
- `PRUNE_INSECURE=0|1`
- `PRUNE_RADIOS=0|1`
- `PRUNE_DMA_ATTACK_SURFACE=0|1`

These are narrower switches for optional subsystems and aggressive cleanup.

## Auto-Detection Behavior

Several parameters support `auto`. Current detection logic is intentionally simple and host-based.

- `CPU_VENDOR_FILTER=auto`
  Reads `/proc/cpuinfo`.

- `VIDEO_SUPPORT=auto`
  Tries `/sys/class/drm` first, then falls back to PCI vendor detection.

- `UEFI_SUPPORT=auto`
  Checks `/sys/firmware/efi`.

- `INITRD_SUPPORT=auto`
  Tries boot parameters from `/sys/kernel/boot_params/data`, with a fallback to `initrd=` in `/proc/cmdline`.

- `TPM_SUPPORT=auto`
  Checks `/sys/class/tpm/tpm*` and tries to identify version `1.2` or `2.0`.

- `DMA_ENGINE_SUPPORT=auto`
  Checks for devices under `/sys/class/dma`.

- `IOMMU_SUPPORT=auto`
  Resolves the CPU vendor and selects `AMD_IOMMU` or `INTEL_IOMMU`.

- `NUMA_SUPPORT=auto`
  Checks `/sys/devices/system/node`.

- `NR_CPUS=auto`
  Uses CPU topology from `/sys/devices/system/cpu`, then falls back to `getconf`, `nproc`, or `/proc/cpuinfo`.

- XFS feature checks
  If XFS is present and mounted, the script inspects mounted filesystems with `xfs_info` and toggles:
  `CONFIG_XFS_SUPPORT_V4`
  `CONFIG_XFS_SUPPORT_ASCII_CI`

Important limitation:

Auto-detection only sees the currently running host. It does not try to infer hardware that is absent, unplugged, disabled in firmware, or not visible to the running kernel.

## Examples

### Minimal run

```bash
./kernel-config.sh /usr/src/linux
```

### Server-oriented pruning

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --all-optimizations \
  --optimization-profile server \
  --keep-observability 0 \
  --prune-legacy \
  --prune-debug-trace-kconfig \
  --prune-selftest-kconfig
```

### Desktop build with Intel graphics and UEFI

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --optimization-profile desktop \
  --cpu-vendor-filter intel \
  --video-support intel \
  --uefi-support on \
  --initrd-support on
```

### AMD host with automatic IOMMU and CPU count

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --cpu-vendor-filter auto \
  --iommu-support auto \
  --nr-cpus auto
```

### Fileserver profile

```bash
APPLICATIONS="samba,nfs-server,cryptsetup" \
TPM_SUPPORT=auto \
./kernel-config.sh /usr/src/linux
```

### Virtualization host

```bash
APPLICATIONS="qemu,docker,openvswitch" \
IOMMU_SUPPORT=auto \
NR_CPUS=auto \
./kernel-config.sh /usr/src/linux
```

### Explicit config file and manual overrides

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --config-file /usr/src/linux/.config \
  VIDEO_SUPPORT=nouveau \
  UEFI_SUPPORT=off \
  NR_CPUS=16
```

## Output and Safety

The script:

- creates a backup named like `.config.bak.YYYYMMDD-HHMMSS`
- prints each symbol it enables or disables
- runs `make olddefconfig`
- prints a suggested `diff` command at the end

Recommended review flow:

```bash
diff -u /path/to/.config.bak.TIMESTAMP /path/to/.config | less
```

If your kernel tree provides it:

```bash
scripts/diffconfig old.config new.config
```

## Caveats

- This script is opinionated. Review the final config before using it in production.
- `PRUNE_HARDENING=1` reduces security hardening.
- `PRUNE_LEGACY=1` may remove compatibility features you still depend on.
- `PRUNE_SANITIZERS=0`, `PRUNE_COVERAGE=0`, and `PRUNE_FAULT_INJECTION=0` keep test-oriented instrumentation that the script now prunes by default.
- Application profiles enable common requirements, not every optional kernel feature that a project can use.
- `HOST_TYPE` and `APPLICATIONS` can re-enable symbols after broader pruning phases.
- Some symbols are architecture-specific, so results depend on the target kernel tree and baseline config.

## License

This repository includes a [`LICENSE`](LICENSE) file. See it for distribution terms.
