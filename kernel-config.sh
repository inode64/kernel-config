#!/usr/bin/env bash
set -Eeuo pipefail

# Usage:
#   ./kernel-config.sh [KERNEL_SRCDIR] [CONFIG_FILE]
#
# Examples:
#   ./kernel-config.sh /usr/src/linux
#   KEEP_OBSERVABILITY=0 PRUNE_LEGACY=1 ./kernel-config.sh /usr/src/linux
#
# Optional variables:
#   KEEP_OBSERVABILITY=1   -> keep useful options for perf/bpf/ftrace/debugfs
#   PRUNE_LEGACY=0         -> do not touch legacy/compat options by default
#   OPTIMIZE_SERVER_SPEED=0 -> do not force extra throughput tuning for servers
#   PRUNE_DEBUG_TRACE_KCONFIG=0 -> disable debug/trace symbols detected from Kconfig prompts
#   PRUNE_HARDENING_KCONFIG=0 -> disable hardening/mitigation symbols detected from Kconfig prompts
#   CPU_VENDOR_FILTER=none -> none, auto, amd, intel; disable x86 options for the other vendor
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

KSRCDIR="${1:-$(detect_default_ksrcdir)}"
CONFIG_FILE="${2:-$KSRCDIR/.config}"

KEEP_OBSERVABILITY="${KEEP_OBSERVABILITY:-1}"
PRUNE_LEGACY="${PRUNE_LEGACY:-0}"
OPTIMIZE_SERVER_SPEED="${OPTIMIZE_SERVER_SPEED:-0}"
PRUNE_DEBUG_TRACE_KCONFIG="${PRUNE_DEBUG_TRACE_KCONFIG:-0}"
PRUNE_HARDENING_KCONFIG="${PRUNE_HARDENING_KCONFIG:-0}"
CPU_VENDOR_FILTER="${CPU_VENDOR_FILTER:-none}"

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

discover_legacy_kconfig_symbols() {
    find "$KSRCDIR" -type f \( -name 'Kconfig' -o -name 'Kconfig.*' \) -print0 2>/dev/null \
        | xargs -0 awk '
            BEGIN {
                IGNORECASE = 1
                pattern = "(legacy|deprecated|obsolete|obsolet[oa]s?)"
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

discover_debug_trace_kconfig_symbols() {
    find "$KSRCDIR" -type f \( -name 'Kconfig' -o -name 'Kconfig.*' \) -print0 2>/dev/null \
        | xargs -0 awk '
            BEGIN {
                IGNORECASE = 1
                pattern = "(debug|tracing|tracer|trace|ftrace|kgdb|kdb|kprobe|uprobe|sanitizer|gcov|coverage|fault[- ]?injection|runtime testing)"
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

discover_hardening_kconfig_symbols() {
    local path

    for path in \
        "$KSRCDIR/arch/Kconfig" \
        "$KSRCDIR/arch/x86/Kconfig" \
        "$KSRCDIR/arch/arm64/Kconfig" \
        "$KSRCDIR/security/Kconfig" \
        "$KSRCDIR/init/Kconfig" \
        "$KSRCDIR/distro/Kconfig" \
        "$KSRCDIR/drivers/firmware/efi/Kconfig"; do
        if [[ -f "$path" ]]; then
            printf '%s\0' "$path"
        fi
    done \
        | xargs -0 awk '
            BEGIN {
                IGNORECASE = 1
                pattern = "(hardening|hardened|mitigations? for cpu vulnerabilities|stack protector|shadow stack|fortify|control flow integrity|kcfi|strict kernel rwx|strict module rwx|memory protection keys|remove the kernel mapping in user mode|reset memory attack mitigation)"
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
KEEP_BPF="${KEEP_BPF:-1}"
if [[ "$KEEP_BPF" != "1" ]]; then
    disable_if_present DEBUG_INFO_BTF KPROBES
fi

# optional: 32-bit compat
KEEP_COMPAT32="${KEEP_COMPAT32:-1}"
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

if [[ "$OPTIMIZE_SERVER_SPEED" == "1" ]]; then
    echo
    echo "==> Applying server performance profile"
    echo "    (prioritizes throughput over power saving and desktop-oriented tuning)"

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
        disable_if_present \
            HZ_100 \
            HZ_300 \
            HZ_1000
        echo "Selecting: CONFIG_HZ_250"
        cfg --enable HZ_250 || true
    fi
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

        echo "==> Adjusting x86 options for ${CPU_VENDOR_EFFECTIVE^^} CPU"
        echo "    (disabling ${CPU_VENDOR_TO_DISABLE^^}-specific symbols)"

        mapfile -t cpu_vendor_syms < <(discover_vendor_kconfig_symbols "$CPU_VENDOR_TO_DISABLE")
        if ((${#cpu_vendor_syms[@]} > 0)); then
            disable_if_present "${cpu_vendor_syms[@]}"
        fi
    fi
fi

# Uncommon or legacy network protocols for a general-purpose server
PRUNE_UNUSED_NET="${PRUNE_UNUSED_NET:-1}"
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
PRUNE_OLD_HW="${PRUNE_OLD_HW:-1}"
PRUNE_X86_OLD_PLATFORMS="${PRUNE_X86_OLD_PLATFORMS:-1}"
KEEP_LEGACY_ATA="${KEEP_LEGACY_ATA:-1}"

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
PRUNE_INSECURE="${PRUNE_INSECURE:-1}"

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
PRUNE_RADIOS="${PRUNE_RADIOS:-1}"

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
PRUNE_DMA_ATTACK_SURFACE="${PRUNE_DMA_ATTACK_SURFACE:-1}"

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
echo "  - OPTIMIZE_SERVER_SPEED=1 applies a conservative server-throughput profile."
echo "  - PRUNE_DEBUG_TRACE_KCONFIG=1 prunes debug/trace options based on Kconfig."
echo "  - PRUNE_HARDENING_KCONFIG=1 prunes hardening/mitigation options based on Kconfig."
echo "  - CPU_VENDOR_FILTER=auto/amd/intel prunes x86 options for the other vendor."
echo "  - PRUNE_LEGACY=1 touches old compatibility options; use it only if you know you want that."
echo "  - Hardening/security options remain intact unless PRUNE_HARDENING_KCONFIG=1 is set."
