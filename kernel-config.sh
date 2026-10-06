#!/usr/bin/env bash
set -Eeuo pipefail

if ((BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 2))); then
    echo "This script requires bash 5.2 or later (found ${BASH_VERSION})" >&2
    exit 1
fi

SCRIPT_DIR="$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")"

# Usage:
#   ./kernel-config.sh [OPTIONS] [KERNEL_SRCDIR] [CONFIG_FILE] [VAR=VALUE...]
#
# Examples:
#   ./kernel-config.sh /usr/src/linux
#   PRUNE_OBSERVABILITY=true PRUNE_LEGACY=true ./kernel-config.sh /usr/src/linux
#   ./kernel-config.sh /usr/src/linux .config PRUNE_LEGACY=true PRUNE_OBSERVABILITY=true
#   ./kernel-config.sh --kernel-srcdir /usr/src/linux --config-file .config --prune-legacy
#   ./kernel-config.sh --dry-run /usr/src/linux
#   ./kernel-config.sh --kernel-srcdir /usr/src/linux --all-optimizations
#
# Optional variables and flags:
#   ALL_OPTIMIZATIONS         -> enable the script's full optimization preset (flag-only via --all-optimizations)
#   DRY_RUN                   -> show only the config symbols that would change without modifying the real file
#   CHECK                     -> validate inputs and prerequisites without changing files or probing modules
#   STRICT                    -> reject unmet requests or changed protected symbols after olddefconfig
#   SCHED_CACHE=none           -> none, on, off; control cache-aware scheduler load balancing
#   KERNEL_COMPRESSION=keep    -> keep, gzip, bzip2, lzma, xz, lzo, lz4, zstd
#   INITRD_COMPRESSION=keep    -> keep, auto, none, gzip, bzip2, lzma, xz, lzo, lz4, zstd; retain existing decoders
#   INITRAMFS_GENERATOR=auto   -> auto, genkernel, ugrd, none; read-only compatibility inspection
#   INITRAMFS_CONFIG           -> optional producer configuration (relative to invocation directory)
#   INITRAMFS_IMAGE            -> optional existing image to inspect (relative to invocation directory)
#   INITRAMFS_COMPRESSION=auto -> producer CLI compression override; does not edit/run the producer
#   UCLAMP=keep                -> keep, on, off; utilization clamping capability
#   AUTOGROUP=keep             -> keep, on, off; automatic session grouping capability
#   MODULE_FORCE_LOAD=keep     -> keep, on, off; permit forced loading of modules
#   MODULE_FORCE_UNLOAD=keep   -> keep, on, off; permit forced unloading of modules
#   NFS_UDP=keep               -> keep, on, off; NFS client UDP transport (inverse Kconfig option)
#   OBSOLETE_CRYPTO=keep       -> keep, on, off; obsolete algorithms, independent of the AF_ALG ABI
#   AUDIT_KCONFIG              -> read-only warning report; also accepts Kconfig-only snapshots
#   FIRMWARE_COMPRESSION=keep  -> keep, on, off; on enables XZ and ZSTD firmware decoding
#   PREEMPTION=keep       -> keep, none, voluntary, full, lazy, rt; select preemption model
#   PREEMPT_DYNAMIC=keep       -> keep, on, off; allow boot-time preemption changes
#   TICK_MODE=keep       -> keep, periodic, idle, full; select timer tick handling
#   THP=keep       -> keep, off, always, madvise, never; transparent hugepage policy
#   LRU_GEN=keep       -> keep, on, off; multi-generation LRU and its default activation
#   ZSWAP=keep       -> keep, on, off; compressed swap cache and its default activation
#   ZSWAP_COMPRESSOR=keep       -> keep, lzo, lz4, lz4hc, zstd, deflate, 842
#   ZRAM=keep       -> keep, off, module, builtin; compressed RAM block device
#   ZRAM_COMPRESSOR=keep       -> keep, lzo-rle, lzo, lz4, lz4hc, zstd, deflate, 842
#   NUMA_BALANCING=keep       -> keep, on, off; NUMA memory placement and its default activation
#   KMALLOC_PARTITION=keep       -> keep, off, random, typed; slab cache partitioning
#   TCP_CONGESTION=keep       -> keep, cubic, bbr, reno; default TCP congestion control
#   IO_URING=keep       -> keep, on, off; io_uring support
#   PRUNE_RUNTIME_VERIFICATION -> disable Runtime Verification and its monitors
#   OPTIMIZATION_PROFILE=none -> none, server, desktop, realtime; tune scheduler/tick defaults
#   VALIDATION_MODE=warn      -> warn or fail (strict) when olddefconfig overrides requested values
#   PREEMPT_MODE=auto         -> auto, none, voluntary, lazy, full, rt; override profile preemption
#   TIMER_HZ=auto             -> auto, 100, 250, 300, 1000; override profile timer frequency
#   SCHED_CACHE_MODE=auto     -> auto, on, off; control cache-aware scheduler load balancing
#   MGLRU_MODE=auto           -> auto, on, off; control Multi-Gen LRU and its default state
#   NUMA_BALANCING_MODE=auto  -> auto, on, off; control NUMA balancing and 7.2 NUMA migration support
#   NATIVE_CPU=none           -> none, on, off; build with -march=native via CONFIG_X86_NATIVE_CPU (6.16+, x86_64 only)
#   PRUNE_OBSERVABILITY       -> disable perf/bpf/ftrace/debugfs and related observability features
#   PRUNE_LEGACY              -> disable old compatibility options and legacy/deprecated symbols
#   PRUNE_DEBUG_TRACE         -> disable debug/trace symbols
#   PRUNE_HARDENING           -> disable hardening/mitigation symbols
#   PRUNE_SELFTEST            -> disable selftest symbols
#   PRUNE_SANITIZERS          -> disable sanitizer-related symbols
#   PRUNE_COVERAGE            -> disable coverage/profiling symbols
#   PRUNE_FAULT_INJECTION     -> disable fault-injection/test failure symbols
#   PRUNE_DANGEROUS           -> disable symbols explicitly marked DANGEROUS in Kconfig
#   PRUNE_UNUSED_MODULES      -> probe module configs not currently loaded and disable direct module symbols that can be tested safely
#   CPU_VENDOR_FILTER=none    -> none, auto, amd, intel; disable x86 options for the other vendor and select its pstate driver
#   VIDEO_SUPPORT=none        -> none, auto, amd, intel, nvidia, nouveau; keep only the selected GPU stack
#   UEFI_SUPPORT=none         -> none, auto, on, off; keep or prune common EFI/UEFI kernel support
#   INITRD_SUPPORT=none       -> none, auto, on, off; keep or prune initramfs/initrd boot support
#   TPM_SUPPORT=none          -> none, auto, on, off; keep or prune TPM support and detect TPM 1.2/2.0 on the host
#   DMA_ENGINE_SUPPORT=none   -> none, auto, on, off; keep or prune DMA Engine support based on currently exposed dmaengine devices
#   IOMMU_SUPPORT=none        -> none, auto, on, off; keep or prune IOMMU support and select AMD/Intel IOMMU by CPU vendor
#   NUMA_SUPPORT=none         -> none, auto, on, off; keep or prune NUMA support based on currently exposed NUMA nodes
#   NR_CPUS=none              -> none, auto, or an integer; adjust CONFIG_NR_CPUS to the detected or requested CPU count
#   PROTECTED_CONFIG_SYMBOLS  -> comma-separated config symbols the script must not alter; defaults to CONFIG_ARCH_PKEY_BITS
#   APPLICATIONS=none         -> comma-separated app profiles: desktop, multimedia, rocm, nebula, warp, samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, qemu, atop, bmon, btop, htop, iotop-c, cryptsetup
#   HOST_TYPE=none            -> none, baremetal, qemu (alias: kvm), vmware, hyperv, virtualbox; tune guest-specific options
#
# Recommended:
#   ./kernel-config.sh /path/to/kernel
#   make -j$(nproc)

detect_default_ksrcdir() {
    if [[ -f "$PWD/Kconfig" ]]; then
        printf '%s\n' "$PWD"
        return
    fi

    local candidate
    candidate="$(
        find "$PWD" -maxdepth 1 -mindepth 1 -type d -name 'linux-*' 2>/dev/null \
            | sort -rV \
            | head -n 1
    )"

    if [[ -n "$candidate" && -f "$candidate/Kconfig" ]]; then
        printf '%s\n' "$candidate"
        return
    fi

    printf '%s\n' "$PWD"
}

usage() {
    cat <<'EOF'
Usage:
  ./kernel-config.sh [OPTIONS] [KERNEL_SRCDIR] [CONFIG_FILE] [VAR=VALUE...]

Options:
  --kernel-srcdir PATH
  --config-file PATH
  --dry-run
  --check
  --audit-kconfig
  --strict
  --sched-cache MODE
  --kernel-compression FORMAT
  --initrd-compression FORMAT
  --initramfs-generator NAME
  --initramfs-config PATH
  --initramfs-image PATH
  --initramfs-compression FORMAT
  --uclamp MODE
  --autogroup MODE
  --module-force-load MODE
  --module-force-unload MODE
  --nfs-udp MODE
  --obsolete-crypto MODE
  --firmware-compression MODE
  --preemption VALUE
  --preempt-dynamic VALUE
  --tick-mode VALUE
  --thp VALUE
  --lru-gen VALUE
  --zswap VALUE
  --zswap-compressor VALUE
  --zram VALUE
  --zram-compressor VALUE
  --numa-balancing VALUE
  --kmalloc-partition VALUE
  --tcp-congestion VALUE
  --io-uring VALUE
  --prune-runtime-verification
  --disable-symbols LIST
  --module-symbols LIST
  --enable-symbols LIST
  --all-optimizations
  --optimization-profile PROFILE
  --validation-mode MODE
  --preempt-mode MODE
  --timer-hz VALUE
  --sched-cache-mode MODE
  --mglru-mode MODE
  --numa-balancing-mode MODE
  --native-cpu MODE
  --cpu-vendor-filter MODE
  --video-support MODE
  --uefi-support MODE
  --initrd-support MODE
  --tpm-support MODE
  --dma-engine-support MODE
  --iommu-support MODE
  --numa-support MODE
  --nr-cpus VALUE
  --protected-config-symbols LIST
  --applications LIST
  --host-type TYPE
  --prune-observability
  --prune-legacy
  --prune-debug-trace
  --prune-hardening
  --prune-selftest
  --prune-sanitizers
  --prune-coverage
  --prune-fault-injection
  --prune-dangerous
  --prune-unused-modules
  --prune-bpf
  --prune-compat32
  --prune-unused-net
  --prune-old-hw
  --prune-x86-old-platforms
  --prune-legacy-ata
  --prune-insecure
  --prune-radios
  --prune-dma-attack-surface
  -h, --help

Notes:
  Environment variables set defaults.
  CLI flags and VAR=VALUE arguments override environment variables.
  Boolean flags are off unless enabled explicitly with --foo.
  VAR=VALUE and --foo=value accept true/false, yes/no, on/off, enable/disable, and 1/0.
  --all-optimizations is flag-only and does not accept a value.
  --dry-run shows only the config symbols that would change without modifying the real file.
  --check validates inputs and prerequisites without running make or probing modules.
  --audit-kconfig reports textual warnings and exits without applying tuning or running make.
  Audit mode accepts Kconfig-only snapshots and an optional .config; needs Python 3.11+.
  --strict rejects unmet config requests and changed protected symbols after olddefconfig.
  --sched-cache accepts: none, on, off (default: none).
  --kernel-compression accepts: keep, gzip, bzip2, lzma, xz, lzo, lz4, zstd.
  --initrd-compression also accepts auto (detected requirements) and none (uncompressed support).
  --initrd-compression adds decoders without removing existing decoders or changing the producer.
  --initramfs-generator accepts auto, genkernel, ugrd, none (default: auto).
  --initramfs-config and --initramfs-image are optional read-only inputs.
  --initramfs-compression accepts auto, none, best, fastest, gzip, bzip2, lzma, xz, lzo, lz4, zstd.
  It describes a producer CLI override; no generator/configuration/image is modified.
  Compression inspection needs Python 3.11+ and lib/initramfs_check.py beside this script.
  --uclamp and --autogroup accept keep, on, off (default: keep).
  --module-force-load, --module-force-unload, --nfs-udp and --obsolete-crypto accept keep, on, off.
  These controls default to keep. --nfs-udp=off enables NFS_DISABLE_UDP_SUPPORT.
  --firmware-compression accepts: keep, on, off (default: keep).
  --preemption accepts: keep, none, voluntary, full, lazy, rt (default: keep).
  --preempt-dynamic accepts: keep, on, off (default: keep).
  --tick-mode accepts: keep, periodic, idle, full (default: keep).
  --thp accepts: keep, off, always, madvise, never (default: keep).
  --lru-gen accepts: keep, on, off (default: keep).
  --zswap accepts: keep, on, off (default: keep).
  --zswap-compressor accepts: keep, lzo, lz4, lz4hc, zstd, deflate, 842 (default: keep).
  --zram accepts: keep, off, module, builtin (default: keep).
  --zram-compressor accepts: keep, lzo-rle, lzo, lz4, lz4hc, zstd, deflate, 842 (default: keep).
  --numa-balancing accepts: keep, on, off (default: keep).
  --kmalloc-partition accepts: keep, off, random, typed (default: keep).
  --tcp-congestion accepts: keep, cubic, bbr, reno (default: keep).
  --io-uring accepts: keep, on, off (default: keep).
  Symbol lists are comma-separated, case-sensitive Kconfig names (optional CONFIG_ prefix).
  --disable-symbols, --module-symbols and --enable-symbols apply last; module requires MODULES=y.
  Explicit tuning controls override profile defaults. keep leaves the profile/baseline unchanged.
  The all-optimizations preset does not enable --prune-hardening.
  --optimization-profile accepts: none, server, desktop, realtime.
  --validation-mode accepts: warn or strict.
  --preempt-mode accepts: auto, none, voluntary, lazy, full, or rt.
  --timer-hz accepts: auto, 100, 250, 300, or 1000.
  --sched-cache-mode accepts: auto, on, or off.
  --mglru-mode accepts: auto, on, or off.
  --numa-balancing-mode accepts: auto, on, or off.
  --native-cpu accepts: none, on, or off (x86_64, kernel 6.16+; the kernel only runs on the build CPU).
  --video-support accepts: none, auto, amd, intel, nvidia, nouveau.
  --uefi-support accepts: none, auto, on, off.
  --initrd-support accepts: none, auto, on, off.
  --tpm-support accepts: none, auto, on, off.
  --dma-engine-support accepts: none, auto, on, off.
  --iommu-support accepts: none, auto, on, off.
  --numa-support accepts: none, auto, on, off.
  --nr-cpus accepts: none, auto, or a positive integer.
  --protected-config-symbols accepts a comma-separated list such as CONFIG_FOO,CONFIG_BAR.
  --prune-unused-modules requires root, a configured tree matching the running kernelrelease, and only probes direct one-symbol/one-module Kbuild mappings.
  --applications accepts a comma-separated list of app profiles.
  KERNEL_SRCDIR and CONFIG_FILE can be passed as positional arguments or via flags.
EOF
}

apply_all_optimizations() {
    ALL_OPTIMIZATIONS=true
    OPTIMIZATION_PROFILE=server
    KERNEL_COMPRESSION=zstd
    PRUNE_OBSERVABILITY=true
    PRUNE_LEGACY=true
    PRUNE_DEBUG_TRACE=true
    PRUNE_SELFTEST=true
    PRUNE_SANITIZERS=true
    PRUNE_COVERAGE=true
    PRUNE_FAULT_INJECTION=true
    PRUNE_DANGEROUS=true
    PRUNE_BPF=true
    PRUNE_COMPAT32=true
    PRUNE_UNUSED_NET=true
    PRUNE_OLD_HW=true
    PRUNE_X86_OLD_PLATFORMS=true
    PRUNE_LEGACY_ATA=true
    PRUNE_INSECURE=true
    PRUNE_RADIOS=true
    PRUNE_DMA_ATTACK_SURFACE=true
}

is_boolean_option() {
    case "$1" in
        audit-kconfig | prune-runtime-verification | check | strict | dry-run | all-optimizations | prune-observability | prune-legacy | prune-debug-trace | prune-hardening | prune-selftest | prune-sanitizers | prune-coverage | prune-fault-injection | prune-dangerous | prune-unused-modules | prune-bpf | prune-compat32 | prune-unused-net | prune-old-hw | prune-x86-old-platforms | prune-legacy-ata | prune-insecure | prune-radios | prune-dma-attack-surface)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_boolean_tunable() {
    case "$1" in
        AUDIT_KCONFIG | PRUNE_RUNTIME_VERIFICATION | CHECK | STRICT | DRY_RUN | PRUNE_OBSERVABILITY | PRUNE_LEGACY | PRUNE_DEBUG_TRACE | PRUNE_HARDENING | PRUNE_SELFTEST | PRUNE_SANITIZERS | PRUNE_COVERAGE | PRUNE_FAULT_INJECTION | PRUNE_DANGEROUS | PRUNE_UNUSED_MODULES | PRUNE_BPF | PRUNE_COMPAT32 | PRUNE_UNUSED_NET | PRUNE_OLD_HW | PRUNE_X86_OLD_PLATFORMS | PRUNE_LEGACY_ATA | PRUNE_INSECURE | PRUNE_RADIOS | PRUNE_DMA_ATTACK_SURFACE)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

normalize_boolean_value() {
    local value
    value="${1@L}"

    case "$value" in
        1 | true | yes | on | enable | enabled)
            printf '%s\n' "true"
            ;;
        0 | false | no | off | disable | disabled)
            printf '%s\n' "false"
            ;;
        *)
            return 1
            ;;
    esac
}

is_enabled() {
    [[ "$1" == "true" ]]
}

init_tunable() {
    local name="$1"
    local default_value="$2"
    local raw_value="${!name:-$default_value}"

    set_tunable "$name" "$raw_value"
}

set_tunable() {
    local name="$1"
    local value="$2"
    local normalized_value=""

    if is_boolean_tunable "$name"; then
        if ! normalized_value="$(normalize_boolean_value "$value")"; then
            echo "Invalid boolean for $name: $value" >&2
            exit 1
        fi

        printf -v "$name" '%s' "$normalized_value"
        return
    fi

    case "$name" in
        ALL_OPTIMIZATIONS)
            echo "ALL_OPTIMIZATIONS does not accept values. Use --all-optimizations without true/false." >&2
            exit 1
            ;;
        OPTIMIZATION_PROFILE | VALIDATION_MODE | PREEMPT_MODE | TIMER_HZ | SCHED_CACHE_MODE | MGLRU_MODE | NUMA_BALANCING_MODE | NATIVE_CPU | CPU_VENDOR_FILTER | VIDEO_SUPPORT | UEFI_SUPPORT | INITRD_SUPPORT | TPM_SUPPORT | DMA_ENGINE_SUPPORT | IOMMU_SUPPORT | NUMA_SUPPORT | NR_CPUS | PROTECTED_CONFIG_SYMBOLS | APPLICATIONS | HOST_TYPE | MODULE_FORCE_LOAD | MODULE_FORCE_UNLOAD | NFS_UDP | OBSOLETE_CRYPTO | INITRAMFS_GENERATOR | INITRAMFS_CONFIG | INITRAMFS_IMAGE | INITRAMFS_COMPRESSION | UCLAMP | AUTOGROUP | DISABLE_SYMBOLS | MODULE_SYMBOLS | ENABLE_SYMBOLS | PREEMPTION | PREEMPT_DYNAMIC | TICK_MODE | THP | LRU_GEN | ZSWAP | ZSWAP_COMPRESSOR | ZRAM | ZRAM_COMPRESSOR | NUMA_BALANCING | KMALLOC_PARTITION | TCP_CONGESTION | IO_URING | SCHED_CACHE | KERNEL_COMPRESSION | INITRD_COMPRESSION | FIRMWARE_COMPRESSION)
            printf -v "$name" '%s' "$value"
            ;;
        *)
            echo "Unknown setting: $name" >&2
            exit 1
            ;;
    esac
}

set_option() {
    local name="$1"
    local value="$2"

    case "$name" in
        kernel-srcdir)
            KSRCDIR="$value"
            ;;
        config-file)
            CONFIG_FILE="$value"
            ;;
        all-optimizations)
            if [[ "$value" != "__flag__" ]]; then
                echo "--all-optimizations is a flag and does not accept true/false values" >&2
                exit 1
            fi
            apply_all_optimizations
            ;;
        *)
            local tunable_name="${name//-/_}"
            set_tunable "${tunable_name@U}" "$value"
            ;;
    esac
}

# flag-only: set by --all-optimizations, never from the environment
if [[ -v ALL_OPTIMIZATIONS ]]; then
    echo "ALL_OPTIMIZATIONS does not accept values. Use --all-optimizations without true/false." >&2
    exit 1
fi
ALL_OPTIMIZATIONS=false
init_tunable DRY_RUN false
init_tunable CHECK false
init_tunable AUDIT_KCONFIG false
init_tunable STRICT false
init_tunable SCHED_CACHE none
init_tunable DISABLE_SYMBOLS none
init_tunable MODULE_SYMBOLS none
init_tunable ENABLE_SYMBOLS none
init_tunable PREEMPTION keep
init_tunable PREEMPT_DYNAMIC keep
init_tunable TICK_MODE keep
init_tunable THP keep
init_tunable LRU_GEN keep
init_tunable ZSWAP keep
init_tunable ZSWAP_COMPRESSOR keep
init_tunable ZRAM keep
init_tunable ZRAM_COMPRESSOR keep
init_tunable NUMA_BALANCING keep
init_tunable KMALLOC_PARTITION keep
init_tunable TCP_CONGESTION keep
init_tunable IO_URING keep
init_tunable PRUNE_RUNTIME_VERIFICATION false
init_tunable KERNEL_COMPRESSION keep
init_tunable INITRD_COMPRESSION keep
init_tunable INITRAMFS_GENERATOR auto
init_tunable INITRAMFS_CONFIG ""
init_tunable INITRAMFS_IMAGE ""
init_tunable INITRAMFS_COMPRESSION auto
init_tunable UCLAMP keep
init_tunable AUTOGROUP keep
init_tunable MODULE_FORCE_LOAD keep
init_tunable MODULE_FORCE_UNLOAD keep
init_tunable NFS_UDP keep
init_tunable OBSOLETE_CRYPTO keep
init_tunable FIRMWARE_COMPRESSION keep
init_tunable OPTIMIZATION_PROFILE none
init_tunable VALIDATION_MODE warn
init_tunable PREEMPT_MODE auto
init_tunable TIMER_HZ auto
init_tunable SCHED_CACHE_MODE auto
init_tunable MGLRU_MODE auto
init_tunable NUMA_BALANCING_MODE auto
init_tunable NATIVE_CPU none
init_tunable PRUNE_OBSERVABILITY false
init_tunable PRUNE_LEGACY false
init_tunable PRUNE_DEBUG_TRACE false
init_tunable PRUNE_HARDENING false
init_tunable PRUNE_SELFTEST false
init_tunable PRUNE_SANITIZERS false
init_tunable PRUNE_COVERAGE false
init_tunable PRUNE_FAULT_INJECTION false
init_tunable PRUNE_DANGEROUS false
init_tunable PRUNE_UNUSED_MODULES false
init_tunable CPU_VENDOR_FILTER none
init_tunable VIDEO_SUPPORT none
init_tunable UEFI_SUPPORT none
init_tunable INITRD_SUPPORT none
init_tunable TPM_SUPPORT none
init_tunable DMA_ENGINE_SUPPORT none
init_tunable IOMMU_SUPPORT none
init_tunable NUMA_SUPPORT none
init_tunable NR_CPUS none
init_tunable PROTECTED_CONFIG_SYMBOLS CONFIG_ARCH_PKEY_BITS
init_tunable APPLICATIONS none
init_tunable HOST_TYPE none
init_tunable PRUNE_BPF false
init_tunable PRUNE_COMPAT32 false
init_tunable PRUNE_UNUSED_NET false
init_tunable PRUNE_OLD_HW false
init_tunable PRUNE_X86_OLD_PLATFORMS false
init_tunable PRUNE_LEGACY_ATA false
init_tunable PRUNE_INSECURE false
init_tunable PRUNE_RADIOS false
init_tunable PRUNE_DMA_ATTACK_SURFACE false

KSRCDIR="${KSRCDIR:-}"
CONFIG_FILE="${CONFIG_FILE:-}"
positionals=()

while (($# > 0)); do
    case "$1" in
        -h | --help)
            usage
            exit 0
            ;;
        --)
            shift
            while (($# > 0)); do
                positionals+=("$1")
                shift
            done
            break
            ;;
        --*=*)
            opt_name="${1%%=*}"
            opt_name="${opt_name#--}"
            if [[ -z "$opt_name" ]]; then
                echo "Invalid option: $1" >&2
                exit 1
            fi
            set_option "$opt_name" "${1#*=}"
            ;;
        --*)
            opt_name="${1#--}"
            if is_boolean_option "$opt_name"; then
                if [[ "$opt_name" == "all-optimizations" ]]; then
                    set_option "$opt_name" "__flag__"
                else
                    set_option "$opt_name" "true"
                fi
            else
                shift
                if (($# == 0)); then
                    echo "Missing value for --$opt_name" >&2
                    exit 1
                fi
                set_option "$opt_name" "$1"
            fi
            ;;
        *=*)
            set_tunable "${1%%=*}" "${1#*=}"
            ;;
        *)
            positionals+=("$1")
            ;;
    esac
    shift
done

if ((${#positionals[@]} > 2)); then
    echo "Too many positional arguments: ${positionals[*]}" >&2
    usage >&2
    exit 1
fi

WORK_DIR=""
ORIGINAL_CONFIG_FILE=""
BACKUP=""
MODULE_PROBE_ACTIVE=false
declare -a MODULE_PROBE_BASELINE=()

die() {
    echo "Error: $*" >&2
    exit 1
}

cleanup() {
    local status=$?
    if is_enabled "$MODULE_PROBE_ACTIVE"; then
        if ! restore_loaded_modules_to_initial_state MODULE_PROBE_BASELINE; then
            echo "Error: could not restore the original module set during cleanup" >&2
            status=1
        fi
    fi
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR"
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

prepare_paths() {
    local command_name required
    for command_name in make realpath mktemp cp mv cmp chmod awk sed grep find sort xargs; do
        command -v "$command_name" >/dev/null 2>&1 || die "Required tool not found: $command_name"
    done
    KSRCDIR="${KSRCDIR:-${positionals[0]:-$(detect_default_ksrcdir)}}"
    # Unlike the kernel .config argument, producer/image paths belong to the
    # caller's directory, not the kernel source tree we are about to enter.
    for required in INITRAMFS_CONFIG INITRAMFS_IMAGE; do
        if [[ -n "${!required}" ]]; then
            printf -v "$required" '%s' "$(realpath -m -- "${!required}")"
        fi
    done
    KSRCDIR="$(realpath -e -- "$KSRCDIR")" || die "Invalid kernel source directory: $KSRCDIR"
    [[ -d "$KSRCDIR" ]] || die "Not a directory: $KSRCDIR"
    cd -- "$KSRCDIR"
    # Relative config paths are relative to the selected kernel tree.
    CONFIG_FILE="${CONFIG_FILE:-${positionals[1]:-.config}}"
    for required in Kconfig Makefile scripts/config scripts/Kconfig.include kernel/Kconfig.preempt; do
        [[ -r "$KSRCDIR/$required" ]] || die "Incomplete kernel tree: missing $required in $KSRCDIR"
    done
    [[ -x "$KSRCDIR/scripts/config" ]] || die "scripts/config must be executable (it is supplied with kernel sources)"
    [[ -f "$CONFIG_FILE" && -r "$CONFIG_FILE" ]] || die "Config is not a readable regular file: $CONFIG_FILE"
    ORIGINAL_CONFIG_FILE="$(realpath -e -- "$CONFIG_FILE")"
    CONFIG_FILE="$ORIGINAL_CONFIG_FILE"
    if ! is_enabled "$DRY_RUN"; then
        [[ -w "$ORIGINAL_CONFIG_FILE" && -w "${ORIGINAL_CONFIG_FILE%/*}" ]] \
            || die "Config and its parent directory must be writable: $ORIGINAL_CONFIG_FILE"
    fi
}

prepare_transaction() {
    local temp_parent="${ORIGINAL_CONFIG_FILE%/*}"
    if is_enabled "$DRY_RUN"; then
        temp_parent="${TMPDIR:-/tmp}"
    fi
    WORK_DIR="$(mktemp -d -- "$temp_parent/.kernel-config.XXXXXX")"
    cp -p -- "$ORIGINAL_CONFIG_FILE" "$WORK_DIR/original.config"
    cp -p -- "$ORIGINAL_CONFIG_FILE" "$WORK_DIR/config"
    CONFIG_FILE="$WORK_DIR/config"
    # A read-only baseline is still usable for dry-run.
    chmod u+w -- "$CONFIG_FILE"
    echo "Working on temporary config: $CONFIG_FILE"
}

commit_transaction() {
    cmp -s -- "$ORIGINAL_CONFIG_FILE" "$WORK_DIR/original.config" \
        || die "Original config changed during execution; refusing to overwrite it"
    if cmp -s -- "$ORIGINAL_CONFIG_FILE" "$CONFIG_FILE"; then
        echo "No changes."
        return
    fi
    local timestamp
    printf -v timestamp '%(%Y%m%d-%H%M%S)T' -1
    BACKUP="$(mktemp -- "${ORIGINAL_CONFIG_FILE}.bak.${timestamp}.XXXXXX")"
    cp -p -- "$ORIGINAL_CONFIG_FILE" "$BACKUP"
    # olddefconfig may create a replacement file; restore original metadata.
    cp --attributes-only --preserve=mode,ownership -- "$ORIGINAL_CONFIG_FILE" "$CONFIG_FILE"
    mv -f -- "$CONFIG_FILE" "$ORIGINAL_CONFIG_FILE"
    echo "Backup: $BACKUP"
    echo "Done. Review changes with:"
    printf '  diff -u %q %q\n' "$BACKUP" "$ORIGINAL_CONFIG_FILE"
}

show_config_changes() {
    local before_file="$1"
    local after_file="$2"
    local changes

    changes="$(
        awk '
            function capture_config_line(line, source, sym, value) {
                if (line ~ /^CONFIG_[A-Za-z0-9_]+=.*$/) {
                    sym = line
                    sub(/^CONFIG_/, "", sym)
                    value = sym
                    sub(/^[^=]*=/, "", value)
                    sub(/=.*/, "", sym)
                } else if (line ~ /^# CONFIG_[A-Za-z0-9_]+ is not set$/) {
                    sym = line
                    sub(/^# CONFIG_/, "", sym)
                    sub(/ is not set$/, "", sym)
                    value = "n"
                } else {
                    return
                }

                seen[sym] = 1
                if (source == "before") {
                    before[sym] = value
                } else {
                    after[sym] = value
                }
            }

            FNR == NR {
                capture_config_line($0, "before")
                next
            }

            {
                capture_config_line($0, "after")
            }

            END {
                for (sym in seen) {
                    if (sym == "CC_VERSION_TEXT" || sym == "AS_VERSION" || sym == "RUSTC_LLVM_VERSION" || sym == "RUSTC_VERSION" || sym == "LD_VERSION" || sym == "PAHOLE_VERSION") {
                        continue
                    }
                    old_value = (sym in before) ? before[sym] : "n"
                    new_value = (sym in after) ? after[sym] : "n"
                    if (old_value != new_value) {
                        printf "CONFIG_%s\t%s\t%s\n", sym, old_value, new_value
                    }
                }
            }
        ' "$before_file" "$after_file" \
            | sort \
            | awk -F '\t' '{ printf "  %s: %s -> %s\n", $1, $2, $3 }'
    )"

    if [[ -z "$changes" ]]; then
        echo
        echo "No changes."
        return
    fi

    printf '%s\n' "$changes"
}

cfg() {
    "$KSRCDIR/scripts/config" --keep-case --file "$CONFIG_FILE" "$@" \
        || die "scripts/config failed: $*"
}

record_config_request_issue() {
    _UNSUPPORTED_REQUESTS+=("$1")
}

declare -A _SYMBOL_VALUE_CACHE=()
declare -i _SYMBOL_CACHE_LOADED=0
declare -A _DEFINED_SYMBOLS=() _REQUESTED_VALUES=() _PROTECTED_ORIGINAL_VALUES=()
declare -A _KCONFIG_TYPES=() _KCONFIG_PROMPTS=() _EXPLICIT_SYMBOL_VALUES=()
declare -A _KCONFIG_SELECTORS=()
declare -a _UNSUPPORTED_REQUESTS=()

_load_symbol_cache() {
    _SYMBOL_VALUE_CACHE=()
    local sym value entries
    [[ -f "$CONFIG_FILE" && -r "$CONFIG_FILE" ]] || die "Cannot read config: $CONFIG_FILE"
    entries="$(awk '
        /^CONFIG_[A-Za-z0-9_]+=/ {
            line = $0
            sub(/^CONFIG_/, "", line)
            idx = index(line, "=")
            print substr(line, 1, idx - 1) "\t" substr(line, idx + 1)
        }
        /^# CONFIG_[A-Za-z0-9_]+ is not set$/ {
            sym = $0
            sub(/^# CONFIG_/, "", sym)
            sub(/ is not set$/, "", sym)
            print sym "\tn"
        }
    ' "$CONFIG_FILE")" || die "Could not parse config: $CONFIG_FILE"
    while IFS=$'\t' read -r sym value; do
        [[ -z "$sym" ]] && continue
        _SYMBOL_VALUE_CACHE["$sym"]="$value"
    done <<<"$entries"
    _SYMBOL_CACHE_LOADED=1
}

invalidate_symbol_cache() {
    _SYMBOL_CACHE_LOADED=0
}

# Symbols built as modules in the baseline. Enabling one of them keeps it =m:
# promoting a working module to built-in only grows the image and can break
# drivers that load firmware from the root filesystem (amdgpu, iwlwifi...).
declare -A _BASELINE_MODULE_SYMBOLS=()

load_baseline_module_symbols() {
    local sym

    _BASELINE_MODULE_SYMBOLS=()
    while IFS= read -r sym; do
        _BASELINE_MODULE_SYMBOLS["$sym"]=1
    done < <(sed -n 's/^CONFIG_\([A-Za-z0-9_]\+\)=m$/\1/p' "$CONFIG_FILE")
}

is_baseline_module_symbol() {
    [[ -v _BASELINE_MODULE_SYMBOLS[$1] ]]
}

config_has_symbol() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    [[ -v _SYMBOL_VALUE_CACHE[$1] ]]
}

have_symbol() {
    [[ -v _DEFINED_SYMBOLS[$1] ]]
}

load_defined_symbols() {
    local symbols sym
    # Keep failures visible instead of hiding them in a process substitution.
    # shellcheck disable=SC2016
    symbols="$(find_kconfig_files | xargs -0 -r awk '
        /^[[:space:]]*(menuconfig|config)[[:space:]]+[A-Za-z0-9_]+/ { print $2 }
    ' | sort -u)" || die "Could not read Kconfig definitions"
    while IFS= read -r sym; do
        [[ -n "$sym" ]] && _DEFINED_SYMBOLS["$sym"]=1
    done <<<"$symbols"
}

load_kconfig_metadata() {
    local srcarch="${ARCH:-$(uname -m)}" entries sym kind guard
    case "$srcarch" in
        x86_64 | i?86) srcarch=x86 ;;
        aarch64) srcarch=arm64 ;;
        ppc*) srcarch=powerpc ;;
        riscv*) srcarch=riscv ;;
        s390x) srcarch=s390 ;;
    esac
    # This is conservative metadata, not a replacement for Kconfig evaluation.
    # Other architectures must not turn a target's hidden symbol into a prompt.
    # shellcheck disable=SC2016
    entries="$(find_kconfig_files | xargs -0 -r awk -v root="$KSRCDIR/arch/" -v arch="$srcarch/" '
        function emit( i) {
            if (sym == "" || kind == "") return
            print sym "\t" kind "\t-"
            for (i = 1; i <= count; i++) print sym "\t" kind "\t" guards[i]
        }
        function reset() { sym = ""; kind = ""; count = 0; delete guards }
        # Read a complete quoted Kconfig string, not an inner pair of quotes.
        # Expose the decoded text and the untouched tail for prompt guards.
        function quoted_property(line, quote, i, ch, escaped) {
            kconfig_text = ""; kconfig_tail = ""
            sub(/^[[:space:]]*[a-z_]+[[:space:]]*/, "", line)
            quote = substr(line, 1, 1)
            if (quote != "\"" && quote != sprintf("%c", 39)) return 0
            for (i = 2; i <= length(line); i++) {
                ch = substr(line, i, 1)
                if (escaped) { kconfig_text = kconfig_text ch; escaped = 0 }
                else if (ch == "\\") escaped = 1
                else if (ch == quote) { kconfig_tail = substr(line, i + 1); return 1 }
                else kconfig_text = kconfig_text ch
            }
            return 0
        }
        function prompt( text, tail) {
            if (quoted_property(text)) {
                tail = kconfig_tail
                sub(/^[[:space:]]*/, "", tail)
                sub(/[[:space:]]*#.*/, "", tail)
                sub(/[[:space:]]*$/, "", tail)
                if (tail == "") guards[++count] = "y"
                else if (tail ~ /^if[[:space:]]+/) {
                    sub(/^if[[:space:]]+/, "", tail)
                    guards[++count] = tail
                }
            }
        }
        FNR == 1 {
            emit(); reset(); in_help = 0
            skip = index(FILENAME, root) == 1 && index(FILENAME, root arch) != 1
        }
        skip { next }
        /^[[:space:]]*(help|---help---)[[:space:]]*$/ { in_help = 1; help_indent = -1; next }
        in_help {
            if (/^[[:space:]]*$/) next
            indent = match($0, /^[[:space:]]+/) ? RLENGTH : 0
            if (help_indent < 0) { help_indent = indent; next }
            if (indent >= help_indent) next
            in_help = 0
        }
        /^[[:space:]]*(choice|endchoice|menu|endmenu|if|endif|source|rsource|osource|orsource|comment)([[:space:]]|$)/ {
            emit(); reset(); next
        }
        /^[[:space:]]*(config|menuconfig)[[:space:]]+[A-Za-z0-9_]+/ {
            emit(); reset(); sym = $2; next
        }
        /^[[:space:]]*(bool|tristate|def_bool|def_tristate|int|hex|string)([[:space:]]|$)/ {
            kind = $1
            if (kind !~ /^def_/) prompt($0)
            sub(/^def_/, "", kind); next
        }
        /^[[:space:]]*select[[:space:]]+[A-Za-z0-9_]+/ {
            if (sym != "") print $2 "\tselect\t" sym
            next
        }
        /^[[:space:]]*prompt[[:space:]]/ { prompt($0) }
        END { emit() }
    ')" || die "Could not read Kconfig types/prompts"
    while IFS=$'\t' read -r sym kind guard; do
        [[ -n "$sym" ]] || continue
        if [[ "$kind" == select ]]; then
            _KCONFIG_SELECTORS["$sym"]+="$guard"$'\n'
            continue
        fi
        _KCONFIG_TYPES["$sym"]="$kind"
        [[ "$guard" == - ]] || _KCONFIG_PROMPTS["$sym"]+="$guard"$'\n'
    done <<<"$entries"
}

preserve_selected_prune_targets() {
    local -n targets="$1"
    local sym selector changed=1
    local -A planned=()
    local -a filtered=()
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    for sym in "${targets[@]}"; do
        is_protected_config_symbol "$sym" || planned["$sym"]=1
    done
    # Keep the closure of dependencies selected by retained enabled features.
    # Conditional selects are conservatively treated as potentially active.
    while ((changed)); do
        changed=0
        for sym in "${!planned[@]}"; do
            while IFS= read -r selector; do
                [[ -n "$selector" ]] || continue
                if [[ "${_SYMBOL_VALUE_CACHE[$selector]:-n}" != n && ! -v planned[$selector] ]]; then
                    echo "Retaining CONFIG_$sym: selected by enabled CONFIG_$selector"
                    unset 'planned[$sym]'
                    changed=1
                    break
                fi
            done <<<"${_KCONFIG_SELECTORS[$sym]:-}"
        done
    done
    for sym in "${targets[@]}"; do
        [[ -v planned[$sym] ]] && filtered+=("$sym")
    done
    targets=("${filtered[@]}")
}

is_prunable_toggle() {
    local sym="$1" guard
    case "${_KCONFIG_TYPES[$sym]:-}" in bool | tristate) ;; *) return 1 ;; esac
    case "$sym" in ARCH_* | HAVE_*) return 1 ;; esac
    while IFS= read -r guard; do
        [[ -n "$guard" ]] || continue
        case "$guard" in
            y) return 0 ;;
            '!y' | n) continue ;;
        esac
        if [[ "$guard" =~ ^[A-Za-z0-9_]+$ ]] && is_symbol_enabled_now "$guard"; then
            return 0
        fi
        if [[ "$guard" =~ ^![A-Za-z0-9_]+$ ]] && ! is_symbol_enabled_now "${guard:1}"; then
            return 0
        fi
        # Complex/continued prompt guards are left to explicit user controls.
    done <<<"${_KCONFIG_PROMPTS[$sym]:-}"
    return 1
}

symbol_value() {
    if config_has_symbol "$1"; then
        printf '%s\n' "${_SYMBOL_VALUE_CACHE[$1]}"
    fi
}

is_symbol_enabled_now() {
    normalize_config_symbol_name "$1"
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    [[ -v _SYMBOL_VALUE_CACHE[$REPLY] ]] && [[ "${_SYMBOL_VALUE_CACHE[$REPLY]}" == y ]]
}

find_kconfig_files() {
    find -L "$KSRCDIR" -path "$KSRCDIR/scripts/kconfig/tests" -prune -o \
        -type f \( -name 'Kconfig' -o -name 'Kconfig.*' \) -print0
}

find_kbuild_files() {
    find -L "$KSRCDIR" -type f \( -name 'Makefile' -o -name 'Kbuild' \) -print0 2>/dev/null
}

declare -A MODULE_SYMBOL_TO_MODULES=()
declare -i MODULE_SYMBOL_MAP_READY=0

capture_loaded_modules() {
    lsmod | awk 'NR > 1 { print $1 }'
}

read_loaded_modules() {
    local -n destination="$1"
    local output
    output="$(capture_loaded_modules)" || return 1
    destination=()
    if [[ -n "$output" ]]; then
        # Output is assigned through the caller's nameref.
        # shellcheck disable=SC2034
        mapfile -t destination <<<"$output"
    fi
}

normalize_kernel_module_name() {
    REPLY="${1//[[:space:]]/}"
    REPLY="${REPLY//-/_}"
}

_RUNNING_KERNEL_RELEASE=""

running_kernel_release() {
    if [[ -z "$_RUNNING_KERNEL_RELEASE" ]]; then
        _RUNNING_KERNEL_RELEASE="$(uname -r)"
    fi
    printf '%s\n' "$_RUNNING_KERNEL_RELEASE"
}

module_exists_for_running_kernel() {
    local module_name="$1"
    local normalized_module_name kr

    normalize_kernel_module_name "$module_name"
    normalized_module_name="$REPLY"
    kr="$(running_kernel_release)"

    modinfo -k "$kr" "$module_name" >/dev/null 2>&1 \
        || modinfo -k "$kr" "$normalized_module_name" >/dev/null 2>&1
}

is_module_loaded_now() {
    local module_name="$1"

    normalize_kernel_module_name "$module_name"
    [[ -f "/sys/module/$REPLY/initstate" ]]
}

discover_config_module_symbols() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    local sym
    for sym in "${!_SYMBOL_VALUE_CACHE[@]}"; do
        [[ "${_SYMBOL_VALUE_CACHE[$sym]}" == "m" ]] && printf '%s\n' "$sym"
    done
}

build_module_symbol_candidate_map() {
    local sym module existing_modules

    ((MODULE_SYMBOL_MAP_READY)) && return

    MODULE_SYMBOL_TO_MODULES=()
    # Kbuild expressions in the awk program must remain literal.
    # shellcheck disable=SC2016
    while IFS=$'\t' read -r sym module; do
        [[ -n "$sym" && -n "$module" ]] || continue
        if [[ -v MODULE_SYMBOL_TO_MODULES[$sym] ]]; then
            existing_modules="${MODULE_SYMBOL_TO_MODULES[$sym]}"
        else
            existing_modules=""
        fi
        case " $existing_modules " in
            *" $module "*)
                ;;
            *)
                MODULE_SYMBOL_TO_MODULES["$sym"]="${existing_modules:+$existing_modules }$module"
                ;;
        esac
    done < <(
        # shellcheck disable=SC2016
        find_kbuild_files \
            | xargs -0 -r awk '
                {
                    line = $0
                    sub(/[[:space:]]*#.*/, "", line)
                }

                line ~ /^[[:space:]]*obj-\$\(CONFIG_[A-Za-z0-9_]+\)[[:space:]]*[-+?:]?=[[:space:]]*/ {
                    sym = line
                    sub(/^[[:space:]]*obj-\$\(CONFIG_/, "", sym)
                    sub(/\).*/, "", sym)

                    rest = line
                    sub(/^[[:space:]]*obj-\$\(CONFIG_[A-Za-z0-9_]+\)[[:space:]]*[-+?:]?=[[:space:]]*/, "", rest)

                    count = split(rest, items, /[[:space:]]+/)
                    for (i = 1; i <= count; i++) {
                        item = items[i]
                        if (item ~ /^[^\/[:space:]]+\.o$/) {
                            module = item
                            sub(/\.o$/, "", module)
                            print sym "\t" module
                        }
                    }
                }
            ' \
            | sort -u
    )

    MODULE_SYMBOL_MAP_READY=1
}

restore_loaded_modules_to_initial_state() {
    local -n initial_modules_arr="$1"
    local current_module normalized idx
    local -A initial_modules_map=()
    local -A current_modules_map=()
    local -a current_modules=()
    local -a extra_modules=()
    local -a missing_modules=()

    for current_module in "${initial_modules_arr[@]}"; do
        normalized="${current_module//-/_}"
        initial_modules_map["$normalized"]=1
    done

    for _ in 1 2 3; do
        current_modules=()
        current_modules_map=()
        extra_modules=()
        missing_modules=()

        read_loaded_modules current_modules || return 1
        for current_module in "${current_modules[@]}"; do
            normalized="${current_module//-/_}"
            current_modules_map["$normalized"]=1
        done

        for ((idx=${#current_modules[@]} - 1; idx >= 0; idx--)); do
            current_module="${current_modules[idx]}"
            normalized="${current_module//-/_}"
            if ! [[ -v initial_modules_map[$normalized] ]]; then
                extra_modules+=("$current_module")
            fi
        done

        for current_module in "${initial_modules_arr[@]}"; do
            normalized="${current_module//-/_}"
            if ! [[ -v current_modules_map[$normalized] ]]; then
                missing_modules+=("$current_module")
            fi
        done

        if ((${#extra_modules[@]} == 0 && ${#missing_modules[@]} == 0)); then
            return 0
        fi

        for current_module in "${extra_modules[@]}"; do
            modprobe -r "$current_module" >/dev/null 2>&1 || true
        done

        for current_module in "${missing_modules[@]}"; do
            modprobe "$current_module" >/dev/null 2>&1 || true
        done
    done

    current_modules=()
    current_modules_map=()
    extra_modules=()
    missing_modules=()

    read_loaded_modules current_modules || return 1
    for current_module in "${current_modules[@]}"; do
        normalized="${current_module//-/_}"
        current_modules_map["$normalized"]=1
        if ! [[ -v initial_modules_map[$normalized] ]]; then
            extra_modules+=("$current_module")
        fi
    done

    for current_module in "${initial_modules_arr[@]}"; do
        normalized="${current_module//-/_}"
        if ! [[ -v current_modules_map[$normalized] ]]; then
            missing_modules+=("$current_module")
        fi
    done

    ((${#extra_modules[@]} == 0 && ${#missing_modules[@]} == 0))
}

probe_unloaded_module_candidate() {
    local module_name="$1"
    local initial_modules_var_name="$2"

    echo "    Probing module: ${module_name}"

    if ! modprobe "$module_name" >/dev/null 2>&1; then
        echo "      (probe failed: could not load)"
        if restore_loaded_modules_to_initial_state "$initial_modules_var_name"; then
            return 0
        fi
        echo "      (probe failed: could not restore original module set)" >&2
        return 2
    fi

    if ! is_module_loaded_now "$module_name"; then
        echo "      (probe loaded nothing persistent; module did not stay initialized)"
        if restore_loaded_modules_to_initial_state "$initial_modules_var_name"; then
            return 0
        fi

        echo "      (probe failed: could not restore original module set)"
        return 2
    fi

    if restore_loaded_modules_to_initial_state "$initial_modules_var_name"; then
        echo "      (module can be loaded; keeping config and only reporting it)"
        return 1
    fi

    echo "      (probe failed: could not restore original module set)"
    return 2
}

probe_and_prune_unused_module_symbols() {
    local -
    local running_kernel_release target_kernel_release module_name normalized_module_name
    local probe_status
    local symbol module_list
    local -A initially_loaded_map=()
    local -a initially_loaded_modules=()
    local -a module_symbols=()
    local -a module_candidates=()

    echo
    echo "==> Probing currently unloaded module configs"

    if is_enabled "$DRY_RUN"; then
        echo "    (dry-run: active module probing is skipped)"
        return
    fi

    if ((EUID != 0)); then
        echo "    (requires root to load/unload modules safely; skipping)"
        return
    fi

    if ! command -v lsmod >/dev/null 2>&1 || ! command -v modprobe >/dev/null 2>&1 || ! command -v modinfo >/dev/null 2>&1; then
        echo "    (lsmod/modprobe/modinfo are required; skipping)"
        return
    fi

    running_kernel_release="$(running_kernel_release)"
    target_kernel_release="$(make -s KCONFIG_CONFIG="$CONFIG_FILE" kernelrelease 2>/dev/null || true)"
    if [[ -z "$target_kernel_release" ]]; then
        echo "    (could not resolve target kernelrelease; skipping)"
        return
    fi

    if [[ "$target_kernel_release" != "$running_kernel_release" ]]; then
        echo "    (target kernelrelease $target_kernel_release does not match running kernel $running_kernel_release; skipping)"
        return
    fi

    build_module_symbol_candidate_map
    read_loaded_modules initially_loaded_modules || die "Could not read the loaded module set; skipping probes"
    # Referenced by name through the cleanup function's nameref.
    # shellcheck disable=SC2034
    MODULE_PROBE_BASELINE=("${initially_loaded_modules[@]}")
    MODULE_PROBE_ACTIVE=true
    for module_name in "${initially_loaded_modules[@]}"; do
        initially_loaded_map["${module_name//-/_}"]=1
    done

    mapfile -t module_symbols < <(discover_config_module_symbols)
    for symbol in "${module_symbols[@]}"; do
        [[ -v MODULE_SYMBOL_TO_MODULES[$symbol] ]] || continue
        module_list="${MODULE_SYMBOL_TO_MODULES[$symbol]}"

        read -r -a module_candidates <<<"$module_list"
        if ((${#module_candidates[@]} != 1)); then
            continue
        fi

        module_name="${module_candidates[0]}"
        normalized_module_name="${module_name//-/_}"
        if [[ -v initially_loaded_map[$normalized_module_name] ]]; then
            continue
        fi

        if ! module_exists_for_running_kernel "$module_name"; then
            continue
        fi

        if probe_unloaded_module_candidate "$module_name" initially_loaded_modules; then
            probe_status=0
        else
            probe_status=$?
        fi

        case "$probe_status" in
            0)
                echo "    (disabling CONFIG_${symbol})"
                disable_config_symbol "$symbol"
                ;;
            1)
                echo "    (keeping CONFIG_${symbol}; module ${module_name} is currently unused but loadable)"
                ;;
            *)
                die "Module probe could not restore the original module set; config was not committed"
                ;;
        esac
    done

    MODULE_PROBE_ACTIVE=false
    return 0
}

discover_kconfig_symbols_by_pattern() {
    local pattern="$1"

    # shellcheck disable=SC2016
    find_kconfig_files \
        | xargs -0 -r awk -v pattern="$pattern" '
            BEGIN {
                IGNORECASE = 1
            }

            FNR == 1 {
                sym = ""; is_toggle = 0; is_menuconfig = 0
                in_help = 0; in_continuation = 0
                menu_depth = 0; if_depth = 0
                last_menuconfig_sym = ""; menuconfig_pattern_match = 0
                delete menu_matches; delete if_matches
            }

            /\\[[:space:]]*$/ {
                in_continuation = 1
                next
            }

            in_continuation {
                in_continuation = /\\[[:space:]]*$/
                next
            }

            # Read a complete quoted Kconfig string, not an inner pair of quotes.
            # Expose the decoded text and the untouched tail for prompt guards.
            function quoted_property(line, quote, i, ch, escaped) {
                kconfig_text = ""; kconfig_tail = ""
                sub(/^[[:space:]]*[a-z_]+[[:space:]]*/, "", line)
                quote = substr(line, 1, 1)
                if (quote != "\"" && quote != sprintf("%c", 39)) return 0
                for (i = 2; i <= length(line); i++) {
                    ch = substr(line, i, 1)
                    if (escaped) { kconfig_text = kconfig_text ch; escaped = 0 }
                    else if (ch == "\\") escaped = 1
                    else if (ch == quote) { kconfig_tail = substr(line, i + 1); return 1 }
                    else kconfig_text = kconfig_text ch
                }
                return 0
            }

            function menu_context_matches(depth) {
                for (depth = menu_depth; depth >= 1; depth--) {
                    if (menu_matches[depth]) {
                        return 1
                    }
                }

                for (depth = if_depth; depth >= 1; depth--) {
                    if (if_matches[depth]) {
                        return 1
                    }
                }

                return 0
            }

            function maybe_emit(text) {
                if (sym != "" && is_toggle && (text ~ pattern || menu_context_matches())) {
                    print sym
                }
            }

            function clear_symbol() { sym = ""; is_toggle = 0; is_menuconfig = 0 }

            /^[[:space:]]*(help|---help---)([[:space:]]*)$/ {
                in_help = 1
                help_indent = -1
                next
            }

            in_help {
                if (/^[[:space:]]*$/) next
                if (match($0, /^[[:space:]]+/)) {
                    cur_indent = RLENGTH
                } else {
                    cur_indent = 0
                }
                if (help_indent < 0) {
                    help_indent = cur_indent
                    next
                }
                if (cur_indent >= help_indent) next
                in_help = 0
            }

            /^[[:space:]]*menu[[:space:]]/ {
                clear_symbol()
                menu_depth++
                menu_matches[menu_depth] = (quoted_property($0) && kconfig_text ~ pattern)
                next
            }

            /^[[:space:]]*endmenu([[:space:]]|$)/ {
                clear_symbol()
                if (menu_depth > 0) {
                    delete menu_matches[menu_depth]
                    menu_depth--
                }
                next
            }

            /^[[:space:]]*if[[:space:]]/ {
                save_ic = IGNORECASE; IGNORECASE = 0
                kw = $0; sub(/^[[:space:]]*/, "", kw)
                is_kconfig_kw = (substr(kw, 1, 3) == "if ")
                IGNORECASE = save_ic
                if (!is_kconfig_kw) next

                clear_symbol()
                if_depth++
                if_matches[if_depth] = 0
                if (menuconfig_pattern_match && last_menuconfig_sym != "") {
                    cond = kw
                    sub(/^if[[:space:]]+/, "", cond)
                    if (match(cond, "(^|[^A-Za-z0-9_])" last_menuconfig_sym "($|[^A-Za-z0-9_])")) {
                        if_matches[if_depth] = 1
                    }
                }
                next
            }

            /^[[:space:]]*endif([[:space:]]|$)/ {
                save_ic = IGNORECASE; IGNORECASE = 0
                kw = $0; sub(/^[[:space:]]*/, "", kw)
                is_kconfig_kw = (substr(kw, 1, 5) == "endif")
                IGNORECASE = save_ic
                if (!is_kconfig_kw) next

                clear_symbol()
                if (if_depth > 0) {
                    delete if_matches[if_depth]
                    if_depth--
                }
                next
            }

            /^[[:space:]]*(choice|endchoice|source|rsource|osource|orsource|comment)([[:space:]]|$)/ {
                clear_symbol(); next
            }

            /^[[:space:]]*menuconfig[[:space:]]+[A-Za-z0-9_]+/ {
                sym = $2
                is_toggle = 0
                is_menuconfig = 1
                last_menuconfig_sym = $2
                menuconfig_pattern_match = 0
                next
            }

            /^[[:space:]]*config[[:space:]]+[A-Za-z0-9_]+/ {
                sym = $2
                is_toggle = 0
                is_menuconfig = 0
                next
            }

            /^[[:space:]]*(bool|tristate|def_bool|def_tristate)([[:space:]]|$)/ {
                is_toggle = 1
                # def_* takes a default expression, never an inline prompt.
                if ($1 ~ /^def_/) next
                if (quoted_property($0)) {
                    text = kconfig_text
                    maybe_emit(text)
                    if (is_menuconfig && text ~ pattern) {
                        menuconfig_pattern_match = 1
                    }
                }
                next
            }

            /^[[:space:]]*prompt[[:space:]]/ {
                if (!quoted_property($0)) next
                maybe_emit(kconfig_text)
                if (is_menuconfig && kconfig_text ~ pattern) {
                    menuconfig_pattern_match = 1
                }
            }
        ' \
        | sort -u
}

discover_legacy_kconfig_symbols() {
    # AF_ALG remains an application ABI even when its prompts say deprecated.
    discover_kconfig_symbols_by_pattern "(legacy|deprecated|obsolete|obsolet[oa]s?|backward[[:space:]-]?compat(ibility)?|backwards[[:space:]-]?compat(ibility)?|compatibility layer|provided only for backwards compatibility|provided only for backward compatibility|here only for backward compatibility|here only for backwards compatibility|(^|[^[:alpha:]])old([^[:alpha:]]|$))" \
        | awk '!/^CRYPTO_USER_API($|_)/ && $0 != "DRM_FBDEV_EMULATION"'
}

discover_debug_trace_kconfig_symbols() {
    # PROC_MEM_FORCE_PTRACE only matches through "ptrace()": it is the hardened
    # /proc/pid/mem choice, and disabling it falls back to PROC_MEM_ALWAYS_FORCE.
    # IPV6_IOAM6_LWTUNNEL is IOAM in-band telemetry ("Trace insertion"), not debugging.
    discover_kconfig_symbols_by_pattern "(debug|tracing|tracer|trace|ftrace|kgdb|kdb|kprobe|uprobe|sanitizer|gcov|coverage|fault[- ]?injection|runtime testing|developer use only|debugging only|only be enabled for testing|intended for testing|testing purposes|test only|not suitable for production|not for use in production|not in production kernels|not be enabled in production|do not use (it )?on production|do not enable on production|production (systems?|kernels?|builds?))" \
        | awk '$0 != "PROC_MEM_FORCE_PTRACE" && $0 != "IPV6_IOAM6_LWTUNNEL"'
}

discover_hardening_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(hardening|hardened|mitigations? for cpu vulnerabilities|stack protector|shadow stack|fortify|control flow integrity|kcfi|strict kernel rwx|strict module rwx|memory protection keys|remove the kernel mapping in user mode|reset memory attack mitigation)" \
        | awk '$0 != "QCOM_RPMH"'
}

discover_selftest_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(self[- ]?tests?|selftest|kunit tests?|boot[- ]time self[- ]tests?|unit tests?|test for )"
}

discover_dangerous_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(dangerous|unsafe)"
}

discover_coverage_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(gcov|coverage|kernel profiling|code profiling|function profiler|branch profil|profile guided optimi[sz]ation|profile all if conditionals|likely/unlikely profiler)"
}

discover_fault_injection_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(fault[- ]?injection|fault injector|inject faults?|simulate io errors|failure injection|error[- ]?inj)"
}

is_x86_config() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    [[ "${_SYMBOL_VALUE_CACHE[X86]:-}" == "y" ]] \
        || [[ "${_SYMBOL_VALUE_CACHE[X86_64]:-}" == "y" ]] \
        || [[ "${_SYMBOL_VALUE_CACHE[X86_32]:-}" == "y" ]]
}

detect_host_cpu_vendor() {
    local vendor_id="" line

    if [[ -r /proc/cpuinfo ]]; then
        while IFS= read -r line; do
            if [[ "$line" == vendor_id* ]]; then
                vendor_id="${line#*: }"
                break
            fi
        done < /proc/cpuinfo
    fi

    case "$vendor_id" in
        GenuineIntel)
            printf '%s\n' "intel"
            ;;
        AuthenticAMD | HygonGenuine)
            printf '%s\n' "amd"
            ;;
        *)
            printf '%s\n' "unknown"
            ;;
    esac
}

append_unique_item() {
    local value="$1"
    local -n items_ref="$2"
    local existing

    [[ -n "$value" ]] || return 0

    for existing in "${items_ref[@]}"; do
        if [[ "$existing" == "$value" ]]; then
            return 0
        fi
    done

    items_ref+=("$value")
}

normalize_config_symbol_name() {
    REPLY="${1//[[:space:]]/}"
    REPLY="${REPLY#CONFIG_}"
}

declare -A _PROTECTED_CONFIG_SYMBOL_MAP=()

load_protected_config_symbols() {
    local raw_sym
    local -a raw_symbols=()

    _PROTECTED_CONFIG_SYMBOL_MAP=()

    IFS=',' read -r -a raw_symbols <<<"$PROTECTED_CONFIG_SYMBOLS"
    for raw_sym in "${raw_symbols[@]}"; do
        normalize_config_symbol_name "$raw_sym"
        if [[ -n "$REPLY" ]]; then
            [[ "$REPLY" =~ ^[A-Za-z0-9_]+$ ]] || die "Invalid protected symbol: $raw_sym"
            _PROTECTED_CONFIG_SYMBOL_MAP["$REPLY"]=1
        fi
    done
}

is_protected_config_symbol() {
    [[ -v _PROTECTED_CONFIG_SYMBOL_MAP[$1] ]]
}

disable_config_symbol() {
    normalize_config_symbol_name "$1"
    local normalized_sym="$REPLY"

    case "${_KCONFIG_TYPES[$normalized_sym]:-}" in
        bool | tristate) ;;
        *) echo "Skipping non-toggle symbol: CONFIG_${normalized_sym}"; return 0 ;;
    esac

    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi

    echo "Disabling: CONFIG_${normalized_sym}"
    cfg --disable "$normalized_sym"
    _SYMBOL_VALUE_CACHE["$normalized_sym"]=n
    _REQUESTED_VALUES["$normalized_sym"]=n
}

enable_config_symbol() {
    normalize_config_symbol_name "$1"
    local normalized_sym="$REPLY"

    case "${_KCONFIG_TYPES[$normalized_sym]:-}" in
        bool | tristate) ;;
        *) echo "Skipping non-toggle symbol: CONFIG_${normalized_sym}"; return 0 ;;
    esac

    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi

    if [[ "${2:-preserve}" == preserve ]] && is_baseline_module_symbol "$normalized_sym"; then
        echo "Enabling: CONFIG_${normalized_sym} (kept as module)"
        module_config_symbol "$normalized_sym"
        return 0
    fi

    echo "Enabling: CONFIG_${normalized_sym}"
    cfg --enable "$normalized_sym"
    _SYMBOL_VALUE_CACHE["$normalized_sym"]=y
    _REQUESTED_VALUES["$normalized_sym"]=y
}

module_config_symbol() {
    normalize_config_symbol_name "$1"
    local normalized_sym="$REPLY"
    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi
    echo "Modularizing: CONFIG_${normalized_sym}"
    cfg --module "$normalized_sym"
    _SYMBOL_VALUE_CACHE["$normalized_sym"]=m
    _REQUESTED_VALUES["$normalized_sym"]=m
}

set_val_config_symbol() {
    local value="$2"
    normalize_config_symbol_name "$1"
    local normalized_sym="$REPLY"

    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi

    echo "Setting: CONFIG_${normalized_sym}=$value"
    cfg --set-val "$normalized_sym" "$value"
    _SYMBOL_VALUE_CACHE["$normalized_sym"]="$value"
    _REQUESTED_VALUES["$normalized_sym"]="$value"
}

resolve_cpu_vendor_filter() {
    local mode
    mode="${CPU_VENDOR_FILTER@L}"

    case "$mode" in
        "" | none | off | 0)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_cpu_vendor
            ;;
        amd | intel)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid CPU_VENDOR_FILTER: $CPU_VENDOR_FILTER (use none, auto, amd, or intel)" >&2
            exit 1
            ;;
    esac
}

detect_host_video_support() {
    local path module vendor class profile=""
    local -a detected_profiles=()

    while IFS= read -r -d '' path; do
        module=""
        if [[ -L "$path/device/driver/module" ]]; then
            module="$(readlink -f "$path/device/driver/module")"
            module="${module##*/}"
        elif [[ -L "$path/device/driver" ]]; then
            module="$(readlink -f "$path/device/driver")"
            module="${module##*/}"
        fi

        case "$module" in
            amdgpu | radeon)
                profile="amd"
                ;;
            i915 | xe)
                profile="intel"
                ;;
            nouveau)
                profile="nouveau"
                ;;
            nvidia | nvidia_drm | nvidia_modeset | nvidia_uvm)
                profile="nvidia"
                ;;
            *)
                profile=""
                ;;
        esac

        append_unique_item "$profile" detected_profiles
    done < <(find /sys/class/drm -mindepth 1 -maxdepth 1 -type l -name 'card[0-9]*' -print0 2>/dev/null)

    if ((${#detected_profiles[@]} == 1)); then
        printf '%s\n' "${detected_profiles[0]}"
        return
    elif ((${#detected_profiles[@]} > 1)); then
        printf '%s\n' "multiple"
        return
    fi

    while IFS= read -r -d '' path; do
        [[ -r "$path/class" && -r "$path/vendor" ]] || continue
        class="$(<"$path/class")"
        class="${class#0x}"

        case "$class" in
            030000 | 030200 | 038000)
                ;;
            *)
                continue
                ;;
        esac

        vendor="$(<"$path/vendor")"
        case "$vendor" in
            0x1002)
                profile="amd"
                ;;
            0x8086)
                profile="intel"
                ;;
            0x10de)
                profile="nvidia"
                ;;
            *)
                profile=""
                ;;
        esac

        append_unique_item "$profile" detected_profiles
    done < <(find /sys/bus/pci/devices -mindepth 1 -maxdepth 1 -print0 2>/dev/null)

    if ((${#detected_profiles[@]} == 1)); then
        printf '%s\n' "${detected_profiles[0]}"
    elif ((${#detected_profiles[@]} > 1)); then
        printf '%s\n' "multiple"
    else
        printf '%s\n' "unknown"
    fi
}

resolve_video_support() {
    local mode
    mode="${VIDEO_SUPPORT@L}"

    case "$mode" in
        "" | none | off | 0)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_video_support
            ;;
        amd | intel | nvidia | nouveau)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid VIDEO_SUPPORT: $VIDEO_SUPPORT (use none, auto, amd, intel, nvidia, or nouveau)" >&2
            exit 1
            ;;
    esac
}

detect_host_uefi_support() {
    if [[ -d /sys/firmware/efi ]]; then
        printf '%s\n' "on"
    else
        printf '%s\n' "off"
    fi
}

resolve_on_off_support() {
    local var_name="$1"
    local detect_fn="$2"
    local extra_on="${3:-}"
    local extra_off="${4:-}"
    local raw mode alias

    raw="${!var_name}"
    mode="${raw@L}"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            return
            ;;
        auto)
            if [[ -n "$detect_fn" ]]; then
                "$detect_fn"
            else
                printf '%s\n' "auto"
            fi
            return
            ;;
        on | yes | true | 1 | enable | enabled)
            printf '%s\n' "on"
            return
            ;;
        off | no | false | 0 | disable | disabled)
            printf '%s\n' "off"
            return
            ;;
    esac

    for alias in $extra_on; do
        if [[ "$mode" == "$alias" ]]; then
            printf '%s\n' "on"
            return
        fi
    done

    for alias in $extra_off; do
        if [[ "$mode" == "$alias" ]]; then
            printf '%s\n' "off"
            return
        fi
    done

    echo "Invalid $var_name: $raw (use none, auto, on, or off)" >&2
    exit 1
}

resolve_uefi_support() {
    resolve_on_off_support UEFI_SUPPORT detect_host_uefi_support "uefi" "bios legacy"
}

detect_host_initrd_support() {
    local ramdisk_image=""
    local ramdisk_size=""

    if [[ -r /sys/kernel/boot_params/data ]]; then
        ramdisk_image="$(od -An -j $((0x218)) -N 4 -t u4 /sys/kernel/boot_params/data 2>/dev/null)" || true
        ramdisk_image="${ramdisk_image//[[:space:]]/}"
        ramdisk_size="$(od -An -j $((0x21c)) -N 4 -t u4 /sys/kernel/boot_params/data 2>/dev/null)" || true
        ramdisk_size="${ramdisk_size//[[:space:]]/}"

        if [[ -n "$ramdisk_image" && -n "$ramdisk_size" ]]; then
            if [[ "$ramdisk_image" != "0" && "$ramdisk_size" != "0" ]]; then
                printf '%s\n' "on"
            else
                printf '%s\n' "off"
            fi
            return
        fi
    fi

    local _cmdline
    if [[ -r /proc/cmdline ]] && { _cmdline="$(</proc/cmdline)"; [[ "$_cmdline" =~ (^|[[:space:]])initrd= ]]; }; then
        printf '%s\n' "on"
    else
        printf '%s\n' "unknown"
    fi
}

resolve_initrd_support() {
    resolve_on_off_support INITRD_SUPPORT detect_host_initrd_support "initrd initramfs"
}

detect_host_tpm_versions() {
    local path version description
    local -a versions=()

    for path in /sys/class/tpm/tpm*; do
        [[ -d "$path" ]] || continue

        version=""
        if [[ -r "$path/tpm_version_major" ]]; then
            local tpm_ver_raw
            tpm_ver_raw="$(<"$path/tpm_version_major")"
            tpm_ver_raw="${tpm_ver_raw//[[:space:]]/}"
            case "$tpm_ver_raw" in
                1)
                    version="1.2"
                    ;;
                2)
                    version="2.0"
                    ;;
            esac
        fi

        if [[ -z "$version" ]]; then
            if [[ -r "$path/device/description" ]]; then
                description="$(<"$path/device/description")"
                description="${description@L}"
            elif [[ -r "$path/description" ]]; then
                description="$(<"$path/description")"
                description="${description@L}"
            else
                description=""
            fi

            case "$description" in
                *"2.0"*)
                    version="2.0"
                    ;;
                *"1.2"*)
                    version="1.2"
                    ;;
                *)
                    version="unknown"
                    ;;
            esac
        fi

        append_unique_item "$version" versions
    done

    if ((${#versions[@]} == 0)); then
        printf '%s\n' "none"
        return
    fi

    printf '%s\n' "${versions[@]}"
}

resolve_tpm_support() {
    local mode detected

    mode="${TPM_SUPPORT@L}"
    if [[ "$mode" == "auto" ]]; then
        detected="$(detect_host_tpm_versions)"
        if [[ "$detected" == "none" ]]; then
            printf '%s\n' "off"
        else
            printf '%s\n' "$detected"
        fi
        return
    fi

    resolve_on_off_support TPM_SUPPORT detect_host_tpm_versions "tpm"
}

detect_host_dma_engine_support() {
    local path

    for path in /sys/class/dma/*; do
        [[ -e "$path" ]] || continue
        printf '%s\n' "on"
        return
    done

    printf '%s\n' "off"
}

resolve_dma_engine_support() {
    resolve_on_off_support DMA_ENGINE_SUPPORT detect_host_dma_engine_support "dma dmaengine"
}

resolve_iommu_support() {
    resolve_on_off_support IOMMU_SUPPORT "" "iommu"
}

detect_host_numa_support() {
    local online=""

    if [[ ! -d /sys/devices/system/node ]]; then
        printf '%s\n' "off"
        return
    fi

    if compgen -G "/sys/devices/system/node/node[1-9]*" >/dev/null; then
        printf '%s\n' "on"
        return
    fi

    if [[ -r /sys/devices/system/node/online ]]; then
        online="$(</sys/devices/system/node/online)"
        online="${online//[[:space:]]/}"
        case "$online" in
            "" | 0)
                printf '%s\n' "off"
                ;;
            *)
                printf '%s\n' "on"
                ;;
        esac
        return
    fi

    printf '%s\n' "off"
}

resolve_numa_support() {
    resolve_on_off_support NUMA_SUPPORT detect_host_numa_support "numa"
}

resolve_numa_support_for_profile() {
    local mode
    mode="${NUMA_SUPPORT@L}"

    case "$mode" in
        "" | none)
            detect_host_numa_support
            ;;
        *)
            resolve_numa_support
            ;;
    esac
}

count_cpu_list_entries() {
    local cpu_list="$1"
    local cpu_tokens token start end count=0

    IFS=',' read -r -a cpu_tokens <<<"$cpu_list"
    for token in "${cpu_tokens[@]}"; do
        case "$token" in
            '' )
                ;;
            *-*)
                if [[ "$token" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                    start="${BASH_REMATCH[1]}"
                    end="${BASH_REMATCH[2]}"
                    if (( end < start )); then
                        return 1
                    fi
                    ((count += end - start + 1))
                else
                    return 1
                fi
                ;;
            *)
                if [[ "$token" =~ ^[0-9]+$ ]]; then
                    ((count += 1))
                else
                    return 1
                fi
                ;;
        esac
    done

    printf '%s\n' "$count"
}

detect_host_nr_cpus() {
    local path cpu_list count

    for path in \
        /sys/devices/system/cpu/present \
        /sys/devices/system/cpu/possible \
        /sys/devices/system/cpu/online; do
        [[ -r "$path" ]] || continue
        cpu_list="$(<"$path")"
        cpu_list="${cpu_list//[[:space:]]/}"
        count="$(count_cpu_list_entries "$cpu_list" 2>/dev/null || true)"
        if [[ "$count" =~ ^[1-9][0-9]*$ ]]; then
            printf '%s\n' "$count"
            return
        fi
    done

    if command -v getconf >/dev/null 2>&1; then
        count="$(getconf _NPROCESSORS_CONF 2>/dev/null || true)"
        if [[ "$count" =~ ^[1-9][0-9]*$ ]]; then
            printf '%s\n' "$count"
            return
        fi
    fi

    if command -v nproc >/dev/null 2>&1; then
        count="$(nproc --all 2>/dev/null || true)"
        if [[ "$count" =~ ^[1-9][0-9]*$ ]]; then
            printf '%s\n' "$count"
            return
        fi
    fi

    if [[ -r /proc/cpuinfo ]]; then
        count="$(awk '/^processor[[:space:]]*:/{count++} END{print count+0}' /proc/cpuinfo 2>/dev/null || true)"
        if [[ "$count" =~ ^[1-9][0-9]*$ ]]; then
            printf '%s\n' "$count"
            return
        fi
    fi

    printf '%s\n' "unknown"
}

resolve_nr_cpus() {
    local raw

    raw="${NR_CPUS@L}"

    case "$raw" in
        "" | none | off | keep)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_nr_cpus
            ;;
        *)
            if [[ "$raw" =~ ^[1-9][0-9]*$ ]]; then
                printf '%s\n' "$raw"
            else
                echo "Invalid NR_CPUS: $NR_CPUS (use none, auto, or a positive integer)" >&2
                exit 1
            fi
            ;;
    esac
}

resolve_application_profiles() {
    local raw normalized token
    local saw_none=0
    local -a profiles=()

    raw="${APPLICATIONS@L}"
    raw="${raw//,/ }"

    for token in $raw; do
        normalized="${token//_/-}"

        case "$normalized" in
            "" )
                ;;
            none | off | 0)
                saw_none=1
                ;;
            desktop | multimedia | rocm | nebula | warp | samba | firehol | firewalld | openvswitch | ceph | nfs-client | nfs-server | openvpn | wireguard | docker | qemu | atop | bmon | btop | htop | iotop-c | cryptsetup)
                if [[ "$saw_none" == "1" ]]; then
                    echo "Invalid APPLICATIONS: cannot combine 'none' with app profiles" >&2
                    exit 1
                fi
                append_unique_item "$normalized" profiles
                ;;
            *)
                echo "Invalid APPLICATIONS entry: $token" >&2
                echo "Use: desktop, multimedia, rocm, nebula, warp, samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, qemu, atop, bmon, btop, htop, iotop-c, cryptsetup" >&2
                exit 1
                ;;
        esac
    done

    if [[ "$saw_none" == "1" ]]; then
        if ((${#profiles[@]} > 0)); then
            echo "Invalid APPLICATIONS: cannot combine 'none' with app profiles" >&2
            exit 1
        fi
        printf '%s\n' "none"
        return
    fi

    if ((${#profiles[@]} == 0)); then
        printf '%s\n' "none"
        return
    fi

    printf '%s\n' "${profiles[@]}"
}

resolve_cpu_vendor_or_detect() {
    local vendor_mode

    vendor_mode="${CPU_VENDOR_FILTER@L}"
    case "$vendor_mode" in
        "" | none | off | 0)
            detect_host_cpu_vendor
            ;;
        *)
            resolve_cpu_vendor_filter
            ;;
    esac
}

list_xfs_mountpoints() {
    if command -v findmnt >/dev/null 2>&1; then
        findmnt -rn -t xfs -o TARGET 2>/dev/null || true
    else
        awk '$3 == "xfs" { print $2 }' /proc/self/mounts 2>/dev/null || true
    fi
}

probe_xfs_deprecated_features() {
    local mountpoint info
    local saw_xfs=0
    local need_v4=0
    local need_ascii_ci=0

    if ! command -v xfs_info >/dev/null 2>&1; then
        printf '%s\n' "unavailable"
        return
    fi

    while IFS= read -r mountpoint; do
        [[ -n "$mountpoint" ]] || continue
        saw_xfs=1
        info="$(xfs_info "$mountpoint" 2>/dev/null || true)"
        if [[ -z "$info" ]]; then
            printf '%s\n' "unknown"
            return
        fi

        if [[ "$info" == *"crc=0"* ]]; then
            need_v4=1
        fi

        if [[ "$info" == *"ascii-ci=1"* ]]; then
            need_ascii_ci=1
        fi
    done < <(list_xfs_mountpoints)

    if [[ "$saw_xfs" == "0" ]]; then
        printf '%s\n' "none"
        return
    fi

    if [[ "$need_v4" == "0" && "$need_ascii_ci" == "0" ]]; then
        printf '%s\n' "clean"
        return
    fi

    if [[ "$need_v4" == "1" ]]; then
        printf '%s\n' "v4"
    fi

    if [[ "$need_ascii_ci" == "1" ]]; then
        printf '%s\n' "ascii_ci"
    fi
}

resolve_host_type() {
    local mode
    # Assigned by init_tunable through printf -v.
    # shellcheck disable=SC2153
    mode="${HOST_TYPE@L}"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            ;;
        baremetal | native)
            printf '%s\n' "baremetal"
            ;;
        qemu | kvm)
            printf '%s\n' "qemu"
            ;;
        vmware | hyperv | virtualbox)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid HOST_TYPE: $HOST_TYPE (use none, baremetal, qemu (or kvm), vmware, hyperv, or virtualbox)" >&2
            exit 1
            ;;
    esac
}

resolve_optimization_profile() {
    local mode
    mode="${OPTIMIZATION_PROFILE@L}"

    case "$mode" in
        "" | none | off | 0)
            printf '%s\n' "none"
            ;;
        server | desktop | realtime)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid OPTIMIZATION_PROFILE: $OPTIMIZATION_PROFILE (use none, server, desktop, or realtime)" >&2
            exit 1
            ;;
    esac
}

resolve_validation_mode() {
    local mode="${VALIDATION_MODE@L}"

    case "$mode" in
        warn | strict)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid VALIDATION_MODE: $VALIDATION_MODE (use warn or strict)" >&2
            exit 1
            ;;
    esac
}

resolve_preempt_mode() {
    local mode="${PREEMPT_MODE@L}"

    case "$mode" in
        "" | auto)
            printf '%s\n' "auto"
            ;;
        none | voluntary | lazy | full | rt)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid PREEMPT_MODE: $PREEMPT_MODE (use auto, none, voluntary, lazy, full, or rt)" >&2
            exit 1
            ;;
    esac
}

resolve_timer_hz() {
    local value="${TIMER_HZ@L}"

    case "$value" in
        "" | auto)
            printf '%s\n' "auto"
            ;;
        100 | 250 | 300 | 1000)
            printf '%s\n' "$value"
            ;;
        *)
            echo "Invalid TIMER_HZ: $TIMER_HZ (use auto, 100, 250, 300, or 1000)" >&2
            exit 1
            ;;
    esac
}

resolve_auto_on_off_mode() {
    local name="$1"
    local value="${!name}"
    value="${value@L}"

    case "$value" in
        "" | auto)
            printf '%s\n' "auto"
            ;;
        on | off)
            printf '%s\n' "$value"
            ;;
        *)
            echo "Invalid $name: ${!name} (use auto, on, or off)" >&2
            exit 1
            ;;
    esac
}

resolve_native_cpu_mode() {
    local value="${NATIVE_CPU@L}"

    case "$value" in
        "" | none)
            printf '%s\n' "none"
            ;;
        on | off)
            printf '%s\n' "$value"
            ;;
        *)
            echo "Invalid NATIVE_CPU: $NATIVE_CPU (use none, on, or off)" >&2
            exit 1
            ;;
    esac
}

vendor_kconfig_files() {
    local path

    for path in \
        "$KSRCDIR/arch/x86/Kconfig" \
        "$KSRCDIR/arch/x86/Kconfig.cpu" \
        "$KSRCDIR/arch/x86/events/Kconfig" \
        "$KSRCDIR/arch/x86/kvm/Kconfig" \
        "$KSRCDIR/drivers/cpufreq/Kconfig.x86" \
        "$KSRCDIR/drivers/dma/Kconfig" \
        "$KSRCDIR/drivers/dma/amd/Kconfig" \
        "$KSRCDIR/drivers/edac/Kconfig" \
        "$KSRCDIR/drivers/idle/Kconfig" \
        "$KSRCDIR/drivers/iommu/amd/Kconfig" \
        "$KSRCDIR/drivers/iommu/intel/Kconfig" \
        "$KSRCDIR/drivers/ntb/amd/Kconfig" \
        "$KSRCDIR/drivers/ntb/intel/Kconfig" \
        "$KSRCDIR/drivers/pinctrl/intel/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/hfi/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/hsmp/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/pmc/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/pmf/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/atomisp2/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/ifs/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/int1092/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/int3472/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/pmc/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/pmt/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/speed_select_if/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/telemetry/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/uncore-frequency/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/wmi/Kconfig" \
        "$KSRCDIR/drivers/thermal/intel/Kconfig" \
        "$KSRCDIR/drivers/virt/coco/tdx-guest/Kconfig" \
        "$KSRCDIR/drivers/virt/coco/sev-guest/Kconfig"; do
        if [[ -f "$path" ]]; then
            printf '%s\0' "$path"
        fi
    done
}

discover_vendor_kconfig_symbols() {
    local vendor="$1"
    local include_re exclude_re

    case "$vendor" in
        intel)
            include_re='CPU_SUP_INTEL|KVM_INTEL|INTEL_TDX|X86_INTEL_|INTEL_IDLE|INTEL_IFS|X86_SGX'
            # OFF is the default of a choice that also exists on AMD hosts.
            # Disabling every choice member just makes Kconfig restore OFF.
            exclude_re='CPU_SUP_AMD|CPU_SUP_HYGON|KVM_AMD|AMD_MEM_ENCRYPT|SEV|X86_AMD_|AMD_HFI|X86_INTEL_TSX_MODE_OFF'
            ;;
        amd)
            include_re='CPU_SUP_AMD|CPU_SUP_HYGON|KVM_AMD|AMD_MEM_ENCRYPT|SEV|X86_AMD_|AMD_HFI'
            exclude_re='CPU_SUP_INTEL|KVM_INTEL|INTEL_TDX|X86_INTEL_|INTEL_IDLE|INTEL_IFS|X86_SGX'
            ;;
        *)
            return 0
            ;;
    esac

    # shellcheck disable=SC2016
    vendor_kconfig_files \
        | xargs -0 -r awk -v include_re="$include_re" -v exclude_re="$exclude_re" '
            function emit() {
                if (sym != "" && is_toggle && saw_include && !saw_exclude) {
                    print sym
                }
            }

            function positive_match(text, pattern, prefix) {
                if (!match(text, pattern)) {
                    return 0
                }

                prefix = substr(text, 1, RSTART - 1)
                gsub(/[[:space:](]+$/, "", prefix)
                return prefix !~ /!$/
            }

            # A dependency is vendor-only when every top-level "||" alternative
            # needs the vendor. MITIGATION_RETBLEED depends on
            # "(CPU_SUP_INTEL && ...) || MITIGATION_UNRET_ENTRY || ..." and also
            # covers AMD Zen 1/2, so a bare match on CPU_SUP_INTEL is not enough.
            function vendor_only_dependency(text, pattern, expr, depth, i, c, part) {
                expr = text
                sub(/^[[:space:]]*depends on[[:space:]]+/, "", expr)
                sub(/[[:space:]]*#.*$/, "", expr)
                depth = 0
                part = ""
                for (i = 1; i <= length(expr); i++) {
                    c = substr(expr, i, 1)
                    if (c == "(") {
                        depth++
                    } else if (c == ")") {
                        depth--
                    } else if (depth == 0 && substr(expr, i, 2) == "||") {
                        if (!positive_match(part, pattern)) {
                            return 0
                        }
                        part = ""
                        i++
                        continue
                    }
                    part = part c
                }

                return positive_match(part, pattern)
            }

            /^[[:space:]]*(config|menuconfig)[[:space:]]+[A-Za-z0-9_]+/ {
                emit()
                sym = $2
                is_toggle = 0
                saw_include = (sym ~ include_re)
                saw_exclude = (sym ~ exclude_re)
                next
            }

            /^[[:space:]]*(bool|tristate)([[:space:]]|$)/ {
                is_toggle = 1
                next
            }

            /^[[:space:]]*depends on[[:space:]]+/ {
                if (vendor_only_dependency($0, include_re)) {
                    saw_include = 1
                }

                if (positive_match($0, exclude_re)) {
                    saw_exclude = 1
                }
            }

            END {
                emit()
            }
        ' \
        | sort -u
}

disable_if_present() {
    local -a unique_syms=()
    local sym

    prepare_sorted_unique_symbols unique_syms "$@"
    for sym in "${unique_syms[@]}"; do
        if have_symbol "$sym"; then
            disable_config_symbol "$sym"
        fi
    done
}

optimize_compression() {
    enable_if_present ZSWAP ZSWAP_DEFAULT_ON
    select_if_present ZSWAP_COMPRESSOR_DEFAULT_LZO \
        ZSWAP_COMPRESSOR_DEFAULT_DEFLATE ZSWAP_COMPRESSOR_DEFAULT_842 \
        ZSWAP_COMPRESSOR_DEFAULT_LZ4 ZSWAP_COMPRESSOR_DEFAULT_LZ4HC \
        ZSWAP_COMPRESSOR_DEFAULT_ZSTD
}

request_explicit_symbol() {
    local sym="$1" value="$2"
    if ! have_symbol "$sym"; then
        _UNSUPPORTED_REQUESTS+=("CONFIG_$sym is not defined in this kernel tree")
        return
    fi
    case "${_KCONFIG_TYPES[$sym]:-}" in
        bool | tristate) ;;
        *) _UNSUPPORTED_REQUESTS+=("CONFIG_$sym is not a bool/tristate symbol"); return ;;
    esac
    if [[ "$value" == m && "${_KCONFIG_TYPES[$sym]}" != tristate ]]; then
        _UNSUPPORTED_REQUESTS+=("CONFIG_$sym cannot be built as a module")
        return
    fi
    if is_protected_config_symbol "$sym"; then
        # Preserve the symbol, but retain the explicit request for final validation.
        _REQUESTED_VALUES["$sym"]="$value"
        return
    fi
    case "$value" in
        y) enable_config_symbol "$sym" builtin ;;
        m) module_config_symbol "$sym" ;;
        n) disable_config_symbol "$sym" ;;
        *) die "Unsupported symbol request: $sym=$value" ;;
    esac
}

require_control_symbols() {
    local sym missing=0
    for sym in "$@"; do
        if ! have_symbol "$sym"; then
            _UNSUPPORTED_REQUESTS+=("CONFIG_$sym is not defined in this kernel tree")
            missing=1
        fi
    done
    ((missing == 0))
}

forget_control_requests() {
    # A later explicit parent override supersedes a profile's child requests.
    # Protected values are tracked separately and must still be preserved.
    local prefix="$1" sym
    for sym in "${!_REQUESTED_VALUES[@]}"; do
        if [[ "$sym" == "$prefix"* ]]; then
            unset '_REQUESTED_VALUES[$sym]'
        fi
    done
}

request_choice() {
    local selected="$1" sym desired
    shift
    require_control_symbols "$selected" || return 0
    for sym in "$selected" "$@"; do
        desired=n
        [[ "$sym" == "$selected" ]] && desired=y
        if is_protected_config_symbol "$sym" && [[ "${_SYMBOL_VALUE_CACHE[$sym]:-n}" != "$desired" ]]; then
            _UNSUPPORTED_REQUESTS+=("choice CONFIG_$selected conflicts with protected CONFIG_$sym")
            return 0
        fi
    done
    for sym in "$@"; do
        if [[ "$sym" != "$selected" ]] && have_symbol "$sym"; then
            request_explicit_symbol "$sym" n
        fi
    done
    request_explicit_symbol "$selected" y
}

configure_preemption() {
    local mode="$1" selected
    case "$mode" in
        keep) return ;;
        none) selected=PREEMPT_NONE ;;
        voluntary) selected=PREEMPT_VOLUNTARY ;;
        full | rt) selected=PREEMPT ;;
        lazy) selected=PREEMPT_LAZY ;;
    esac
    require_control_symbols "$selected" || return 0
    if [[ "$mode" == rt ]]; then
        require_control_symbols PREEMPT_RT || return 0
        request_explicit_symbol PREEMPT_RT y
    elif have_symbol PREEMPT_RT; then
        request_explicit_symbol PREEMPT_RT n
    fi
    # RT and DYNAMIC are outside the model choice. RT uses the full model.
    request_choice "$selected" PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_LAZY
}

configure_tick_mode() {
    local mode="$1" selected
    case "$mode" in
        keep) return ;;
        periodic) selected=HZ_PERIODIC ;;
        idle) selected=NO_HZ_IDLE ;;
        full)
            selected=NO_HZ_FULL
            # Full dynticks selects its own accounting; supersede profile defaults.
            forget_control_requests TICK_CPU_ACCOUNTING
            forget_control_requests VIRT_CPU_ACCOUNTING
            ;;
    esac
    request_choice "$selected" HZ_PERIODIC NO_HZ_IDLE NO_HZ_FULL
    if [[ "$mode" != full ]] && ! is_symbol_enabled_now RCU_EXPERT; then
        # Without full dynticks or expert RCU settings, Kconfig removes offload
        # support. A later explicit symbol override still gets validated.
        forget_control_requests RCU_NOCB_CPU
    fi
}

configure_profile_scheduler() {
    local profile="$1" model="" tick=idle
    if [[ "$profile" != desktop ]] && is_symbol_enabled_now SMP \
        && is_symbol_enabled_now HAVE_CONTEXT_TRACKING_USER \
        && is_symbol_enabled_now HAVE_VIRT_CPU_ACCOUNTING_GEN; then
        tick=full
    fi
    if have_symbol NO_HZ_IDLE; then
        configure_tick_mode "$tick"
    fi
    case "$profile" in
        server)
            if config_has_symbol PREEMPT_NONE; then
                model=none
            elif is_symbol_enabled_now ARCH_HAS_PREEMPT_LAZY && have_symbol PREEMPT_LAZY; then
                model=lazy
            elif config_has_symbol PREEMPT_VOLUNTARY; then
                model=voluntary
            elif have_symbol PREEMPT; then
                model=full
            fi
            disable_if_present PREEMPT_DYNAMIC
            ;;
        desktop)
            if have_symbol PREEMPT && ! is_symbol_enabled_now ARCH_NO_PREEMPT; then
                model=full
            elif config_has_symbol PREEMPT_NONE; then
                model=none
            fi
            if config_has_symbol PREEMPT_DYNAMIC || is_symbol_enabled_now HAVE_PREEMPT_DYNAMIC; then
                enable_if_present PREEMPT_DYNAMIC
            fi
            ;;
        realtime)
            if have_symbol PREEMPT_RT && is_symbol_enabled_now EXPERT \
                && is_symbol_enabled_now ARCH_SUPPORTS_RT && ! is_symbol_enabled_now COMPILE_TEST; then
                model=rt
            elif have_symbol PREEMPT && ! is_symbol_enabled_now ARCH_NO_PREEMPT; then
                echo "    (PREEMPT_RT prerequisites are not enabled; using full preemption)"
                model=full
            elif config_has_symbol PREEMPT_NONE; then
                echo "    (preemption is unavailable on this target; keeping non-preemptible model)"
                model=none
            fi
            disable_if_present PREEMPT_DYNAMIC
            ;;
    esac
    if [[ -n "$model" ]]; then
        configure_preemption "$model"
    fi
}

configure_thp_control() {
    [[ "$THP" != keep ]] || return 0
    require_control_symbols TRANSPARENT_HUGEPAGE || return 0
    forget_control_requests TRANSPARENT_HUGEPAGE
    if [[ "$THP" == off ]]; then
        forget_control_requests PERSISTENT_HUGE_ZERO_FOLIO
        request_explicit_symbol TRANSPARENT_HUGEPAGE n
    else
        require_control_symbols "TRANSPARENT_HUGEPAGE_${THP@U}" || return 0
        request_explicit_symbol TRANSPARENT_HUGEPAGE y
        request_choice "TRANSPARENT_HUGEPAGE_${THP@U}" \
            TRANSPARENT_HUGEPAGE_ALWAYS TRANSPARENT_HUGEPAGE_MADVISE TRANSPARENT_HUGEPAGE_NEVER
    fi
}

configure_zswap_control() {
    [[ "$ZSWAP" != keep || "$ZSWAP_COMPRESSOR" != keep ]] || return 0
    require_control_symbols ZSWAP || return 0
    if [[ "$ZSWAP" == off ]]; then
        forget_control_requests ZSWAP
        request_explicit_symbol ZSWAP n
        return
    fi
    request_explicit_symbol SWAP y
    request_explicit_symbol ZSWAP y
    if [[ "$ZSWAP" == on ]]; then
        request_explicit_symbol ZSWAP_DEFAULT_ON y
    fi
    if [[ "$ZSWAP_COMPRESSOR" != keep ]]; then
        request_choice "ZSWAP_COMPRESSOR_DEFAULT_${ZSWAP_COMPRESSOR@U}" \
            ZSWAP_COMPRESSOR_DEFAULT_LZO ZSWAP_COMPRESSOR_DEFAULT_LZ4 \
            ZSWAP_COMPRESSOR_DEFAULT_LZ4HC ZSWAP_COMPRESSOR_DEFAULT_ZSTD \
            ZSWAP_COMPRESSOR_DEFAULT_DEFLATE ZSWAP_COMPRESSOR_DEFAULT_842
    fi
}

configure_zram_control() {
    [[ "$ZRAM" != keep || "$ZRAM_COMPRESSOR" != keep ]] || return 0
    require_control_symbols ZRAM || return 0
    local mode="$ZRAM" suffix backend
    if [[ "$mode" == keep ]]; then
        if [[ "$(symbol_value ZRAM)" == m ]]; then
            mode=module
        else
            mode=builtin
        fi
    fi
    if [[ "$mode" == module ]] && ! is_symbol_enabled_now MODULES; then
        _UNSUPPORTED_REQUESTS+=("ZRAM=module requires CONFIG_MODULES=y; module support was not changed")
        return
    fi
    case "$mode" in
        off) request_explicit_symbol ZRAM n; return ;;
        module) request_explicit_symbol ZRAM m ;;
        builtin) request_explicit_symbol ZRAM y ;;
    esac
    if [[ "$ZRAM_COMPRESSOR" != keep ]]; then
        suffix="${ZRAM_COMPRESSOR@U}"
        backend="$suffix"
        if [[ "$ZRAM_COMPRESSOR" == lzo-rle ]]; then
            suffix=LZORLE
            backend=LZO
        fi
        require_control_symbols "ZRAM_BACKEND_$backend" "ZRAM_DEF_COMP_$suffix" || return 0
        request_explicit_symbol "ZRAM_BACKEND_$backend" y
        request_choice "ZRAM_DEF_COMP_$suffix" ZRAM_DEF_COMP_LZORLE ZRAM_DEF_COMP_LZO \
            ZRAM_DEF_COMP_LZ4 ZRAM_DEF_COMP_LZ4HC ZRAM_DEF_COMP_ZSTD ZRAM_DEF_COMP_DEFLATE ZRAM_DEF_COMP_842
    fi
}

configure_numa_balancing_control() {
    [[ "$NUMA_BALANCING" != keep ]] || return 0
    require_control_symbols NUMA_BALANCING || return 0
    forget_control_requests NUMA_BALANCING
    if [[ "$NUMA_BALANCING" == off ]]; then
        request_explicit_symbol NUMA_BALANCING n
    else
        request_explicit_symbol NUMA y
        if have_symbol NUMA_MIGRATION; then
            request_explicit_symbol NUMA_MIGRATION y
        else
            request_explicit_symbol MIGRATION y
        fi
        request_explicit_symbol NUMA_BALANCING y
        request_explicit_symbol NUMA_BALANCING_DEFAULT_ENABLED y
    fi
}

configure_profile_numa_balancing() {
    [[ "$NUMA_SUPPORT_EFFECTIVE" != off && "$NUMA_BALANCING" != off ]] || return 0
    if [[ "$NUMA_BALANCING" == keep && "$NUMA_BALANCING_MODE_EFFECTIVE" == off ]]; then
        return 0
    fi
    if [[ "$NUMA_SUPPORT_EFFECTIVE" == on ]] || is_symbol_enabled_now NUMA; then
        # Reuse version-aware dependencies without changing the explicit control
        # or enabling NUMA on a non-NUMA baseline.
        NUMA_BALANCING=on configure_numa_balancing_control
    fi
}

configure_kmalloc_partition_control() {
    [[ "$KMALLOC_PARTITION" != keep ]] || return 0
    if have_symbol KMALLOC_PARTITION_CACHES; then
        # RANDOM_KMALLOC_CACHES is transitional in 7.2; do not request its value.
        if [[ "$KMALLOC_PARTITION" == off ]]; then
            request_explicit_symbol KMALLOC_PARTITION_CACHES n
        else
            require_control_symbols "KMALLOC_PARTITION_${KMALLOC_PARTITION@U}" || return 0
            request_explicit_symbol KMALLOC_PARTITION_CACHES y
            request_choice "KMALLOC_PARTITION_${KMALLOC_PARTITION@U}" \
                KMALLOC_PARTITION_RANDOM KMALLOC_PARTITION_TYPED
        fi
    elif [[ "$KMALLOC_PARTITION" == typed ]]; then
        _UNSUPPORTED_REQUESTS+=("typed kmalloc partitioning is not supported by this kernel")
    elif [[ "$KMALLOC_PARTITION" == random ]]; then
        request_explicit_symbol RANDOM_KMALLOC_CACHES y
    else
        request_explicit_symbol RANDOM_KMALLOC_CACHES n
    fi
}

configure_tcp_congestion_control() {
    [[ "$TCP_CONGESTION" != keep ]] || return 0
    require_control_symbols "DEFAULT_${TCP_CONGESTION@U}" || return 0
    request_explicit_symbol NET y
    request_explicit_symbol INET y
    request_explicit_symbol TCP_CONG_ADVANCED y
    if [[ "$TCP_CONGESTION" != reno ]]; then
        request_explicit_symbol "TCP_CONG_${TCP_CONGESTION@U}" y
    fi
    if [[ "$TCP_CONGESTION" == bbr ]]; then
        request_explicit_symbol NET_SCHED y
        request_explicit_symbol NET_SCH_FQ y
    fi
    request_choice "DEFAULT_${TCP_CONGESTION@U}" DEFAULT_BIC DEFAULT_CUBIC DEFAULT_HTCP \
        DEFAULT_HYBLA DEFAULT_VEGAS DEFAULT_VENO DEFAULT_WESTWOOD DEFAULT_DCTCP DEFAULT_CDG DEFAULT_BBR DEFAULT_RENO
}

configure_extended_controls() {
    configure_risk_controls
    case "$UCLAMP" in
        on)
            request_explicit_symbol CPU_FREQ_GOV_SCHEDUTIL y
            request_explicit_symbol UCLAMP_TASK y
            ;;
        off) forget_control_requests UCLAMP_TASK; request_explicit_symbol UCLAMP_TASK n ;;
    esac
    case "$AUTOGROUP" in
        on) request_explicit_symbol SCHED_AUTOGROUP y ;;
        off) request_explicit_symbol SCHED_AUTOGROUP n ;;
    esac
    _load_symbol_cache
    configure_preemption "$PREEMPTION"
    if [[ "$PREEMPTION_EFFECTIVE" == rt ]]; then
        # These profile defaults cannot survive PREEMPT_RT's dependencies.
        forget_control_requests TRANSPARENT_HUGEPAGE
        forget_control_requests PERSISTENT_HUGE_ZERO_FOLIO
        forget_control_requests NUMA_BALANCING
        disable_if_present TRANSPARENT_HUGEPAGE NUMA_BALANCING
    fi
    case "$PREEMPT_DYNAMIC" in
        on) request_explicit_symbol PREEMPT_DYNAMIC y ;;
        off) request_explicit_symbol PREEMPT_DYNAMIC n ;;
    esac
    configure_tick_mode "$TICK_MODE"
    configure_thp_control
    case "$LRU_GEN" in
        on)
            request_explicit_symbol LRU_GEN y
            request_explicit_symbol LRU_GEN_ENABLED y
            ;;
        off)
            request_explicit_symbol LRU_GEN_ENABLED n
            request_explicit_symbol LRU_GEN n
            ;;
    esac
    configure_zswap_control
    configure_zram_control
    configure_numa_balancing_control
    configure_kmalloc_partition_control
    configure_tcp_congestion_control
    case "$IO_URING" in
        on) request_explicit_symbol IO_URING y ;;
        off)
            if is_symbol_enabled_now EXPERT; then
                request_explicit_symbol IO_URING n
            else
                _UNSUPPORTED_REQUESTS+=("IO_URING=off requires CONFIG_EXPERT=y; EXPERT was not changed")
            fi
            ;;
    esac
}

configure_risk_controls() {
    local setting sym mode
    _load_symbol_cache
    for setting in MODULE_FORCE_LOAD MODULE_FORCE_UNLOAD OBSOLETE_CRYPTO; do
        mode="${!setting}"
        [[ "$mode" != keep ]] || continue
        sym="$setting"
        [[ "$setting" != OBSOLETE_CRYPTO ]] || sym=CRYPTO_USER_API_ENABLE_OBSOLETE
        if [[ "$mode" == on ]]; then
            if [[ "$setting" == MODULE_FORCE_* ]] && ! is_symbol_enabled_now MODULES; then
                _UNSUPPORTED_REQUESTS+=("$setting=on requires CONFIG_MODULES=y; module support was not changed")
                continue
            fi
            if [[ "$setting" == MODULE_FORCE_UNLOAD ]] && ! is_symbol_enabled_now MODULE_UNLOAD; then
                _UNSUPPORTED_REQUESTS+=("MODULE_FORCE_UNLOAD=on requires CONFIG_MODULE_UNLOAD=y")
                continue
            fi
            request_explicit_symbol "$sym" y
        else
            request_explicit_symbol "$sym" n
        fi
    done
    if [[ "$NFS_UDP" != keep ]]; then
        _load_symbol_cache
        if [[ "${_SYMBOL_VALUE_CACHE[NFS_FS]:-n}" == n ]]; then
            _UNSUPPORTED_REQUESTS+=("NFS_UDP=$NFS_UDP requires enabled CONFIG_NFS_FS; NFS was not enabled implicitly")
        elif [[ "$NFS_UDP" == off ]]; then
            request_explicit_symbol NFS_DISABLE_UDP_SUPPORT y
        else
            request_explicit_symbol NFS_DISABLE_UDP_SUPPORT n
        fi
    fi
}

prune_deprecated_aliases() {
    local alias replacement desired current
    local -a alias_targets=()
    # These aliases only select/imply replacement drivers in the audited trees.
    # Keep the replacement at least as available as the former alias. Kconfig
    # and strict validation still decide whether its dependencies are satisfied.
    while read -r alias replacement; do
        is_prunable_toggle "$alias" || continue
        _load_symbol_cache
        desired="${_SYMBOL_VALUE_CACHE[$alias]:-n}"
        [[ "$desired" == y || "$desired" == m ]] || continue
        alias_targets=("$alias")
        preserve_selected_prune_targets alias_targets
        ((${#alias_targets[@]})) || continue
        if ! have_symbol "$replacement"; then
            echo "Retaining CONFIG_$alias: replacement CONFIG_$replacement is unavailable"
            continue
        fi
        current="${_SYMBOL_VALUE_CACHE[$replacement]:-n}"
        [[ "$current" != y ]] || desired=y
        if is_protected_config_symbol "$replacement" && [[ "$current" != "$desired" ]]; then
            echo "Retaining CONFIG_$alias: replacement CONFIG_$replacement is protected at $current"
            continue
        fi
        echo "Migrating CONFIG_$alias to CONFIG_$replacement=$desired"
        request_explicit_symbol "$replacement" "$desired"
        disable_config_symbol "$alias"
    done <<'EOF'
HID_THINGM HID_LED
AK09911 AK8975
USB_EHCI_TEGRA USB_CHIPIDEA_TEGRA
USB_OHCI_HCD_OMAP3 USB_OHCI_HCD_PLATFORM
SND_SOC_INTEL_GLK_DA7219_MAX98357A_MACH SND_SOC_INTEL_SOF_DA7219_MACH
SND_SOC_INTEL_GLK_RT5682_MAX98357A_MACH SND_SOC_INTEL_SOF_RT5682_MACH
SND_SOC_INTEL_CML_LP_DA7219_MAX98357A_MACH SND_SOC_INTEL_SOF_DA7219_MACH
SND_SOC_INTEL_SOF_CML_RT1011_RT5682_MACH SND_SOC_INTEL_SOF_RT5682_MACH
EOF
}

configure_symbol_overrides() {
    local sym value
    # Apply built-ins first so an explicit MODULES=y can precede module requests.
    for value in y m n; do
        for sym in "${!_EXPLICIT_SYMBOL_VALUES[@]}"; do
            [[ "${_EXPLICIT_SYMBOL_VALUES[$sym]}" == "$value" ]] || continue
            if [[ "$value" == m ]] && ! is_symbol_enabled_now MODULES; then
                _UNSUPPORTED_REQUESTS+=("CONFIG_$sym=m requires CONFIG_MODULES=y")
                continue
            fi
            request_explicit_symbol "$sym" "$value"
        done
    done
}

_INITRAMFS_EVIDENCE=""
declare -a _INITRAMFS_REQUIRED_SYMBOLS=()

prepare_initramfs_check() {
    if [[ "$INITRAMFS_GENERATOR" == none && -z "$INITRAMFS_IMAGE" ]]; then
        [[ -z "$INITRAMFS_CONFIG" && "$INITRAMFS_COMPRESSION" == auto ]] \
            || die "Producer config/compression requires an initramfs generator"
        if [[ "$INITRD_COMPRESSION" == auto ]]; then
            die "INITRD_COMPRESSION=auto requires a generator or an initramfs image"
        fi
        return
    fi
    command -v python3 >/dev/null || die "Initramfs inspection requires Python 3.11+ (or use --initramfs-generator=none)"
    python3 -B -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
        || die "Initramfs inspection requires Python 3.11 or later"
    [[ -r "$SCRIPT_DIR/lib/initramfs_check.py" ]] || die "Missing helper: $SCRIPT_DIR/lib/initramfs_check.py"
    _INITRAMFS_EVIDENCE="$(python3 -B "$SCRIPT_DIR/lib/initramfs_check.py" discover \
        --generator "$INITRAMFS_GENERATOR" --producer-config "$INITRAMFS_CONFIG" \
        --image "$INITRAMFS_IMAGE" --compression "$INITRAMFS_COMPRESSION")" \
        || die "Could not inspect initramfs compression; original config was not modified"
    if [[ "$INITRD_COMPRESSION" == auto ]]; then
        local requirements
        if requirements="$(python3 -B "$SCRIPT_DIR/lib/initramfs_check.py" requirements <<<"$_INITRAMFS_EVIDENCE")"; then
            mapfile -t _INITRAMFS_REQUIRED_SYMBOLS <<<"$requirements"
        else
            _UNSUPPORTED_REQUESTS+=("Cannot infer initramfs decoder requirements")
        fi
    fi
}

verify_initramfs_result() {
    if [[ -z "$_INITRAMFS_EVIDENCE" ]]; then
        echo "Initramfs compression: not checked (--initramfs-generator=none)"
        return
    fi
    if ! python3 -B "$SCRIPT_DIR/lib/initramfs_check.py" validate --kernel-config "$CONFIG_FILE" <<<"$_INITRAMFS_EVIDENCE"; then
        if is_enabled "$STRICT"; then
            die "Strict initramfs validation failed; original config was not modified"
        fi
        echo "Warning: initramfs compatibility is not established; use --strict to reject this result." >&2
    fi
}

configure_explicit_controls() {
    local sym selected
    if [[ "$SCHED_CACHE" != none ]]; then
        if [[ "$SCHED_CACHE" == on ]]; then
            request_explicit_symbol SCHED_CACHE y
        else
            request_explicit_symbol SCHED_CACHE n
        fi
    fi
    if [[ "$KERNEL_COMPRESSION" != keep ]]; then
        selected="KERNEL_${KERNEL_COMPRESSION@U}"
        request_choice "$selected" KERNEL_GZIP KERNEL_BZIP2 KERNEL_LZMA KERNEL_XZ KERNEL_LZO KERNEL_LZ4 KERNEL_ZSTD
    fi
    if [[ "$INITRD_COMPRESSION" == auto ]]; then
        for sym in "${_INITRAMFS_REQUIRED_SYMBOLS[@]}"; do
            request_explicit_symbol "$sym" y
        done
    elif [[ "$INITRD_COMPRESSION" != keep ]]; then
        request_explicit_symbol BLK_DEV_INITRD y
        if [[ "$INITRD_COMPRESSION" != none ]]; then
            request_explicit_symbol "RD_${INITRD_COMPRESSION@U}" y
        fi
    fi
    case "$FIRMWARE_COMPRESSION" in
        on)
            request_explicit_symbol FW_LOADER_COMPRESS y
            request_explicit_symbol FW_LOADER_COMPRESS_XZ y
            request_explicit_symbol FW_LOADER_COMPRESS_ZSTD y
            ;;
        off)
            request_explicit_symbol FW_LOADER_COMPRESS n
            ;;
    esac
}

prepare_sorted_unique_symbols() {
    local -n symbols_ref="$1"
    local sym
    shift

    symbols_ref=()
    if (($# == 0)); then
        return
    fi

    local -A _dedup=()
    for sym in "$@"; do
        normalize_config_symbol_name "$sym"
        if [[ -n "$REPLY" ]] && ! [[ -v _dedup[$REPLY] ]]; then
            _dedup["$REPLY"]=1
        fi
    done

    # shellcheck disable=SC2034
    readarray -t symbols_ref < <(printf '%s\n' "${!_dedup[@]}" | sort)
}

is_inverse_disable_symbol() {
    normalize_config_symbol_name "$1"
    case "$REPLY" in
        *_DISABLE | *_DISABLE_* | *_DISABLED | *_DISABLED_*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

disable_discovered_and_fixed_symbols() {
    local discover_fn="$1"
    shift
    local sym
    local -a syms=()
    local -a disable_syms=()
    local -a enable_inverse_syms=()

    if [[ -n "$discover_fn" ]]; then
        mapfile -t syms < <("$discover_fn")
    fi

    syms+=("$@")
    for sym in "${syms[@]}"; do
        is_prunable_toggle "$sym" || continue
        if is_inverse_disable_symbol "$sym"; then
            # Do not enable an inverse choice whose parent is absent/disabled.
            config_has_symbol "$sym" || continue
            enable_inverse_syms+=("$sym")
        else
            disable_syms+=("$sym")
        fi
    done

    if ((${#disable_syms[@]} > 0)); then
        preserve_selected_prune_targets disable_syms
        disable_if_present "${disable_syms[@]}"
    fi

    if ((${#enable_inverse_syms[@]} > 0)); then
        enable_if_present "${enable_inverse_syms[@]}"
    fi
}

enable_if_present() {
    local -a unique_syms=()
    local sym

    prepare_sorted_unique_symbols unique_syms "$@"
    for sym in "${unique_syms[@]}"; do
        if have_symbol "$sym"; then
            enable_config_symbol "$sym"
        fi
    done
}

# Refresh effective values after enabling parents; symbol discovery uses Kconfig.
refresh_config_visibility() {
    make KCONFIG_CONFIG="$CONFIG_FILE" olddefconfig >/dev/null
    invalidate_symbol_cache
}

# Like enable_if_present, but refreshes the config when any symbol was not
# already =y, so that symbols depending on it can be configured afterwards.
enable_parents_if_present() {
    local -a unique_syms=()
    local sym
    local needs_refresh=false

    prepare_sorted_unique_symbols unique_syms "$@"
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    for sym in "${unique_syms[@]}"; do
        if have_symbol "$sym" && ! is_protected_config_symbol "$sym" \
            && [[ "${_SYMBOL_VALUE_CACHE[$sym]:-n}" != "y" ]]; then
            needs_refresh=true
        fi
    done

    enable_if_present "${unique_syms[@]}"
    if is_enabled "$needs_refresh"; then
        refresh_config_visibility
    fi
}

# Like enable_if_present, but leaves symbols that are already =m or =y alone.
# Used for "make available" items such as I/O schedulers or TCP algorithms.
enable_if_unset() {
    local -a unique_syms=()
    local sym

    prepare_sorted_unique_symbols unique_syms "$@"
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    for sym in "${unique_syms[@]}"; do
        if have_symbol "$sym" && [[ "${_SYMBOL_VALUE_CACHE[$sym]:-n}" == "n" ]]; then
            enable_config_symbol "$sym"
        fi
    done
}

list_config_hz_symbols() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    local sym
    for sym in "${!_SYMBOL_VALUE_CACHE[@]}"; do
        [[ "$sym" == HZ_[0-9]* ]] && printf '%s\n' "$sym"
    done
}

configure_hz_profile() {
    local profile="$1"
    local sym
    local -a hz_syms=()
    local -a preferred_syms=()
    local -a other_syms=()

    mapfile -t hz_syms < <(list_config_hz_symbols)
    if ((${#hz_syms[@]} == 0)); then
        return
    fi

    case "$profile" in
        server)
            preferred_syms=(HZ_100 HZ_250 HZ_300 HZ_1000)
            ;;
        desktop)
            preferred_syms=(HZ_1000 HZ_300 HZ_250 HZ_100)
            ;;
        realtime)
            preferred_syms=(HZ_1000 HZ_300 HZ_250 HZ_100)
            ;;
        *)
            return
            ;;
    esac

    local selected_sym=""
    for sym in "${preferred_syms[@]}"; do
        if have_symbol "$sym"; then
            selected_sym="$sym"
            break
        fi
    done

    if [[ -z "$selected_sym" ]]; then
        echo "    (no compatible HZ_* option found for profile $profile; leaving current HZ selection unchanged)"
        return
    fi

    for sym in "${hz_syms[@]}"; do
        if [[ "$sym" != "$selected_sym" ]]; then
            other_syms+=("$sym")
        fi
    done

    select_if_present "$selected_sym" "${other_syms[@]}"
    if have_symbol HZ; then
        set_val_config_symbol HZ "${selected_sym#HZ_}"
    fi
}

select_if_present() {
    local selected="$1"
    shift
    normalize_config_symbol_name "$selected"
    selected="$REPLY"
    if have_symbol "$selected"; then
        if is_protected_config_symbol "$selected"; then
            echo "Skipping protected choice: CONFIG_${selected}"
            return
        fi
        disable_if_present "$@"
        enable_config_symbol "$selected"
    fi
}

configure_explicit_timer_hz() {
    local value="$1"
    local selected="HZ_${value}"
    local sym
    local -a hz_syms=()
    local -a other_syms=()

    echo
    echo "==> Applying explicit timer frequency: ${value} Hz"

    mapfile -t hz_syms < <(list_config_hz_symbols)
    if have_symbol "$selected"; then
        for sym in "${hz_syms[@]}"; do
            [[ "$sym" == "$selected" ]] || other_syms+=("$sym")
        done
        select_if_present "$selected" "${other_syms[@]}"
        if have_symbol HZ; then
            set_val_config_symbol HZ "$value"
        fi
        return
    fi

    if ((${#hz_syms[@]} == 0)) && have_symbol HZ; then
        set_val_config_symbol HZ "$value"
        return
    fi

    record_config_request_issue "TIMER_HZ=$value is unavailable in this kernel configuration"
}

configure_explicit_preempt_mode() {
    local mode="$1"
    local selected=""

    case "$mode" in
        none) selected="PREEMPT_NONE" ;;
        voluntary) selected="PREEMPT_VOLUNTARY" ;;
        lazy) selected="PREEMPT_LAZY" ;;
        full) selected="PREEMPT" ;;
        rt) selected="PREEMPT_RT" ;;
    esac

    echo
    echo "==> Applying explicit preemption mode: $mode"

    if ! have_symbol "$selected"; then
        record_config_request_issue "PREEMPT_MODE=$mode requires unavailable CONFIG_${selected}"
        return
    fi

    configure_preemption "$mode"

    if [[ "$mode" == "full" || "$mode" == "lazy" ]]; then
        enable_if_present PREEMPT_DYNAMIC
    else
        disable_if_present PREEMPT_DYNAMIC
    fi
}

enable_numa_balancing_support() {
    local explicit_request="${1:-false}"

    if ! have_symbol NUMA_BALANCING; then
        if is_enabled "$explicit_request"; then
            record_config_request_issue "NUMA_BALANCING_MODE=on requires unavailable CONFIG_NUMA_BALANCING"
        fi
        return
    fi

    # Linux 7.2 split NUMA-specific page migration from the generic
    # CONFIG_MIGRATION symbol. Enable whichever dependency exists.
    enable_if_present NUMA_MIGRATION MIGRATION NUMA_BALANCING NUMA_BALANCING_DEFAULT_ENABLED
}

disable_numa_balancing_support() {
    disable_if_present NUMA_BALANCING_DEFAULT_ENABLED NUMA_BALANCING
}

configure_sched_cache_mode() {
    local mode="$1"
    local profile="$2"

    if [[ "$mode" == "auto" ]]; then
        if [[ "$profile" == "server" || "$profile" == "desktop" ]]; then
            enable_if_present SCHED_CACHE
        fi
        return
    fi

    echo
    echo "==> Applying scheduler cache mode: $mode"
    if ! have_symbol SCHED_CACHE; then
        record_config_request_issue "SCHED_CACHE_MODE=$mode requires unavailable CONFIG_SCHED_CACHE"
        return
    fi

    if [[ "$mode" == "on" ]]; then
        enable_if_present SCHED_CACHE
    else
        disable_if_present SCHED_CACHE
    fi
}

configure_mglru_mode() {
    local mode="$1"
    local profile="$2"

    if [[ "$mode" == "auto" ]]; then
        if [[ "$profile" == "server" || "$profile" == "desktop" ]]; then
            enable_parents_if_present LRU_GEN
            enable_if_present LRU_GEN_ENABLED
            disable_if_present LRU_GEN_STATS
        fi
        return
    fi

    echo
    echo "==> Applying Multi-Gen LRU mode: $mode"
    if ! have_symbol LRU_GEN; then
        record_config_request_issue "MGLRU_MODE=$mode requires unavailable CONFIG_LRU_GEN"
        return
    fi

    if [[ "$mode" == "on" ]]; then
        enable_parents_if_present LRU_GEN
        enable_if_present LRU_GEN_ENABLED
        disable_if_present LRU_GEN_STATS
    else
        disable_if_present LRU_GEN_STATS LRU_GEN_ENABLED LRU_GEN
    fi
}

configure_explicit_numa_balancing_mode() {
    local mode="$1"

    [[ "$mode" == "auto" ]] && return

    echo
    echo "==> Applying explicit NUMA balancing mode: $mode"
    if [[ "$mode" == "on" ]]; then
        enable_numa_balancing_support true
    else
        disable_numa_balancing_support
    fi
}

configure_throughput_memory_defaults() {
    local profile="$1"
    local -a zswap_choice=(
        ZSWAP_COMPRESSOR_DEFAULT_DEFLATE
        ZSWAP_COMPRESSOR_DEFAULT_LZO
        ZSWAP_COMPRESSOR_DEFAULT_842
        ZSWAP_COMPRESSOR_DEFAULT_LZ4
        ZSWAP_COMPRESSOR_DEFAULT_LZ4HC
        ZSWAP_COMPRESSOR_DEFAULT_ZSTD
    )
    local -a preferred_compressors=()
    local sym selected=""
    local -a others=()

    # PERSISTENT_HUGE_ZERO_FOLIO (6.18+), RSEQ_SLICE_EXTENSION (7.0+) and
    # ZSWAP_SHRINKER_DEFAULT_ON are no-ops on older trees.
    enable_if_present \
        PERSISTENT_HUGE_ZERO_FOLIO \
        RSEQ_SLICE_EXTENSION \
        ZSMALLOC \
        ZSWAP_SHRINKER_DEFAULT_ON

    # zswap compressor: zstd favours ratio (server), lz4 favours latency (desktop)
    case "$profile" in
        server) preferred_compressors=(ZSWAP_COMPRESSOR_DEFAULT_ZSTD ZSWAP_COMPRESSOR_DEFAULT_LZ4) ;;
        desktop) preferred_compressors=(ZSWAP_COMPRESSOR_DEFAULT_LZ4 ZSWAP_COMPRESSOR_DEFAULT_ZSTD) ;;
    esac

    for sym in "${preferred_compressors[@]}"; do
        if have_symbol "$sym"; then
            selected="$sym"
            break
        fi
    done

    if [[ -n "$selected" ]]; then
        for sym in "${zswap_choice[@]}"; do
            [[ "$sym" == "$selected" ]] || others+=("$sym")
        done
        select_if_present "$selected" "${others[@]}"
    fi

    # Tick-based cputime accounting avoids the per-transition overhead of
    # VIRT_CPU_ACCOUNTING_GEN. NO_HZ_FULL selects the latter, so leave it alone then.
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    if [[ "${_SYMBOL_VALUE_CACHE[NO_HZ_FULL]:-n}" != "y" ]]; then
        select_if_present TICK_CPU_ACCOUNTING VIRT_CPU_ACCOUNTING_GEN VIRT_CPU_ACCOUNTING_NATIVE
    fi
}

configure_native_cpu_profile() {
    local mode="$1"

    echo
    echo "==> Applying native CPU profile: $mode"

    if ! is_x86_config; then
        echo "    (CONFIG_X86_NATIVE_CPU is x86_64 only; .config is not x86, skipping)"
        return
    fi

    if ! have_symbol X86_NATIVE_CPU; then
        if [[ "$mode" == "on" ]]; then
            record_config_request_issue "NATIVE_CPU=on requires unavailable CONFIG_X86_NATIVE_CPU (kernel 6.16+ with -march=native support)"
        else
            echo "    (CONFIG_X86_NATIVE_CPU not present in this tree; nothing to disable)"
        fi
        return
    fi

    if [[ "$mode" == "on" ]]; then
        echo "    (the resulting kernel is only valid on the CPU model used to build it)"
        enable_if_present X86_NATIVE_CPU
    else
        disable_if_present X86_NATIVE_CPU
    fi
}

configure_optimization_profile() {
    local profile="$1"

    if [[ "$profile" == "none" ]]; then
        return
    fi

    echo
    echo "==> Applying optimization profile: $profile"

    local wants_observability=true
    if is_enabled "$PRUNE_OBSERVABILITY" || is_enabled "$PRUNE_DEBUG_TRACE"; then
        wants_observability=false
    fi

    # common: compiler optimizations, topology-aware scheduling, and the
    # tickless-friendly TEO cpuidle governor for all profiles
    enable_if_present \
        CC_OPTIMIZE_FOR_PERFORMANCE \
        CPU_IDLE \
        CPU_IDLE_GOV_TEO \
        CPU_ISOLATION \
        JUMP_LABEL \
        RSEQ \
        SCHED_CLUSTER \
        SCHED_MC \
        SCHED_MC_PRIO \
        SCHED_SMT

    # SLUB_TINY trades throughput for footprint; never wanted on a tuned kernel
    disable_if_present \
        CC_OPTIMIZE_FOR_SIZE \
        SLUB_TINY

    configure_profile_scheduler "$profile"
    case "$profile" in
        server)
            echo "    (prioritizes throughput and low background overhead)"

            disable_if_present \
                CPU_FREQ_DEFAULT_GOV_CONSERVATIVE \
                CPU_FREQ_DEFAULT_GOV_ONDEMAND \
                CPU_FREQ_DEFAULT_GOV_POWERSAVE \
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_DEFAULT_GOV_USERSPACE \
                SCHED_AUTOGROUP \
                WQ_POWER_EFFICIENT_DEFAULT

            # Project C (BMQ/PDS, gentoo-sources USE=experimental) replaces EEVDF and
            # rules out PSI, SCHED_CACHE, SCHED_CORE, NUMA_BALANCING and sched_ext; it
            # defaults to y when the Gentoo defaults patch is not applied.
            ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
            if [[ "${_SYMBOL_VALUE_CACHE[SCHED_ALT]:-n}" == "y" ]] && ! is_protected_config_symbol SCHED_ALT; then
                disable_if_present SCHED_ALT
                refresh_config_visibility
            fi

            enable_parents_if_present \
                BLK_CGROUP \
                CGROUP_SCHED \
                TCP_CONG_ADVANCED \
                TRANSPARENT_HUGEPAGE \
                ZSWAP

            # I/O schedulers and network algorithms are only made available
            # (existing =m stays =m); DEFAULT_* choices are left untouched.
            enable_if_present \
                BLK_CGROUP \
                BLK_CGROUP_IOCOST \
                BLK_DEV_THROTTLING \
                BLK_WBT \
                BLK_WBT_MQ \
                CFS_BANDWIDTH \
                CGROUP_SCHED \
                CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
                CPU_FREQ_GOV_PERFORMANCE \
                FAIR_GROUP_SCHED \
                KSM \
                MEMCG \
                RCU_NOCB_CPU \
                RCU_NOCB_CPU_DEFAULT_ALL \
                TRANSPARENT_HUGEPAGE \
                ZSWAP \
                ZSWAP_DEFAULT_ON

            enable_if_unset \
                MQ_IOSCHED_DEADLINE \
                MQ_IOSCHED_KYBER \
                NET_SCH_FQ \
                NET_SCH_FQ_CODEL \
                TCP_CONG_ADVANCED \
                TCP_CONG_BBR

            if have_symbol TRANSPARENT_HUGEPAGE_MADVISE; then
                select_if_present TRANSPARENT_HUGEPAGE_MADVISE \
                    TRANSPARENT_HUGEPAGE_ALWAYS TRANSPARENT_HUGEPAGE_NEVER
            fi

            configure_throughput_memory_defaults server

            # observability: only enable monitoring symbols when not pruning
            if is_enabled "$wants_observability"; then
                enable_if_present \
                    CPU_FREQ_STAT \
                    IRQ_TIME_ACCOUNTING \
                    PSI
                disable_if_present PSI_DEFAULT_DISABLED
            fi

            # NUMA-aware balancing: only if the host actually has NUMA
            configure_profile_numa_balancing

            if [[ "$TIMER_HZ_EFFECTIVE" == "auto" ]]; then
                configure_hz_profile server
            fi

            ;;
        desktop)
            echo "    (prioritizes interactivity and responsive scheduling)"

            disable_if_present \
                CPU_FREQ_DEFAULT_GOV_CONSERVATIVE \
                CPU_FREQ_DEFAULT_GOV_ONDEMAND \
                CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
                CPU_FREQ_DEFAULT_GOV_POWERSAVE \
                CPU_FREQ_DEFAULT_GOV_USERSPACE \
                PREEMPT_RT

            enable_parents_if_present \
                CGROUP_SCHED \
                TRANSPARENT_HUGEPAGE \
                ZSWAP

            enable_if_unset \
                BFQ_GROUP_IOSCHED \
                IOSCHED_BFQ \
                MQ_IOSCHED_DEADLINE

            enable_if_present \
                BLK_WBT \
                BLK_WBT_MQ \
                CGROUP_SCHED \
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_GOV_SCHEDUTIL \
                ENERGY_MODEL \
                FAIR_GROUP_SCHED \
                HIGH_RES_TIMERS \
                KSM \
                MEMCG \
                SCHED_AUTOGROUP \
                TRANSPARENT_HUGEPAGE \
                UCLAMP_TASK \
                WQ_POWER_EFFICIENT_DEFAULT \
                ZSWAP \
                ZSWAP_DEFAULT_ON

            if have_symbol TRANSPARENT_HUGEPAGE_MADVISE; then
                select_if_present TRANSPARENT_HUGEPAGE_MADVISE \
                    TRANSPARENT_HUGEPAGE_ALWAYS TRANSPARENT_HUGEPAGE_NEVER
            fi

            configure_throughput_memory_defaults desktop

            if is_enabled "$wants_observability"; then
                enable_if_present PSI
                disable_if_present PSI_DEFAULT_DISABLED
            fi

            configure_profile_numa_balancing

            if [[ "$TIMER_HZ_EFFECTIVE" == "auto" ]]; then
                configure_hz_profile desktop
            fi

            ;;
        realtime)
            echo "    (prioritizes low latency and deterministic wakeups)"

            # The compression preset's child requests cannot survive ZSWAP=n.
            forget_control_requests ZSWAP
            disable_if_present \
                CPU_FREQ_DEFAULT_GOV_CONSERVATIVE \
                CPU_FREQ_DEFAULT_GOV_ONDEMAND \
                CPU_FREQ_DEFAULT_GOV_POWERSAVE \
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_DEFAULT_GOV_USERSPACE \
                KSM \
                PSI \
                PSI_DEFAULT_DISABLED \
                SCHED_AUTOGROUP \
                WQ_POWER_EFFICIENT_DEFAULT \
                ZSWAP

            enable_if_present \
                BLK_WBT \
                BLK_WBT_MQ \
                CGROUP_SCHED \
                CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
                CPU_FREQ_GOV_PERFORMANCE \
                FAIR_GROUP_SCHED \
                HIGH_RES_TIMERS \
                RCU_BOOST \
                RCU_NOCB_CPU \
                RCU_NOCB_CPU_CB_BOOST

            if [[ "$NUMA_BALANCING_MODE_EFFECTIVE" == "auto" ]]; then
                disable_numa_balancing_support
            fi

            if [[ "$TIMER_HZ_EFFECTIVE" == "auto" ]]; then
                configure_hz_profile realtime
            fi

            ;;
    esac
}

configure_host_type_profile() {
    local host_type="$1"
    local sym desc
    local -A enable_set=()
    local -a type_syms=()
    local -a disable_syms=()

    local -a common_guest_syms=(
        HYPERVISOR_GUEST
        PARAVIRT
        PARAVIRT_XXL
        PARAVIRT_SPINLOCKS
        PARAVIRT_TIME_ACCOUNTING
    )
    # PARAVIRT_CLOCK is promptless and only selected by KVM_GUEST/XEN.
    # VIRTIO_VSOCKETS_COMMON is promptless too and also selected by
    # VSOCKETS_LOOPBACK, so it is left to Kconfig.
    local -a qemu_syms=(
        KVM_GUEST
        PARAVIRT_CLOCK
        VIRTIO
        VIRTIO_PCI
        VIRTIO_PCI_LIB
        VIRTIO_PCI_LIB_LEGACY
        VIRTIO_MMIO
        VIRTIO_MMIO_CMDLINE_DEVICES
        VIRTIO_BLK
        VIRTIO_NET
        VIRTIO_CONSOLE
        VIRTIO_BALLOON
        VIRTIO_FS
        SCSI_VIRTIO
        HW_RANDOM_VIRTIO
        VSOCKETS
        VSOCKETS_LOOPBACK
        VIRTIO_VSOCKETS
        PVPANIC
        PVPANIC_MMIO
        PVPANIC_PCI
        FW_CFG_SYSFS
        FW_CFG_SYSFS_CMDLINE
    )
    local -a vmware_syms=(
        VMWARE_VMCI
        VMWARE_BALLOON
        VMWARE_PVSCSI
        VMXNET3
        VSOCKETS
        VSOCKETS_LOOPBACK
        VMWARE_VMCI_VSOCKETS
    )
    local -a hyperv_syms=(
        HYPERV
        HYPERV_TIMER
        HYPERV_UTILS
        HYPERV_BALLOON
        HYPERV_VMBUS
        HYPERV_NET
        HYPERV_STORAGE
        PCI_HYPERV
        PCI_HYPERV_INTERFACE
        HYPERV_IOMMU # removed in 7.1+; the IRQ remapping code is built with HYPERV
        VSOCKETS
        VSOCKETS_LOOPBACK
        HYPERV_VSOCKETS
        HYPERV_KEYBOARD
        HID_HYPERV_MOUSE
    )
    # Synthetic video: DRM_HYPERV replaces FB_HYPERV, which Kconfig marks as
    # deprecated (so PRUNE_LEGACY disables it) but is the only console driver
    # when the baseline has no DRM.
    local -a hyperv_video_syms=(
        DRM_HYPERV
        FB_HYPERV
    )
    local -a virtualbox_syms=(
        VBOXGUEST
        VBOXSF_FS
    )

    local -a all_guest_syms=(
        "${common_guest_syms[@]}"
        "${qemu_syms[@]}"
        "${vmware_syms[@]}"
        "${hyperv_syms[@]}"
        "${hyperv_video_syms[@]}"
        "${virtualbox_syms[@]}"
    )

    echo
    echo "==> Applying host type profile: $host_type"

    case "$host_type" in
        baremetal)
            echo "    (disabling guest virtualization drivers for bare metal)"
            disable_if_present "${all_guest_syms[@]}"
            ;;
        qemu | vmware | hyperv | virtualbox)
            case "$host_type" in
                qemu)
                    type_syms=("${qemu_syms[@]}")
                    desc="enabling KVM/QEMU guest drivers and disabling other guest stacks"
                    ;;
                vmware)
                    type_syms=("${vmware_syms[@]}")
                    desc="enabling VMware guest drivers and disabling other guest stacks"
                    ;;
                hyperv)
                    type_syms=("${hyperv_syms[@]}")
                    desc="enabling Hyper-V guest drivers and disabling other guest stacks"
                    ;;
                virtualbox)
                    type_syms=("${virtualbox_syms[@]}")
                    desc="enabling VirtualBox guest drivers and disabling other guest stacks"
                    ;;
            esac

            echo "    ($desc)"

            for sym in "${common_guest_syms[@]}" "${type_syms[@]}"; do
                enable_set["$sym"]=1
            done

            for sym in "${all_guest_syms[@]}"; do
                if ! [[ -v enable_set[$sym] ]]; then
                    disable_syms+=("$sym")
                fi
            done

            enable_parents_if_present "${common_guest_syms[@]}" "${type_syms[@]}"
            enable_if_present "${type_syms[@]}"
            if ((${#disable_syms[@]} > 0)); then
                disable_if_present "${disable_syms[@]}"
            fi

            if [[ "$host_type" == "hyperv" ]]; then
                ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
                if [[ "${_SYMBOL_VALUE_CACHE[DRM]:-n}" != "n" ]] && have_symbol DRM_HYPERV; then
                    enable_if_present DRM_HYPERV
                else
                    echo "    (DRM_HYPERV is unavailable; keeping FB_HYPERV for the console)"
                    enable_if_present FB_HYPERV
                fi
            fi
            ;;
    esac
}

configure_video_support_profile() {
    local mode="$1"
    local -a enable_syms=()
    local -a disable_syms=()

    echo
    echo "==> Applying video support profile: $mode"

    case "$mode" in
        amd)
            echo "    (keeping AMD display drivers and pruning Intel/NVIDIA stacks)"
            # DRM_RADEON and FB_RADEON (pre-2015 cards) stay as the baseline has them
            enable_syms=(
                DRM_AMDGPU
            )
            disable_syms=(
                DRM_I915
                DRM_XE
                INTEL_GTT
                DRM_NOUVEAU
                FB_NVIDIA
            )
            ;;
        intel)
            echo "    (keeping Intel display drivers and pruning AMD/NVIDIA stacks)"
            enable_syms=(
                DRM_I915
                DRM_XE
                INTEL_GTT
            )
            disable_syms=(
                DRM_AMDGPU
                DRM_RADEON
                FB_RADEON
                DRM_NOUVEAU
                FB_NVIDIA
            )
            ;;
        nouveau)
            echo "    (keeping Nouveau support and pruning AMD/Intel stacks)"
            enable_syms=(
                DRM_NOUVEAU
                FB_NVIDIA
            )
            disable_syms=(
                DRM_AMDGPU
                DRM_RADEON
                FB_RADEON
                DRM_I915
                DRM_XE
                INTEL_GTT
            )
            ;;
        nvidia)
            echo "    (for proprietary NVIDIA modules; pruning in-kernel AMD/Intel/Nouveau drivers)"
            disable_syms=(
                DRM_AMDGPU
                DRM_RADEON
                FB_RADEON
                DRM_I915
                DRM_XE
                INTEL_GTT
                DRM_NOUVEAU
                FB_NVIDIA
            )
            ;;
    esac

    if ((${#enable_syms[@]} > 0)); then
        enable_parents_if_present DRM
        enable_if_present "${enable_syms[@]}"
    fi

    if ((${#disable_syms[@]} > 0)); then
        disable_if_present "${disable_syms[@]}"
    fi
}

configure_xfs_feature_support() {
    local probe_result
    local need_v4=0
    local need_ascii_ci=0
    local -a probe_results=()

    if ! have_symbol XFS_SUPPORT_V4 && ! have_symbol XFS_SUPPORT_ASCII_CI; then
        return
    fi

    echo
    echo "==> Checking mounted XFS filesystems for deprecated format features"

    mapfile -t probe_results < <(probe_xfs_deprecated_features)
    if ((${#probe_results[@]} == 0)); then
        echo "    (no XFS probe result; leaving XFS deprecated feature support unchanged)"
        return
    fi

    for probe_result in "${probe_results[@]}"; do
        case "$probe_result" in
            unavailable)
                echo "    (xfs_info not available; leaving XFS deprecated feature support unchanged)"
                return
                ;;
            unknown)
                echo "    (could not inspect one or more mounted XFS filesystems; leaving settings unchanged)"
                return
                ;;
            none)
                echo "    (no mounted XFS filesystems detected)"
                ;;
            clean)
                echo "    (all mounted XFS filesystems use modern format; disabling deprecated feature support)"
                ;;
            v4)
                need_v4=1
                ;;
            ascii_ci)
                need_ascii_ci=1
                ;;
        esac
    done

    if [[ "$need_v4" == "1" ]]; then
        enable_if_present XFS_SUPPORT_V4
    else
        disable_if_present XFS_SUPPORT_V4
    fi

    if [[ "$need_ascii_ci" == "1" ]]; then
        enable_if_present XFS_SUPPORT_ASCII_CI
    else
        disable_if_present XFS_SUPPORT_ASCII_CI
    fi
}

configure_uefi_support_profile() {
    local mode="$1"
    local -a enable_syms=()
    local -a disable_syms=()

    echo
    echo "==> Applying UEFI support profile: $mode"

    case "$mode" in
        on)
            echo "    (keeping common EFI runtime, boot stub, and EFI partition support)"
            enable_syms=(
                EFI
                EFI_STUB
                EFI_PARTITION
                EFIVAR_FS
                EFI_VARS_PSTORE
            )
            ;;
        off)
            echo "    (pruning common EFI runtime and boot stub support; keeping GPT/EFI_PARTITION)"
            disable_syms=(
                EFI
                EFI_STUB
                EFI_HANDOVER_PROTOCOL
                EFI_MIXED
                EFI_ESRT
                EFI_VARS_PSTORE
                EFI_VARS_PSTORE_DEFAULT_DISABLE
                EFI_SOFT_RESERVE
                EFI_DXE_MEM_ATTRIBUTES
                EFI_BOOTLOADER_CONTROL
                EFI_CAPSULE_LOADER
                EFI_TEST
                EFI_DISABLE_PCI_DMA
                EFI_EARLYCON
                EFI_CUSTOM_SSDT_OVERLAYS
                EFI_DISABLE_RUNTIME
                EFI_EMBEDDED_FIRMWARE
                EFIVAR_FS
            )
            ;;
    esac

    if ((${#enable_syms[@]} > 0)); then
        enable_if_present "${enable_syms[@]}"
    fi

    if ((${#disable_syms[@]} > 0)); then
        disable_if_present "${disable_syms[@]}"
    fi
}

configure_initrd_support_profile() {
    local mode="$1"

    echo
    echo "==> Applying initrd support profile: $mode"

    case "$mode" in
        on)
            echo "    (keeping initramfs/initrd boot support)"
            enable_if_present BLK_DEV_INITRD
            ;;
        off)
            echo "    (pruning initramfs/initrd boot support)"
            disable_if_present BLK_DEV_INITRD
            ;;
    esac
}

configure_tpm_support_profile() {
    local mode="$1"
    shift
    local version
    local logged_version=0
    local -a enable_syms=()
    local -a disable_syms=()

    echo
    echo "==> Applying TPM support profile: $mode"

    for version in "$@"; do
        case "$version" in
            2.0)
                echo "    (host TPM version detected: 2.0)"
                logged_version=1
                ;;
            1.2)
                echo "    (host TPM version detected: 1.2)"
                logged_version=1
                ;;
            unknown)
                echo "    (host TPM present but version could not be determined)"
                logged_version=1
                ;;
        esac
    done

    if [[ "$logged_version" == "0" && "$mode" == "off" ]]; then
        echo "    (no host TPM detected)"
    fi

    case "$mode" in
        on)
            echo "    (keeping generic TPM support)"
            enable_syms=(
                TCG_TPM
                HW_RANDOM_TPM
                TCG_TIS_CORE
                TCG_TIS
                TCG_CRB
                TCG_VTPM_PROXY
            )
            for version in "$@"; do
                case "$version" in
                    2.0)
                        append_unique_item "TCG_TPM2_HMAC" enable_syms
                        append_unique_item "TCG_CRB" enable_syms
                        append_unique_item "TCG_TIS" enable_syms
                        ;;
                    1.2)
                        append_unique_item "TCG_TIS" enable_syms
                        ;;
                esac
            done
            ;;
        off)
            echo "    (pruning TPM support)"
            disable_syms=(
                HW_RANDOM_TPM
                TCG_TPM2_HMAC
                TCG_TIS
                TCG_TIS_CORE
                TCG_TIS_SPI
                TCG_TIS_SPI_CR50
                TCG_TIS_I2C
                TCG_TIS_I2C_CR50
                TCG_TIS_I2C_ATMEL
                TCG_TIS_I2C_INFINEON
                TCG_TIS_I2C_NUVOTON
                TCG_NSC
                TCG_ATMEL
                TCG_INFINEON
                TCG_CRB
                TCG_VTPM_PROXY
                TCG_TIS_ST33ZP24
                TCG_TIS_ST33ZP24_I2C
                TCG_IBMVTPM
                TCG_XEN
                TCG_TPM
            )
            ;;
    esac

    if ((${#enable_syms[@]} > 0)); then
        enable_if_present "${enable_syms[@]}"
    fi

    if ((${#disable_syms[@]} > 0)); then
        disable_if_present "${disable_syms[@]}"
    fi
}

configure_dma_engine_support_profile() {
    local mode="$1"

    echo
    echo "==> Applying DMA Engine support profile: $mode"

    case "$mode" in
        on)
            echo "    (keeping CONFIG_DMADEVICES enabled)"
            enable_if_present DMADEVICES
            ;;
        off)
            echo "    (pruning CONFIG_DMADEVICES)"
            disable_if_present DMADEVICES
            ;;
    esac
}

configure_iommu_support_profile() {
    local mode="$1"
    local cpu_vendor="$2"
    local -a enable_syms=()
    local -a disable_syms=()

    echo
    echo "==> Applying IOMMU support profile: $mode"

    case "$mode" in
        on | auto)
            case "$cpu_vendor" in
                intel)
                    echo "    (keeping generic IOMMU support and selecting Intel IOMMU)"
                    enable_syms=(
                        IOMMU_SUPPORT
                        IOMMUFD
                        IOMMU_DMA
                        INTEL_IOMMU
                    )
                    disable_syms=(
                        AMD_IOMMU
                    )
                    ;;
                amd)
                    echo "    (keeping generic IOMMU support and selecting AMD IOMMU)"
                    enable_syms=(
                        IOMMU_SUPPORT
                        IOMMUFD
                        IOMMU_DMA
                        AMD_IOMMU
                    )
                    disable_syms=(
                        INTEL_IOMMU
                    )
                    ;;
                *)
                    echo "    (CPU vendor could not be determined; keeping only generic IOMMU support)"
                    enable_syms=(
                        IOMMU_SUPPORT
                        IOMMUFD
                        IOMMU_DMA
                    )
                    disable_syms=(
                        AMD_IOMMU
                        INTEL_IOMMU
                    )
                    ;;
            esac
            ;;
        off)
            echo "    (pruning generic and vendor-specific IOMMU support)"
            disable_syms=(
                AMD_IOMMU
                INTEL_IOMMU
                IOMMUFD
                IOMMUFD_DRIVER
                IOMMU_DMA
                IOMMU_SVA
                IOMMU_IOPF
                IOMMU_SUPPORT
            )
            ;;
    esac

    if ((${#enable_syms[@]} > 0)); then
        enable_if_present "${enable_syms[@]}"
    fi

    if ((${#disable_syms[@]} > 0)); then
        disable_if_present "${disable_syms[@]}"
    fi
}

configure_numa_support_profile() {
    local mode="$1"

    echo
    echo "==> Applying NUMA support profile: $mode"

    case "$mode" in
        on)
            echo "    (keeping CONFIG_NUMA enabled)"
            enable_if_present NUMA
            ;;
        off)
            echo "    (pruning CONFIG_NUMA)"
            disable_if_present NUMA
            ;;
    esac
}

get_config_numeric_value() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    if [[ -v _SYMBOL_VALUE_CACHE[$1] ]]; then
        printf '%s\n' "${_SYMBOL_VALUE_CACHE[$1]}"
    fi
}

clamp_nr_cpus_to_config_range() {
    local requested="$1"
    local adjusted="$requested"
    local range_begin range_end

    range_begin="$(get_config_numeric_value NR_CPUS_RANGE_BEGIN)"
    range_end="$(get_config_numeric_value NR_CPUS_RANGE_END)"

    if [[ "$range_begin" =~ ^[1-9][0-9]*$ ]] && (( adjusted < range_begin )); then
        adjusted="$range_begin"
    fi

    if [[ "$range_end" =~ ^[1-9][0-9]*$ ]] && (( adjusted > range_end )); then
        adjusted="$range_end"
    fi

    printf '%s\n' "$adjusted"
}

configure_nr_cpus_profile() {
    local requested="$1"
    local adjusted

    echo
    echo "==> Applying NR_CPUS profile: $requested"

    if ! have_symbol NR_CPUS; then
        echo "    (CONFIG_NR_CPUS is not present in this .config; skipping)"
        return
    fi

    adjusted="$(clamp_nr_cpus_to_config_range "$requested")"
    if [[ "$adjusted" != "$requested" ]]; then
        echo "    (requested $requested CPUs, clamped to $adjusted by the configured NR_CPUS range)"
    else
        echo "    (setting CONFIG_NR_CPUS=$adjusted)"
    fi

    set_val_config_symbol NR_CPUS "$adjusted"
}

configure_application_profiles() {
    local profile sym
    local qemu_cpu_vendor=""
    local -a enable_syms=()
    local -a available_syms=()

    echo
    echo "==> Enabling application profiles: $*"

    for profile in "$@"; do
        case "$profile" in
            desktop)
                # GNOME/Wayland, browser sandboxes, IDE file watchers and portals.
                # Scheduling policy remains in OPTIMIZATION_PROFILE=desktop.
                for sym in NET UNIX INET NAMESPACES USER_NS PID_NS IPC_NS UTS_NS NET_NS SECCOMP SECCOMP_FILTER CGROUPS MEMCG CGROUP_PIDS CGROUP_SCHED FAIR_GROUP_SCHED CFS_BANDWIDTH FUTEX EPOLL EVENTFD SIGNALFD TIMERFD INOTIFY_USER FANOTIFY UNIX98_PTYS INPUT INPUT_EVDEV INPUT_UINPUT HID HIDRAW USB_SUPPORT USB USB_HID DRM SYNC_FILE; do
                    append_unique_item "$sym" enable_syms
                done
                append_unique_item FUSE_FS available_syms
                ;;
            multimedia)
                # PipeWire/OBS/Resolve: ALSA, USB audio/cameras and userspace HID
                # controllers. GPU compute is opt-in via rocm, not vendor-implied.
                for sym in SOUND SND SND_PCM SND_TIMER SND_HRTIMER HIGH_RES_TIMERS USB_SUPPORT USB SND_USB MEDIA_SUPPORT MEDIA_CAMERA_SUPPORT MEDIA_USB_SUPPORT VIDEO_DEV INPUT HID HIDRAW USB_HID; do
                    append_unique_item "$sym" enable_syms
                done
                for sym in SND_USB_AUDIO USB_VIDEO_CLASS; do
                    append_unique_item "$sym" available_syms
                done
                ;;
            rocm)
                # KFD is part of amdgpu, so it will not appear separately in lsmod.
                # DEVICE_PRIVATE supplies HMM/SVM; Kconfig selects HMM_MIRROR.
                for sym in PCI DRM HSA_AMD DRM_AMDGPU_USERPTR MEMORY_HOTPLUG MEMORY_HOTREMOVE ZONE_DEVICE DEVICE_PRIVATE HSA_AMD_SVM; do
                    append_unique_item "$sym" enable_syms
                done
                append_unique_item DRM_AMDGPU available_syms
                ;;
            nebula | warp)
                # Userspace tunnels need TUN, not the in-kernel WireGuard driver.
                for sym in NET INET TUN; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            samba)
                for sym in CIFS CIFS_XATTR CIFS_UPCALL CIFS_DFS_UPCALL DNS_RESOLVER KEYS KEY_DH_OPERATIONS CRYPTO_MD4 CRYPTO_MD5 CRYPTO_HMAC CRYPTO_SHA256 CRYPTO_SHA512 CRYPTO_AES CRYPTO_CMAC CRYPTO_DES; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            firehol)
                # 6.17+ gates the iptables-legacy tables behind NETFILTER_XTABLES_LEGACY,
                # which PRUNE_LEGACY disables; FireHOL still needs them with that backend.
                for sym in NETFILTER NETFILTER_ADVANCED NETFILTER_XTABLES NETFILTER_XTABLES_LEGACY NF_CONNTRACK NF_NAT NF_TABLES IP_SET IP_NF_IPTABLES IP6_NF_IPTABLES IP_NF_IPTABLES_LEGACY IP6_NF_IPTABLES_LEGACY IP_NF_FILTER IP6_NF_FILTER IP_NF_MANGLE IP6_NF_MANGLE IP_NF_RAW IP6_NF_RAW IP_NF_NAT IP6_NF_NAT NFT_CT NFT_NAT NFT_MASQ NFT_REDIR NETFILTER_XT_MATCH_CONNTRACK NETFILTER_XT_MATCH_COMMENT NETFILTER_XT_MATCH_ADDRTYPE NETFILTER_XT_SET NETFILTER_XT_TARGET_MASQUERADE NETFILTER_XT_TARGET_REDIRECT NETFILTER_XT_TARGET_LOG; do
                    append_unique_item "$sym" enable_syms
                done
                # Targets and matches that `firehol debug` emits for a plain
                # version 5/6 configuration (interface/router with physdev,
                # dnat/snat/redirect, `tcpmss auto`, `tosfix`, mark/connmark,
                # limit/connlimit/hashlimit/recent, owner/mac/iprange/pkttype,
                # CT --helper and the FTP helper). Missing symbols are skipped.
                for sym in NETFILTER_XT_TARGET_CT NETFILTER_XT_TARGET_TCPMSS NETFILTER_XT_TARGET_DSCP NETFILTER_XT_TARGET_NETMAP NETFILTER_XT_TARGET_NFLOG NETFILTER_XT_MARK NETFILTER_XT_CONNMARK NETFILTER_XT_MATCH_MULTIPORT NETFILTER_XT_MATCH_LIMIT NETFILTER_XT_MATCH_STATE NETFILTER_XT_MATCH_OWNER NETFILTER_XT_MATCH_PHYSDEV NETFILTER_XT_MATCH_MAC NETFILTER_XT_MATCH_IPRANGE NETFILTER_XT_MATCH_RECENT NETFILTER_XT_MATCH_HASHLIMIT NETFILTER_XT_MATCH_CONNLIMIT NETFILTER_XT_MATCH_HELPER NETFILTER_XT_MATCH_PKTTYPE NF_CONNTRACK_FTP NF_NAT_FTP IP_NF_TARGET_NETMAP IP_NF_TARGET_REDIRECT IP_NF_TARGET_TTL IP6_NF_TARGET_HL; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            firewalld)
                for sym in NETFILTER NETFILTER_ADVANCED NETFILTER_XTABLES NF_CONNTRACK NF_NAT NF_TABLES NF_TABLES_INET NF_TABLES_IPV4 NF_TABLES_IPV6 NF_TABLES_ARP NF_TABLES_BRIDGE NF_CONNTRACK_BRIDGE BRIDGE_NETFILTER IP_SET NFT_CT NFT_NAT NFT_MASQ NFT_REDIR NFT_REJECT NFT_REJECT_INET NFT_FIB NFT_FIB_INET NFT_FIB_IPV4 NFT_FIB_IPV6 IP_NF_IPTABLES IP6_NF_IPTABLES; do
                    append_unique_item "$sym" enable_syms
                done
                # Since 6.17 these require explicitly opting into legacy tables.
                # The nftables backend does not need that stack. Preserve any
                # existing legacy configuration, and cover older kernels below.
                if ! have_symbol NETFILTER_XTABLES_LEGACY; then
                    append_unique_item IP_NF_NAT enable_syms
                    append_unique_item IP6_NF_NAT enable_syms
                fi
                ;;
            openvswitch)
                for sym in OPENVSWITCH NF_CONNTRACK NF_CONNTRACK_OVS NF_NAT_OVS NETFILTER VXLAN GENEVE NET_IPGRE_DEMUX NET_UDP_TUNNEL; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            ceph)
                # LIBCRC32C was removed in 6.15; newer trees select CRC32 from CEPH_LIB.
                for sym in CEPH_LIB CEPH_FS CRYPTO LIBCRC32C; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            nfs-client)
                # NFS_V4_1 was folded into NFS_V4 in 7.0; NFS_V4_2 still exists.
                for sym in NFS_FS NFS_V3 NFS_V4 NFS_V4_1 NFS_V4_2 SUNRPC SUNRPC_GSS LOCKD LOCKD_V4 GRACE_PERIOD DNS_RESOLVER; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            nfs-server)
                for sym in NFSD NFSD_V3_ACL NFSD_V4 NFSD_PNFS NFSD_BLOCKLAYOUT NFSD_SCSILAYOUT SUNRPC SUNRPC_GSS LOCKD LOCKD_V4 GRACE_PERIOD EXPORTFS FSNOTIFY; do
                    append_unique_item "$sym" enable_syms
                done
                # 6.9+ made the in-kernel NFSv4 client tracking opt-in and
                # deprecated; without it nfsd logs "Unable to initialize client
                # recovery tracking" unless the nfsdcld daemon runs before nfsd.
                if have_symbol NFSD_LEGACY_CLIENT_TRACKING && ! is_symbol_enabled_now NFSD_LEGACY_CLIENT_TRACKING; then
                    echo "    (NFSv4 client tracking: run nfsdcld from nfs-utils before nfsd, or pass --enable-symbols=NFSD_LEGACY_CLIENT_TRACKING)"
                fi
                ;;
            openvpn)
                for sym in TUN CRYPTO_USER_API CRYPTO_USER_API_AEAD CRYPTO_USER_API_SKCIPHER CRYPTO_USER_API_HASH CRYPTO_AES CRYPTO_GCM CRYPTO_SHA256; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            wireguard)
                # CRYPTO_CURVE25519 moved to the promptless CRYPTO_LIB_CURVE25519 in 6.18
                # (selected by WIREGUARD); the old name is kept for 6.12-6.17 trees.
                for sym in WIREGUARD NET_UDP_TUNNEL CRYPTO_CHACHA20POLY1305 CRYPTO_CURVE25519 CRYPTO_LIB_CHACHA20POLY1305; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            docker)
                for sym in NAMESPACES UTS_NS IPC_NS USER_NS PID_NS NET_NS CGROUPS CGROUP_BPF CGROUP_CPUACCT CGROUP_DEVICE CGROUP_FREEZER CGROUP_PIDS CGROUP_SCHED CFS_BANDWIDTH FAIR_GROUP_SCHED MEMCG BLK_CGROUP POSIX_MQUEUE VETH BRIDGE BRIDGE_NETFILTER OVERLAY_FS NF_CONNTRACK NF_NAT NF_TABLES NFT_CT NFT_NAT NFT_MASQ NETFILTER_XTABLES IP_NF_IPTABLES IP6_NF_IPTABLES IP_NF_NAT IP6_NF_NAT VXLAN; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            qemu)
                qemu_cpu_vendor="$(resolve_cpu_vendor_or_detect)"
                case "$qemu_cpu_vendor" in
                    amd | intel)
                        echo "    (qemu uses ${qemu_cpu_vendor@U} host virtualization support)"
                        ;;
                    unknown)
                        echo "    (qemu host CPU vendor could not be detected; enabling generic KVM support only)"
                        ;;
                esac

                # VSOCKETS is the parent of VHOST_VSOCK; HOST_TYPE=baremetal disables it
                # together with the guest transports.
                # VHOST/VHOST_IOTLB are hidden tristates selected by the drivers;
                # pinning their old =m conflicts with a newly built-in consumer.
                for sym in KVM KVM_X86 KVM_VFIO VFIO TUN VHOST_MENU VHOST_NET VSOCKETS VHOST_VSOCK; do
                    append_unique_item "$sym" enable_syms
                done

                case "$qemu_cpu_vendor" in
                    intel)
                        append_unique_item "KVM_INTEL" enable_syms
                        ;;
                    amd)
                        append_unique_item "KVM_AMD" enable_syms
                        ;;
                esac
                ;;
            atop)
                for sym in TASKSTATS TASK_DELAY_ACCT PSI SCHEDSTATS PROC_EVENTS; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            bmon)
                for sym in PACKET PACKET_DIAG NETLINK_DIAG INET_DIAG UNIX_DIAG; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            btop | htop)
                for sym in TASKSTATS TASK_DELAY_ACCT PSI; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            iotop-c)
                for sym in TASKSTATS TASK_DELAY_ACCT TASK_IO_ACCOUNTING PSI; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            cryptsetup)
                for sym in BLK_DEV_DM DM_CRYPT CRYPTO CRYPTO_USER_API CRYPTO_USER_API_AEAD CRYPTO_USER_API_SKCIPHER CRYPTO_USER_API_HASH CRYPTO_AES CRYPTO_XTS CRYPTO_SHA256; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
        esac
    done

    if ((${#enable_syms[@]} > 0)); then
        enable_parents_if_present "${enable_syms[@]}"
        if ((${#available_syms[@]} > 0)); then
            # Keep already modular hotplug/GPU drivers modular. Refresh after
            # making their parents available, then reapply child capabilities.
            enable_if_unset "${available_syms[@]}"
            refresh_config_visibility
        fi
        enable_if_present "${enable_syms[@]}"
    fi
}

validate_enum() {
    local setting="$1" choices="$2" raw allowed
    local -a values=()
    raw="${!setting}"
    raw="${raw@L}"
    IFS='|' read -r -a values <<<"$choices"
    for allowed in "${values[@]}"; do
        if [[ "$raw" == "$allowed" ]]; then
            printf -v "$setting" '%s' "$raw"
            return
        fi
    done
    die "Invalid $setting: $raw (use ${choices//|/, })"
}

validate_tunables() {
    local setting value sym raw effective_numa_balancing
    local -a symbols=()
    VALIDATION_MODE_EFFECTIVE="$(resolve_validation_mode)"
    PREEMPT_MODE_EFFECTIVE="$(resolve_preempt_mode)"
    TIMER_HZ_EFFECTIVE="$(resolve_timer_hz)"
    SCHED_CACHE_MODE_EFFECTIVE="$(resolve_auto_on_off_mode SCHED_CACHE_MODE)"
    MGLRU_MODE_EFFECTIVE="$(resolve_auto_on_off_mode MGLRU_MODE)"
    NUMA_BALANCING_MODE_EFFECTIVE="$(resolve_auto_on_off_mode NUMA_BALANCING_MODE)"
    NATIVE_CPU_EFFECTIVE="$(resolve_native_cpu_mode)"
    if [[ "$VALIDATION_MODE_EFFECTIVE" == strict ]]; then
        STRICT=true
    fi
    for setting in DISABLE_SYMBOLS MODULE_SYMBOLS ENABLE_SYMBOLS; do
        case "$setting" in
            DISABLE_SYMBOLS) value=n ;;
            MODULE_SYMBOLS) value=m ;;
            ENABLE_SYMBOLS) value=y ;;
        esac
        raw="${!setting}"
        [[ "$raw" == none || -z "$raw" ]] && continue
        IFS=',' read -r -a symbols <<<"$raw"
        for sym in "${symbols[@]}"; do
            normalize_config_symbol_name "$sym"
            sym="$REPLY"
            [[ "$sym" =~ ^[A-Za-z0-9_]+$ ]] || die "Invalid symbol in $setting: $sym"
            if [[ -v _EXPLICIT_SYMBOL_VALUES[$sym] && "${_EXPLICIT_SYMBOL_VALUES[$sym]}" != "$value" ]]; then
                die "Conflicting explicit values for CONFIG_$sym"
            fi
            _EXPLICIT_SYMBOL_VALUES["$sym"]="$value"
        done
    done
    OPTIMIZATION_PROFILE_EFFECTIVE="$(resolve_optimization_profile)"
    CPU_VENDOR_EFFECTIVE="$(resolve_cpu_vendor_filter)"
    VIDEO_SUPPORT_EFFECTIVE="$(resolve_video_support)"
    UEFI_SUPPORT_EFFECTIVE="$(resolve_uefi_support)"
    INITRD_SUPPORT_EFFECTIVE="$(resolve_initrd_support)"
    TPM_RESOLVED="$(resolve_tpm_support)"
    DMA_ENGINE_SUPPORT_EFFECTIVE="$(resolve_dma_engine_support)"
    IOMMU_SUPPORT_EFFECTIVE="$(resolve_iommu_support)"
    NUMA_SUPPORT_EFFECTIVE="$(resolve_numa_support)"
    NR_CPUS_EFFECTIVE="$(resolve_nr_cpus)"
    HOST_TYPE_EFFECTIVE="$(resolve_host_type)"
    APPLICATIONS_RESOLVED="$(resolve_application_profiles)"
    validate_enum PREEMPTION 'keep|none|voluntary|full|lazy|rt'
    validate_enum PREEMPT_DYNAMIC 'keep|on|off'
    validate_enum TICK_MODE 'keep|periodic|idle|full'
    validate_enum THP 'keep|off|always|madvise|never'
    validate_enum LRU_GEN 'keep|on|off'
    validate_enum ZSWAP 'keep|on|off'
    validate_enum ZSWAP_COMPRESSOR 'keep|lzo|lz4|lz4hc|zstd|deflate|842'
    validate_enum ZRAM 'keep|off|module|builtin'
    validate_enum ZRAM_COMPRESSOR 'keep|lzo-rle|lzo|lz4|lz4hc|zstd|deflate|842'
    validate_enum NUMA_BALANCING 'keep|on|off'
    validate_enum KMALLOC_PARTITION 'keep|off|random|typed'
    validate_enum TCP_CONGESTION 'keep|cubic|bbr|reno'
    validate_enum IO_URING 'keep|on|off'
    validate_enum UCLAMP 'keep|on|off'
    validate_enum AUTOGROUP 'keep|on|off'
    validate_enum MODULE_FORCE_LOAD 'keep|on|off'
    validate_enum MODULE_FORCE_UNLOAD 'keep|on|off'
    validate_enum NFS_UDP 'keep|on|off'
    validate_enum OBSOLETE_CRYPTO 'keep|on|off'
    validate_enum INITRAMFS_GENERATOR 'auto|none|genkernel|ugrd'
    validate_enum INITRAMFS_COMPRESSION 'auto|none|best|fastest|gzip|bzip2|lzma|xz|lzo|lz4|zstd'
    validate_enum INITRD_COMPRESSION 'keep|auto|none|gzip|bzip2|lzma|xz|lzo|lz4|zstd'
    if [[ "$ZSWAP" == off && "$ZSWAP_COMPRESSOR" != keep ]]; then
        die "ZSWAP=off conflicts with ZSWAP_COMPRESSOR=$ZSWAP_COMPRESSOR"
    fi
    if [[ "$ZRAM" == off && "$ZRAM_COMPRESSOR" != keep ]]; then
        die "ZRAM=off conflicts with ZRAM_COMPRESSOR=$ZRAM_COMPRESSOR"
    fi
    # Resolve cross-family precedence before checking conflicts or applying
    # legacy controls. An overridden control must not leave requests behind.
    PREEMPTION_EFFECTIVE="$PREEMPTION"
    if [[ "$PREEMPTION" == keep && "$PREEMPT_MODE_EFFECTIVE" != auto ]]; then
        PREEMPTION_EFFECTIVE="$PREEMPT_MODE_EFFECTIVE"
    fi
    effective_numa_balancing="$NUMA_BALANCING"
    if [[ "$effective_numa_balancing" == keep ]]; then
        effective_numa_balancing="$NUMA_BALANCING_MODE_EFFECTIVE"
    fi
    if [[ "$effective_numa_balancing" == on && "$NUMA_SUPPORT_EFFECTIVE" == off ]]; then
        die "NUMA balancing=on conflicts with NUMA_SUPPORT=off"
    fi
    if [[ "$PREEMPTION_EFFECTIVE" == rt ]]; then
        if [[ "$THP" != keep && "$THP" != off ]]; then
            die "PREEMPTION=rt conflicts with THP=$THP"
        fi
        if [[ "$effective_numa_balancing" == on ]]; then
            die "PREEMPTION=rt conflicts with NUMA balancing=on"
        fi
    fi
    SCHED_CACHE="${SCHED_CACHE@L}"
    case "$SCHED_CACHE" in
        none | on | off) ;;
        *) die "Invalid SCHED_CACHE: $SCHED_CACHE (use none, on, off)" ;;
    esac
    validate_enum KERNEL_COMPRESSION 'keep|gzip|bzip2|lzma|xz|lzo|lz4|zstd'
    FIRMWARE_COMPRESSION="${FIRMWARE_COMPRESSION@L}"
    case "$FIRMWARE_COMPRESSION" in
        keep | on | off) ;;
        *) die "Invalid FIRMWARE_COMPRESSION: $FIRMWARE_COMPRESSION (use keep, on, off)" ;;
    esac
    if [[ "$INITRD_COMPRESSION" != keep && "$INITRD_SUPPORT_EFFECTIVE" == off ]]; then
        die "INITRD_COMPRESSION conflicts with INITRD_SUPPORT=off"
    fi
}

capture_protected_values() {
    local sym
    _load_symbol_cache
    for sym in "${!_PROTECTED_CONFIG_SYMBOL_MAP[@]}"; do
        _PROTECTED_ORIGINAL_VALUES["$sym"]="${_SYMBOL_VALUE_CACHE[$sym]:-__absent__}"
    done
}

verify_config_result() {
    local sym actual expected issue
    local failures=0
    _load_symbol_cache
    for issue in "${_UNSUPPORTED_REQUESTS[@]}"; do
        echo "Unmet request: $issue" >&2
        failures=$((failures + 1))
    done
    for sym in "${!_REQUESTED_VALUES[@]}"; do
        expected="${_REQUESTED_VALUES[$sym]}"
        actual="${_SYMBOL_VALUE_CACHE[$sym]:-n}"
        if [[ "$expected" != "$actual" ]]; then
            echo "Unmet request: CONFIG_$sym requested=$expected final=$actual (check Kconfig dependencies/choices)" >&2
            failures=$((failures + 1))
        fi
    done
    for sym in "${!_PROTECTED_ORIGINAL_VALUES[@]}"; do
        expected="${_PROTECTED_ORIGINAL_VALUES[$sym]}"
        actual="${_SYMBOL_VALUE_CACHE[$sym]:-__absent__}"
        if [[ "$expected" != "$actual" ]]; then
            echo "Protected symbol changed: CONFIG_$sym before=$expected final=$actual" >&2
            failures=$((failures + 1))
        fi
    done
    if ((failures > 0)); then
        if is_enabled "$STRICT"; then
            die "Strict validation failed ($failures issues); original config was not modified"
        fi
        echo "Validation: $failures unmet requests/protected changes; use --strict to reject them." >&2
    else
        echo "==> Validation passed: ${#_REQUESTED_VALUES[@]} requested CONFIG values are effective"
    fi
}

if is_enabled "$AUDIT_KCONFIG"; then
    command -v python3 >/dev/null || die "Kconfig audit requires Python 3.11+"
    audit_tree="${KSRCDIR:-${positionals[0]:-$(detect_default_ksrcdir)}}"
    audit_args=(--kernel-srcdir "$audit_tree" --arch "${ARCH:-$(uname -m)}")
    if [[ -n "${CONFIG_FILE:-${positionals[1]:-}}" ]]; then
        audit_args+=(--config-file "${CONFIG_FILE:-${positionals[1]}}")
    fi
    python3 -B "$SCRIPT_DIR/lib/kconfig_audit.py" "${audit_args[@]}"
    exit 0
fi

prepare_paths
validate_tunables
load_protected_config_symbols
prepare_initramfs_check
if is_enabled "$CHECK"; then
    verify_initramfs_result
    echo "Check passed: inputs and kernel tree prerequisites are valid. No files were modified."
    exit 0
fi
load_defined_symbols
load_kconfig_metadata
capture_protected_values
prepare_transaction
load_baseline_module_symbols

if is_enabled "$ALL_OPTIMIZATIONS"; then
    echo
    echo "==> Applying compression optimization preset"
    optimize_compression
fi

if is_enabled "$PRUNE_SANITIZERS"; then
    echo
    echo "==> Disabling sanitizers"

    disable_if_present \
        KASAN \
        KASAN_GENERIC \
        KASAN_HW_TAGS \
        KASAN_SW_TAGS \
        KCOV \
        KCSAN \
        DEBUG_KMEMLEAK \
        MEMTEST \
        UBSAN \
        UBSAN_BOUNDS \
        UBSAN_LOCAL_BOUNDS \
        UBSAN_TRAP
fi

if is_enabled "$PRUNE_COVERAGE"; then
    echo
    echo "==> Disabling coverage and profiling"

    disable_discovered_and_fixed_symbols \
        discover_coverage_kconfig_symbols \
        GCOV_KERNEL \
        GCOV_PROFILE_ALL

    enable_if_present BRANCH_PROFILE_NONE
fi

if is_enabled "$PRUNE_FAULT_INJECTION"; then
    echo
    echo "==> Disabling fault injection"

    disable_discovered_and_fixed_symbols \
        discover_fault_injection_kconfig_symbols \
        FAILSLAB \
        FAIL_FUTEX \
        FAIL_IO_TIMEOUT \
        FAIL_MAKE_REQUEST \
        FAIL_PAGE_ALLOC \
        FAULT_INJECTION \
        FAULT_INJECTION_DEBUG_FS
fi

if is_enabled "$PRUNE_DANGEROUS"; then
    echo
    echo "==> Disabling dangerous/unsafe options and reviewed non-production features"

    disable_discovered_and_fixed_symbols \
        discover_dangerous_kconfig_symbols \
        ADFS_FS_RW \
        CXL_MEM_RAW_COMMANDS \
        DRM_FBDEV_LEAK_PHYS_SMEM \
        FB_VIA_DIRECT_PROCFS \
        MEMSTICK_UNSAFE_RESUME \
        MICROCODE_LATE_LOADING \
        MODULE_FORCE_LOAD \
        MODULE_FORCE_UNLOAD \
        MMC_TEST \
        IOMMUFD_TEST \
        I2C_AT91_SLAVE_EXPERIMENTAL \
        MTD_TESTS \
        NFSD_FLEXFILELAYOUT \
        SPI_INTEL_PLATFORM \
        UFS_FS_WRITE \
        USB4_DEBUGFS_MARGINING \
        USB4_DEBUGFS_WRITE
fi

if is_enabled "$PRUNE_SELFTEST"; then
    echo
    echo "==> Disabling selftest symbols"

    disable_discovered_and_fixed_symbols \
        discover_selftest_kconfig_symbols \
        CORESIGHT \
        CRYPTO_BENCHMARK \
        HVC_UDBG \
        IOMMUFD_TEST \
        MMC_TEST \
        NFSD_FLEXFILELAYOUT \
        KDB \
        KGDB \
        KGDB_KDB \
        KGDB_TESTS \
        KUNIT \
        KUNIT_ALL_TESTS \
        KUNIT_TEST \
        IO_URING_MOCK_FILE \
        LKDTM \
        RUNTIME_TESTING_MENU \
        TEST_KSTRTOX \
        TEST_LIST_SORT
fi

if is_enabled "$PRUNE_OBSERVABILITY"; then
    echo
    echo "==> Aggressive mode: disabling tracing/observability"
    echo "    (this can affect perf/ftrace/bpftrace and similar tools)"

    disable_if_present \
        DYNAMIC_FTRACE \
        BLK_DEV_IO_TRACE \
        DEBUG_FS \
        FTRACE \
        FUNCTION_GRAPH_TRACER \
        FUNCTION_TRACER \
        HIST_TRIGGERS \
        IRQSOFF_TRACER \
        KPROBE_EVENTS \
        MAGIC_SYSRQ \
        PREEMPT_TRACER \
        PROFILE_ALL_BRANCHES \
        SCHED_TRACER \
        STACK_TRACER \
        TRACEPOINTS \
        TRACING \
        UPROBE_EVENTS

    enable_if_present BRANCH_PROFILE_NONE
fi

if is_enabled "$PRUNE_LEGACY"; then
    echo
    echo "==> Disabling legacy/obsolete interfaces"
    echo "    (optional block; review compatibility before using it in production)"

    # Remove https://git.kernel.org/pub/scm/linux/kernel/git/netdev/net-next.git/commit/?id=d6e0f04bf22d9b25b530c5e04f82664eac942719 UPP-LITE

    # Version notes (kept for 6.12 LTS; missing symbols are skipped by have_symbol):
    #   NF_CT_PROTO_UDPLITE -> removed in 7.1+
    #   USELIB              -> removed in 6.15
    disable_discovered_and_fixed_symbols \
        discover_legacy_kconfig_symbols \
        BLK_DEV_FD \
        COMPAT_BRK \
        FAST_SYSCALL_XTENSA \
        FAST_SYSCALL_SPILL_REGISTERS \
        GPIO_SYSFS \
        GPIO_CDEV_V1 \
        LEGACY_PTYS \
        NF_CT_PROTO_UDPLITE \
        NO_HZ \
        PARPORT \
        PROVE_RCU \
        S390_HYPFS_FS \
        SGETMASK_SYSCALL \
        SND_HDA_CTL_DEV_ID \
        SYSFS_DEPRECATED \
        SYSFS_DEPRECATED_V2 \
        SYSFS_SYSCALL \
        UID16 \
        USELIB

    prune_deprecated_aliases
    if is_symbol_enabled_now SGETMASK_SYSCALL && ! is_symbol_enabled_now EXPERT; then
        echo "Retaining CONFIG_SGETMASK_SYSCALL: its prompt requires CONFIG_EXPERT=y; EXPERT was not changed"
    fi
fi

# extra: debug info choice — only when actively pruning debug/coverage symbols
if is_enabled "$PRUNE_DEBUG_TRACE" || is_enabled "$PRUNE_COVERAGE"; then
    if have_symbol DEBUG_INFO_NONE; then
        echo "Selecting: CONFIG_DEBUG_INFO_NONE"
        enable_config_symbol DEBUG_INFO_NONE
    fi
fi
# optional: BPF/observability
if is_enabled "$PRUNE_BPF"; then
    echo
    echo "==> Disabling BPF features"
    disable_if_present DEBUG_INFO_BTF KPROBES
fi

# optional: 32-bit compat
if is_enabled "$PRUNE_COMPAT32"; then
    echo
    echo "==> Disabling COMPATIBILITY 32BIT support"
    disable_if_present IA32_EMULATION
fi

if is_enabled "$PRUNE_DEBUG_TRACE"; then
    echo
    echo "==> Disabling debug/trace symbols"

    # Fixed entries cover symbols whose Kconfig prompt does not mention debugging
    # or that live in Kconfig.* files outside the menu context (KFENCE, stats).
    # SCHED_DEBUG exists up to 6.14 only (always-on since 6.15).
    disable_discovered_and_fixed_symbols \
        discover_debug_trace_kconfig_symbols \
        BOOTPARAM_HARDLOCKUP_PANIC \
        BOOTPARAM_HUNG_TASK_PANIC \
        BOOTPARAM_SOFTLOCKUP_PANIC \
        CONTEXT_TRACKING_USER_FORCE \
        DAMON_DEBUG_SANITY \
        DEBUG_ATOMIC_SLEEP \
        DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
        DEBUG_INFO_REDUCED \
        DEBUG_IRQFLAGS \
        DEBUG_KERNEL \
        DEBUG_LIST \
        DEBUG_LOCK_ALLOC \
        DEBUG_MEMORY_INIT \
        DEBUG_MUTEXES \
        DEBUG_NOTIFIERS \
        DEBUG_OBJECTS \
        DEBUG_OBJECTS_FREE \
        DEBUG_OBJECTS_RCU_HEAD \
        DEBUG_OBJECTS_SELFTEST \
        DEBUG_OBJECTS_TIMERS \
        DEBUG_OBJECTS_WORK \
        DEBUG_PAGEALLOC \
        DEBUG_PER_CPU_MAPS \
        DEBUG_PLIST \
        DEBUG_PREEMPT \
        DEBUG_RT_MUTEXES \
        DEBUG_RWSEMS \
        DEBUG_SG \
        DEBUG_SPINLOCK \
        DEBUG_VIRTUAL \
        DEBUG_VM \
        DEBUG_VM_PGFLAGS \
        DEBUG_WW_MUTEX_SLOWPATH \
        DETECT_HUNG_TASK \
        DYNAMIC_DEBUG \
        GDB_SCRIPTS \
        HARDLOCKUP_DETECTOR \
        KFENCE \
        LATENCYTOP \
        LOCKDEP \
        LOCKUP_DETECTOR \
        LOCK_STAT \
        PAGE_EXTENSION \
        PAGE_OWNER \
        PAGE_POISONING \
        PROVE_LOCKING \
        RSEQ_STATS \
        SCHEDSTATS \
        SCHED_DEBUG \
        SLUB_DEBUG \
        SLUB_DEBUG_ON \
        SLUB_STATS \
        ZRAM_MEMORY_TRACKING \
        SOFTLOCKUP_DETECTOR \
        ZSMALLOC_STAT
fi

if is_enabled "$PRUNE_RUNTIME_VERIFICATION" || is_enabled "$PRUNE_OBSERVABILITY" || is_enabled "$PRUNE_DEBUG_TRACE"; then
    echo
    echo "==> Disabling Runtime Verification and its dependent monitors"
    disable_if_present RV
fi

if is_enabled "$PRUNE_HARDENING"; then
    echo
    echo "==> Disabling hardening/mitigation symbols"
    echo "    (this reduces kernel security hardening)"

    # Fixed entries: allocator/page randomization and page-table checking cost
    # runtime but their prompts do not say "hardening", so pattern discovery misses them.
    # RANDOM_KMALLOC_CACHES is the 6.12-7.0 name of KMALLOC_PARTITION_* (7.2+).
    disable_discovered_and_fixed_symbols \
        discover_hardening_kconfig_symbols \
        KMALLOC_PARTITION_CACHES \
        KMALLOC_PARTITION_RANDOM \
        KMALLOC_PARTITION_TYPED \
        PAGE_TABLE_CHECK \
        PAGE_TABLE_CHECK_ENFORCED \
        RANDOMIZE_KSTACK_OFFSET_DEFAULT \
        RANDOM_KMALLOC_CACHES \
        SHUFFLE_PAGE_ALLOCATOR \
        SLAB_FREELIST_HARDENED \
        SLAB_FREELIST_RANDOM
fi

invalidate_symbol_cache

configure_optimization_profile "$OPTIMIZATION_PROFILE_EFFECTIVE"

if [[ "$PREEMPTION" == keep && "$PREEMPT_MODE_EFFECTIVE" != auto ]]; then
    configure_explicit_preempt_mode "$PREEMPT_MODE_EFFECTIVE"
fi

if [[ "$TIMER_HZ_EFFECTIVE" != "auto" ]]; then
    configure_explicit_timer_hz "$TIMER_HZ_EFFECTIVE"
fi

if [[ "$SCHED_CACHE" == none ]]; then
    configure_sched_cache_mode "$SCHED_CACHE_MODE_EFFECTIVE" "$OPTIMIZATION_PROFILE_EFFECTIVE"
fi
if [[ "$LRU_GEN" == keep ]]; then
    configure_mglru_mode "$MGLRU_MODE_EFFECTIVE" "$OPTIMIZATION_PROFILE_EFFECTIVE"
fi
if [[ "$NUMA_BALANCING" == keep ]]; then
    configure_explicit_numa_balancing_mode "$NUMA_BALANCING_MODE_EFFECTIVE"
fi

CPU_VENDOR_EFFECTIVE="$(resolve_cpu_vendor_filter)"
if [[ "$CPU_VENDOR_EFFECTIVE" != "none" ]]; then
    echo

    if ! is_x86_config; then
        echo "==> Skipping CPU_VENDOR_FILTER: .config is not x86"
    elif [[ "$CPU_VENDOR_EFFECTIVE" == "unknown" ]]; then
        echo "==> Could not detect local CPU vendor; use CPU_VENDOR_FILTER=amd or intel"
    else
        if [[ "$CPU_VENDOR_EFFECTIVE" == "amd" ]]; then
            CPU_VENDOR_TO_DISABLE="intel"
        else
            CPU_VENDOR_TO_DISABLE="amd"
        fi

        echo "==> Adjusting x86 options for ${CPU_VENDOR_EFFECTIVE@U} CPU"
        echo "    (disabling ${CPU_VENDOR_TO_DISABLE@U}-specific symbols)"

        # CPU_SUP_* only get a prompt under PROCESSOR_SELECT, which itself
        # needs EXPERT. Without that, olddefconfig forces them back to y and
        # SCHED_MC_PRIO re-selects the other vendor's pstate driver.
        ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
        can_prune_cpu_sup=false
        if [[ "${_SYMBOL_VALUE_CACHE[EXPERT]:-n}" == "y" ]] && have_symbol PROCESSOR_SELECT; then
            enable_if_present PROCESSOR_SELECT
            can_prune_cpu_sup=true
        else
            echo "    (CONFIG_EXPERT is off: CPU_SUP_* vendor support and the ${CPU_VENDOR_TO_DISABLE@U} pstate driver stay as-is)"
        fi

        mapfile -t cpu_vendor_syms < <(discover_vendor_kconfig_symbols "$CPU_VENDOR_TO_DISABLE")
        if ! is_enabled "$can_prune_cpu_sup"; then
            cpu_vendor_syms_filtered=()
            for cpu_vendor_sym in "${cpu_vendor_syms[@]}"; do
                case "$cpu_vendor_sym" in
                    CPU_SUP_* | X86_INTEL_PSTATE | X86_AMD_PSTATE) ;;
                    *) cpu_vendor_syms_filtered+=("$cpu_vendor_sym") ;;
                esac
            done
            cpu_vendor_syms=("${cpu_vendor_syms_filtered[@]}")
        fi

        if ((${#cpu_vendor_syms[@]} > 0)); then
            disable_if_present "${cpu_vendor_syms[@]}"
        fi

        # keep the vendor's own cpufreq driver (amd-pstate / intel_pstate)
        if [[ "$CPU_VENDOR_EFFECTIVE" == "amd" ]]; then
            enable_if_present X86_AMD_PSTATE
        else
            enable_if_present X86_INTEL_PSTATE
        fi
    fi
fi

if [[ "$NATIVE_CPU_EFFECTIVE" != "none" ]]; then
    configure_native_cpu_profile "$NATIVE_CPU_EFFECTIVE"
fi

VIDEO_SUPPORT_EFFECTIVE="$(resolve_video_support)"
if [[ "$VIDEO_SUPPORT_EFFECTIVE" != "none" ]]; then
    if [[ "$VIDEO_SUPPORT_EFFECTIVE" == "unknown" ]]; then
        echo
        echo "==> Could not detect local video stack; use VIDEO_SUPPORT=amd, intel, nvidia, or nouveau"
    elif [[ "$VIDEO_SUPPORT_EFFECTIVE" == "multiple" ]]; then
        echo
        echo "==> Multiple local video stacks detected; use VIDEO_SUPPORT=amd, intel, nvidia, or nouveau"
    else
        configure_video_support_profile "$VIDEO_SUPPORT_EFFECTIVE"
    fi
fi

configure_xfs_feature_support

if [[ "$UEFI_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_uefi_support_profile "$UEFI_SUPPORT_EFFECTIVE"
fi

if [[ "$INITRD_SUPPORT_EFFECTIVE" != "none" ]]; then
    if [[ "$INITRD_SUPPORT_EFFECTIVE" == "unknown" ]]; then
        echo
        echo "==> Could not detect current initrd usage; use INITRD_SUPPORT=on or off"
    else
        configure_initrd_support_profile "$INITRD_SUPPORT_EFFECTIVE"
    fi
fi

if [[ "$TPM_RESOLVED" != "none" ]]; then
    TPM_MODE="$TPM_RESOLVED"

    if [[ "$TPM_MODE" != "on" && "$TPM_MODE" != "off" ]]; then
        # auto-detected: TPM_RESOLVED contains version strings (1.2, 2.0, unknown)
        # reuse them directly instead of calling detect_host_tpm_versions again
        TPM_HOST_VERSIONS="$TPM_RESOLVED"
        TPM_MODE="on"
    else
        # explicit on/off: detect versions for informational logging
        TPM_HOST_VERSIONS="$(detect_host_tpm_versions)"
    fi

    if [[ "$TPM_HOST_VERSIONS" != "none" ]]; then
        mapfile -t TPM_EFFECTIVE <<<"$TPM_HOST_VERSIONS"
        configure_tpm_support_profile "$TPM_MODE" "${TPM_EFFECTIVE[@]}"
    else
        configure_tpm_support_profile "$TPM_MODE"
    fi
fi

if [[ "$DMA_ENGINE_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_dma_engine_support_profile "$DMA_ENGINE_SUPPORT_EFFECTIVE"
fi

if [[ "$IOMMU_SUPPORT_EFFECTIVE" != "none" ]]; then
    if ! is_x86_config; then
        echo
        echo "==> Skipping IOMMU_SUPPORT: .config is not x86"
    else
        IOMMU_CPU_VENDOR="$(resolve_cpu_vendor_or_detect)"
        configure_iommu_support_profile "$IOMMU_SUPPORT_EFFECTIVE" "$IOMMU_CPU_VENDOR"
    fi
fi

if [[ "$NUMA_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_numa_support_profile "$NUMA_SUPPORT_EFFECTIVE"
fi

if [[ "$NR_CPUS_EFFECTIVE" != "none" ]]; then
    if [[ "$NR_CPUS_EFFECTIVE" == "unknown" ]]; then
        echo
        echo "==> Could not detect host CPU count; use NR_CPUS=<number>"
    else
        configure_nr_cpus_profile "$NR_CPUS_EFFECTIVE"
    fi
fi

if [[ "$HOST_TYPE_EFFECTIVE" != "none" ]]; then
    configure_host_type_profile "$HOST_TYPE_EFFECTIVE"
fi

# Uncommon or legacy network protocols for a general-purpose server
if is_enabled "$PRUNE_UNUSED_NET"; then
    echo
    echo "==> Disabling uncommon/legacy network protocols"
    # Version notes: IP_DCCP removed in 6.16; ATALK, CAIF removed in 7.1+.
    disable_if_present \
        6LOWPAN \
        AF_RXRPC \
        ATALK \
        ATM \
        BATMAN_ADV \
        CAIF \
        IEEE802154 \
        IP_DCCP \
        L2TP \
        LAPB \
        MAC802154 \
        MPLS \
        PHONET \
        RDS \
        TIPC \
        X25
fi

# Old / obsolete hardware
if is_enabled "$PRUNE_OLD_HW"; then
    echo
    echo "==> Disabling legacy or uncommon hardware"
    disable_if_present \
        BLK_DEV_FD \
        FIREWIRE \
        GAMEPORT \
        GAMEPORT_NS558 \
        PARPORT \
        PCCARD \
        PCMCIA \
        PNPBIOS \
        PPDEV \
        SND_FIREWIRE
fi

if is_enabled "$PRUNE_X86_OLD_PLATFORMS"; then
    echo
    echo "==> Disabling special/old x86 platforms"
    # X86_RDC321X removed in 7.1+.
    disable_if_present \
        X86_EXTENDED_PLATFORM \
        X86_GOLDFISH \
        X86_INTEL_CE \
        X86_INTEL_MID \
        X86_INTEL_QUARK \
        X86_NUMACHIP \
        X86_RDC321X \
        X86_UV \
        X86_VSMP
fi

if is_enabled "$PRUNE_LEGACY_ATA"; then
    echo
    echo "==> Disabling legacy ATA/PATA support"
    disable_if_present ATA_SFF
fi

# Insecure or legacy protocols/compat
if is_enabled "$PRUNE_INSECURE"; then
    echo
    echo "==> Disabling legacy or less secure protocols/compat"
    disable_if_present \
        CIFS_ALLOW_INSECURE_LEGACY \
        NFS_V2 \
        NFSD_V2
fi

# Radio / proximity / IoT features usually unnecessary on servers
if is_enabled "$PRUNE_RADIOS"; then
    echo
    echo "==> Disabling unused radio and proximity protocols"
    # HAMRADIO removed in 7.1+.
    disable_if_present \
        NFC \
        IEEE802154 \
        6LOWPAN \
        HAMRADIO
fi

# FireWire / USB4-Thunderbolt / gadget debug
if is_enabled "$PRUNE_DMA_ATTACK_SURFACE"; then
    echo
    echo "==> Disabling buses and features with extra physical attack surface"
    disable_if_present \
        FIREWIRE \
        FIREWIRE_OHCI \
        FIREWIRE_SBP2 \
        FIREWIRE_NET \
        USB4_DEBUGFS_WRITE \
        USB4_DEBUGFS_MARGINING \
        USB4_DMA_TEST \
        USB_GADGETFS
fi

invalidate_symbol_cache

if [[ "$APPLICATIONS_RESOLVED" != "none" ]]; then
    mapfile -t APPLICATIONS_EFFECTIVE <<<"$APPLICATIONS_RESOLVED"
    configure_application_profiles "${APPLICATIONS_EFFECTIVE[@]}"
fi

configure_explicit_controls
configure_extended_controls
configure_symbol_overrides

if is_enabled "$PRUNE_UNUSED_MODULES"; then
    probe_and_prune_unused_module_symbols
fi

echo
echo "==> Running olddefconfig to normalize dependencies"
echo "    (note: Kconfig 'select' statements may re-enable symbols that were disabled above)"
make KCONFIG_CONFIG="$CONFIG_FILE" olddefconfig >/dev/null
verify_config_result
verify_initramfs_result

echo "Scheduler capabilities for the next build: UCLAMP_TASK=${_SYMBOL_VALUE_CACHE[UCLAMP_TASK]:-n}, SCHED_AUTOGROUP=${_SYMBOL_VALUE_CACHE[SCHED_AUTOGROUP]:-n}"
echo "These capabilities do not establish a measured performance improvement."

echo
if is_enabled "$DRY_RUN"; then
    echo "==> Dry-run changes for $ORIGINAL_CONFIG_FILE"
    show_config_changes "$ORIGINAL_CONFIG_FILE" "$CONFIG_FILE"
    echo
    echo "Dry-run complete. Original config was not modified; active module probes were skipped."
else
    commit_transaction
fi
