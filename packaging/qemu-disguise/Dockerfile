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
# NOTE: matching the build environment (Alpine version, configure flags, shared
# libs) to the dockur base is the fiddly part — iterate until the patched
# binary runs in the final image. This recipe targets dockur's Alpine base.

ARG DOCKUR_IMAGE=docker.io/dockurr/windows:latest
ARG QEMU_VERSION=10.0.8

# --- stage 1: build patched QEMU in the same Alpine base dockur uses ---
FROM ${DOCKUR_IMAGE} AS base

FROM alpine:3.21 AS build
ARG QEMU_VERSION
RUN apk add --no-cache \
        build-base ninja meson python3 bash pkgconf glib-dev pixman-dev \
        zlib-dev curl tar xz linux-headers bzip2-dev \
        libslirp-dev libusb-dev usbredir-dev spice-dev \
        libcap-ng-dev attr-dev
WORKDIR /build
RUN curl -fsSL "https://download.qemu.org/qemu-${QEMU_VERSION}.tar.xz" -o qemu.tar.xz \
    && tar xf qemu.tar.xz && mv "qemu-${QEMU_VERSION}" qemu
COPY patch-strings.sh /build/patch-strings.sh
RUN bash /build/patch-strings.sh /build/qemu
WORKDIR /build/qemu
# Build only the x86_64 system target. Add/trim --enable flags to match what
# your dockur image's QEMU was built with (check its `qemu-system-x86_64
# --version` / configure summary) so the binary links cleanly in stage 2.
RUN ./configure --target-list=x86_64-softmmu --prefix=/usr \
        --enable-slirp --enable-usb-redir --disable-docs --disable-gtk \
    && make -j"$(nproc)" \
    && strip build/qemu-system-x86_64

# --- stage 2: drop the patched binary into the pinned dockur image ---
FROM base
COPY --from=build /build/qemu/build/qemu-system-x86_64 /usr/bin/qemu-system-x86_64
# Sanity check at build time (fails the build if the binary won't run).
RUN qemu-system-x86_64 --version
