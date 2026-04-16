"""libvirt/KVM backend for running Windows VM."""

from __future__ import annotations

import logging
import subprocess

from winpodx.backend.base import Backend

log = logging.getLogger(__name__)


class LibvirtBackend(Backend):
    @property
    def vm_name(self) -> str:
        return self.cfg.pod.vm_name or "RDPWindows"

    def start(self) -> None:
        try:
            subprocess.run(
                ["virsh", "start", "--", self.vm_name],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            log.info("VM started (libvirt): %s", self.vm_name)
        except subprocess.CalledProcessError as e:
            log.error("virsh start %s failed: %s", self.vm_name, e.stderr.strip())
            raise
        except subprocess.TimeoutExpired:
            log.error("virsh start %s timed out (60s)", self.vm_name)
            raise

    def stop(self) -> None:
        try:
            result = subprocess.run(
                ["virsh", "shutdown", "--", self.vm_name],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log.warning(
                    "virsh shutdown %s failed (rc=%d): %s",
                    self.vm_name,
                    result.returncode,
                    result.stderr.strip(),
                )
        except subprocess.TimeoutExpired:
            log.error("virsh shutdown %s timed out (60s)", self.vm_name)

    def is_running(self) -> bool:
        try:
            result = subprocess.run(
                ["virsh", "domstate", "--", self.vm_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                log.warning(
                    "virsh domstate failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return False
            return "running" in result.stdout.lower()
        except FileNotFoundError:
            log.warning("virsh not found in PATH")
            return False

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for libvirt VM to be running and RDP port available."""
        import time

        from winpodx.core.pod import check_rdp_port

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running() and check_rdp_port(self.get_ip(), self.cfg.rdp.port, timeout=3):
                return True
            time.sleep(5)
        return False

    def get_ip(self) -> str:
        if self.cfg.rdp.ip:
            return self.cfg.rdp.ip

        try:
            result = subprocess.run(
                ["virsh", "domifaddr", "--", self.vm_name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 4 and "/" in parts[-1]:
                        return parts[-1].split("/")[0]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("virsh domifaddr failed for %s", self.vm_name)
        return "127.0.0.1"
