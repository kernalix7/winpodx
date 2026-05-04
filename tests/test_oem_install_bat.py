"""Static checks for config/oem/install.bat first-boot glue."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_BAT = REPO_ROOT / "config" / "oem" / "install.bat"


def test_install_bat_exists() -> None:
    assert INSTALL_BAT.is_file()


def test_install_bat_has_no_non_ascii() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text)


def test_install_bat_uses_absolute_windows_tar() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert '"%SystemRoot%\\System32\\tar.exe" -xf' in text


def test_install_bat_does_not_self_lock_setup_log() -> None:
    """Do not Add-Content to setup.log from a process whose stderr is
    already redirected to setup.log. Windows can hold the redirection handle
    exclusively, producing noisy setup.log WriteError records and hiding the
    real agent-spawn state.
    """

    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "Add-Content -LiteralPath '%SETUP_LOG%'" not in text
    assert '>>"%SETUP_LOG%" 2>&1' in text
