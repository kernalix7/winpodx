# SPDX-License-Identifier: MIT
"""Static checks for config/oem/install.bat first-boot glue."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_BAT = REPO_ROOT / "config" / "oem" / "install.bat"


def _active_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().casefold().startswith("rem ")
    ]


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


def test_install_bat_oem_version_matches_expected_setup_contract() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "set WINPODX_OEM_VERSION=26" in text
    assert "(echo %WINPODX_OEM_VERSION%)>C:\\winpodx\\oem_version.txt" in text


def test_install_bat_uses_tar_not_expand_archive_command() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    active_text = "\n".join(_active_lines(text)).casefold()

    assert "expand-archive" not in active_text
    assert '"%systemroot%\\system32\\tar.exe" -xf' in active_text


def test_install_bat_registers_and_spawns_guest_agent() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")

    assert "Set-ItemProperty -Path $key -Name 'WinpodxAgent'" in text
    assert "Set-ItemProperty -Path $key -Name 'WinpodxMedia'" in text
    assert "agent-spawn: wscript+hidden-launcher.vbs" in text
    assert "agent-spawn: direct-powershell-fallback" in text


def test_install_bat_stages_agent_keepalive_launcher() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    # Keep-alive script is part of the launcher-staging loop so it lands in
    # the Public launchers dir like the other wscript-wrapped scripts.
    assert '"agent-keepalive.ps1"' in text


def test_install_bat_registers_keepalive_scheduled_task() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "WinpodxAgentKeepAlive" in text
    assert "Register-ScheduledTask -TaskName 'WinpodxAgentKeepAlive'" in text
    # BOTH triggers: AtLogOn + 1-minute repetition.
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "New-TimeSpan -Minutes 1" in text
    # Interactive user principal -- NOT SYSTEM / S4U -- so discovery +
    # reverse-open keep the user's HKCU / Start Menu context.
    assert "-LogonType Interactive" in text


def test_install_bat_writes_setup_done_before_final_termservice_cycle() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    setup_done_idx = text.index("(echo done)>C:\\winpodx\\setup_done.txt")
    last_step_idx = text.index("REM TermService cycle -- ABSOLUTELY LAST STEP.")
    stop_idx = text.index("net stop TermService /y")
    start_idx = text.index("net start TermService")

    assert setup_done_idx < last_step_idx < stop_idx < start_idx
