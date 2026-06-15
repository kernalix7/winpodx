# SPDX-License-Identifier: MIT
"""Podman backend for running Windows container."""

from __future__ import annotations

import logging
import subprocess
import time

from winpodx.backend._hostenv import host_env
from winpodx.backend.base import Backend, _container_uptime_secs
from winpodx.utils.paths import config_dir

log = logging.getLogger(__name__)


class PodmanBackend(Backend):
    def _compose_file(self) -> str:
        return str(config_dir() / "compose.yaml")

    def _compose_cmd(self) -> list[str]:
        # We require podman-compose proper -- the `podman compose`
        # subcommand delegates to whatever compose provider it finds,
        # and on Fedora-family systems with the docker-compose CLI
        # plugin installed (Nobara default) it routes through
        # docker-compose. docker-compose does not understand podman's
        # `group_add: [keep-groups]` magic value (which we need for
        # rootless /dev/kvm access), so the container start fails with
        # "looking up supplemental groups ... Unable to find group
        # keep-groups". See #288 (magicdiablo).
        #
        # Thin AppImage (#357 root-cause fix, 0.6.0 item A): podman-
        # compose is no longer bundled, so the standard ``shutil.which``
        # finds the host copy directly -- no host-first dance needed.
        import shutil

        if shutil.which("podman-compose"):
            return ["podman-compose", "-f", self._compose_file()]
        raise RuntimeError(
            "podman-compose not found on PATH. WinPodX requires podman-compose "
            "(not the `podman compose` subcommand, which delegates to "
            "docker-compose and breaks our keep-groups extension -- see #288). "
            "Install it with your package manager: "
            "Fedora/Nobara: `sudo dnf install podman-compose`, "
            "Debian/Ubuntu: `sudo apt install podman-compose`, "
            "Arch: `sudo pacman -S podman-compose`, "
            "openSUSE: `sudo zypper install podman-compose`."
        )

    def start(self) -> None:
        # Activity-based timeout: as long as podman is printing progress
        # lines (image pull layers, container creation, etc.) we let it
        # keep going. Only fail when the output goes silent for too long.
        # The previous fixed 120s budget failed on slow connections during
        # the first-run dockur image pull (#288-class issue). Idle limit
        # 5min absorbs network blips while still failing cleanly on a
        # genuinely stalled pull.
        try:
            self._run_streaming(
                [*self._compose_cmd(), "up", "-d"],
                idle_limit=300,
                hard_cap=4 * 3600,
                description="podman compose up",
                env=host_env(),
            )
            log.info("Pod started (podman)")
        except subprocess.CalledProcessError as e:
            log.error("podman compose up failed: %s", e.stderr.strip() if e.stderr else "")
            raise
        except subprocess.TimeoutExpired:
            log.error("podman compose up went idle for >300s")
            raise

    def _run_streaming(
        self,
        cmd: list[str],
        *,
        idle_limit: int,
        hard_cap: int,
        description: str,
        env: dict[str, str] | None = None,
    ) -> None:
        """Run a subprocess while watching its output for activity.

        Kills the process when its combined stdout+stderr stream goes
        silent for ``idle_limit`` seconds, or when total wall time
        exceeds ``hard_cap``. Raises ``CalledProcessError`` on non-zero
        exit, ``TimeoutExpired`` on idle or cap.

        ``env`` is forwarded to ``subprocess.Popen``. ``None`` (the
        default, and always the case outside an AppImage) means inherit
        the current environment -- unchanged behaviour. Inside an AppImage
        the caller passes :func:`host_env` so the host podman + the host
        helpers it spawns load HOST libraries (#363).
        """
        import threading
        import time

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        last_activity = [time.monotonic()]
        captured: list[str] = []

        def _drain() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                last_activity[0] = time.monotonic()
                captured.append(line)

        t = threading.Thread(target=_drain, daemon=True)
        t.start()
        start = time.monotonic()
        while True:
            try:
                rc = proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                now = time.monotonic()
                if now - last_activity[0] > idle_limit:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise subprocess.TimeoutExpired(
                        cmd=cmd,
                        timeout=idle_limit,
                        output="".join(captured),
                        stderr=None,
                    ) from None
                if now - start > hard_cap:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise subprocess.TimeoutExpired(
                        cmd=cmd,
                        timeout=hard_cap,
                        output="".join(captured),
                        stderr=None,
                    ) from None
        t.join(timeout=5)
        out = "".join(captured)
        if rc != 0:
            raise subprocess.CalledProcessError(returncode=rc, cmd=cmd, output=out, stderr=out)

    def stop(self) -> None:
        # `compose stop`, NOT `compose down`: `down` REMOVES the container, so a
        # later `winpodx install` / `migrate` sees no container and "heals" it by
        # recreating from compose every time the user updates while stopped
        # ("Container 'winpodx-windows' is missing — creating it ...") — a needless
        # Windows reboot. `stop` keeps the (stopped) container so `start`'s
        # `compose up -d` just restarts it, and the heal path correctly no-ops
        # because the container still exists. The persistent disk volume survives
        # either way; the difference is whether the container object is kept.
        try:
            result = subprocess.run(
                [*self._compose_cmd(), "stop"],
                capture_output=True,
                text=True,
                timeout=180,
                env=host_env(),
            )
            if result.returncode != 0:
                log.warning(
                    "podman compose stop failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
        except subprocess.TimeoutExpired:
            log.error("podman compose stop timed out (180s)")

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
                env=host_env(),
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
        return _container_uptime_secs("podman", self.cfg.pod.container_name)

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
