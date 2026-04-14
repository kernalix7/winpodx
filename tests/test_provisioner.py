"""Tests for auto-provisioning engine."""

from winpodx.core.provisioner import ProvisionError


def test_provision_error():
    """ProvisionError should be a proper exception."""
    err = ProvisionError("test error")
    assert str(err) == "test error"
    assert isinstance(err, Exception)


def test_ensure_config_creates_default(tmp_path, monkeypatch):
    """Should create a default config when none exists."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.provisioner import _ensure_config

    cfg = _ensure_config()

    assert cfg.rdp.user == "User"
    assert cfg.rdp.ip == "127.0.0.1"
    assert (tmp_path / "winpodx" / "winpodx.toml").exists()
