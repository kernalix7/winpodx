# SPDX-License-Identifier: MIT
"""Live USB passthrough via usbredir — bypasses the container's frozen libusb.

Why not ``device_add usb-host``: dockur runs QEMU inside a rootless Podman
container whose netns/userns block kernel USB uevents, so QEMU's in-process
libusb never sees hotplug — its device list is frozen at boot and a runtime
``device_add usb-host`` for anything not present-at-boot yields an empty stub.
See ``docs/design/USB-PASSTHROUGH-INVESTIGATION.md``.

What works (verified live on a USB3 SuperSpeed device): redirect from the
**host**, which has live libusb hotplug.

    [host] usbredirect --device VID:PID  <->  relay  <->  [qemu] usb-redir chardev  ->  Windows

* QEMU side: a ``usb-redir`` device backed by a socket ``chardev``, added to
  the *running* guest via HMP ``chardev-add`` + ``device_add`` — no recreate.
* The socket lives on the container's loopback; the host reaches it through the
  same ``<backend> exec ... /dev/tcp`` transport winpodx already uses for the
  monitor (so no compose ``ports:`` map, no recreate). A small **relay**
  process bridges a host-loopback port to it.
* Host ``usbredirect`` grabs the device with the host's libusb and speaks the
  usbredir protocol over that socket. Spawn = attach, exit = detach → live.
* Opening a root-owned device node needs privilege for **just usbredirect** —
  run it under ``pkexec``/``sudo`` at attach time. No persistent udev rule, no
  group, nothing installed; the only privileged process is one short-lived USB
  forwarder. (uaccess devices like security keys don't even need that.)

State for each attached device is tracked in ``<data>/usbredir/<id>.json`` so a
later ``detach`` (a separate process) can tear it down.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from winpodx.core.devices import (
    DeviceConfig,
    HmpError,
    hmp_command,
)

log = logging.getLogger(__name__)

# Port ranges. The qemu port is on the *container* loopback (only has to avoid
# dockur's own: 3389 RDP, 8006 VNC, 8765 agent, 7100 monitor). The host port is
# on the host loopback. They live in different namespaces so the two ranges may
# overlap numerically without conflict; we keep them distinct for clarity.
_QEMU_PORT_BASE = 7310
_HOST_PORT_BASE = 7410
_MAX_SLOTS = 16  # generous cap on concurrent redirected USB devices


def _state_dir() -> Path:
    from winpodx.utils.paths import data_dir

    d = data_dir() / "usbredir"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _qom_id(dev: DeviceConfig) -> str:
    """Stable QOM/chardev id for a redirected device (add + del use it)."""
    return "wpxur-" + dev.did.replace(":", "")


def _state_path(dev: DeviceConfig) -> Path:
    return _state_dir() / f"{_qom_id(dev)}.json"


def _proc_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Dependency + slot helpers
# ---------------------------------------------------------------------------


def usbredirect_path() -> str | None:
    """Return the host ``usbredirect`` binary, or None if not installed."""
    return shutil.which("usbredirect")


def _privilege_wrapper() -> list[str] | None:
    """Return the command prefix that runs ``usbredirect`` as root.

    Prefers ``pkexec`` (graphical polkit prompt, right for the GUI) when a
    display is present, else ``sudo`` (terminal prompt). Returns ``None`` if
    neither is available.
    """
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    pkexec = shutil.which("pkexec")
    sudo = shutil.which("sudo")
    if has_display and pkexec:
        return [pkexec]
    if sudo:
        return [sudo]
    if pkexec:
        return [pkexec]
    return None


def _active_slots() -> set[int]:
    used: set[int] = set()
    for f in _state_dir().glob("*.json"):
        try:
            used.add(int(json.loads(f.read_text()).get("slot", -1)))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    used.discard(-1)
    return used


def _alloc_slot() -> int:
    used = _active_slots()
    for slot in range(_MAX_SLOTS):
        if slot not in used:
            return slot
    raise HmpError(f"too many USB devices redirected at once (max {_MAX_SLOTS})")


# ---------------------------------------------------------------------------
# The relay (run as a detached child: `python -m winpodx.core.usbredir relay …`)
# ---------------------------------------------------------------------------


def _run_relay(backend: str, container: str, qemu_port: int, host_port: int) -> int:
    """Bridge a host-loopback TCP port to the container's QEMU usb-redir socket.

    Listens on ``127.0.0.1:host_port``; on the first connection (from
    ``usbredirect``) it opens the container's ``127.0.0.1:qemu_port`` via
    ``<backend> exec ... /dev/tcp`` and shuttles bytes both ways until either
    side closes. One-shot: exits when that single session ends (usbredirect
    does not reconnect; winpodx tears the relay down on detach).
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", host_port))
    srv.listen(1)
    print(f"relay up 127.0.0.1:{host_port} <-> {container}:{qemu_port}", flush=True)
    try:
        conn, _addr = srv.accept()
    except OSError:
        return 0
    bridge = subprocess.Popen(
        [
            backend,
            "exec",
            "-i",
            container,
            "bash",
            "-c",
            f"exec 3<>/dev/tcp/127.0.0.1/{qemu_port}; cat <&3 & cat >&3",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        bufsize=0,
    )

    def _sock_to_bridge() -> None:
        try:
            while True:
                data = conn.recv(65536)
                if not data:
                    break
                assert bridge.stdin is not None
                bridge.stdin.write(data)
        except OSError:
            pass
        finally:
            try:
                assert bridge.stdin is not None
                bridge.stdin.close()
            except OSError:
                pass

    def _bridge_to_sock() -> None:
        try:
            assert bridge.stdout is not None
            fd = bridge.stdout.fileno()
            while True:
                data = os.read(fd, 65536)
                if not data:
                    break
                conn.sendall(data)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    t1 = threading.Thread(target=_sock_to_bridge, daemon=True)
    t2 = threading.Thread(target=_bridge_to_sock, daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    try:
        bridge.terminate()
    except OSError:
        pass
    return 0


# ---------------------------------------------------------------------------
# Attach / detach
# ---------------------------------------------------------------------------


def is_attached(dev: DeviceConfig) -> bool:
    """True if a live redirection session for *dev* is recorded and running."""
    path = _state_path(dev)
    if not path.exists():
        return False
    try:
        state = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    return _proc_alive(state.get("relay_pid"))


def attach(backend: str, container: str, dev: DeviceConfig) -> None:
    """Start a live usbredir session redirecting *dev* into the running guest.

    Raises :class:`HmpError` on any failure (after cleaning up partial state).
    """
    if dev.dtype != "usb":
        raise HmpError(f"usbredir attach only supports USB devices, not {dev.dtype!r}")
    if is_attached(dev):
        log.info("usbredir: %s already attached", dev.did)
        return

    ured = usbredirect_path()
    if not ured:
        raise HmpError(
            "usbredirect not found. Install the host 'usbredir' package "
            "(openSUSE: sudo zypper install usbredir; "
            "Fedora: sudo dnf install usbredir-tools; "
            "Atomic Fedora: rpm-ostree install --apply-live usbredir-tools; "
            "Debian/Ubuntu: sudo apt install usbredirect)."
        )
    priv = _privilege_wrapper()
    if priv is None:
        raise HmpError("neither pkexec nor sudo found — cannot open the USB device as root")

    vid, pid = dev.did.split(":")
    qom = _qom_id(dev)
    slot = _alloc_slot()
    qemu_port = _QEMU_PORT_BASE + slot
    host_port = _HOST_PORT_BASE + slot

    # 1) Add the usb-redir channel to the running guest (live, no recreate).
    #    chardev = socket SERVER on the container's loopback; the relay dials in.
    chardev_cmd = f"chardev-add socket,id={qom},host=127.0.0.1,port={qemu_port},server=on,wait=off"
    reply = hmp_command(backend, container, chardev_cmd)
    if _looks_like_error(reply):
        raise HmpError(f"chardev-add for {dev.did} failed: {_tail(reply)}")
    dev_cmd = f"device_add usb-redir,chardev={qom},id={qom}"
    reply = hmp_command(backend, container, dev_cmd)
    if _looks_like_error(reply):
        _hmp_cleanup(backend, container, qom, drop_device=False)
        raise HmpError(f"device_add usb-redir for {dev.did} failed: {_tail(reply)}")

    relay_log = _state_dir() / f"{qom}.relay.log"
    ured_log = _state_dir() / f"{qom}.usbredirect.log"
    relay_proc = None
    ured_proc = None
    try:
        # 2) Relay: host:host_port <-> container:qemu_port (detached).
        relay_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "winpodx.core.usbredir",
                "relay",
                backend,
                container,
                str(qemu_port),
                str(host_port),
            ],
            stdin=subprocess.DEVNULL,
            stdout=open(relay_log, "wb"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        # Wait for the relay to be *listening* — by polling its log for the
        # "relay up" marker, NOT by connecting. The relay accepts exactly one
        # connection, so a probe connect would be consumed in usbredirect's
        # place (the relay would then bridge an empty socket and exit, leaving
        # usbredirect with "connection refused"). Once listen() has run the OS
        # queues incoming connections, so usbredirect can connect with no race.
        _wait_relay_ready(relay_log, timeout=5.0)

        # 3) usbredirect (root, transient) grabs the device + connects to relay.
        ured_proc = subprocess.Popen(
            [*priv, ured, "--device", dev.did, "--to", f"127.0.0.1:{host_port}"],
            stdin=subprocess.DEVNULL,
            stdout=open(ured_log, "wb"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

        # 4) Confirm the channel actually connected (usbredirect reached qemu).
        #    Generous timeout: a pkexec/polkit password dialog can take a while
        #    to authenticate before usbredirect even starts grabbing the device.
        if not _wait_chardev_connected(backend, container, qom, timeout=45.0):
            raise HmpError(
                f"usbredir channel for {dev.did} never connected — "
                f"see {ured_log} (auth declined? device busy? not present?)"
            )

        _state_path(dev).write_text(
            json.dumps(
                {
                    "did": dev.did,
                    "qom": qom,
                    "slot": slot,
                    "qemu_port": qemu_port,
                    "host_port": host_port,
                    "relay_pid": relay_proc.pid,
                    "usbredirect_pid": ured_proc.pid,
                }
            )
        )
        log.info("usbredir attach %s ok (slot %d)", dev.did, slot)
    except Exception:
        # Roll everything back so a failed attach leaves no orphans.
        _kill(relay_proc)
        _kill(ured_proc)
        _hmp_cleanup(backend, container, qom, drop_device=True)
        raise


def detach(backend: str, container: str, dev: DeviceConfig) -> None:
    """Tear down the live usbredir session for *dev* (idempotent)."""
    if dev.dtype != "usb":
        raise HmpError(f"usbredir detach only supports USB devices, not {dev.dtype!r}")
    path = _state_path(dev)
    qom = _qom_id(dev)
    state: dict = {}
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            state = {}

    # Killing the (user-owned) relay closes the socket; usbredirect exits on
    # its own when its connection drops, so we don't need root to detach.
    _kill_pid(state.get("relay_pid"))
    time.sleep(0.3)
    if _proc_alive(state.get("usbredirect_pid")):
        # usbredirect didn't exit on socket close — it's root, so escalate.
        _kill_pid(state.get("usbredirect_pid"), privileged=True)

    _hmp_cleanup(backend, container, qom, drop_device=True)
    path.unlink(missing_ok=True)
    log.info("usbredir detach %s ok", dev.did)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_ERR_TOKENS = ("error", "could not", "failed", "no such", "unable", "cannot")


def _looks_like_error(reply: str) -> bool:
    low = " ".join(reply.split()).lower()
    return any(tok in low for tok in _ERR_TOKENS)


def _tail(reply: str) -> str:
    return " ".join(reply.split())[-200:]


def _wait_relay_ready(log_path: Path, timeout: float) -> None:
    """Wait until the relay has called ``listen()`` (it logs ``relay up``).

    We poll the relay's log rather than opening a probe connection: the relay
    accepts a single connection, so a probe would be consumed in usbredirect's
    place. After ``listen()`` the kernel queues connects, so usbredirect won't
    get "connection refused".
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if "relay up" in log_path.read_text(errors="replace"):
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise HmpError("relay did not start listening in time")


def _wait_chardev_connected(backend: str, container: str, qom: str, timeout: float) -> bool:
    """Poll ``info chardev`` until the usb-redir socket shows a peer (``<->``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            reply = hmp_command(backend, container, "info chardev")
        except HmpError:
            reply = ""
        for line in reply.replace("\r", "").splitlines():
            if qom in line and "<->" in line and "disconnected" not in line:
                return True
        time.sleep(0.6)
    return False


def _hmp_cleanup(backend: str, container: str, qom: str, *, drop_device: bool) -> None:
    """Best-effort removal of the usb-redir device + chardev from the guest."""
    try:
        if drop_device:
            hmp_command(backend, container, f"device_del {qom}")
            time.sleep(0.3)
        hmp_command(backend, container, f"chardev-remove {qom}")
    except HmpError as e:
        log.warning("usbredir cleanup of %s incomplete: %s", qom, e)


def _kill(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except OSError:
        try:
            proc.terminate()
        except OSError:
            pass


def _kill_pid(pid: int | None, *, privileged: bool = False) -> None:
    if not pid:
        return
    if privileged:
        priv = _privilege_wrapper()
        if priv:
            subprocess.run([*priv, "kill", str(pid)], check=False)
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _main(argv: list[str]) -> int:
    if len(argv) >= 5 and argv[0] == "relay":
        return _run_relay(argv[1], argv[2], int(argv[3]), int(argv[4]))
    print(
        "usage: python -m winpodx.core.usbredir relay <backend> <container> <qport> <hport>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
