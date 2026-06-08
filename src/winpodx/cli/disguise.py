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


def disguise_image_present(cfg) -> bool:  # type: ignore[no-untyped-def]
    """True if the patched-QEMU disguise image already exists locally.

    Lets callers (e.g. the GUI switching to hardened mode) decide whether a
    build is needed without rebuilding the ~20-40 min image every time.
    """
    backend = cfg.pod.backend if cfg.pod.backend in ("podman", "docker") else "podman"
    try:
        return (
            subprocess.run(
                [backend, "image", "inspect", _DISGUISE_TAG],
                capture_output=True,
                text=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def build_disguise_image(cfg, *, on_line=None, should_cancel=None) -> bool:  # type: ignore[no-untyped-def]
    """Build the patched-QEMU disguise image locally. Returns True on success.

    Compiles QEMU with the ACPI OEM + disk-model strings patched to the HOST's
    real values (~20-40 min). Streams build output line-by-line to ``on_line``
    (a callable taking one str) and aborts if ``should_cancel`` returns True.
    On success sets ``cfg.pod.disguise_image`` + saves. Never raises -- returns
    False on any failure so callers can fall back.

    LOCAL-ONLY: winpodx ships the recipe (packaging/qemu-disguise/), never a
    patched binary, and the host values are read into the local image, never
    committed -- so there is no GPL-binary redistribution and no host identity
    in git.
    """
    from winpodx.core.config import DOCKUR_IMAGE_PIN

    def _emit(line: str) -> None:
        if on_line is not None:
            try:
                on_line(line)
            except Exception:  # noqa: BLE001
                pass

    recipe = _recipe_dir()
    if recipe is None:
        _emit("disguise build: recipe not found (packaging/qemu-disguise); need a source checkout")
        return False

    backend = cfg.pod.backend if cfg.pod.backend in ("podman", "docker") else "podman"
    pin = cfg.pod.image or DOCKUR_IMAGE_PIN
    qver = _qemu_version(backend, pin) or "10.0.8"
    vendor = _host_dmi("sys_vendor") or _host_dmi("bios_vendor")
    disk = _host_disk_model()

    build_args = ["--build-arg", f"DOCKUR_IMAGE={pin}", "--build-arg", f"QEMU_VERSION={qver}"]
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
    _emit(
        f"Building {_DISGUISE_TAG} from {pin} (QEMU {qver}); "
        f"ACPI/disk = {vendor or '(default)'} / {disk or '(default)'}; compiling, ~20-40 min..."
    )
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _emit(f"disguise build: failed to start ({exc})")
        return False

    if proc.stdout is not None:
        for line in proc.stdout:
            _emit(line.rstrip())
            if should_cancel is not None and should_cancel():
                proc.terminate()
                _emit("disguise build: cancelled")
                return False
    rc = proc.wait()
    if rc != 0:
        _emit(f"disguise build: FAILED (exit {rc})")
        return False

    cfg.pod.disguise_image = _DISGUISE_TAG
    cfg.save()
    _emit(f"disguise build: done -> {_DISGUISE_TAG}")
    return True


def _build_image(args: argparse.Namespace) -> None:
    from winpodx.core.config import Config

    cfg = Config.load()
    if not build_disguise_image(cfg, on_line=print):
        print(tr("Build failed. See the output above."))
        sys.exit(1)
    print(
        tr(
            "Built {tag} and set pod.disguise_image. To use it:\n"
            "  winpodx config set pod.disguise_level max\n"
            "  winpodx pod recreate --wipe-storage"
        ).format(tag=_DISGUISE_TAG)
    )
