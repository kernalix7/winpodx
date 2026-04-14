"""Docker backend for running Windows via dockur/windows container."""

from __future__ import annotations

import logging
import subprocess
import time

from winpodx.backend.base import Backend
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)


class DockerBackend(Backend):
    def _compose_file(self) -> str:
        return str(config_dir() / "compose.yaml")

    def _compose_cmd(self) -> list[str]:
        return ["docker", "compose", "-f", self._compose_file()]

    def start(self) -> None:
        try:
            subprocess.run(
                [*self._compose_cmd(), "up", "-d"],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            log.info("Pod started (docker)")
        except subprocess.CalledProcessError as e:
            log.error("docker compose up failed: %s", e.stderr.strip())
            raise
        except subprocess.TimeoutExpired:
            log.error("docker compose up timed out (120s)")
            raise

    def stop(self) -> None:
        try:
            result = subprocess.run(
                [*self._compose_cmd(), "down"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                log.warning(
                    "docker compose down failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except subprocess.TimeoutExpired:
            log.error("docker compose down timed out (60s)")

    def is_running(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=winpodx-windows", "--format", "{{.State}}"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                log.warning(
                    "docker ps failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return False
            return "running" in result.stdout.lower()
        except FileNotFoundError:
            log.warning("docker not found in PATH")
            return False

    def get_ip(self) -> str:
        return self.cfg.rdp.ip or "127.0.0.1"

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for the container to be running and RDP port available."""
        from winpodx.core.pod import check_rdp_port

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running() and check_rdp_port(self.get_ip(), self.cfg.rdp.port, timeout=3):
                return True
            time.sleep(5)
        return False
