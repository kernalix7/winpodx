# SPDX-License-Identifier: MIT
"""Docker backend for running Windows via dockur/windows container."""

from __future__ import annotations

import logging
import subprocess
import time

from winpodx.backend._hostenv import host_env
from winpodx.backend.base import Backend, _container_uptime_secs
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)


class DockerBackend(Backend):
    def _compose_file(self) -> str:
        return str(config_dir() / "compose.yaml")

    def _compose_cmd(self) -> list[str]:
        # Thin AppImage (#357 / #363 root-cause fix, 0.6.0 item A): docker
        # is no longer bundled, so standard PATH resolution finds the host
        # binary directly. ``host_env()`` (passed as ``env=`` on every
        # subprocess.run below) still strips ``${APPDIR}`` from
        # ``LD_LIBRARY_PATH`` so host helpers load HOST libs.
        return ["docker", "compose", "-f", self._compose_file()]

    def start(self) -> None:
        # Match podman backend: large hard cap so first-run image pull
        # on slow connections doesn't fail. Docker doesn't need the
        # activity-based wrapper because the docker daemon handles its
        # own pull progress; only the wall-clock timeout is at risk.
        try:
            subprocess.run(
                [*self._compose_cmd(), "up", "-d"],
                check=True,
                capture_output=True,
                text=True,
                timeout=4 * 3600,
                env=host_env(),
            )
            log.info("Pod started (docker)")
        except subprocess.CalledProcessError as e:
            log.error("docker compose up failed: %s", e.stderr.strip())
            raise
        except subprocess.TimeoutExpired:
            log.error("docker compose up timed out (4h hard cap)")
            raise

    def stop(self) -> None:
        try:
            result = subprocess.run(
                [*self._compose_cmd(), "down"],
                capture_output=True,
                text=True,
                timeout=180,
                env=host_env(),
            )
            if result.returncode != 0:
                log.warning(
                    "docker compose down failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except subprocess.TimeoutExpired:
            log.error("docker compose down timed out (180s)")

    def _container_state(self) -> str:
        """Return the lower-cased container state, or empty string if unavailable."""
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
                env=host_env(),
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
        # Treat paused as alive; pod_status() distinguishes via is_paused().
        state = self._container_state()
        return "running" in state or "paused" in state

    def is_paused(self) -> bool:
        return "paused" in self._container_state()

    def uptime_secs(self) -> int | None:
        """Seconds since the container was last started, or None on probe failure."""
        return _container_uptime_secs("docker", self.cfg.pod.container_name)

    def get_ip(self) -> str:
        return self.cfg.rdp.ip or "127.0.0.1"

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for the container to be running and RDP port available."""
        from winpodx.core.pod import check_rdp_port

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (
                self.is_running()
                and not self.is_paused()
                and check_rdp_port(self.get_ip(), self.cfg.rdp.port, timeout=3)
            ):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(1.0, remaining))
        return False
