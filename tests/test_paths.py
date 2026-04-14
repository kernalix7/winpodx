"""Tests for XDG path management."""

from winpodx.utils.paths import applications_dir, config_dir, data_dir


def test_config_dir(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-config")
    assert str(config_dir()) == "/tmp/test-config/winpodx"


def test_data_dir(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/test-data")
    assert str(data_dir()) == "/tmp/test-data/winpodx"


def test_applications_dir(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/test-data")
    assert str(applications_dir()) == "/tmp/test-data/applications"
