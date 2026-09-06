#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT="$SCRIPT_DIR/kernel-config.sh"
TEST_TMP="$(mktemp -d "${TMPDIR:-/tmp}/kernel-config-tests.XXXXXX")"

cleanup() {
    if [[ -n "$TEST_TMP" && -d "$TEST_TMP" ]]; then
        rm -rf "$TEST_TMP"
    fi
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local file="$1"
    local expected="$2"
    grep -Fq -- "$expected" "$file" || fail "missing '$expected' in $file"
}

create_fixture() {
    local tree="$1"
    mkdir -p "$tree/scripts"

    cat >"$tree/Makefile" <<'EOF'
kernelversion:
	@echo 7.2.0-test

scripts:
	@:

olddefconfig:
	@if [ "$${FORCE_SCHED_CACHE:-}" = y ]; then scripts/config --file "$$KCONFIG_CONFIG" --enable SCHED_CACHE; fi
	@for sym in $${DROP_SYMBOLS:-}; do sed -i -e "/^CONFIG_$$sym=/d" -e "/^# CONFIG_$$sym is not set$$/d" "$$KCONFIG_CONFIG"; done
EOF

    cat >"$tree/scripts/config" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail

[[ "${1:-}" == "--file" ]] || exit 2
config_file="$2"
operation="$3"
symbol="$4"
value="${5:-}"
temp_file="${config_file}.scripts-config"

awk -v symbol="$symbol" '
    $0 == "CONFIG_" symbol "=" substr($0, index($0, "=") + 1) { next }
    $0 == "# CONFIG_" symbol " is not set" { next }
    { print }
' "$config_file" >"$temp_file"

case "$operation" in
    --enable)
        printf 'CONFIG_%s=y\n' "$symbol" >>"$temp_file"
        ;;
    --disable)
        printf '# CONFIG_%s is not set\n' "$symbol" >>"$temp_file"
        ;;
    --set-val)
        printf 'CONFIG_%s=%s\n' "$symbol" "$value" >>"$temp_file"
        ;;
    *)
        exit 2
        ;;
esac

mv "$temp_file" "$config_file"
EOF
    chmod +x "$tree/scripts/config"

    mkdir -p "$tree/arch/x86" "$tree/drivers/cpufreq"
    cat >"$tree/arch/x86/Kconfig.cpu" <<'EOF'
config PROCESSOR_SELECT
	bool "Supported processor vendors" if EXPERT

config CPU_SUP_INTEL
	default y
	bool "Support Intel processors" if PROCESSOR_SELECT

config CPU_SUP_AMD
	default y
	bool "Support AMD processors" if PROCESSOR_SELECT
EOF
    cat >"$tree/drivers/cpufreq/Kconfig.x86" <<'EOF'
config X86_INTEL_PSTATE
	bool "Intel P state control"

config X86_AMD_PSTATE
	bool "AMD Processor P-State driver"

config X86_AMD_PSTATE_DEFAULT_MODE
	int "AMD Processor P-State default mode"
	depends on X86_AMD_PSTATE
EOF

    cat >"$tree/.config" <<'EOF'
CONFIG_X86=y
CONFIG_MMU=y
CONFIG_SMP=y
CONFIG_PREEMPT=y
CONFIG_PREEMPT_DYNAMIC=y
# CONFIG_PREEMPT_NONE is not set
# CONFIG_PREEMPT_VOLUNTARY is not set
# CONFIG_PREEMPT_LAZY is not set
# CONFIG_PREEMPT_RT is not set
# CONFIG_HZ_100 is not set
# CONFIG_HZ_250 is not set
# CONFIG_HZ_300 is not set
CONFIG_HZ_1000=y
CONFIG_HZ=1000
CONFIG_SCHED_CACHE=y
# CONFIG_LRU_GEN is not set
# CONFIG_LRU_GEN_ENABLED is not set
CONFIG_LRU_GEN_STATS=y
CONFIG_NUMA=y
# CONFIG_MIGRATION is not set
# CONFIG_NUMA_MIGRATION is not set
# CONFIG_NUMA_BALANCING is not set
# CONFIG_NUMA_BALANCING_DEFAULT_ENABLED is not set
CONFIG_ARCH_PKEY_BITS=4
# CONFIG_SCHED_MC is not set
# CONFIG_CPU_IDLE_GOV_TEO is not set
CONFIG_SLUB_TINY=y
CONFIG_ZSWAP=y
CONFIG_ZSWAP_COMPRESSOR_DEFAULT_LZO=y
# CONFIG_ZSWAP_COMPRESSOR_DEFAULT_LZ4 is not set
# CONFIG_ZSWAP_COMPRESSOR_DEFAULT_ZSTD is not set
CONFIG_VIRT_CPU_ACCOUNTING_GEN=y
# CONFIG_TICK_CPU_ACCOUNTING is not set
# CONFIG_NO_HZ_FULL is not set
# CONFIG_X86_NATIVE_CPU is not set
# CONFIG_MQ_IOSCHED_DEADLINE is not set
# CONFIG_IOSCHED_BFQ is not set
# CONFIG_TCP_CONG_BBR is not set
CONFIG_KFENCE=y
CONFIG_SLAB_FREELIST_RANDOM=y
CONFIG_FIREWIRE=y
CONFIG_FIREWIRE_OHCI=y
CONFIG_NET_SCH_FQ=m
CONFIG_EXPERT=y
# CONFIG_PROCESSOR_SELECT is not set
CONFIG_CPU_SUP_INTEL=y
CONFIG_CPU_SUP_AMD=y
# CONFIG_X86_INTEL_PSTATE is not set
CONFIG_X86_AMD_PSTATE=y
CONFIG_X86_AMD_PSTATE_DEFAULT_MODE=3
EOF
}

test_explicit_controls() {
    local tree="$TEST_TMP/explicit"
    local output="$TEST_TMP/explicit.out"
    create_fixture "$tree"

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --preempt-mode voluntary \
        --timer-hz 250 \
        --sched-cache-mode off \
        --mglru-mode on \
        --numa-balancing-mode on \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Kernel version: 7.2.0-test"
    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_PREEMPT_VOLUNTARY: n -> y"
    assert_contains "$output" "CONFIG_HZ: 1000 -> 250"
    assert_contains "$output" "CONFIG_SCHED_CACHE: y -> n"
    assert_contains "$output" "CONFIG_LRU_GEN: n -> y"
    assert_contains "$output" "CONFIG_LRU_GEN_STATS: y -> n"
    assert_contains "$output" "CONFIG_NUMA_MIGRATION: n -> y"
    assert_contains "$output" "CONFIG_NUMA_BALANCING: n -> y"
    grep -Fq 'CONFIG_SCHED_CACHE=y' "$tree/.config" || fail "dry-run modified the original config"
}

test_profile_auto_extensions() {
    local tree="$TEST_TMP/profile"
    local output="$TEST_TMP/profile.out"
    create_fixture "$tree"
    "$tree/scripts/config" --file "$tree/.config" --disable SCHED_CACHE

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --optimization-profile server \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "CONFIG_HZ_100: n -> y"
    assert_contains "$output" "CONFIG_PREEMPT_NONE: n -> y"
    assert_contains "$output" "CONFIG_SCHED_CACHE: n -> y"
    assert_contains "$output" "CONFIG_LRU_GEN_ENABLED: n -> y"
    assert_contains "$output" "CONFIG_SCHED_MC: n -> y"
    assert_contains "$output" "CONFIG_CPU_IDLE_GOV_TEO: n -> y"
    assert_contains "$output" "CONFIG_SLUB_TINY: y -> n"
    assert_contains "$output" "CONFIG_ZSWAP_COMPRESSOR_DEFAULT_ZSTD: n -> y"
    assert_contains "$output" "CONFIG_ZSWAP_COMPRESSOR_DEFAULT_LZO: y -> n"
    assert_contains "$output" "CONFIG_TICK_CPU_ACCOUNTING: n -> y"
    assert_contains "$output" "CONFIG_VIRT_CPU_ACCOUNTING_GEN: y -> n"
    assert_contains "$output" "CONFIG_MQ_IOSCHED_DEADLINE: n -> y"
    assert_contains "$output" "CONFIG_TCP_CONG_BBR: n -> y"
    if grep -Fq 'CONFIG_X86_NATIVE_CPU' "$output"; then fail "server profile must not touch X86_NATIVE_CPU"; fi
    if grep -Fq 'CONFIG_NET_SCH_FQ:' "$output"; then fail "server profile must keep NET_SCH_FQ=m"; fi
}

test_cpu_vendor_filter() {
    local tree="$TEST_TMP/vendor"
    local output="$TEST_TMP/vendor.out"
    create_fixture "$tree"

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --cpu-vendor-filter intel \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_PROCESSOR_SELECT: n -> y"
    assert_contains "$output" "CONFIG_CPU_SUP_AMD: y -> n"
    assert_contains "$output" "CONFIG_X86_AMD_PSTATE: y -> n"
    assert_contains "$output" "CONFIG_X86_INTEL_PSTATE: n -> y"
    if grep -Fq 'Disabling: CONFIG_X86_AMD_PSTATE_DEFAULT_MODE' "$output"; then fail "int symbols must not be pruned by the vendor filter"; fi

    # without EXPERT the CPU_SUP_* prompts do not exist; they must be left alone
    "$tree/scripts/config" --file "$tree/.config" --disable EXPERT
    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --cpu-vendor-filter intel \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_EXPERT is off"
    assert_contains "$output" "CONFIG_X86_INTEL_PSTATE: n -> y"
    if grep -Fq 'CONFIG_CPU_SUP_AMD:' "$output"; then fail "CPU_SUP_AMD must stay untouched without EXPERT"; fi
    if grep -Fq 'CONFIG_X86_AMD_PSTATE:' "$output"; then fail "X86_AMD_PSTATE must stay untouched without EXPERT"; fi
}

test_desktop_profile() {
    local tree="$TEST_TMP/desktop"
    local output="$TEST_TMP/desktop.out"
    create_fixture "$tree"

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --optimization-profile desktop \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_ZSWAP_COMPRESSOR_DEFAULT_LZ4: n -> y"
    assert_contains "$output" "CONFIG_IOSCHED_BFQ: n -> y"
    assert_contains "$output" "CONFIG_TICK_CPU_ACCOUNTING: n -> y"
    assert_contains "$output" "CONFIG_CPU_IDLE_GOV_TEO: n -> y"
}

test_native_cpu() {
    local tree="$TEST_TMP/native"
    local output="$TEST_TMP/native.out"
    create_fixture "$tree"

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --native-cpu on \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_X86_NATIVE_CPU: n -> y"

    # a tree without the symbol (kernel < 6.16) must report the explicit request
    sed -i '/CONFIG_X86_NATIVE_CPU/d' "$tree/.config"
    if "$SCRIPT" --dry-run \
        --validation-mode strict \
        --native-cpu on \
        "$tree" "$tree/.config" >"$output" 2>&1; then
        fail "NATIVE_CPU=on on a tree without X86_NATIVE_CPU unexpectedly succeeded"
    fi
    assert_contains "$output" "requires unavailable CONFIG_X86_NATIVE_CPU"
}

test_prune_gaps() {
    local tree="$TEST_TMP/prune"
    local output="$TEST_TMP/prune.out"
    create_fixture "$tree"

    "$SCRIPT" --dry-run \
        --validation-mode strict \
        --prune-hardening \
        --prune-debug-trace \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_SLAB_FREELIST_RANDOM: y -> n"
    assert_contains "$output" "CONFIG_KFENCE: y -> n"
}

test_missing_counts_as_disabled() {
    local tree="$TEST_TMP/missing"
    local output="$TEST_TMP/missing.out"
    create_fixture "$tree"

    # FIREWIRE_OHCI becomes invisible once FIREWIRE is off and vanishes from .config
    DROP_SYMBOLS=FIREWIRE_OHCI "$SCRIPT" --dry-run \
        --validation-mode strict \
        --prune-dma-attack-surface \
        "$tree" "$tree/.config" >"$output" 2>&1

    assert_contains "$output" "Validation passed:"
    assert_contains "$output" "CONFIG_FIREWIRE: y -> n"
    assert_contains "$output" "CONFIG_FIREWIRE_OHCI: y -> n"
}

test_strict_validation_failure() {
    local tree="$TEST_TMP/strict"
    local output="$TEST_TMP/strict.out"
    create_fixture "$tree"

    if FORCE_SCHED_CACHE=y "$SCRIPT" --dry-run \
        --validation-mode strict \
        --sched-cache-mode off \
        "$tree" "$tree/.config" >"$output" 2>&1; then
        fail "strict validation unexpectedly succeeded"
    fi

    assert_contains "$output" "CONFIG_SCHED_CACHE requested=n effective=y"
    assert_contains "$output" "strict validation failures"
}

test_invalid_value() {
    local tree="$TEST_TMP/invalid"
    local output="$TEST_TMP/invalid.out"
    create_fixture "$tree"

    if "$SCRIPT" --dry-run --timer-hz 500 "$tree" "$tree/.config" >"$output" 2>&1; then
        fail "invalid TIMER_HZ unexpectedly succeeded"
    fi

    assert_contains "$output" "Invalid TIMER_HZ"
}

test_explicit_controls
test_profile_auto_extensions
test_desktop_profile
test_cpu_vendor_filter
test_native_cpu
test_prune_gaps
test_missing_counts_as_disabled
test_strict_validation_failure
test_invalid_value

echo "PASS: performance controls"
