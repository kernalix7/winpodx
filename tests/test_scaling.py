"""Tests for DPI scaling detection."""

from winpodx.display.scaling import detect_scale_factor


def test_scale_factor_returns_valid():
    """Scale factor should be one of the valid RDP values."""
    result = detect_scale_factor()
    assert result in (100, 140, 180)


def test_env_scale_gdk(monkeypatch):
    """GDK_SCALE environment variable should be detected."""
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "2")
    assert _env_scale() == 2.0


def test_env_scale_qt(monkeypatch):
    """QT_SCALE_FACTOR should be detected."""
    from winpodx.display.scaling import _env_scale

    monkeypatch.delenv("GDK_SCALE", raising=False)
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.5")
    assert _env_scale() == 1.5


def test_env_scale_fallback(monkeypatch):
    """With no scale env vars, _env_scale should return 1.0 (not 0.0)."""
    from winpodx.display.scaling import _env_scale

    for var in ("GDK_SCALE", "QT_SCALE_FACTOR", "ELM_SCALE"):
        monkeypatch.delenv(var, raising=False)
    assert _env_scale() == 1.0


def test_env_scale_zero_guard(monkeypatch):
    """GDK_SCALE=0 should return 1.0, not 0.0."""
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "0")
    assert _env_scale() == 1.0


def test_env_scale_negative_guard(monkeypatch):
    """Negative scale should return 1.0."""
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "-1")
    assert _env_scale() == 1.0


def test_xrdb_zero_dpi_guard(monkeypatch):
    """xrdb returning Xft.dpi: 0 must not produce 0.0 scale."""
    import subprocess

    from winpodx.display.scaling import _xrdb_scale

    def mock_run(*args, **kwargs):
        result = subprocess.CompletedProcess(args[0], 0)
        result.stdout = "Xft.dpi:\t0\n"
        return result

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert _xrdb_scale() == 1.0


def test_xrdb_valid_dpi(monkeypatch):
    """xrdb with valid DPI should return correct scale."""
    import subprocess

    from winpodx.display.scaling import _xrdb_scale

    def mock_run(*args, **kwargs):
        result = subprocess.CompletedProcess(args[0], 0)
        result.stdout = "Xft.dpi:\t192\n"
        return result

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert _xrdb_scale() == 2.0
