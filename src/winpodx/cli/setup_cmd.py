"""Interactive setup wizard — no external dependencies."""

from __future__ import annotations

import argparse
import secrets
import shutil
import string
from pathlib import Path

from winpodx.core.config import Config
from winpodx.utils.compat import import_winapps_config
from winpodx.utils.deps import check_all
from winpodx.utils.paths import config_dir

COMPOSE_TEMPLATE = """\
name: "winpodx"
volumes:
  winpodx-data:
services:
  windows:
    image: ghcr.io/dockur/windows:latest
    container_name: winpodx-windows
    environment:
      VERSION: "{win_version}"
      RAM_SIZE: "{ram}G"
      CPU_CORES: "{cpu}"
      DISK_SIZE: "64G"
      USERNAME: "{user}"
      PASSWORD: "{password}"
      HOME: "{home}"
      LANGUAGE: "English"
      REGION: "en-001"
      KEYBOARD: "en-US"
      ARGUMENTS: "-cpu host,arch_capabilities=off"
      NETWORK: "slirp"
    volumes:
      - winpodx-data:/storage:Z
      - {oem_dir}:/oem:Z
    ports:
      - "127.0.0.1:{rdp_port}:3389/tcp"
      - "127.0.0.1:{rdp_port}:3389/udp"
      - "127.0.0.1:{vnc_port}:8006"
    devices:
      - /dev/kvm
      - /dev/net/tun
    cap_add:
      - NET_ADMIN
    group_add:
      - keep-groups
    annotations:
      run.oci.keep_original_groups: "1"
    stop_grace_period: 2m
    restart: unless-stopped
"""


def _generate_password(length: int = 20) -> str:
    """Generate a cryptographically secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    # Ensure at least one of each required type
    pw = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&*"),
    ]
    pw += [secrets.choice(alphabet) for _ in range(length - 4)]
    # Shuffle to avoid predictable positions
    result = list(pw)
    secrets.SystemRandom().shuffle(result)
    return "".join(result)


def handle_setup(args: argparse.Namespace) -> None:
    """Run the setup wizard."""
    backend = args.backend
    non_interactive = args.non_interactive

    print("=== winpodx setup ===\n")

    # Check dependencies
    print("Checking dependencies...")
    deps = check_all()
    for name, dep in deps.items():
        status = "OK" if dep.found else "MISSING"
        print(f"  {name:<15} [{status}] {dep.note}")

    if not deps["freerdp"].found:
        print("\nFreeRDP 3+ is required. Install it and try again.")
        raise SystemExit(1)

    kvm_ok = Path("/dev/kvm").exists()
    kvm_status = "OK" if kvm_ok else "MISSING"
    print(f"  {'kvm':<15} [{kvm_status}] Hardware virtualization")
    print()

    # Import existing winapps config
    existing = import_winapps_config()
    if existing and not non_interactive:
        answer = input("Found existing winapps.conf. Import settings? (Y/n): ").strip().lower()
        if answer in ("", "y", "yes"):
            existing.save()
            print(f"Config saved to {Config.path()}")
            return

    # Reuse existing config if present (avoid overwriting passwords/compose)
    if Config.path().exists():
        cfg = Config.load()
        if non_interactive:
            print(f"Existing config found at {Config.path()}, skipping setup.")
            return
    else:
        cfg = Config()

    # Backend selection
    if backend:
        cfg.pod.backend = backend
    elif non_interactive:
        cfg.pod.backend = "podman" if shutil.which("podman") else "docker"
    else:
        available = []
        if deps.get("podman") and deps["podman"].found:
            available.append("podman")
        if deps.get("docker") and deps["docker"].found:
            available.append("docker")
        if deps.get("virsh") and deps["virsh"].found:
            available.append("libvirt")
        available.append("manual")

        if len(available) == 1 and available[0] == "manual":
            print("No container/VM backends found. Install podman or docker.")

        print(f"Available backends: {', '.join(available)}")
        choice = input(f"Select backend [{available[0]}]: ").strip() or available[0]
        if choice in available:
            cfg.pod.backend = choice
        else:
            print(f"Invalid choice: {choice}")
            raise SystemExit(1)

    # Credentials
    from datetime import datetime, timezone

    if non_interactive:
        cfg.rdp.user = "User"
        cfg.rdp.password = _generate_password()
        cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
        cfg.rdp.ip = "127.0.0.1"
    else:
        cfg.rdp.user = input("Windows username [User]: ").strip() or "User"
        import getpass

        cfg.rdp.password = (
            getpass.getpass("Windows password (Enter for random): ") or _generate_password()
        )
        cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
        if cfg.pod.backend == "manual":
            cfg.rdp.ip = input("Windows IP address: ").strip()
        else:
            cfg.rdp.ip = input("Windows IP [127.0.0.1]: ").strip() or "127.0.0.1"

    # Resource allocation
    if cfg.pod.backend in ("podman", "docker"):
        if not non_interactive:
            cpu_input = input("CPU cores [4]: ").strip()
            try:
                cfg.pod.cpu_cores = int(cpu_input) if cpu_input else 4
            except ValueError:
                print("Invalid number, using default: 4")
                cfg.pod.cpu_cores = 4
            ram_input = input("RAM (GB) [4]: ").strip()
            try:
                cfg.pod.ram_gb = int(ram_input) if ram_input else 4
            except ValueError:
                print("Invalid number, using default: 4")
                cfg.pod.ram_gb = 4

        _generate_compose(cfg)
        _recreate_container(cfg)

    if cfg.pod.backend == "libvirt" and not non_interactive:
        cfg.pod.vm_name = input("VM name [RDPWindows]: ").strip() or "RDPWindows"

    # DPI auto-detection
    from winpodx.display.scaling import detect_raw_scale, detect_scale_factor

    detected_scale = detect_scale_factor()
    if detected_scale != 100:
        print(f"\nDetected display scale: {detected_scale}%")
        cfg.rdp.scale = detected_scale

    raw = detect_raw_scale()
    detected_dpi = round(raw * 100)
    if detected_dpi > 100:
        print(f"Detected Windows DPI: {detected_dpi}%")
        cfg.rdp.dpi = detected_dpi

    cfg.save()
    print(f"\nConfig saved to {Config.path()}")

    from winpodx.core.provisioner import _install_bundled_apps_if_needed

    _install_bundled_apps_if_needed()
    _register_all_desktop_entries()

    # Summary
    print("\n" + "=" * 40)
    print(" Setup Complete")
    print("=" * 40)
    print(f"  Backend:  {cfg.pod.backend}")
    print(f"  User:     {cfg.rdp.user}")
    print(f"  IP:       {cfg.rdp.ip}")
    print(f"  Scale:    {cfg.rdp.scale}%")
    dpi_str = f"{cfg.rdp.dpi}%" if cfg.rdp.dpi > 0 else "auto"
    print(f"  DPI:      {dpi_str}")
    if cfg.pod.backend in ("podman", "docker"):
        print(f"  CPU:      {cfg.pod.cpu_cores} cores")
        print(f"  RAM:      {cfg.pod.ram_gb} GB")
        compose_path = config_dir() / "compose.yaml"
        print(f"  Compose:  {compose_path}")
    print()
    print("Apps are now in your application menu.")
    print("Just click any app — winpodx handles the rest automatically.")


def _generate_compose(cfg: Config) -> None:
    """Generate a compose.yaml for Podman/Docker backend."""
    compose_path = config_dir() / "compose.yaml"
    compose_path.parent.mkdir(parents=True, exist_ok=True)

    home = str(Path.home())

    # Find OEM directory (bundled with winpodx)
    oem_candidates = [
        Path(__file__).parent.parent.parent.parent / "config" / "oem",
        Path.home() / ".local" / "bin" / "winpodx-app" / "config" / "oem",
    ]
    oem_dir = str(oem_candidates[0])
    for candidate in oem_candidates:
        if candidate.exists():
            oem_dir = str(candidate)
            break

    password = cfg.rdp.password or _generate_password()

    # Escape values for safe YAML embedding (prevent format string injection)
    def _yaml_escape(val: str) -> str:
        """Escape a value for safe embedding in YAML double-quoted string."""
        return (
            val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
        )

    content = COMPOSE_TEMPLATE.format(
        ram=cfg.pod.ram_gb,
        cpu=cfg.pod.cpu_cores,
        user=_yaml_escape(cfg.rdp.user),
        password=_yaml_escape(password),
        home=home,
        win_version=cfg.pod.win_version,
        rdp_port=cfg.rdp.port,
        vnc_port=cfg.pod.vnc_port,
        oem_dir=oem_dir,
    )

    # Atomic write with secure permissions from creation
    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(dir=compose_path.parent, prefix=".compose-", suffix=".tmp")
    fd_closed = False
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd_closed = True
        os.rename(tmp_path, str(compose_path))
    except Exception:
        if not fd_closed:
            os.close(fd)
        Path(tmp_path).unlink(missing_ok=True)
        raise

    print(f"\nGenerated {compose_path}")


def _recreate_container(cfg: Config) -> None:
    """Stop existing container and start fresh with new compose config."""
    import subprocess as sp

    compose_path = config_dir() / "compose.yaml"
    backend = cfg.pod.backend  # podman or docker

    # Find compose command
    compose_cmd: list[str] | None = None
    if backend == "podman":
        if shutil.which("podman-compose"):
            compose_cmd = ["podman-compose"]
        else:
            try:
                sp.run(
                    ["podman", "compose", "version"],
                    capture_output=True,
                    check=True,
                )
                compose_cmd = ["podman", "compose"]
            except (FileNotFoundError, sp.CalledProcessError):
                pass
    elif backend == "docker":
        if shutil.which("docker-compose"):
            compose_cmd = ["docker-compose"]
        else:
            compose_cmd = ["docker", "compose"]

    if not compose_cmd:
        print("Compose command not found, skipping container recreation.")
        return

    print("\nRecreating container with new settings...")
    sp.run(
        [*compose_cmd, "down"],
        cwd=compose_path.parent,
        capture_output=True,
        timeout=120,
    )
    result = sp.run(
        [*compose_cmd, "up", "-d"],
        cwd=compose_path.parent,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode == 0:
        print("Container started.")
    else:
        msg = result.stderr.strip()
        print(f"Failed to start container: {msg}")
        raise RuntimeError(f"Container start failed: {msg}")


def handle_rotate_password(args: argparse.Namespace) -> None:
    """Rotate the Windows RDP password.

    Changes the password inside Windows first (via net user),
    then updates config and compose.yaml. No container recreation needed.
    """
    from datetime import datetime, timezone

    from winpodx.core.pod import PodState, pod_status
    from winpodx.core.provisioner import _change_windows_password

    cfg = Config.load()

    if cfg.pod.backend not in ("podman", "docker"):
        print("Password rotation is only supported for podman/docker backends.")
        raise SystemExit(1)

    status = pod_status(cfg)
    if status.state != PodState.RUNNING:
        print("Container is not running. Start it first: winpodx pod start --wait")
        raise SystemExit(1)

    new_password = _generate_password()

    # Change password inside Windows first
    print("Changing Windows user password...")
    if not _change_windows_password(cfg, new_password):
        print("Failed to change Windows password. Is the container fully booted?")
        raise SystemExit(1)

    # Windows password changed — update config and compose to match
    cfg.rdp.password = new_password
    cfg.rdp.password_updated = datetime.now(timezone.utc).isoformat()
    cfg.save()
    _generate_compose(cfg)

    print("Password rotated successfully.")
    print(f"New password saved to {Config.path()}")


def _register_all_desktop_entries() -> None:
    """Register all app definitions as .desktop entries."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry
    from winpodx.desktop.icons import install_winpodx_icon, update_icon_cache

    install_winpodx_icon()

    apps = list_available_apps()
    for app_info in apps:
        install_desktop_entry(app_info)

    if apps:
        update_icon_cache()
        print(f"Registered {len(apps)} apps in desktop environment.")
