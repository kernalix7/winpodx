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

    def get_ip(self) -> str:
        return self.cfg.rdp.ip
