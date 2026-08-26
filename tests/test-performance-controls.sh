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
test_strict_validation_failure
test_invalid_value

echo "PASS: performance controls"
