# Bare-metal disguise — patched-QEMU image (advanced, opt-in) (#246)

`balanced`/`max` hide most VM signatures through QEMU **command-line args**
(no recompile): the hypervisor CPUID bit, the KVM signature, the SMBIOS
vendor/model (mirrored from your real host), synthetic sensors, and — at `max`
— emulated SATA/std-VGA devices.

A few VM markers live in QEMU's compiled-in **strings**, which args can't reach:

| al-khaser check | string | where |
|-----------------|--------|-------|
| ACPI table strings / "QEMU ACPI tables" | ACPI OEM ID `BOCHS` / `BXPC` | `include/hw/acpi/aml-build.h` |
| SetupDi / Disk\Enum / Enum\IDE·SCSI | disk model `QEMU HARDDISK` | `hw/ide/core.c`, `hw/scsi/scsi-disk.c` |

To clear those you need a QEMU built with those strings changed. This directory
builds a custom dockur image that does exactly that, and winpodx uses it when
you opt in.

## What this does NOT do

- **PCI vendor IDs (`0x1AF4` / `0x1B36`) are left untouched.** Spoofing them to
  Intel breaks the Windows virtio driver binding — including dockur's own
  **virtio-serial** channel — so the guest and dockur fail to boot. So
  `VEN_1AF4` / `VEN_1B36` stay visible. This is a hard dockur constraint.
- It is **not an anti-cheat bypass.** Kernel anti-cheat (EAC/BattlEye/Vanguard)
  anchors on TPM attestation + RDTSC VM-exit timing, neither of which this
  touches. Bypassing online-game anti-cheat also violates game ToS. This is
  signature-level hiding for VM-hostile apps and malware-analysis sandboxes.
- winpodx **never ships a patched binary** — you build the image locally, so
  there's no GPL-binary redistribution and nothing for AV to flag in winpodx's
  own packages.

## Build (one command)

```bash
winpodx disguise build-image
```

This detects the QEMU version inside your pinned dockur image, reads your
**host's real ACPI OEM (vendor) + disk model** (world-readable `/sys` —
no root, nothing committed), builds the patched image locally, and points
`cfg.pod.disguise_image` at it. The compile takes ~20–40 minutes. The image
lives only in your local podman/docker store; git/winpodx never see a patched
binary.

Then enable it:

```bash
winpodx config set pod.disguise_level max
winpodx pod recreate --wipe-storage   # device + firmware change → reinstall
```

`disguise_image` is only honoured at `disguise_level = max`. Re-run
`winpodx disguise build-image` whenever you bump the dockur pin (the QEMU
version changes).

### Manual build (if you want to tweak flags)

The build stage is **Debian** (dockur's base is Debian/glibc — an Alpine/musl
QEMU won't run in the image). To override the baked strings:

```bash
podman build -t winpodx-windows-disguise \
  --build-arg DOCKUR_IMAGE=docker.io/dockurr/windows:latest \
  --build-arg QEMU_VERSION=10.0.8 \
  --build-arg ACPI_OEM6=ACME --build-arg DISK_MODEL='ACME SSD 1TB' \
  -f packaging/qemu-disguise/Dockerfile packaging/qemu-disguise
```

If `qemu-system-x86_64 --version` fails in stage 2, adjust the `./configure`
`--enable-*` flags to match the image's QEMU build, and rebuild.

## The patch

`patch-strings.sh` is plain `sed` string-replacement (robust across QEMU minor
versions). Read it — it's short. Tweak the replacement model/OEM strings to
taste (keep ACPI OEM lengths at 6 and 8 bytes).
