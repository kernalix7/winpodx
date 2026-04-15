"""Manual backend — direct RDP connection to an existing Windows machine."""

from __future__ import annotations

from winpodx.backend.base import Backend


class ManualBackend(Backend):
    def start(self) -> None:
        pass  # Nothing to start — user manages the machine

    def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        from winpodx.core.pod import check_rdp_port

        return check_rdp_port(self.get_ip(), self.cfg.rdp.port)

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for remote RDP to become available."""
        import time

        from winpodx.core.pod import check_rdp_port

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if check_rdp_port(self.get_ip(), self.cfg.rdp.port, timeout=3):
                return True
            time.sleep(5)
        return False

    def get_ip(self) -> str:
        return self.cfg.rdp.ip
