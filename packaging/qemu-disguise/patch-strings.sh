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

# --- FADT rev6 "Hypervisor Vendor Identity" ---
# build_fadt() hardcodes the 8-byte Hypervisor Vendor Identity field to "QEMU"
# (hw/acpi/aml-build.c). al-khaser's "ACPI table strings" scan reads the FADT
# and flags that literal even after the OEM ID is changed. Rewrite it to the
# host vendor (a non-VM string) so the FADT no longer announces a hypervisor.
HV_ID="$(printf '%s' "$ACPI_OEM6" | tr -d ' ' | cut -c1-8)"
[ -n "$HV_ID" ] || HV_ID="ALASKA"
sed -i "s/build_append_padded_str(tbl, \"QEMU\", 8/build_append_padded_str(tbl, \"${HV_ID}\", 8/" \
    hw/acpi/aml-build.c

# --- ACPI device _HID strings (QEMU* -> host vendor prefix) ---
# fw_cfg ("QEMU0002"), pvpanic ("QEMU0001") and vmgenid ("QEMUVGID") declare
# _HIDs that land verbatim in the DSDT; al-khaser's ACPI-table scan flags the
# "QEMU" prefix. The vmgenid _HID is also what Windows binds
# \Device\VmGenerationCounter to (a VM/Hyper-V tell), so renaming it drops that
# device too. Windows has no driver for fw_cfg / pvpanic, so renaming their
# _HIDs is cosmetic. Use a 4-char vendor prefix derived from the host (valid
# ACPI ID form: leading letter), falling back to a neutral non-VM default.
HID4="$(printf '%s' "$ACPI_OEM6" | tr -cd 'A-Za-z0-9' | tr 'a-z' 'A-Z' | cut -c1-4)"
case "$HID4" in [A-Z]???) : ;; *) HID4="ACPI" ;; esac
sed -i "s/aml_string(\"QEMU0002\")/aml_string(\"${HID4}0002\")/" \
    hw/nvram/fw_cfg-acpi.c hw/i386/fw_cfg.c
sed -i "s/aml_string(\"QEMU0001\")/aml_string(\"${HID4}0001\")/" hw/misc/pvpanic-isa.c
sed -i "s/aml_string(\"QEMUVGID\")/aml_string(\"${HID4}VGID\")/" hw/acpi/vmgenid.c

# --- WAET table signature ---
# QEMU emits a WAET ("Windows ACPI Emulated devices Table") -- a table only
# present under emulation, which al-khaser's ACPI check enumerates as a VM tell.
# Rename its 4-byte signature so it's no longer the recognised WAET (Windows
# ignores the now-unknown table; we lose only WAET's RTC/PM-timer read hint).
# Done via the signature, not by dropping the call -- that would leave
# build_waet() unused and fail QEMU's -Werror build.
sed -i 's/\.sig = "WAET"/.sig = "WAFT"/' hw/i386/acpi-build.c

# --- Disk / optical model strings ---
# ATA (ide-hd, used by DISK_TYPE=sata) + SCSI defaults report "QEMU HARDDISK"
# / "QEMU DVD-ROM"; al-khaser scans Disk\Enum + IDE/SCSI for "QEMU".
sed -i "s/\"QEMU HARDDISK\"/\"${DISK_MODEL}\"/g" hw/ide/core.c hw/scsi/scsi-disk.c
sed -i "s/\"QEMU DVD-ROM\"/\"${DVD_MODEL}\"/g" hw/ide/core.c hw/ide/atapi.c
sed -i "s/\"QEMU CD-ROM\"/\"${DVD_MODEL}\"/g" hw/scsi/scsi-disk.c

echo "winpodx: identity-string patch applied (ACPI OEM + FADT HV vendor + _HIDs + WAET + disk model)."
