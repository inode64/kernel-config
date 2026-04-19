#!/usr/bin/env bash
set -Eeuo pipefail

if ((BASH_VERSINFO[0] < 5 || (BASH_VERSINFO[0] == 5 && BASH_VERSINFO[1] < 2))); then
    echo "This script requires bash 5.2 or later (found ${BASH_VERSION})" >&2
    exit 1
fi

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
#   OPTIMIZATION_PROFILE=none -> none, server, desktop, realtime; tune scheduler/tick defaults
#   PRUNE_OBSERVABILITY       -> disable perf/bpf/ftrace/debugfs and related observability features
#   PRUNE_LEGACY              -> do not touch legacy/compat options by default
#   PRUNE_DEBUG_TRACE         -> disable debug/trace symbols
#   PRUNE_HARDENING           -> disable hardening/mitigation symbols
#   PRUNE_SELFTEST            -> disable selftest symbols
#   PRUNE_SANITIZERS          -> disable sanitizer-related symbols
#   PRUNE_COVERAGE            -> disable coverage/profiling symbols
#   PRUNE_FAULT_INJECTION     -> disable fault-injection/test failure symbols
#   PRUNE_DANGEROUS           -> disable symbols explicitly marked DANGEROUS in Kconfig
#   PRUNE_UNUSED_MODULES      -> probe module configs not currently loaded and disable direct module symbols that can be tested safely
#   CPU_VENDOR_FILTER=none    -> none, auto, amd, intel; disable x86 options for the other vendor
#   VIDEO_SUPPORT=none        -> none, auto, amd, intel, nvidia, nouveau; keep only the selected GPU stack
#   UEFI_SUPPORT=none         -> none, auto, on, off; keep or prune common EFI/UEFI kernel support
#   INITRD_SUPPORT=none       -> none, auto, on, off; keep or prune initramfs/initrd boot support
#   TPM_SUPPORT=none          -> none, auto, on, off; keep or prune TPM support and detect TPM 1.2/2.0 on the host
#   DMA_ENGINE_SUPPORT=none   -> none, auto, on, off; keep or prune DMA Engine support based on currently exposed dmaengine devices
#   IOMMU_SUPPORT=none        -> none, auto, on, off; keep or prune IOMMU support and select AMD/Intel IOMMU by CPU vendor
#   NUMA_SUPPORT=none         -> none, auto, on, off; keep or prune NUMA support based on currently exposed NUMA nodes
#   NR_CPUS=none              -> none, auto, or an integer; adjust CONFIG_NR_CPUS to the detected or requested CPU count
#   PROTECTED_CONFIG_SYMBOLS  -> comma-separated config symbols the script must not alter; defaults to CONFIG_ARCH_PKEY_BITS
#   APPLICATIONS=none         -> comma-separated app profiles: samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, qemu, atop, bmon, btop, htop, iotop-c, cryptsetup
#   HOST_TYPE=none            -> none, baremetal, qemu, vmware, hyperv, virtualbox; tune guest-specific options
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
  --all-optimizations
  --optimization-profile PROFILE
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
  The all-optimizations preset does not enable --prune-hardening.
  --optimization-profile accepts: none, server, desktop, realtime.
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
    OPTIMIZATION_PROFILE=server
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
        dry-run | all-optimizations | prune-observability | prune-legacy | prune-debug-trace | prune-hardening | prune-selftest | prune-sanitizers | prune-coverage | prune-fault-injection | prune-dangerous | prune-unused-modules | prune-bpf | prune-compat32 | prune-unused-net | prune-old-hw | prune-x86-old-platforms | prune-legacy-ata | prune-insecure | prune-radios | prune-dma-attack-surface)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_boolean_tunable() {
    case "$1" in
        DRY_RUN | PRUNE_OBSERVABILITY | PRUNE_LEGACY | PRUNE_DEBUG_TRACE | PRUNE_HARDENING | PRUNE_SELFTEST | PRUNE_SANITIZERS | PRUNE_COVERAGE | PRUNE_FAULT_INJECTION | PRUNE_DANGEROUS | PRUNE_UNUSED_MODULES | PRUNE_BPF | PRUNE_COMPAT32 | PRUNE_UNUSED_NET | PRUNE_OLD_HW | PRUNE_X86_OLD_PLATFORMS | PRUNE_LEGACY_ATA | PRUNE_INSECURE | PRUNE_RADIOS | PRUNE_DMA_ATTACK_SURFACE)
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
        OPTIMIZATION_PROFILE | CPU_VENDOR_FILTER | VIDEO_SUPPORT | UEFI_SUPPORT | INITRD_SUPPORT | TPM_SUPPORT | DMA_ENGINE_SUPPORT | IOMMU_SUPPORT | NUMA_SUPPORT | NR_CPUS | PROTECTED_CONFIG_SYMBOLS | APPLICATIONS | HOST_TYPE)
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

init_tunable DRY_RUN false
init_tunable OPTIMIZATION_PROFILE none
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

if [[ -z "$KSRCDIR" ]]; then
    KSRCDIR="${positionals[0]:-$(detect_default_ksrcdir)}"
fi

if [[ -z "$CONFIG_FILE" ]]; then
    CONFIG_FILE="${positionals[1]:-$KSRCDIR/.config}"
fi

cd "$KSRCDIR"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "Does not exist: $CONFIG_FILE" >&2
    exit 1
fi

ORIGINAL_CONFIG_FILE="$(realpath "$CONFIG_FILE")"

if [[ ! -x scripts/config ]]; then
    echo "scripts/config is missing or not executable; trying to generate it..."
    make -s scripts >/dev/null
fi

if [[ ! -x scripts/config ]]; then
    echo "Could not prepare scripts/config" >&2
    exit 1
fi

WORK_CONFIG_FILE="$ORIGINAL_CONFIG_FILE"
BACKUP=""
DRY_RUN_TEMP=""

cleanup() {
    if [[ -n "$DRY_RUN_TEMP" && -f "$DRY_RUN_TEMP" ]]; then
        rm -f "$DRY_RUN_TEMP"
    fi
}

trap cleanup EXIT

show_config_changes() {
    local before_file="$1"
    local after_file="$2"
    local changes

    changes="$(
        awk '
            function capture_config_line(line, source, sym, value) {
                if (line ~ /^CONFIG_[A-Z0-9_]+=.*$/) {
                    sym = line
                    sub(/^CONFIG_/, "", sym)
                    value = sym
                    sub(/^[^=]*=/, "", value)
                    sub(/=.*/, "", sym)
                } else if (line ~ /^# CONFIG_[A-Z0-9_]+ is not set$/) {
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

if is_enabled "$DRY_RUN"; then
    DRY_RUN_TEMP="$(mktemp "${TMPDIR:-/tmp}/kernel-config.dry-run.XXXXXX")"
    cp -a "$ORIGINAL_CONFIG_FILE" "$DRY_RUN_TEMP"
    WORK_CONFIG_FILE="$DRY_RUN_TEMP"
    echo "Dry-run: using temporary config copy $WORK_CONFIG_FILE"
else
    printf -v _backup_ts '%(%Y%m%d-%H%M%S)T' -1
    BACKUP="${ORIGINAL_CONFIG_FILE}.bak.${_backup_ts}"
    cp -a "$ORIGINAL_CONFIG_FILE" "$BACKUP"
    echo "Backup: $BACKUP"
fi

CONFIG_FILE="$WORK_CONFIG_FILE"

cfg() {
    scripts/config --file "$CONFIG_FILE" "$@"
}

declare -A _SYMBOL_VALUE_CACHE=()
declare -i _SYMBOL_CACHE_LOADED=0

_load_symbol_cache() {
    _SYMBOL_VALUE_CACHE=()
    local sym value
    while IFS=$'\t' read -r sym value; do
        _SYMBOL_VALUE_CACHE["$sym"]="$value"
    done < <(awk '
        /^CONFIG_[A-Z0-9_]+=/ {
            line = $0
            sub(/^CONFIG_/, "", line)
            idx = index(line, "=")
            print substr(line, 1, idx - 1) "\t" substr(line, idx + 1)
        }
        /^# CONFIG_[A-Z0-9_]+ is not set$/ {
            sym = $0
            sub(/^# CONFIG_/, "", sym)
            sub(/ is not set$/, "", sym)
            print sym "\tn"
        }
    ' "$CONFIG_FILE")
    _SYMBOL_CACHE_LOADED=1
}

invalidate_symbol_cache() {
    _SYMBOL_CACHE_LOADED=0
}

have_symbol() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    [[ -v _SYMBOL_VALUE_CACHE[$1] ]]
}

symbol_value() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    if [[ -v _SYMBOL_VALUE_CACHE[$1] ]]; then
        printf '%s\n' "${_SYMBOL_VALUE_CACHE[$1]}"
    fi
}

find_kconfig_files() {
    find -L "$KSRCDIR" -type f \( -name 'Kconfig' -o -name 'Kconfig.*' \) -print0 2>/dev/null
}

find_kbuild_files() {
    find -L "$KSRCDIR" -type f \( -name 'Makefile' -o -name 'Kbuild' \) -print0 2>/dev/null
}

declare -A MODULE_SYMBOL_TO_MODULES=()
declare -i MODULE_SYMBOL_MAP_READY=0

capture_loaded_modules() {
    lsmod | awk 'NR > 1 { print $1 }'
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
        find_kbuild_files \
            | xargs -0 -r awk '
                {
                    line = $0
                    sub(/[[:space:]]*#.*/, "", line)
                }

                line ~ /^[[:space:]]*obj-\$\(CONFIG_[A-Z0-9_]+\)[[:space:]]*[-+?:]?=[[:space:]]*/ {
                    sym = line
                    sub(/^[[:space:]]*obj-\$\(CONFIG_/, "", sym)
                    sub(/\).*/, "", sym)

                    rest = line
                    sub(/^[[:space:]]*obj-\$\(CONFIG_[A-Z0-9_]+\)[[:space:]]*[-+?:]?=[[:space:]]*/, "", rest)

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

        mapfile -t current_modules < <(capture_loaded_modules)
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

    mapfile -t current_modules < <(capture_loaded_modules)
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
        return 0
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

    if ((EUID != 0)); then
        echo "    (requires root to load/unload modules safely; skipping)"
        return
    fi

    if ! command -v lsmod >/dev/null 2>&1 || ! command -v modprobe >/dev/null 2>&1 || ! command -v modinfo >/dev/null 2>&1; then
        echo "    (lsmod/modprobe/modinfo are required; skipping)"
        return
    fi

    running_kernel_release="$(running_kernel_release)"
    target_kernel_release="$(make -s kernelrelease 2>/dev/null || true)"
    if [[ -z "$target_kernel_release" ]]; then
        echo "    (could not resolve target kernelrelease; skipping)"
        return
    fi

    if [[ "$target_kernel_release" != "$running_kernel_release" ]]; then
        echo "    (target kernelrelease $target_kernel_release does not match running kernel $running_kernel_release; skipping)"
        return
    fi

    build_module_symbol_candidate_map
    mapfile -t initially_loaded_modules < <(capture_loaded_modules)
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

        set +e  # restored automatically via local -
        probe_unloaded_module_candidate "$module_name" initially_loaded_modules
        probe_status="$?"

        case "$probe_status" in
            0)
                echo "    (disabling CONFIG_${symbol})"
                disable_config_symbol "$symbol"
                ;;
            1)
                echo "    (keeping CONFIG_${symbol}; module ${module_name} is currently unused but loadable)"
                ;;
            *)
                echo "    (module probe could not restore the original module set; stopping further module probes)" >&2
                break
                ;;
        esac
    done

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

            /\\[[:space:]]*$/ {
                in_continuation = 1
                next
            }

            in_continuation {
                in_continuation = /\\[[:space:]]*$/
                next
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

            /^[[:space:]]*menu[[:space:]]*"[^"]+"/ {
                menu_depth++
                menu_matches[menu_depth] = ($0 ~ pattern)
                next
            }

            /^[[:space:]]*endmenu([[:space:]]|$)/ {
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

                if (if_depth > 0) {
                    delete if_matches[if_depth]
                    if_depth--
                }
                next
            }

            /^[[:space:]]*menuconfig[[:space:]]+[A-Z0-9_]+/ {
                sym = $2
                is_toggle = 0
                is_menuconfig = 1
                last_menuconfig_sym = $2
                menuconfig_pattern_match = 0
                next
            }

            /^[[:space:]]*config[[:space:]]+[A-Z0-9_]+/ {
                sym = $2
                is_toggle = 0
                is_menuconfig = 0
                next
            }

            /^[[:space:]]*(bool|tristate)([[:space:]]|$)/ {
                is_toggle = 1
                if (match($0, /"[^"]+"/)) {
                    text = substr($0, RSTART, RLENGTH)
                    maybe_emit(text)
                    if (is_menuconfig && text ~ pattern) {
                        menuconfig_pattern_match = 1
                    }
                } else {
                    maybe_emit("")
                }
                next
            }

            /^[[:space:]]*prompt[[:space:]]*"[^"]+"/ {
                maybe_emit($0)
                if (is_menuconfig && $0 ~ pattern) {
                    menuconfig_pattern_match = 1
                }
            }
        ' \
        | sort -u
}

discover_legacy_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(legacy|deprecated|obsolete|obsolet[oa]s?|backward[[:space:]-]?compat(ibility)?|backwards[[:space:]-]?compat(ibility)?|compatibility layer|provided only for backwards compatibility|provided only for backward compatibility|here only for backward compatibility|here only for backwards compatibility|(^|[^[:alpha:]])old([^[:alpha:]]|$))"
}

discover_debug_trace_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(debug|tracing|tracer|trace|ftrace|kgdb|kdb|kprobe|uprobe|sanitizer|gcov|coverage|fault[- ]?injection|runtime testing|developer use only|debugging only|only be enabled for testing|intended for testing|testing purposes|test only|not suitable for production|not for use in production|not in production kernels|not be enabled in production|do not use (it )?on production|do not enable on production|production (systems?|kernels?|builds?))"
}

discover_hardening_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(hardening|hardened|mitigations? for cpu vulnerabilities|stack protector|shadow stack|fortify|control flow integrity|kcfi|strict kernel rwx|strict module rwx|memory protection keys|remove the kernel mapping in user mode|reset memory attack mitigation)"
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
    REPLY="${REPLY@U}"
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

    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi

    echo "Disabling: CONFIG_${normalized_sym}"
    cfg --disable "$normalized_sym" || true
}

enable_config_symbol() {
    normalize_config_symbol_name "$1"
    local normalized_sym="$REPLY"

    if is_protected_config_symbol "$normalized_sym"; then
        echo "Skipping protected symbol: CONFIG_${normalized_sym}"
        return 0
    fi

    echo "Enabling: CONFIG_${normalized_sym}"
    cfg --enable "$normalized_sym" || true
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
    cfg --set-val "$normalized_sym" "$value" || true
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
            samba | firehol | firewalld | openvswitch | ceph | nfs-client | nfs-server | openvpn | wireguard | docker | qemu | atop | bmon | btop | htop | iotop-c | cryptsetup)
                if [[ "$saw_none" == "1" ]]; then
                    echo "Invalid APPLICATIONS: cannot combine 'none' with app profiles" >&2
                    exit 1
                fi
                append_unique_item "$normalized" profiles
                ;;
            *)
                echo "Invalid APPLICATIONS entry: $token" >&2
                echo "Use: samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, qemu, atop, bmon, btop, htop, iotop-c, cryptsetup" >&2
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
    mode="${HOST_TYPE@L}"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            ;;
        baremetal)
            printf '%s\n' "baremetal"
            ;;
        qemu | kvm)
            printf '%s\n' "qemu"
            ;;
        vmware | hyperv | virtualbox)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid HOST_TYPE: $HOST_TYPE (use none, baremetal, qemu, vmware, hyperv, or virtualbox)" >&2
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

vendor_kconfig_files() {
    local path

    for path in \
        "$KSRCDIR/arch/x86/Kconfig" \
        "$KSRCDIR/arch/x86/events/Kconfig" \
        "$KSRCDIR/arch/x86/kvm/Kconfig" \
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
            exclude_re='CPU_SUP_AMD|CPU_SUP_HYGON|KVM_AMD|AMD_MEM_ENCRYPT|SEV|X86_AMD_|AMD_HFI'
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
        | xargs -0 awk -v include_re="$include_re" -v exclude_re="$exclude_re" '
            function emit() {
                if (sym != "" && saw_include && !saw_exclude) {
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

            /^[[:space:]]*(config|menuconfig)[[:space:]]+[A-Z0-9_]+/ {
                emit()
                sym = $2
                saw_include = (sym ~ include_re)
                saw_exclude = (sym ~ exclude_re)
                next
            }

            /^[[:space:]]*depends on[[:space:]]+/ {
                if (positive_match($0, include_re)) {
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
        if is_inverse_disable_symbol "$sym"; then
            enable_inverse_syms+=("$sym")
        else
            disable_syms+=("$sym")
        fi
    done

    if ((${#disable_syms[@]} > 0)); then
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

list_config_hz_symbols() {
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    local sym
    for sym in "${!_SYMBOL_VALUE_CACHE[@]}"; do
        [[ "$sym" == HZ_[0-9]* ]] && printf '%s\n' "$sym"
    done
}

is_symbol_enabled_now() {
    normalize_config_symbol_name "$1"
    ((_SYMBOL_CACHE_LOADED)) || _load_symbol_cache
    [[ -v _SYMBOL_VALUE_CACHE[$REPLY] ]] && [[ "${_SYMBOL_VALUE_CACHE[$REPLY]}" == "y" ]]
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
}

select_if_present() {
    local selected="$1"
    shift

    if have_symbol "$selected"; then
        disable_if_present "$@"
        normalize_config_symbol_name "$selected"
        local normalized_selected="$REPLY"
        if is_protected_config_symbol "$normalized_selected"; then
            echo "Skipping protected symbol: CONFIG_${normalized_selected}"
        else
            echo "Selecting: CONFIG_${normalized_selected}"
            cfg --enable "$normalized_selected" || true
        fi
    fi
}

configure_optimization_profile() {
    local profile="$1"

    if [[ "$profile" == "none" ]]; then
        return
    fi

    echo
    echo "==> Applying optimization profile: $profile"

    # resolve NUMA early so profiles can use it
    local numa_mode
    numa_mode="$(resolve_numa_support)"

    local wants_observability=true
    if is_enabled "$PRUNE_OBSERVABILITY" || is_enabled "$PRUNE_DEBUG_TRACE"; then
        wants_observability=false
    fi

    # common: compiler optimizations for all profiles
    enable_if_present \
        CC_OPTIMIZE_FOR_PERFORMANCE \
        CPU_ISOLATION \
        JUMP_LABEL

    disable_if_present CC_OPTIMIZE_FOR_SIZE

    case "$profile" in
        server)
            echo "    (prioritizes throughput and low background overhead)"

            disable_if_present \
                CPU_FREQ_DEFAULT_GOV_CONSERVATIVE \
                CPU_FREQ_DEFAULT_GOV_ONDEMAND \
                CPU_FREQ_DEFAULT_GOV_POWERSAVE \
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_DEFAULT_GOV_USERSPACE \
                HZ_PERIODIC \
                SCHED_AUTOGROUP \
                WQ_POWER_EFFICIENT_DEFAULT

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
                NO_HZ_IDLE \
                TRANSPARENT_HUGEPAGE \
                ZSWAP \
                ZSWAP_DEFAULT_ON

            if have_symbol TRANSPARENT_HUGEPAGE_MADVISE; then
                select_if_present TRANSPARENT_HUGEPAGE_MADVISE \
                    TRANSPARENT_HUGEPAGE_ALWAYS TRANSPARENT_HUGEPAGE_NEVER
            fi

            # observability: only enable monitoring symbols when not pruning
            if is_enabled "$wants_observability"; then
                enable_if_present \
                    CPU_FREQ_STAT \
                    IRQ_TIME_ACCOUNTING \
                    PSI
                disable_if_present PSI_DEFAULT_DISABLED
            fi

            # NUMA-aware balancing: only if the host actually has NUMA
            if [[ "$numa_mode" != "off" ]]; then
                enable_if_present NUMA_BALANCING
            fi

            configure_hz_profile server

            if have_symbol PREEMPT_NONE; then
                select_if_present PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_DYNAMIC PREEMPT_RT
            elif have_symbol PREEMPT_VOLUNTARY; then
                select_if_present PREEMPT_VOLUNTARY PREEMPT_NONE PREEMPT PREEMPT_DYNAMIC PREEMPT_RT
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
                HZ_PERIODIC \
                PREEMPT_RT

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
                NO_HZ_IDLE \
                SCHED_AUTOGROUP \
                TRANSPARENT_HUGEPAGE \
                UCLAMP_TASK \
                WQ_POWER_EFFICIENT_DEFAULT \
                ZSWAP \
                ZSWAP_DEFAULT_ON

            if have_symbol TRANSPARENT_HUGEPAGE_MADVISE || have_symbol TRANSPARENT_HUGEPAGE_ALWAYS; then
                if have_symbol TRANSPARENT_HUGEPAGE_MADVISE; then
                    select_if_present TRANSPARENT_HUGEPAGE_MADVISE \
                        TRANSPARENT_HUGEPAGE_ALWAYS TRANSPARENT_HUGEPAGE_NEVER
                else
                    select_if_present TRANSPARENT_HUGEPAGE_ALWAYS \
                        TRANSPARENT_HUGEPAGE_MADVISE TRANSPARENT_HUGEPAGE_NEVER
                fi
            fi

            if is_enabled "$wants_observability"; then
                enable_if_present PSI
                disable_if_present PSI_DEFAULT_DISABLED
            fi

            if [[ "$numa_mode" != "off" ]]; then
                enable_if_present NUMA_BALANCING
            fi

            configure_hz_profile desktop

            if have_symbol PREEMPT; then
                select_if_present PREEMPT PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT_DYNAMIC PREEMPT_RT PREEMPT_LAZY
            elif have_symbol PREEMPT_LAZY; then
                select_if_present PREEMPT_LAZY PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_DYNAMIC PREEMPT_RT
            elif have_symbol PREEMPT_VOLUNTARY; then
                select_if_present PREEMPT_VOLUNTARY PREEMPT_NONE PREEMPT PREEMPT_DYNAMIC PREEMPT_RT PREEMPT_LAZY
            fi

            enable_if_present PREEMPT_DYNAMIC
            ;;
        realtime)
            echo "    (prioritizes low latency and deterministic wakeups)"

            disable_if_present \
                CPU_FREQ_DEFAULT_GOV_CONSERVATIVE \
                CPU_FREQ_DEFAULT_GOV_ONDEMAND \
                CPU_FREQ_DEFAULT_GOV_POWERSAVE \
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_DEFAULT_GOV_USERSPACE \
                HZ_PERIODIC \
                KSM \
                NUMA_BALANCING \
                PSI \
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
                NO_HZ_IDLE \
                PSI_DEFAULT_DISABLED \
                RCU_BOOST \
                RCU_NOCB_CPU \
                RCU_NOCB_CPU_CB_BOOST

            configure_hz_profile realtime

            if have_symbol PREEMPT_RT; then
                select_if_present PREEMPT_RT PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_DYNAMIC
            elif have_symbol PREEMPT; then
                select_if_present PREEMPT PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT_DYNAMIC PREEMPT_RT
            elif have_symbol PREEMPT_DYNAMIC; then
                select_if_present PREEMPT_DYNAMIC PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_RT
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
        PARAVIRT_CLOCK
        PARAVIRT_TIME_ACCOUNTING
    )
    local -a qemu_syms=(
        KVM_GUEST
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
        VIRTIO_VSOCKETS_COMMON
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
        HYPERV_IOMMU
        VSOCKETS
        VSOCKETS_LOOPBACK
        HYPERV_VSOCKETS
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

            enable_if_present "${common_guest_syms[@]}"
            enable_if_present "${type_syms[@]}"
            if ((${#disable_syms[@]} > 0)); then
                disable_if_present "${disable_syms[@]}"
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
            enable_syms=(
                DRM_AMDGPU
                DRM_RADEON
                FB_RADEON
            )
            disable_syms=(
                DRM_I915
                DRM_XE
                FB_INTEL
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
                FB_INTEL
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
                FB_INTEL
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
                FB_INTEL
                INTEL_GTT
                DRM_NOUVEAU
                FB_NVIDIA
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

    echo
    echo "==> Enabling application profiles: $*"

    for profile in "$@"; do
        case "$profile" in
            samba)
                for sym in CIFS CIFS_XATTR CIFS_UPCALL CIFS_DFS_UPCALL DNS_RESOLVER KEYS KEY_DH_OPERATIONS CRYPTO_MD4 CRYPTO_MD5 CRYPTO_HMAC CRYPTO_SHA256 CRYPTO_SHA512 CRYPTO_AES CRYPTO_CMAC CRYPTO_DES; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            firehol)
                for sym in NETFILTER NETFILTER_ADVANCED NETFILTER_XTABLES NF_CONNTRACK NF_NAT NF_TABLES IP_SET IP_NF_IPTABLES IP6_NF_IPTABLES IP_NF_NAT IP6_NF_NAT NFT_CT NFT_NAT NFT_MASQ NFT_REDIR NETFILTER_XT_MATCH_CONNTRACK NETFILTER_XT_MATCH_COMMENT NETFILTER_XT_MATCH_ADDRTYPE NETFILTER_XT_SET NETFILTER_XT_TARGET_MASQUERADE NETFILTER_XT_TARGET_REDIRECT NETFILTER_XT_TARGET_LOG; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            firewalld)
                for sym in NETFILTER NETFILTER_ADVANCED NETFILTER_XTABLES NF_CONNTRACK NF_NAT NF_TABLES NF_TABLES_INET NF_TABLES_IPV4 NF_TABLES_IPV6 NF_TABLES_ARP NF_TABLES_BRIDGE NF_CONNTRACK_BRIDGE BRIDGE_NETFILTER IP_SET NFT_CT NFT_NAT NFT_MASQ NFT_REDIR NFT_REJECT NFT_REJECT_INET NFT_FIB NFT_FIB_INET NFT_FIB_IPV4 NFT_FIB_IPV6 IP_NF_IPTABLES IP6_NF_IPTABLES IP_NF_NAT IP6_NF_NAT; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            openvswitch)
                for sym in OPENVSWITCH NF_CONNTRACK NF_CONNTRACK_OVS NF_NAT_OVS NETFILTER VXLAN GENEVE GRE NET_UDP_TUNNEL; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            ceph)
                for sym in CEPH_LIB CEPH_FS CRYPTO LIBCRC32C; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            nfs-client)
                for sym in NFS_FS NFS_V3 NFS_V4 NFS_V4_1 NFS_V4_2 SUNRPC SUNRPC_GSS LOCKD LOCKD_V4 GRACE_PERIOD DNS_RESOLVER; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            nfs-server)
                for sym in NFSD NFSD_V3_ACL NFSD_V4 NFSD_PNFS NFSD_BLOCKLAYOUT NFSD_SCSILAYOUT NFSD_FLEXFILELAYOUT SUNRPC SUNRPC_GSS LOCKD LOCKD_V4 GRACE_PERIOD EXPORTFS FSNOTIFY; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            openvpn)
                for sym in TUN CRYPTO_USER_API CRYPTO_USER_API_AEAD CRYPTO_USER_API_SKCIPHER CRYPTO_USER_API_HASH CRYPTO_AES CRYPTO_GCM CRYPTO_SHA256; do
                    append_unique_item "$sym" enable_syms
                done
                ;;
            wireguard)
                for sym in WIREGUARD NET_UDP_TUNNEL UDP_TUNNEL CRYPTO_CHACHA20POLY1305 CRYPTO_CURVE25519 CRYPTO_LIB_CHACHA20POLY1305; do
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

                for sym in KVM KVM_X86 KVM_VFIO VFIO TUN VHOST VHOST_NET VHOST_VSOCK VHOST_IOTLB; do
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
        enable_if_present "${enable_syms[@]}"
    fi
}

load_protected_config_symbols

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
        KMEMLEAK \
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
    echo "==> Disabling options explicitly marked dangerous/unsafe"

    disable_discovered_and_fixed_symbols \
        discover_dangerous_kconfig_symbols \
        ADFS_FS_RW \
        DRM_FBDEV_LEAK_PHYS_SMEM \
        FB_VIA_DIRECT_PROCFS \
        MEMSTICK_UNSAFE_RESUME \
        MICROCODE_LATE_LOADING \
        MTD_TESTS \
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
        KDB \
        KGDB \
        KGDB_KDB \
        KGDB_TESTS \
        KUNIT \
        KUNIT_ALL_TESTS \
        KUNIT_TEST \
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

    disable_discovered_and_fixed_symbols \
        discover_legacy_kconfig_symbols \
        BINFMT_AOUT \
        BLK_DEV_FD \
        NF_CT_PROTO_UDPLITE \
        LEGACY_PTYS \
        PARPORT \
        PROVE_RCU \
        SYSFS_DEPRECATED \
        SYSFS_DEPRECATED_V2 \
        SYSFS_SYSCALL \
        UID16 \
        USELIB
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

    disable_discovered_and_fixed_symbols \
        discover_debug_trace_kconfig_symbols \
        BOOTPARAM_HARDLOCKUP_PANIC \
        BOOTPARAM_HUNG_TASK_PANIC \
        BOOTPARAM_SOFTLOCKUP_PANIC \
        DEBUG_ATOMIC_SLEEP \
        DEBUG_CREDENTIALS \
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
        DEBUG_SLAB \
        DEBUG_SPINLOCK \
        DEBUG_VIRTUAL \
        DEBUG_VM \
        DEBUG_VM_PGFLAGS \
        DEBUG_WW_MUTEX_SLOWPATH \
        DETECT_HUNG_TASK \
        DYNAMIC_DEBUG \
        GDB_SCRIPTS \
        HARDLOCKUP_DETECTOR \
        LATENCYTOP \
        LOCKDEP \
        LOCKUP_DETECTOR \
        LOCK_STAT \
        PAGE_EXTENSION \
        PAGE_OWNER \
        PAGE_POISONING \
        PROVE_LOCKING \
        SCHEDSTATS \
        SCHED_DEBUG \
        SLUB_DEBUG \
        SLUB_DEBUG_ON \
        SOFTLOCKUP_DETECTOR
fi

if is_enabled "$PRUNE_HARDENING"; then
    echo
    echo "==> Disabling hardening/mitigation symbols"
    echo "    (this reduces kernel security hardening)"

    disable_discovered_and_fixed_symbols discover_hardening_kconfig_symbols

    enable_if_present QCOM_RPMH
fi

invalidate_symbol_cache

OPTIMIZATION_PROFILE_EFFECTIVE="$(resolve_optimization_profile)"
configure_optimization_profile "$OPTIMIZATION_PROFILE_EFFECTIVE"

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

        mapfile -t cpu_vendor_syms < <(discover_vendor_kconfig_symbols "$CPU_VENDOR_TO_DISABLE")
        if ((${#cpu_vendor_syms[@]} > 0)); then
            disable_if_present "${cpu_vendor_syms[@]}"
        fi
    fi
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

UEFI_SUPPORT_EFFECTIVE="$(resolve_uefi_support)"
if [[ "$UEFI_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_uefi_support_profile "$UEFI_SUPPORT_EFFECTIVE"
fi

INITRD_SUPPORT_EFFECTIVE="$(resolve_initrd_support)"
if [[ "$INITRD_SUPPORT_EFFECTIVE" != "none" ]]; then
    if [[ "$INITRD_SUPPORT_EFFECTIVE" == "unknown" ]]; then
        echo
        echo "==> Could not detect current initrd usage; use INITRD_SUPPORT=on or off"
    else
        configure_initrd_support_profile "$INITRD_SUPPORT_EFFECTIVE"
    fi
fi

TPM_RESOLVED="$(resolve_tpm_support)"
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

DMA_ENGINE_SUPPORT_EFFECTIVE="$(resolve_dma_engine_support)"
if [[ "$DMA_ENGINE_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_dma_engine_support_profile "$DMA_ENGINE_SUPPORT_EFFECTIVE"
fi

IOMMU_SUPPORT_EFFECTIVE="$(resolve_iommu_support)"
if [[ "$IOMMU_SUPPORT_EFFECTIVE" != "none" ]]; then
    if ! is_x86_config; then
        echo
        echo "==> Skipping IOMMU_SUPPORT: .config is not x86"
    else
        IOMMU_CPU_VENDOR="$(resolve_cpu_vendor_or_detect)"
        configure_iommu_support_profile "$IOMMU_SUPPORT_EFFECTIVE" "$IOMMU_CPU_VENDOR"
    fi
fi

NUMA_SUPPORT_EFFECTIVE="$(resolve_numa_support)"
if [[ "$NUMA_SUPPORT_EFFECTIVE" != "none" ]]; then
    configure_numa_support_profile "$NUMA_SUPPORT_EFFECTIVE"
fi

NR_CPUS_EFFECTIVE="$(resolve_nr_cpus)"
if [[ "$NR_CPUS_EFFECTIVE" != "none" ]]; then
    if [[ "$NR_CPUS_EFFECTIVE" == "unknown" ]]; then
        echo
        echo "==> Could not detect host CPU count; use NR_CPUS=<number>"
    else
        configure_nr_cpus_profile "$NR_CPUS_EFFECTIVE"
    fi
fi

HOST_TYPE_EFFECTIVE="$(resolve_host_type)"
if [[ "$HOST_TYPE_EFFECTIVE" != "none" ]]; then
    configure_host_type_profile "$HOST_TYPE_EFFECTIVE"
fi

# Uncommon or legacy network protocols for a general-purpose server
if is_enabled "$PRUNE_UNUSED_NET"; then
    echo
    echo "==> Disabling uncommon/legacy network protocols"
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
        CIFS_WEAK_PW_HASH \
        NFS_V2 \
        NFSD_V2
fi

# Radio / proximity / IoT features usually unnecessary on servers
if is_enabled "$PRUNE_RADIOS"; then
    echo
    echo "==> Disabling unused radio and proximity protocols"
    disable_if_present \
        NFC \
        IRDA \
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

APPLICATIONS_RESOLVED="$(resolve_application_profiles)"
if [[ "$APPLICATIONS_RESOLVED" != "none" ]]; then
    mapfile -t APPLICATIONS_EFFECTIVE <<<"$APPLICATIONS_RESOLVED"
    configure_application_profiles "${APPLICATIONS_EFFECTIVE[@]}"
fi

if is_enabled "$PRUNE_UNUSED_MODULES"; then
    probe_and_prune_unused_module_symbols
fi

echo
echo "==> Running olddefconfig to normalize dependencies"
echo "    (note: Kconfig 'select' statements may re-enable symbols that were disabled above)"
env KCONFIG_CONFIG="$CONFIG_FILE" make olddefconfig >/dev/null

echo
if is_enabled "$DRY_RUN"; then
    echo "==> Dry-run changes for $ORIGINAL_CONFIG_FILE"
    show_config_changes "$ORIGINAL_CONFIG_FILE" "$CONFIG_FILE"
    echo
    echo "Dry-run complete. No files were modified."
else
    echo "Done."
    echo
    echo "Review changes with:"
    echo "  diff -u \"$BACKUP\" \"$ORIGINAL_CONFIG_FILE\" | less"
    echo
    echo "If you have scripts/diffconfig:"
    echo "  scripts/diffconfig \"$BACKUP\" \"$ORIGINAL_CONFIG_FILE\""
    echo
fi
