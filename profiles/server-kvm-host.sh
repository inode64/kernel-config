#!/usr/bin/env bash
# Reusable bare-metal KVM/Docker server example: server scheduling profile, host
# virtualization and container capabilities, hardware filters resolved on the
# target host, and the --all-optimizations pruning set (every toggle except
# hardening and unused-module probing; turn one off again with, for example,
# --prune-compat32=false).
# Defaults to a preview. Pass --dry-run=false to save to the specified config.
# All trailing kernel-config.sh options override these defaults.
#
# Left to the caller because they depend on the target: the firewall backend
# (`firehol` or `firewalld`, plus `iptables-legacy` when Docker runs on
# iptables-legacy), file servers (`nfs-server`, `nfs-client`, `samba`),
# `cryptsetup`, VPNs, the vendor/UEFI/NUMA auto-detection results (run it on the
# target, or pass explicit values), NR_CPUS, and `--initrd-compression=auto` on
# hosts with an installed initramfs generator (it errors when no generator or
# image can be inspected).
#
# Set explicitly, so a baseline left by an earlier run converges too. An older
# server profile enabled RCU_NOCB_CPU_DEFAULT_ALL, which offloads RCU callbacks
# on every CPU (a kthread per CPU, and a higher call_rcu cost, on a host that
# never sets nohz_full=); the profile now disables it. The dangerous/insecure
# pruning removes MAGIC_SYSRQ, which a remote server should keep for emergencies
# over the console (MAGIC_SYSRQ_SERIAL stays off). Protecting them instead would
# keep whatever the baseline had. A trailing --enable-symbols or --disable-symbols
# replaces these lists, so repeat their entries when passing your own. The
# profile still turns the offload off if that entry is dropped.
set -Eeuo pipefail
project_dir="$(dirname -- "$(dirname -- "$(realpath -- "${BASH_SOURCE[0]}")")")"

exec "$project_dir/kernel-config.sh" \
    --dry-run --strict \
    --optimization-profile=server --all-optimizations \
    --host-type=baremetal --cpu-vendor-filter=auto \
    --uefi-support=auto --tpm-support=auto --dma-engine-support=auto \
    --iommu-support=auto --numa-support=auto \
    --applications=qemu,docker \
    --module-force-load=off --module-force-unload=off \
    --enable-symbols=MAGIC_SYSRQ \
    --disable-symbols=MAGIC_SYSRQ_SERIAL,RCU_NOCB_CPU_DEFAULT_ALL \
    "$@"
