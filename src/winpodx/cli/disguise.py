# SPDX-License-Identifier: MIT
"""CLI for the bare-metal disguise's optional patched-QEMU image (#246).

``winpodx disguise build-image`` builds a custom dockur image whose QEMU has the
ACPI OEM + disk-model strings patched to the HOST's real values (so the guest
reports your machine, not "BOCHS" / "QEMU HARDDISK"), then points
``cfg.pod.disguise_image`` at it. The build runs locally — winpodx ships only
the recipe (packaging/qemu-disguise/), never a patched binary, and the host
values are read at build time into your local image, never committed.
"""

from __future__ import annotations

import argparse
import glob
import re
import subprocess
import sys
from pathlib import Path

from winpodx.core.i18n import tr

_DISGUISE_TAG = "winpodx-windows-disguise"


def handle_disguise(args: argparse.Namespace) -> None:
    """Route ``winpodx disguise`` subcommands."""
    if getattr(args, "disguise_command", None) == "build-image":
        _build_image(args)
    else:
        print(tr("Usage: winpodx disguise build-image"))
        sys.exit(1)


def _recipe_dir() -> Path | None:
    """Locate packaging/qemu-disguise (bundle install or source checkout)."""
    from winpodx.utils.paths import bundle_dir

    candidates = [bundle_dir() / "packaging" / "qemu-disguise"]
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "packaging" / "qemu-disguise")
    for c in candidates:
        if (c / "Dockerfile").is_file():
            return c
    return None


def _host_dmi(name: str) -> str:
    try:
        return (Path("/sys/class/dmi/id") / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _host_disk_model() -> str:
    """Real disk model from /sys/block (world-readable), skipping virtual/QEMU."""
    for model_path in sorted(glob.glob("/sys/block/*/device/model")):
        dev = model_path.split("/")[3]
        if dev.startswith(("loop", "zram", "dm-", "sr", "md")):
            continue
        try:
            model = Path(model_path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if model and "QEMU" not in model.upper():
            return model
    return ""


def _qemu_version(backend: str, image: str) -> str:
    """Read the QEMU version inside the pinned dockur image (best-effort)."""
    try:
        out = subprocess.run(
            [backend, "run", "--rm", "--entrypoint", "qemu-system-x86_64", image, "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"version\s+(\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def _build_image(args: argparse.Namespace) -> None:
    from winpodx.core.config import DOCKUR_IMAGE_PIN, Config

    recipe = _recipe_dir()
    if recipe is None:
        print(tr("Build recipe not found (packaging/qemu-disguise). Use a source checkout."))
        sys.exit(1)

    cfg = Config.load()
    backend = cfg.pod.backend if cfg.pod.backend in ("podman", "docker") else "podman"
    pin = cfg.pod.image or DOCKUR_IMAGE_PIN

    qver = _qemu_version(backend, pin)
    if not qver:
        print(
            tr("Could not detect the QEMU version in {image}; defaulting to 10.0.8.").format(
                image=pin
            )
        )
        qver = "10.0.8"

    vendor = _host_dmi("sys_vendor") or _host_dmi("bios_vendor")
    disk = _host_disk_model()

    build_args = [
        "--build-arg",
        f"DOCKUR_IMAGE={pin}",
        "--build-arg",
        f"QEMU_VERSION={qver}",
    ]
    if vendor:
        build_args += ["--build-arg", f"ACPI_OEM6={vendor}", "--build-arg", f"ACPI_OEM8={vendor}"]
    if disk:
        build_args += ["--build-arg", f"DISK_MODEL={disk}"]

    cmd = [
        backend,
        "build",
        "-t",
        _DISGUISE_TAG,
        *build_args,
        "-f",
        str(recipe / "Dockerfile"),
        str(recipe),
    ]
    print(
        tr(
            "Building {tag} from {image} (QEMU {ver}) — this compiles QEMU and can "
            "take 20-40 minutes.\n  ACPI OEM / disk model: {vendor} / {disk}"
        ).format(
            tag=_DISGUISE_TAG,
            image=pin,
            ver=qver,
            vendor=vendor or "(default)",
            disk=disk or "(default)",
        )
    )
    rc = subprocess.call(cmd)
    if rc != 0:
        print(tr("Build failed (exit {rc}). See the output above.").format(rc=rc))
        sys.exit(1)

    cfg.pod.disguise_image = _DISGUISE_TAG
    cfg.save()
    print(
        tr(
            "Built {tag} and set pod.disguise_image. To use it:\n"
            "  winpodx config set pod.disguise_level max\n"
            "  winpodx pod recreate --wipe-storage"
        ).format(tag=_DISGUISE_TAG)
    )
