"""Tests for backend abstraction."""

from winpodx.backend.manual import ManualBackend
from winpodx.core.config import Config


def test_manual_backend_start_stop():
    """Manual backend start/stop should be no-ops."""
    cfg = Config()
    cfg.rdp.ip = "192.168.1.100"
    backend = ManualBackend(cfg)
    backend.start()  # No-op
    backend.stop()  # No-op
    assert backend.get_ip() == "192.168.1.100"


def test_get_backend():
    """get_backend should return correct backend class."""
    from winpodx.core.pod import get_backend

    cfg = Config()

    cfg.pod.backend = "manual"
    assert type(get_backend(cfg)).__name__ == "ManualBackend"

    cfg.pod.backend = "podman"
    assert type(get_backend(cfg)).__name__ == "PodmanBackend"

    cfg.pod.backend = "docker"
    assert type(get_backend(cfg)).__name__ == "DockerBackend"
