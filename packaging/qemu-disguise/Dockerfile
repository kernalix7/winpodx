# SPDX-License-Identifier: MIT
#
# winpodx bare-metal disguise (#246) — custom dockur image with an
# anti-detection-patched QEMU.
#
# Builds QEMU from source with winpodx's identity-string patch (ACPI OEM + disk
# model) applied, then layers it over the pinned dockur image so dockur runs
# the patched qemu-system-x86_64. winpodx never ships this image — you build it
# yourself and point cfg.pod.disguise_image at your local/registry tag.
#
# Build:
#   docker build -t winpodx-windows-disguise \
#     --build-arg DOCKUR_IMAGE=docker.io/dockurr/windows@sha256:<pin> \
#     --build-arg QEMU_VERSION=<the version inside that image> \
#     -f packaging/qemu-disguise/Dockerfile packaging/qemu-disguise
#
# Find the QEMU version inside your pinned image first:
#   podman run --rm --entrypoint qemu-system-x86_64 <DOCKUR_IMAGE> --version
#
# Then:
#   winpodx config set pod.disguise_level max
#   winpodx config set pod.disguise_image winpodx-windows-disguise
#   winpodx pod recreate --wipe-storage     # device + firmware change → reinstall
#
# NOTE: matching the build environment (Debian release, configure flags, shared
# libs) to the dockur base is the fiddly part — iterate until the patched
# binary runs in the final image. dockur's base is Debian (glibc), so the build
# stage is Debian too; an Alpine/musl QEMU would not run in the image.

ARG DOCKUR_IMAGE=docker.io/dockurr/windows:latest
ARG QEMU_VERSION=10.0.8

# --- stage 1: build patched QEMU in Debian (dockur's base is Debian/glibc, so
# the build MUST be glibc too — an Alpine/musl binary won't run in the image) ---
FROM ${DOCKUR_IMAGE} AS base

FROM debian:trixie-slim AS build
ARG QEMU_VERSION
# Build deps notes:
#  * python3-venv (+ pip): QEMU's ./configure builds a private Python venv for
#    meson, which needs ensurepip -- Debian ships that in python3-venv, not the
#    base python3 package ("Ouch! Python's ensurepip module is not found").
#  * libfdt-dev: gives meson a system libfdt so it doesn't try to download the
#    dtc wrap subproject; git is the fallback fetch path for any other wrap.
#  * libaio-dev + liburing-dev: dockur drives the data disk with `aio=native`
#    (Linux AIO), and winpodx's tuning can request `aio=io_uring`. QEMU
#    auto-enables those backends only when their dev libs are present, so
#    without these the binary boot-loops on "aio=native ... not supported in
#    this build". Their runtime libs are installed into the final image below.
#  * acpica-tools: `iasl` compiles the synthetic-sensor SSDT (ssdt-sensors.asl)
#    that winpodx injects via -acpitable so the guest exposes a thermal zone.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ninja-build meson python3 python3-venv python3-pip \
        git pkg-config flex bison acpica-tools \
        libglib2.0-dev libpixman-1-dev libfdt-dev zlib1g-dev libslirp-dev \
        libaio-dev liburing-dev \
        libusb-1.0-0-dev libusbredirparser-dev libspice-server-dev \
        libcap-ng-dev libattr1-dev curl xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL "https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz" -o qemu.tar.xz \
    && tar xf qemu.tar.xz && mv "qemu-${QEMU_VERSION}" qemu
COPY patch-strings.sh /build/patch-strings.sh
# Host-derived identity strings (passed by `winpodx disguise build-image`);
# patch-strings.sh falls back to generic-real defaults when unset.
ARG ACPI_OEM6=""
ARG ACPI_OEM8=""
ARG DISK_MODEL=""
ARG DVD_MODEL=""
RUN ACPI_OEM6="${ACPI_OEM6}" ACPI_OEM8="${ACPI_OEM8}" \
    DISK_MODEL="${DISK_MODEL}" DVD_MODEL="${DVD_MODEL}" \
    bash /build/patch-strings.sh /build/qemu
# Synthetic ACPI tables injected at runtime via -acpitable (no guest recompile):
#   * ssdt-sensors -- thermal zone + fixed-feature PnP devices al-khaser wants
#   * wsmt         -- the Windows SMM Security Mitigations Table QEMU omits
# Substitute the host vendor as their OEM ID (6 bytes, consistent with the
# patched ACPI tables) and compile both with iasl.
COPY ssdt-sensors.asl /build/ssdt-sensors.asl
COPY wsmt.asl /build/wsmt.asl
RUN OEM6="$(printf '%-6.6s' "${ACPI_OEM6:-ALASKA}")"; \
    [ -n "$(printf '%s' "$OEM6" | tr -d ' ')" ] || OEM6="ALASKA"; \
    sed -i "s/\"ALASKA\"/\"${OEM6}\"/" /build/ssdt-sensors.asl /build/wsmt.asl \
    && iasl -p /build/ssdt-sensors /build/ssdt-sensors.asl \
    && iasl /build/wsmt.asl \
    && test -f /build/ssdt-sensors.aml -a -f /build/wsmt.aml
WORKDIR /build/qemu
# Build only the x86_64 system target. The --enable set mirrors what dockur's
# Debian QEMU package links (slirp networking + usb-redir + spice) so the
# patched binary is a drop-in replacement in stage 2.
RUN ./configure --target-list=x86_64-softmmu --prefix=/usr \
        --enable-slirp --enable-usb-redir --enable-spice --disable-docs \
    && make -j"$(nproc)" \
    && strip build/qemu-system-x86_64

# --- stage 2: drop the patched binary into the pinned dockur image ---
FROM base
# The patched binary links a few shared libs (spice / usb-redir / slirp) that the
# pinned dockur image may not ship, so it can't even run --version without them
# ("error while loading shared libraries: libspice-server.so.1"). Install their
# runtime packages (dockur's base is Debian -> apt is available). Harmless if
# dockur never passes the matching args; required for the binary to load.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libspice-server1 libusbredirparser1 libslirp0 libaio1t64 liburing2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /build/qemu/build/qemu-system-x86_64 /usr/bin/qemu-system-x86_64
# Ship the matching firmware/ROM blobs into the data dir our binary searches
# (--prefix=/usr -> /usr/share/qemu). The dockur image's stock ROMs live under a
# different prefix, so without this the binary boot-loops on
# "failed to find romfile vgabios-stdvga.bin" (max mode uses std VGA).
COPY --from=build /build/qemu/pc-bios/ /usr/share/qemu/
# Synthetic-sensor SSDT, injected by winpodx via -acpitable at disguise max.
COPY --from=build /build/ssdt-sensors.aml /usr/share/qemu/winpodx-ssdt-sensors.aml
COPY --from=build /build/wsmt.aml /usr/share/qemu/winpodx-wsmt.aml
# Sanity check at build time (fails the build if the binary won't run).
RUN qemu-system-x86_64 --version
