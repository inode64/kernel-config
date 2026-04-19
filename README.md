# kernel-config

`kernel-config.sh` is a pragmatic Bash script for pruning and tuning a Linux kernel `.config` file.

It is designed for people who already build custom kernels and want a repeatable way to:

- remove debug, test, legacy, or unused subsystems
- tune scheduler defaults for server, desktop, or realtime use
- keep only the CPU, GPU, firmware, and platform support they actually need
- enable kernel features commonly required by specific applications
- auto-detect some host characteristics and translate them into kernel config changes

The script edits an existing kernel configuration using `scripts/config`, creates a timestamped backup, and finishes with `make olddefconfig` to normalize dependencies.

It also supports a dry run mode that applies all changes to a temporary copy and prints only the config symbols that would change without modifying the real `.config`.

## What It Does

The script works on an existing kernel source tree and `.config` file.

High-level flow:

1. Resolve the kernel source directory and target `.config`.
2. Create a backup of the current config.
3. Enable or disable symbols through `scripts/config`.
4. Optionally auto-detect host traits such as CPU vendor, boot mode, TPM, NUMA, CPU count, and others.
5. Run `make olddefconfig`.

In `--dry-run` mode, steps 2-5 are performed against a temporary config copy and the script prints only the final symbol changes instead of writing the original file.

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
./kernel-config.sh --dry-run /usr/src/linux
./kernel-config.sh /usr/src/linux
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

Boolean flags are off unless enabled explicitly with `--foo`. For `VAR=VALUE` and `--foo=value`, accepted boolean values are `true/`, `yes/no`, `on/off`, `enable/disable`, and `1/0`.

`--dry-run` is a boolean flag. When enabled, the script prints only the final `.config` symbols that would change and leaves the real file untouched.

`PROTECTED_CONFIG_SYMBOLS` accepts a comma-separated list of symbols that the script must not modify. Symbols may be passed as `FOO` or `CONFIG_FOO`.

## Main Parameters

### Core tuning

- `DRY_RUN`
  Shows only the `.config` symbols that would change without modifying the real file.

- `ALL_OPTIMIZATIONS`
  Enables the script's optimization preset. It does not enable hardening pruning.

- `OPTIMIZATION_PROFILE=none|server|desktop|realtime`
  Adjusts defaults such as scheduler/preemption/HZ choices.

- `PRUNE_OBSERVABILITY`
  Disables tracing, perf, debugfs, and related observability features.

- `PRUNE_LEGACY`
  Disables old compatibility options and symbols marked legacy/deprecated in Kconfig.

- `PRUNE_DEBUG_TRACE`
  Disables debug and trace options discovered from Kconfig prompts.

- `PRUNE_HARDENING`
  Disables hardening and mitigation symbols discovered from Kconfig prompts.

- `PRUNE_SELFTEST`
  Disables selftests and test-only options discovered from Kconfig prompts.

- `PRUNE_SANITIZERS`
  Disables sanitizer-related options such as KASAN, KCSAN, UBSAN, KCOV, and KMEMLEAK.

- `PRUNE_COVERAGE`
  Disables gcov coverage and profiling options.

- `PRUNE_FAULT_INJECTION`
  Disables fault-injection and forced-failure testing options.

- `PRUNE_DANGEROUS`
  Disables symbols whose Kconfig prompt is explicitly marked `DANGEROUS` or `unsafe`.

- `PRUNE_UNUSED_MODULES`
  Probes direct `CONFIG_*=m` module mappings that are not currently loaded. It disables symbols only when the module fails to load or does not remain initialized after probing; if the module is simply unused but loadable, it is only reported.
  Module-name comparisons normalize `-` and `_` so Kbuild names like `kvm-amd` still match runtime names such as `kvm_amd`.

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

- `PROTECTED_CONFIG_SYMBOLS=<comma-separated-list>`
  Prevents the script from enabling, disabling, or overwriting the listed symbols. The default protected list already includes `CONFIG_ARCH_PKEY_BITS`.

### Virtualization and workload hints

- `HOST_TYPE=baremetal|qemu|vmware|hyperv|virtualbox`
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

- `PRUNE_BPF`
- `PRUNE_COMPAT32`
- `PRUNE_UNUSED_NET`
- `PRUNE_UNUSED_MODULES`
- `PRUNE_OLD_HW`
- `PRUNE_X86_OLD_PLATFORMS`
- `PRUNE_LEGACY_ATA`
- `PRUNE_INSECURE`
- `PRUNE_RADIOS`
- `PRUNE_DMA_ATTACK_SURFACE`

These are narrower switches for optional subsystems and aggressive cleanup.

`PRUNE_UNUSED_MODULES` is intentionally conservative: it requires root, the target tree's `kernelrelease` to match the running kernel, and only handles direct one-symbol/one-module `obj-$(CONFIG_FOO) += foo.o` mappings.

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

### Preview changes without writing them

```bash
./kernel-config.sh --dry-run /usr/src/linux
```

### Server-oriented pruning

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --all-optimizations \
  --optimization-profile server \
  --prune-observability \
  --prune-legacy \
  --prune-debug-trace \
  --prune-selftest
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

### Protect symbols from script changes

```bash
./kernel-config.sh \
  --kernel-srcdir /usr/src/linux \
  --protected-config-symbols CONFIG_ARCH_PKEY_BITS,CONFIG_NR_CPUS_RANGE_END
```

## Output and Safety

The script:

- creates a backup named like `.config.bak.YYYYMMDD-HHMMSS`
- prints each symbol it enables or disables
- runs `make olddefconfig`
- prints a suggested `diff` command at the end

In `--dry-run`, it prints a compact `CONFIG_FOO: old -> new` summary instead of a unified diff.

If `PRUNE_UNUSED_MODULES` is enabled, the script temporarily probes unloaded modules and then restores the loaded-module set back to its initial state before continuing.
If it cannot restore that initial module set for a probe, it stops further module probing and continues with the rest of the script.

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
- `PRUNE_HARDENING` reduces security hardening.
- `PRUNE_LEGACY` may remove compatibility features you still depend on.
- `PRUNE_SANITIZERS`, `PRUNE_COVERAGE`, and `PRUNE_FAULT_INJECTION` remove test-oriented instrumentation.
- `PRUNE_DANGEROUS` removes edge-case options that upstream Kconfig labels as dangerous or unsafe, such as late microcode loading or risky device/debug paths.
- `PRUNE_UNUSED_MODULES` uses a host/runtime heuristic. It only auto-disables modules that fail to load or do not stay initialized during probing; modules that are merely unused but loadable are reported and kept.
- Application profiles enable common requirements, not every optional kernel feature that a project can use.
- `HOST_TYPE` and `APPLICATIONS` can re-enable symbols after broader pruning phases.
- `PROTECTED_CONFIG_SYMBOLS` only protects against changes made by this script. `make olddefconfig` can still adjust dependent symbols if Kconfig requires it.
- Some symbols are architecture-specific, so results depend on the target kernel tree and baseline config.

## License

This repository includes a [`LICENSE`](LICENSE) file. See it for distribution terms.
