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
# The replacement values come from the HOST (so the guest reports your real
# machine, not a fixed brand) via these env vars, set by `winpodx disguise
# build-image`; each falls back to a generic-but-real default when the host
# value is unavailable:
#   ACPI_OEM6   6-byte ACPI OEM ID            (host bios/sys vendor)   ["ALASKA"]
#   ACPI_OEM8   8-byte ACPI OEM table ID      (host vendor, padded)    ["A M I   "]
#   DISK_MODEL  ATA/SCSI disk model string    (host /sys block model)  ["Samsung SSD 860"]
#   DVD_MODEL   optical model string                                   ["ASUS  DRW-24"]
#
# DELIBERATELY NOT INCLUDED: PCI vendor IDs (0x1AF4/0x1B36 -> 0x8086). Spoofing
# those breaks the Windows virtio driver binding — including dockur's own
# virtio-serial channel — so the guest won't boot. SMBIOS / CPUID are handled
# by winpodx via `-smbios` args + `-cpu kvm=off` (per-VM, no recompile).
#
# Signature-level VM hiding for VM-hostile apps / malware-analysis sandboxes.
# It does NOT defeat kernel anti-cheat (TPM attestation + RDTSC timing untouched).
set -euo pipefail

SRC="${1:-.}"
cd "$SRC"

# Host-derived replacements (build-arg → env), with generic-real fallbacks.
ACPI_OEM6="${ACPI_OEM6:-ALASKA}"
ACPI_OEM8="${ACPI_OEM8:-A M I   }"
DISK_MODEL="${DISK_MODEL:-Samsung SSD 860}"
DVD_MODEL="${DVD_MODEL:-ASUS  DRW-24}"

# ACPI OEM ID is a fixed 6 bytes, OEM Table ID a fixed 8 bytes — pad/trim so the
# struct layout stays valid regardless of the host string's length.
fit() { printf '%-*.*s' "$2" "$2" "$1"; }
ACPI_OEM6="$(fit "$ACPI_OEM6" 6)"
ACPI_OEM8="$(fit "$ACPI_OEM8" 8)"

echo "winpodx: patching QEMU identity strings in $(pwd)"
echo "  ACPI OEM6='${ACPI_OEM6}' OEM8='${ACPI_OEM8}' DISK='${DISK_MODEL}' DVD='${DVD_MODEL}'"

# --- ACPI OEM ID (6 bytes) + OEM Table ID (8 bytes) ---
# QEMU defaults: ACPI_BUILD_APPNAME6 "BOCHS ", ACPI_BUILD_APPNAME8 "BXPC    ".
sed -i "s/\"BOCHS \"/\"${ACPI_OEM6}\"/g" include/hw/acpi/aml-build.h
sed -i "s/\"BXPC    \"/\"${ACPI_OEM8}\"/g" include/hw/acpi/aml-build.h

# --- Disk / optical model strings ---
# ATA (ide-hd, used by DISK_TYPE=sata) + SCSI defaults report "QEMU HARDDISK"
# / "QEMU DVD-ROM"; al-khaser scans Disk\Enum + IDE/SCSI for "QEMU".
sed -i "s/\"QEMU HARDDISK\"/\"${DISK_MODEL}\"/g" hw/ide/core.c hw/scsi/scsi-disk.c
sed -i "s/\"QEMU DVD-ROM\"/\"${DVD_MODEL}\"/g" hw/ide/core.c hw/ide/atapi.c
sed -i "s/\"QEMU CD-ROM\"/\"${DVD_MODEL}\"/g" hw/scsi/scsi-disk.c

echo "winpodx: identity-string patch applied (ACPI OEM + disk model)."
