#!/usr/bin/env bash
set -Eeuo pipefail

# Usage:
#   ./kernel-config.sh [OPTIONS] [KERNEL_SRCDIR] [CONFIG_FILE] [VAR=VALUE...]
#
# Examples:
#   ./kernel-config.sh /usr/src/linux
#   KEEP_OBSERVABILITY=0 PRUNE_LEGACY=1 ./kernel-config.sh /usr/src/linux
#   ./kernel-config.sh /usr/src/linux .config PRUNE_LEGACY=1 KEEP_OBSERVABILITY=0
#   ./kernel-config.sh --kernel-srcdir /usr/src/linux --config-file .config --prune-legacy
#   ./kernel-config.sh --kernel-srcdir /usr/src/linux --all-optimizations
#
# Optional variables and flags:
#   ALL_OPTIMIZATIONS=0    -> enable the script's full optimization preset
#   OPTIMIZATION_PROFILE=none -> none, server, desktop, realtime; tune scheduler/tick defaults
#   KEEP_OBSERVABILITY=1   -> keep useful options for perf/bpf/ftrace/debugfs
#   PRUNE_LEGACY=0         -> do not touch legacy/compat options by default
#   OPTIMIZE_SERVER_SPEED=0 -> legacy alias for OPTIMIZATION_PROFILE=server
#   PRUNE_DEBUG_TRACE_KCONFIG=0 -> disable debug/trace symbols detected from Kconfig prompts
#   PRUNE_HARDENING_KCONFIG=0 -> disable hardening/mitigation symbols detected from Kconfig prompts
#   PRUNE_SELFTEST_KCONFIG=0 -> disable selftest symbols detected from Kconfig prompts
#   CPU_VENDOR_FILTER=none -> none, auto, amd, intel; disable x86 options for the other vendor
#   VIDEO_SUPPORT=none      -> none, auto, amd, intel, nvidia, nouveau; keep only the selected GPU stack
#   UEFI_SUPPORT=none       -> none, auto, on, off; keep or prune common EFI/UEFI kernel support
#   INITRD_SUPPORT=none     -> none, auto, on, off; keep or prune initramfs/initrd boot support
#   TPM_SUPPORT=none        -> none, auto, on, off; keep or prune TPM support and detect TPM 1.2/2.0 on the host
#   APPLICATIONS=none       -> comma-separated app profiles: samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, atop, bmon, btop, htop, iotop-c, cryptsetup
#   HOST_TYPE=native        -> native, qemu, vmware, hyperv, virtualbox; tune guest-specific options
#
# Recommended:
#   cp .config .config.orig
#   ./kernel-config.sh /path/to/kernel
#   make olddefconfig
#   make -j$(nproc)

detect_default_ksrcdir() {
    if [[ -f "$PWD/Kconfig" ]]; then
        printf '%s\n' "$PWD"
        return
    fi

    local candidate
    candidate="$(
        find "$PWD" -maxdepth 1 -mindepth 1 -type d -name 'linux-*' 2>/dev/null \
            | sort \
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
  --all-optimizations [0|1]
  --optimization-profile PROFILE
  --cpu-vendor-filter MODE
  --video-support MODE
  --uefi-support MODE
  --initrd-support MODE
  --tpm-support MODE
  --applications LIST
  --host-type TYPE
  --keep-observability [0|1]
  --prune-legacy [0|1]
  --optimize-server-speed [0|1]
  --prune-debug-trace-kconfig [0|1]
  --prune-hardening-kconfig [0|1]
  --prune-selftest-kconfig [0|1]
  --keep-bpf [0|1]
  --keep-compat32 [0|1]
  --prune-unused-net [0|1]
  --prune-old-hw [0|1]
  --prune-x86-old-platforms [0|1]
  --keep-legacy-ata [0|1]
  --prune-insecure [0|1]
  --prune-radios [0|1]
  --prune-dma-attack-surface [0|1]
  -h, --help

Notes:
  Environment variables set defaults.
  CLI flags and VAR=VALUE arguments override environment variables.
  Boolean flags without a value imply 1.
  The all-optimizations preset does not enable --prune-hardening-kconfig.
  --optimization-profile accepts: none, server, desktop, realtime.
  --video-support accepts: none, auto, amd, intel, nvidia, nouveau.
  --uefi-support accepts: none, auto, on, off.
  --initrd-support accepts: none, auto, on, off.
  --tpm-support accepts: none, auto, on, off.
  --applications accepts a comma-separated list of app profiles.
  KERNEL_SRCDIR and CONFIG_FILE can be passed as positional arguments or via flags.
EOF
}

apply_all_optimizations() {
    OPTIMIZATION_PROFILE=server
    KEEP_OBSERVABILITY=0
    PRUNE_LEGACY=1
    OPTIMIZE_SERVER_SPEED=1
    PRUNE_DEBUG_TRACE_KCONFIG=1
    PRUNE_SELFTEST_KCONFIG=1
    KEEP_BPF=0
    KEEP_COMPAT32=0
    PRUNE_UNUSED_NET=1
    PRUNE_OLD_HW=1
    PRUNE_X86_OLD_PLATFORMS=1
    KEEP_LEGACY_ATA=0
    PRUNE_INSECURE=1
    PRUNE_RADIOS=1
    PRUNE_DMA_ATTACK_SURFACE=1
}

is_boolean_option() {
    case "$1" in
        all-optimizations | keep-observability | prune-legacy | optimize-server-speed | prune-debug-trace-kconfig | prune-hardening-kconfig | prune-selftest-kconfig | keep-bpf | keep-compat32 | prune-unused-net | prune-old-hw | prune-x86-old-platforms | keep-legacy-ata | prune-insecure | prune-radios | prune-dma-attack-surface)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

is_boolean_value() {
    [[ "$1" == "0" || "$1" == "1" ]]
}

set_tunable() {
    local name="$1"
    local value="$2"

    case "$name" in
        ALL_OPTIMIZATIONS)
            printf -v "$name" '%s' "$value"
            if [[ "$value" == "1" ]]; then
                apply_all_optimizations
            fi
            ;;
        OPTIMIZATION_PROFILE | KEEP_OBSERVABILITY | PRUNE_LEGACY | OPTIMIZE_SERVER_SPEED | PRUNE_DEBUG_TRACE_KCONFIG | PRUNE_HARDENING_KCONFIG | PRUNE_SELFTEST_KCONFIG | CPU_VENDOR_FILTER | VIDEO_SUPPORT | UEFI_SUPPORT | INITRD_SUPPORT | TPM_SUPPORT | APPLICATIONS | HOST_TYPE | KEEP_BPF | KEEP_COMPAT32 | PRUNE_UNUSED_NET | PRUNE_OLD_HW | PRUNE_X86_OLD_PLATFORMS | KEEP_LEGACY_ATA | PRUNE_INSECURE | PRUNE_RADIOS | PRUNE_DMA_ATTACK_SURFACE)
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
        *)
            set_tunable "$(printf '%s' "$name" | tr '[:lower:]-' '[:upper:]_')" "$value"
            ;;
    esac
}

ALL_OPTIMIZATIONS="${ALL_OPTIMIZATIONS:-0}"
OPTIMIZATION_PROFILE="${OPTIMIZATION_PROFILE:-none}"
KEEP_OBSERVABILITY="${KEEP_OBSERVABILITY:-1}"
PRUNE_LEGACY="${PRUNE_LEGACY:-0}"
OPTIMIZE_SERVER_SPEED="${OPTIMIZE_SERVER_SPEED:-0}"
PRUNE_DEBUG_TRACE_KCONFIG="${PRUNE_DEBUG_TRACE_KCONFIG:-0}"
PRUNE_HARDENING_KCONFIG="${PRUNE_HARDENING_KCONFIG:-0}"
PRUNE_SELFTEST_KCONFIG="${PRUNE_SELFTEST_KCONFIG:-0}"
CPU_VENDOR_FILTER="${CPU_VENDOR_FILTER:-none}"
VIDEO_SUPPORT="${VIDEO_SUPPORT:-none}"
UEFI_SUPPORT="${UEFI_SUPPORT:-none}"
INITRD_SUPPORT="${INITRD_SUPPORT:-none}"
TPM_SUPPORT="${TPM_SUPPORT:-none}"
APPLICATIONS="${APPLICATIONS:-none}"
HOST_TYPE="${HOST_TYPE:-native}"
KEEP_BPF="${KEEP_BPF:-1}"
KEEP_COMPAT32="${KEEP_COMPAT32:-1}"
PRUNE_UNUSED_NET="${PRUNE_UNUSED_NET:-1}"
PRUNE_OLD_HW="${PRUNE_OLD_HW:-1}"
PRUNE_X86_OLD_PLATFORMS="${PRUNE_X86_OLD_PLATFORMS:-1}"
KEEP_LEGACY_ATA="${KEEP_LEGACY_ATA:-1}"
PRUNE_INSECURE="${PRUNE_INSECURE:-1}"
PRUNE_RADIOS="${PRUNE_RADIOS:-1}"
PRUNE_DMA_ATTACK_SURFACE="${PRUNE_DMA_ATTACK_SURFACE:-1}"

if [[ "$ALL_OPTIMIZATIONS" == "1" ]]; then
    apply_all_optimizations
fi

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
            set_option "$opt_name" "${1#*=}"
            ;;
        --*)
            opt_name="${1#--}"
            if is_boolean_option "$opt_name"; then
                if (($# > 1)) && is_boolean_value "$2"; then
                    shift
                    set_option "$opt_name" "$1"
                else
                    set_option "$opt_name" "1"
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

if [[ ! -x scripts/config ]]; then
    echo "scripts/config is missing or not executable; trying to generate it..."
    make -s scripts >/dev/null
fi

if [[ ! -x scripts/config ]]; then
    echo "Could not prepare scripts/config" >&2
    exit 1
fi

BACKUP="${CONFIG_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp -a "$CONFIG_FILE" "$BACKUP"
echo "Backup: $BACKUP"

cfg() {
    scripts/config --file "$CONFIG_FILE" "$@"
}

have_symbol() {
    local sym="$1"
    grep -Eq "^(CONFIG_${sym}=|# CONFIG_${sym} is not set)" "$CONFIG_FILE"
}

find_kconfig_files() {
    find -L "$KSRCDIR" -type f \( -name 'Kconfig' -o -name 'Kconfig.*' \) -print0 2>/dev/null
}

discover_kconfig_symbols_by_pattern() {
    local pattern="$1"

    find_kconfig_files \
        | xargs -0 -r awk -v pattern="$pattern" '
            BEGIN {
                IGNORECASE = 1
            }

            function maybe_emit(text) {
                if (sym != "" && is_toggle && text ~ pattern) {
                    print sym
                }
            }

            /^[[:space:]]*(config|menuconfig)[[:space:]]+[A-Z0-9_]+/ {
                sym = $2
                is_toggle = 0
                next
            }

            /^[[:space:]]*(bool|tristate)([[:space:]]|$)/ {
                is_toggle = 1
                if (match($0, /"[^"]+"/)) {
                    maybe_emit(substr($0, RSTART, RLENGTH))
                }
                next
            }

            /^[[:space:]]*prompt[[:space:]]*"[^"]+"/ {
                maybe_emit($0)
            }
        ' \
        | sort -u
}

discover_legacy_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(legacy|deprecated|obsolete|obsolet[oa]s?)"
}

discover_debug_trace_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(debug|tracing|tracer|trace|ftrace|kgdb|kdb|kprobe|uprobe|sanitizer|gcov|coverage|fault[- ]?injection|runtime testing)"
}

discover_hardening_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(hardening|hardened|mitigations? for cpu vulnerabilities|stack protector|shadow stack|fortify|control flow integrity|kcfi|strict kernel rwx|strict module rwx|memory protection keys|remove the kernel mapping in user mode|reset memory attack mitigation)"
}

discover_selftest_kconfig_symbols() {
    discover_kconfig_symbols_by_pattern "(self[- ]?tests?|selftest|kunit tests?|boot[- ]time self[- ]tests?)"
}

is_x86_config() {
    grep -Eq '^(CONFIG_X86=y|CONFIG_X86_64=y|CONFIG_X86_32=y)' "$CONFIG_FILE"
}

detect_host_cpu_vendor() {
    local vendor_id=""

    if [[ -r /proc/cpuinfo ]]; then
        vendor_id="$(
            awk -F': *' '/^vendor_id[[:space:]]*:/{print $2; exit}' /proc/cpuinfo 2>/dev/null || true
        )"
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

resolve_cpu_vendor_filter() {
    local mode
    mode="$(printf '%s' "$CPU_VENDOR_FILTER" | tr '[:upper:]' '[:lower:]')"

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
            module="$(basename "$(readlink -f "$path/device/driver/module")")"
        elif [[ -L "$path/device/driver" ]]; then
            module="$(basename "$(readlink -f "$path/device/driver")")"
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
    mode="$(printf '%s' "$VIDEO_SUPPORT" | tr '[:upper:]' '[:lower:]')"

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

resolve_uefi_support() {
    local mode
    mode="$(printf '%s' "$UEFI_SUPPORT" | tr '[:upper:]' '[:lower:]')"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_uefi_support
            ;;
        on | yes | true | 1 | enable | enabled | uefi)
            printf '%s\n' "on"
            ;;
        off | no | false | 0 | disable | disabled | bios | legacy)
            printf '%s\n' "off"
            ;;
        *)
            echo "Invalid UEFI_SUPPORT: $UEFI_SUPPORT (use none, auto, on, or off)" >&2
            exit 1
            ;;
    esac
}

detect_host_initrd_support() {
    local ramdisk_image=""
    local ramdisk_size=""

    if [[ -r /sys/kernel/boot_params/data ]]; then
        ramdisk_image="$(
            od -An -j $((0x218)) -N 4 -t u4 /sys/kernel/boot_params/data 2>/dev/null \
                | awk '{print $1; exit}'
        )"
        ramdisk_size="$(
            od -An -j $((0x21c)) -N 4 -t u4 /sys/kernel/boot_params/data 2>/dev/null \
                | awk '{print $1; exit}'
        )"

        if [[ -n "$ramdisk_image" && -n "$ramdisk_size" ]]; then
            if [[ "$ramdisk_image" != "0" && "$ramdisk_size" != "0" ]]; then
                printf '%s\n' "on"
            else
                printf '%s\n' "off"
            fi
            return
        fi
    fi

    if [[ -r /proc/cmdline ]] && grep -Eq '(^|[[:space:]])initrd=' /proc/cmdline 2>/dev/null; then
        printf '%s\n' "on"
    else
        printf '%s\n' "unknown"
    fi
}

resolve_initrd_support() {
    local mode
    mode="$(printf '%s' "$INITRD_SUPPORT" | tr '[:upper:]' '[:lower:]')"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_initrd_support
            ;;
        on | yes | true | 1 | enable | enabled | initrd | initramfs)
            printf '%s\n' "on"
            ;;
        off | no | false | 0 | disable | disabled)
            printf '%s\n' "off"
            ;;
        *)
            echo "Invalid INITRD_SUPPORT: $INITRD_SUPPORT (use none, auto, on, or off)" >&2
            exit 1
            ;;
    esac
}

detect_host_tpm_versions() {
    local path version description
    local -a versions=()

    for path in /sys/class/tpm/tpm*; do
        [[ -d "$path" ]] || continue

        version=""
        if [[ -r "$path/tpm_version_major" ]]; then
            case "$(tr -d '[:space:]' < "$path/tpm_version_major")" in
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
                description="$(tr '[:upper:]' '[:lower:]' < "$path/device/description")"
            elif [[ -r "$path/description" ]]; then
                description="$(tr '[:upper:]' '[:lower:]' < "$path/description")"
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
    local mode
    mode="$(printf '%s' "$TPM_SUPPORT" | tr '[:upper:]' '[:lower:]')"

    case "$mode" in
        "" | none)
            printf '%s\n' "none"
            ;;
        auto)
            detect_host_tpm_versions
            ;;
        on | yes | true | 1 | enable | enabled | tpm)
            printf '%s\n' "on"
            ;;
        off | no | false | 0 | disable | disabled)
            printf '%s\n' "off"
            ;;
        *)
            echo "Invalid TPM_SUPPORT: $TPM_SUPPORT (use none, auto, on, or off)" >&2
            exit 1
            ;;
    esac
}

resolve_application_profiles() {
    local raw normalized token
    local saw_none=0
    local -a profiles=()

    raw="$(printf '%s' "$APPLICATIONS" | tr '[:upper:]' '[:lower:]')"
    raw="${raw//,/ }"

    for token in $raw; do
        normalized="${token//_/-}"

        case "$normalized" in
            "" )
                ;;
            none | off | 0)
                saw_none=1
                ;;
            samba | firehol | firewalld | openvswitch | ceph | nfs-client | nfs-server | openvpn | wireguard | docker | atop | bmon | btop | htop | iotop-c | cryptsetup)
                if [[ "$saw_none" == "1" ]]; then
                    echo "Invalid APPLICATIONS: cannot combine 'none' with app profiles" >&2
                    exit 1
                fi
                append_unique_item "$normalized" profiles
                ;;
            *)
                echo "Invalid APPLICATIONS entry: $token" >&2
                echo "Use: samba, firehol, firewalld, openvswitch, ceph, nfs-client, nfs-server, openvpn, wireguard, docker, atop, bmon, btop, htop, iotop-c, cryptsetup" >&2
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

    if [[ "$need_v4" == "1" ]]; then
        printf '%s\n' "v4"
    fi

    if [[ "$need_ascii_ci" == "1" ]]; then
        printf '%s\n' "ascii_ci"
    fi
}

resolve_host_type() {
    local mode
    mode="$(printf '%s' "$HOST_TYPE" | tr '[:upper:]' '[:lower:]')"

    case "$mode" in
        "" | native)
            printf '%s\n' "native"
            ;;
        qemu | kvm)
            printf '%s\n' "qemu"
            ;;
        vmware | hyperv | virtualbox)
            printf '%s\n' "$mode"
            ;;
        *)
            echo "Invalid HOST_TYPE: $HOST_TYPE (use native, qemu, vmware, hyperv, or virtualbox)" >&2
            exit 1
            ;;
    esac
}

resolve_optimization_profile() {
    local mode
    mode="$(printf '%s' "$OPTIMIZATION_PROFILE" | tr '[:upper:]' '[:lower:]')"

    case "$mode" in
        "" | none | off | 0)
            if [[ "$OPTIMIZE_SERVER_SPEED" == "1" ]]; then
                printf '%s\n' "server"
            else
                printf '%s\n' "none"
            fi
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
        "$KSRCDIR/drivers/idle/Kconfig" \
        "$KSRCDIR/drivers/thermal/intel/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/intel/ifs/Kconfig" \
        "$KSRCDIR/drivers/platform/x86/amd/hfi/Kconfig" \
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
    local sym
    for sym in "$@"; do
        if have_symbol "$sym"; then
            echo "Disabling: CONFIG_${sym}"
            cfg --disable "$sym" || true
        fi
    done
}

enable_if_present() {
    local sym
    for sym in "$@"; do
        if have_symbol "$sym"; then
            echo "Enabling: CONFIG_${sym}"
            cfg --enable "$sym" || true
        fi
    done
}

select_if_present() {
    local selected="$1"
    shift

    if have_symbol "$selected"; then
        disable_if_present "$@"
        echo "Selecting: CONFIG_${selected}"
        cfg --enable "$selected" || true
    fi
}

configure_optimization_profile() {
    local profile="$1"

    if [[ "$profile" == "none" ]]; then
        return
    fi

    echo
    echo "==> Applying optimization profile: $profile"

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
                SCHED_AUTOGROUP

            enable_if_present \
                CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
                CPU_FREQ_GOV_PERFORMANCE \
                NO_HZ_IDLE

            if have_symbol HZ_250; then
                select_if_present HZ_250 HZ_100 HZ_300 HZ_1000
            elif have_symbol HZ_300; then
                select_if_present HZ_300 HZ_100 HZ_250 HZ_1000
            elif have_symbol HZ_100; then
                select_if_present HZ_100 HZ_250 HZ_300 HZ_1000
            fi

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
                CPU_FREQ_DEFAULT_GOV_SCHEDUTIL \
                CPU_FREQ_GOV_SCHEDUTIL \
                NO_HZ_IDLE \
                SCHED_AUTOGROUP \
                HIGH_RES_TIMERS

            if have_symbol HZ_1000; then
                select_if_present HZ_1000 HZ_100 HZ_250 HZ_300
            elif have_symbol HZ_300; then
                select_if_present HZ_300 HZ_100 HZ_250 HZ_1000
            elif have_symbol HZ_250; then
                select_if_present HZ_250 HZ_100 HZ_300 HZ_1000
            fi

            if have_symbol PREEMPT_DYNAMIC; then
                select_if_present PREEMPT_DYNAMIC PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT PREEMPT_RT
            elif have_symbol PREEMPT; then
                select_if_present PREEMPT PREEMPT_NONE PREEMPT_VOLUNTARY PREEMPT_DYNAMIC PREEMPT_RT
            elif have_symbol PREEMPT_VOLUNTARY; then
                select_if_present PREEMPT_VOLUNTARY PREEMPT_NONE PREEMPT PREEMPT_DYNAMIC PREEMPT_RT
            fi
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
                SCHED_AUTOGROUP

            enable_if_present \
                CPU_FREQ_DEFAULT_GOV_PERFORMANCE \
                CPU_FREQ_GOV_PERFORMANCE \
                HIGH_RES_TIMERS \
                NO_HZ_IDLE

            if have_symbol HZ_1000; then
                select_if_present HZ_1000 HZ_100 HZ_250 HZ_300
            elif have_symbol HZ_300; then
                select_if_present HZ_300 HZ_100 HZ_250 HZ_1000
            elif have_symbol HZ_250; then
                select_if_present HZ_250 HZ_100 HZ_300 HZ_1000
            fi

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
    local -a native_disable_syms=(
        HYPERVISOR_GUEST
        PARAVIRT
        PARAVIRT_XXL
        PARAVIRT_SPINLOCKS
        PARAVIRT_CLOCK
        PARAVIRT_TIME_ACCOUNTING
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
        VIRTIO_VSOCKETS
        VIRTIO_VSOCKETS_COMMON
        PVPANIC
        PVPANIC_MMIO
        PVPANIC_PCI
        FW_CFG_SYSFS
        FW_CFG_SYSFS_CMDLINE
        VMWARE_VMCI
        VMWARE_BALLOON
        VMWARE_PVSCSI
        VMXNET3
        VMWARE_VMCI_VSOCKETS
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
        HYPERV_VSOCKETS
        VBOXGUEST
        VBOXSF_FS
        VSOCKETS
        VSOCKETS_LOOPBACK
    )
    local -a qemu_disable_syms=(
        VMWARE_VMCI
        VMWARE_BALLOON
        VMWARE_PVSCSI
        VMXNET3
        VMWARE_VMCI_VSOCKETS
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
        HYPERV_VSOCKETS
        VBOXGUEST
        VBOXSF_FS
    )
    local -a vmware_disable_syms=(
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
        VIRTIO_VSOCKETS
        VIRTIO_VSOCKETS_COMMON
        PVPANIC
        PVPANIC_MMIO
        PVPANIC_PCI
        FW_CFG_SYSFS
        FW_CFG_SYSFS_CMDLINE
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
        HYPERV_VSOCKETS
        VBOXGUEST
        VBOXSF_FS
    )
    local -a hyperv_disable_syms=(
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
        VIRTIO_VSOCKETS
        VIRTIO_VSOCKETS_COMMON
        PVPANIC
        PVPANIC_MMIO
        PVPANIC_PCI
        FW_CFG_SYSFS
        FW_CFG_SYSFS_CMDLINE
        VMWARE_VMCI
        VMWARE_BALLOON
        VMWARE_PVSCSI
        VMXNET3
        VMWARE_VMCI_VSOCKETS
        VBOXGUEST
        VBOXSF_FS
    )
    local -a virtualbox_disable_syms=(
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
        VIRTIO_VSOCKETS
        VIRTIO_VSOCKETS_COMMON
        PVPANIC
        PVPANIC_MMIO
        PVPANIC_PCI
        FW_CFG_SYSFS
        FW_CFG_SYSFS_CMDLINE
        VMWARE_VMCI
        VMWARE_BALLOON
        VMWARE_PVSCSI
        VMXNET3
        VMWARE_VMCI_VSOCKETS
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
        HYPERV_VSOCKETS
        VSOCKETS
        VSOCKETS_LOOPBACK
    )

    echo
    echo "==> Applying host type profile: $host_type"

    case "$host_type" in
        native)
            echo "    (disabling guest virtualization drivers for bare metal)"
            disable_if_present "${native_disable_syms[@]}"
            ;;
        qemu)
            echo "    (enabling KVM/QEMU guest drivers and disabling other guest stacks)"
            enable_if_present "${common_guest_syms[@]}"
            enable_if_present "${qemu_syms[@]}"
            disable_if_present "${qemu_disable_syms[@]}"
            ;;
        vmware)
            echo "    (enabling VMware guest drivers and disabling other guest stacks)"
            enable_if_present "${common_guest_syms[@]}"
            enable_if_present "${vmware_syms[@]}"
            disable_if_present "${vmware_disable_syms[@]}"
            ;;
        hyperv)
            echo "    (enabling Hyper-V guest drivers and disabling other guest stacks)"
            enable_if_present "${common_guest_syms[@]}"
            enable_if_present "${hyperv_syms[@]}"
            disable_if_present "${hyperv_disable_syms[@]}"
            ;;
        virtualbox)
            echo "    (enabling VirtualBox guest drivers and disabling other guest stacks)"
            enable_if_present "${common_guest_syms[@]}"
            enable_if_present "${virtualbox_syms[@]}"
            disable_if_present "${virtualbox_disable_syms[@]}"
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
            echo "    (pruning common EFI runtime, boot stub, and EFI partition support)"
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
                EFI_PARTITION
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

configure_application_profiles() {
    local profile
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

echo
echo "==> Disabling debug/instrumentation options with real runtime cost"

disable_if_present \
    BOOTPARAM_HARDLOCKUP_PANIC \
    BOOTPARAM_HUNG_TASK_PANIC \
    BOOTPARAM_SOFTLOCKUP_PANIC \
    DEBUG_ATOMIC_SLEEP \
    DEBUG_CREDENTIALS \
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

echo
echo "==> Disabling sanitizers, coverage, and fault injection"

disable_if_present \
    FAILSLAB \
    FAIL_FUTEX \
    FAIL_IO_TIMEOUT \
    FAIL_MAKE_REQUEST \
    FAIL_PAGE_ALLOC \
    FAULT_INJECTION \
    FAULT_INJECTION_DEBUG_FS \
    GCOV_KERNEL \
    GCOV_PROFILE_ALL \
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

echo
echo "==> Disabling tests and development utilities"

disable_if_present \
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

if [[ "$PRUNE_SELFTEST_KCONFIG" == "1" ]]; then
    echo
    echo "==> Disabling selftest symbols detected from Kconfig"

    mapfile -t selftest_syms < <(discover_selftest_kconfig_symbols)
    if ((${#selftest_syms[@]} > 0)); then
        disable_if_present "${selftest_syms[@]}"
    fi
fi

if [[ "$KEEP_OBSERVABILITY" != "1" ]]; then
    echo
    echo "==> Aggressive mode: disabling tracing/observability"
    echo "    (this can affect perf/ftrace/bpftrace and similar tools)"

    disable_if_present \
        BLK_DEV_IO_TRACE \
        BRANCH_PROFILE_NONE \
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
fi

if [[ "$PRUNE_LEGACY" == "1" ]]; then
    echo
    echo "==> Disabling legacy/obsolete interfaces"
    echo "    (optional block; review compatibility before using it in production)"

    disable_if_present \
        BINFMT_AOUT \
        SYSFS_DEPRECATED \
        SYSFS_DEPRECATED_V2 \
        SYSFS_SYSCALL \
        UID16 \
        USELIB

    mapfile -t legacy_syms < <(discover_legacy_kconfig_symbols)
    if ((${#legacy_syms[@]} > 0)); then
        echo "==> Disabling symbols marked as legacy/deprecated in Kconfig"
        disable_if_present "${legacy_syms[@]}"
    fi
fi

# extra: reasonable debug/legacy settings
disable_if_present \
    BLK_DEV_FD \
    DEBUG_SLAB \
    DYNAMIC_DEBUG \
    LEGACY_PTYS \
    PARPORT \
    PROVE_RCU

# extra: debug info choice
if have_symbol DEBUG_INFO_NONE; then
    echo "Selecting: CONFIG_DEBUG_INFO_NONE"
    cfg --enable DEBUG_INFO_NONE || true
fi

disable_if_present \
    DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT \
    DEBUG_INFO_REDUCED \
    GDB_SCRIPTS

# optional: BPF/observability
if [[ "$KEEP_BPF" != "1" ]]; then
    disable_if_present DEBUG_INFO_BTF KPROBES
fi

# optional: 32-bit compat
if [[ "$KEEP_COMPAT32" != "1" ]]; then
    disable_if_present IA32_EMULATION
fi

# optional: aggressive observability pruning
if [[ "$KEEP_OBSERVABILITY" != "1" ]]; then
    disable_if_present DYNAMIC_FTRACE
fi

if [[ "$PRUNE_DEBUG_TRACE_KCONFIG" == "1" ]]; then
    echo
    echo "==> Disabling debug/trace symbols detected from Kconfig"

    mapfile -t debug_trace_syms < <(discover_debug_trace_kconfig_symbols)
    if ((${#debug_trace_syms[@]} > 0)); then
        disable_if_present "${debug_trace_syms[@]}"
    fi
fi

if [[ "$PRUNE_HARDENING_KCONFIG" == "1" ]]; then
    echo
    echo "==> Disabling hardening/mitigation symbols detected from Kconfig"
    echo "    (this reduces kernel security hardening)"

    mapfile -t hardening_syms < <(discover_hardening_kconfig_symbols)
    if ((${#hardening_syms[@]} > 0)); then
        disable_if_present "${hardening_syms[@]}"
    fi
fi

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

        echo "==> Adjusting x86 options for ${CPU_VENDOR_EFFECTIVE^^} CPU"
        echo "    (disabling ${CPU_VENDOR_TO_DISABLE^^}-specific symbols)"

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
    TPM_HOST_VERSIONS="$(detect_host_tpm_versions)"

    if [[ "$TPM_RESOLVED" == "off" ]]; then
        if [[ "$TPM_HOST_VERSIONS" != "none" ]]; then
            mapfile -t TPM_EFFECTIVE <<<"$TPM_HOST_VERSIONS"
            configure_tpm_support_profile "off" "${TPM_EFFECTIVE[@]}"
        else
            configure_tpm_support_profile "off"
        fi
    elif [[ "$TPM_RESOLVED" == "on" ]]; then
        if [[ "$TPM_HOST_VERSIONS" != "none" ]]; then
            mapfile -t TPM_EFFECTIVE <<<"$TPM_HOST_VERSIONS"
            configure_tpm_support_profile "on" "${TPM_EFFECTIVE[@]}"
        else
            configure_tpm_support_profile "on"
        fi
    else
        mapfile -t TPM_EFFECTIVE <<<"$TPM_RESOLVED"
        configure_tpm_support_profile "on" "${TPM_EFFECTIVE[@]}"
    fi
fi

HOST_TYPE_EFFECTIVE="$(resolve_host_type)"
configure_host_type_profile "$HOST_TYPE_EFFECTIVE"

# Uncommon or legacy network protocols for a general-purpose server
if [[ "$PRUNE_UNUSED_NET" == "1" ]]; then
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
if [[ "$PRUNE_OLD_HW" == "1" ]]; then
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

if [[ "$PRUNE_X86_OLD_PLATFORMS" == "1" ]]; then
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

if [[ "$KEEP_LEGACY_ATA" != "1" ]]; then
    echo
    echo "==> Disabling legacy ATA/PATA support"
    disable_if_present ATA_SFF
fi

# Insecure or legacy protocols/compat
if [[ "$PRUNE_INSECURE" == "1" ]]; then
    echo
    echo "==> Disabling legacy or less secure protocols/compat"
    disable_if_present \
        CIFS_ALLOW_INSECURE_LEGACY \
        CIFS_WEAK_PW_HASH \
        NFS_V2 \
        NFSD_V2
fi

# Radio / proximity / IoT features usually unnecessary on servers
if [[ "$PRUNE_RADIOS" == "1" ]]; then
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
if [[ "$PRUNE_DMA_ATTACK_SURFACE" == "1" ]]; then
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

APPLICATIONS_RESOLVED="$(resolve_application_profiles)"
if [[ "$APPLICATIONS_RESOLVED" != "none" ]]; then
    mapfile -t APPLICATIONS_EFFECTIVE <<<"$APPLICATIONS_RESOLVED"
    configure_application_profiles "${APPLICATIONS_EFFECTIVE[@]}"
fi

echo
echo "==> Running olddefconfig to normalize dependencies"
yes "" | make olddefconfig >/dev/null

echo
echo "Done."
echo
echo "Review changes with:"
echo "  diff -u \"$BACKUP\" \"$CONFIG_FILE\" | less"
echo
echo "If you have scripts/diffconfig:"
echo "  scripts/diffconfig \"$BACKUP\" \"$CONFIG_FILE\""
echo
echo "Notes:"
echo "  - KEEP_OBSERVABILITY=1 keeps useful observability options."
echo "  - OPTIMIZATION_PROFILE=server/desktop/realtime selects a kernel tuning profile."
echo "  - OPTIMIZE_SERVER_SPEED=1 is kept as a legacy alias for OPTIMIZATION_PROFILE=server."
echo "  - PRUNE_DEBUG_TRACE_KCONFIG=1 prunes debug/trace options based on Kconfig."
echo "  - PRUNE_HARDENING_KCONFIG=1 prunes hardening/mitigation options based on Kconfig."
echo "  - PRUNE_SELFTEST_KCONFIG=1 prunes selftest options based on Kconfig."
echo "  - ALL_OPTIMIZATIONS=1 enables the optimization preset, excluding hardening pruning."
echo "  - CPU_VENDOR_FILTER=auto/amd/intel prunes x86 options for the other vendor."
echo "  - VIDEO_SUPPORT=auto/amd/intel/nvidia/nouveau prunes display drivers to the selected stack."
echo "  - UEFI_SUPPORT=auto/on/off keeps or prunes common EFI runtime, EFI stub, and EFI partition support."
echo "  - INITRD_SUPPORT=auto/on/off keeps or prunes BLK_DEV_INITRD based on current boot usage or explicit selection."
echo "  - TPM_SUPPORT=auto/on/off keeps or prunes TPM support and reports detected TPM 1.2/2.0 devices."
echo "  - APPLICATIONS=samba,firehol,... enables kernel features commonly needed by the selected apps."
echo "  - Mounted XFS filesystems are checked with xfs_info to keep/drop XFS_SUPPORT_V4 and XFS_SUPPORT_ASCII_CI."
echo "  - HOST_TYPE=native/qemu/vmware/hyperv/virtualbox tunes guest virtualization support."
echo "  - PRUNE_LEGACY=1 touches old compatibility options; use it only if you know you want that."
echo "  - Hardening/security options remain intact unless PRUNE_HARDENING_KCONFIG=1 is set."
