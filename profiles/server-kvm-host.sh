#!/usr/bin/env bash
# Reusable bare-metal KVM/Docker server example: server scheduling profile, host
# virtualization and container capabilities, hardware filters resolved on the
# target host, and the pruning that is safe on any production server.
# Defaults to a preview. Pass --dry-run=false to save to the specified config.
# All trailing kernel-config.sh options override these defaults.
#
# Left to the caller because they depend on the target: the firewall backend
# (`firehol` or `firewalld`), file servers (`nfs-server`, `nfs-client`, `samba`),
# `cryptsetup`, VPNs, the vendor/UEFI/NUMA auto-detection results (run it on the
# target, or pass explicit values), NR_CPUS, the host-specific pruning
# (`--prune-legacy`, `--prune-old-hw`, `--prune-compat32`, `--prune-insecure`),
# and `--initrd-compression=auto` on hosts with an installed initramfs generator
# (it errors when no generator or image can be inspected).
#
# Protected on purpose: the server profile enables RCU_NOCB_CPU_DEFAULT_ALL with
# NO_HZ_FULL, which offloads RCU callbacks on every CPU (a cost on a VM host that
# never sets nohz_full=), and the dangerous/insecure pruning removes MAGIC_SYSRQ,
# which a remote server should keep for emergencies over the console.
set -Eeuo pipefail
project_dir="$(dirname -- "$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")")"

exec "$project_dir/kernel-config.sh" \
    --dry-run --strict \
    --optimization-profile=server --all-optimizations \
    --host-type=baremetal --cpu-vendor-filter=auto \
    --uefi-support=auto --tpm-support=auto --dma-engine-support=auto \
    --iommu-support=auto --numa-support=auto \
    --applications=qemu,docker \
    --prune-selftest --prune-sanitizers --prune-coverage \
    --prune-fault-injection --prune-dangerous --prune-radios \
    --module-force-load=off --module-force-unload=off \
    --protected-config-symbols=CONFIG_MAGIC_SYSRQ,CONFIG_RCU_NOCB_CPU_DEFAULT_ALL \
    "$@"
