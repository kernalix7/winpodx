"""Podman backend for running Windows container."""

from __future__ import annotations

import logging
import subprocess
import time

from winpodx.backend.base import Backend
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)


class PodmanBackend(Backend):
    def _compose_file(self) -> str:
        return str(config_dir() / "compose.yaml")

    def _compose_cmd(self) -> list[str]:
        # Prefer podman-compose directly (avoids docker-compose plugin hijacking)
        import shutil

        if shutil.which("podman-compose"):
            return ["podman-compose", "-f", self._compose_file()]
        return ["podman", "compose", "-f", self._compose_file()]

    def start(self) -> None:
        try:
            subprocess.run(
                [*self._compose_cmd(), "up", "-d"],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )
            log.info("Pod started (podman)")
        except subprocess.CalledProcessError as e:
            log.error("podman compose up failed: %s", e.stderr.strip())
            raise
        except subprocess.TimeoutExpired:
            log.error("podman compose up timed out (120s)")
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
                    "podman compose down failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except subprocess.TimeoutExpired:
            log.error("podman compose down timed out (60s)")

    def _container_state(self) -> str:
        """Return the lower-cased container state, or empty string if unavailable."""
        try:
            result = subprocess.run(
                [
                    "podman",
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
                    "podman ps failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return ""
            return result.stdout.strip().lower()
        except FileNotFoundError:
            log.warning("podman not found in PATH")
            return ""

    def is_running(self) -> bool:
        # Treat paused as alive; pod_status() distinguishes via is_paused().
        state = self._container_state()
        return "running" in state or "paused" in state

    def is_paused(self) -> bool:
        return "paused" in self._container_state()

    def uptime_secs(self) -> int | None:
        """Seconds since the container was last started, or None on probe failure."""
        import datetime
        import subprocess

        try:
            result = subprocess.run(
                [
                    "podman",
                    "inspect",
                    "-f",
                    "{{.State.StartedAt}}",
                    self.cfg.pod.container_name,
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        ts = result.stdout.strip()
        if not ts or result.returncode != 0:
            return None
        # podman prints RFC3339 (`2026-05-20T14:00:00.123456789Z`). Python's
        # fromisoformat handles `+00:00` but not bare `Z` until 3.11, and
        # the nanoseconds suffix until 3.11 either — strip both for the
        # 3.9 / 3.10 fallback path.
        ts = ts.replace("Z", "+00:00")
        if "." in ts:
            head, _, tail = ts.partition(".")
            # Truncate fractional seconds to microseconds (6 digits) so
            # the parser accepts it across Python versions.
            frac, _, tz = tail.partition("+")
            if tz:
                ts = f"{head}.{frac[:6]}+{tz}"
            else:
                ts = f"{head}.{frac[:6]}"
        try:
            started = datetime.datetime.fromisoformat(ts)
        except ValueError:
            return None
        now = datetime.datetime.now(tz=started.tzinfo)
        delta = (now - started).total_seconds()
        return max(0, int(delta))

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
