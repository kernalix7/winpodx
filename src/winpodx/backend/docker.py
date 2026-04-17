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

    def _container_state(self) -> str:
        """Return the lower-cased container state (running/paused/exited/...).

        Empty string when the container does not exist or docker is missing.
        """
        try:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"name={self.cfg.pod.container_name}",
                    "--format",
                    "{{.State}}",
                ],
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
                return ""
            return result.stdout.strip().lower()
        except FileNotFoundError:
            log.warning("docker not found in PATH")
            return ""

    def is_running(self) -> bool:
        # Treat paused as a form of "alive" so callers that ask the pod
        # question get a consistent view. pod_status() distinguishes the
        # two using is_paused().
        state = self._container_state()
        return "running" in state or "paused" in state

    def is_paused(self) -> bool:
        return "paused" in self._container_state()

    def get_ip(self) -> str:
        return self.cfg.rdp.ip or "127.0.0.1"

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for the container to be running and RDP port available.

        Polls at 1-second cadence (previously 5s) while still respecting
        the overall timeout budget — shorter wakeups give faster RDP
        readiness feedback on a healthy container without materially
        increasing load (the subprocess call dominates).
        """
        from winpodx.core.pod import check_rdp_port

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (
                self.is_running()
                and not self.is_paused()
                and check_rdp_port(self.get_ip(), self.cfg.rdp.port, timeout=3)
            ):
                return True
            # Budget check: don't sleep past the deadline.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
        return False
