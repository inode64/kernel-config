#!/usr/bin/env bash
# Reusable desktop example: scheduling and desktop/multimedia capabilities.
# Defaults to a preview. Pass --dry-run=false to save to the specified config.
# All trailing kernel-config.sh options override these defaults.
set -Eeuo pipefail
project_dir="$(dirname -- "$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")")"

# Hardware selection, CPU count, boot/storage support and additional application
# profiles belong to the caller's target configuration, not the test machine.
exec "$project_dir/kernel-config.sh" \
    --dry-run --strict \
    --optimization-profile=desktop \
    --applications=desktop,multimedia \
    "$@"
