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

    def is_paused(self) -> bool:
        """Return True if the environment is paused/suspended.

        Default: False. Container backends (podman/docker) override this
        so the CLI / GUI / tray can surface the ``PAUSED`` pod state that
        the idle monitor puts the container into. libvirt and manual
        have no equivalent primitive.
        """
        return False

    def uptime_secs(self) -> int | None:
        """Return seconds since the backend's runtime started, or None.

        Used by ``pod_status`` to distinguish a still-booting container
        (``STARTING``) from a long-running one whose Windows guest has
        gone unresponsive (``UNRESPONSIVE``). Default: None so backends
        that can't cheaply expose this fall back to legacy behaviour.
        """
        return None

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
