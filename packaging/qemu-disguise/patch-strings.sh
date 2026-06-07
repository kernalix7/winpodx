#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# winpodx bare-metal disguise (#246) — QEMU source string patch.
#
# Rewrites the VM-identifying *strings* that winpodx CANNOT reach via QEMU
# command-line args, so the guest's ACPI OEM IDs and disk model stop reporting
# "BOCHS" / "QEMU HARDDISK". Run against an unpacked QEMU source tree before
# `configure && make`. It is string-replacement only (sed), so it is robust
# across QEMU minor versions — no fragile context-diff to re-port.
#
# DELIBERATELY NOT INCLUDED:
#   * PCI vendor IDs (0x1AF4 / 0x1B36 -> 0x8086). Spoofing these breaks the
#     Windows virtio driver binding — including dockur's own virtio-serial
#     channel — so the guest (and dockur) fail to boot. Leave them alone.
#   * SMBIOS / CPUID. winpodx already handles those via `-smbios` args and
#     `-cpu ...,kvm=off`, per-VM, no recompile.
#
# This is signature-level VM hiding for VM-hostile apps / malware-analysis
# sandboxes. It does NOT defeat kernel anti-cheat (TPM attestation + RDTSC
# timing are untouched) and is not intended to.
set -euo pipefail

SRC="${1:-.}"
cd "$SRC"

echo "winpodx: patching QEMU identity strings in $(pwd)"

# --- ACPI OEM ID (6 bytes) + OEM Table ID (8 bytes) ---
# QEMU defaults: ACPI_BUILD_APPNAME6 "BOCHS ", ACPI_BUILD_APPNAME8 "BXPC    ".
# al-khaser flags "BOCHS". Replace with a common real BIOS OEM (AMI). Lengths
# MUST stay 6 and 8 bytes respectively.
sed -i 's/"BOCHS "/"ALASKA"/g' include/hw/acpi/aml-build.h
sed -i 's/"BXPC    "/"A M I   "/g' include/hw/acpi/aml-build.h

# --- Disk / optical model strings ---
# ATA (ide-hd, used by DISK_TYPE=sata) + SCSI defaults report "QEMU HARDDISK"
# / "QEMU DVD-ROM"; al-khaser scans Disk\Enum + IDE/SCSI for "QEMU".
sed -i 's/"QEMU HARDDISK"/"Samsung SSD 860"/g' hw/ide/core.c hw/scsi/scsi-disk.c
sed -i 's/"QEMU DVD-ROM"/"ASUS  DRW-24"/g' hw/ide/core.c hw/ide/atapi.c
sed -i 's/"QEMU CD-ROM"/"ASUS  DRW-24"/g' hw/scsi/scsi-disk.c

echo "winpodx: identity-string patch applied (ACPI OEM + disk model)."
