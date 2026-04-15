"""Abstract base class for Windows pod backends."""

from __future__ import annotations

from abc import ABC, abstractmethod

from winpodx.core.config import Config


class Backend(ABC):
    """Interface that all pod backends must implement."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    @abstractmethod
    def start(self) -> None:
        """Start the Windows environment."""

    @abstractmethod
    def stop(self) -> None:
        """Stop the Windows environment."""

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the Windows environment is currently running."""

    @abstractmethod
    def get_ip(self) -> str:
        """Return the IP address of the running Windows environment."""

    def wait_for_ready(self, timeout: int = 300) -> bool:
        """Wait for the Windows environment to be ready for RDP."""
        return False

    def restart(self) -> None:
        """Restart the Windows environment."""
        self.stop()
        self.start()
