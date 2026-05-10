"""Static checks for config/oem/install.bat first-boot bootstrap shim.

The agent-first refactor moved every install decision into
config/oem/install-state-helpers.ps1 + install-step-functions.ps1. The
.bat is now a tiny shim: dot-source the two .ps1 files and run the
orchestrator. Tests below pin only that shim contract -- the body of
each install step lives in pwsh and is exercised by the
pwsh-on-Linux harness owned by test-engineer.

Also includes regression guards for security-review findings on
install-step-functions.ps1 / watchdog.ps1 (token path mismatch,
rotation post-condition, watchdog steady-state behaviour, OEM
token ACL tightening). These are static-grep checks -- the
behavioural tests live under tests/pwsh/ in test-engineer's harness.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OEM_DIR = REPO_ROOT / "config" / "oem"
INSTALL_BAT = OEM_DIR / "install.bat"
STEP_FUNCTIONS = OEM_DIR / "install-step-functions.ps1"
WATCHDOG = OEM_DIR / "agent" / "watchdog.ps1"


def test_install_bat_exists() -> None:
    assert INSTALL_BAT.is_file()


def test_install_bat_has_no_non_ascii() -> None:
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert all(ord(ch) < 128 for ch in text)


def test_install_bat_dot_sources_helpers_then_steps() -> None:
    """Helpers must dot-source first; step-functions reference helpers
    at parse time, so reversing the order would NRE at first call."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    helpers_idx = text.index("install-state-helpers.ps1")
    steps_idx = text.index("install-step-functions.ps1")
    invoke_idx = text.index("Invoke-InstallStateMachine")
    assert helpers_idx < steps_idx < invoke_idx


def test_install_bat_runs_orchestrator() -> None:
    """The .bat exit code must reflect the orchestrator return value
    so dockur's FirstLogonCommand surface (and host wait-ready) sees
    a non-zero exit when the state machine fails."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert "exit (Invoke-InstallStateMachine)" in text
    assert "exit /b %WPX_RC%" in text


def test_install_bat_preflight_checks_helper_files() -> None:
    """A missing .ps1 sibling is a packaging bug. Surface it loudly
    rather than letting powershell cold-start, fail to dot-source,
    and emit a confusing 'Invoke-InstallStateMachine not found'."""
    text = INSTALL_BAT.read_text(encoding="utf-8")
    assert 'if not exist "%WPX_HELPERS%"' in text
    assert 'if not exist "%WPX_STEPS%"' in text


# ----- Security review regression guards -----------------------------


def test_step_functions_token_src_at_oem_root() -> None:
    """Security review #1: WpxAgentTokenSrc must point at C:\\OEM\\agent_token.txt
    (the root of OEM, NOT under C:\\OEM\\agent\\). The host stager
    (utils/agent_token.py) and agent.ps1 ($TokenPath) are the source of
    truth -- they both name the root-level path. The earlier
    C:\\OEM\\agent\\agent_token.txt was a typo that made Phase 0.6 fail
    forever and turned Phase 3 rotation into a no-op."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    assert "$script:WpxAgentTokenSrc  = 'C:\\OEM\\agent_token.txt'" in text
    assert "C:\\OEM\\agent\\agent_token.txt" not in text


def test_step_functions_phase3_hardens_oem_token_cleanup() -> None:
    """Security review #2: Phase 3 cleanup must FAIL the step on
    zero/delete failure (not warn-and-continue). Look for the explicit
    return 1 paths on stat/zero failure, AND the post-condition that
    re-checks the OEM-source state."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    # Hard-fail return paths in the body when zeroing fails.
    assert "'oem_token_zero_failed'" in text
    assert "'oem_token_stat_failed'" in text
    # Post-condition explicitly inspects OEM source, not just the dst.
    assert "ReadAllBytes($script:WpxAgentTokenSrc)" in text


def test_step_functions_phase06_tightens_oem_source_acl() -> None:
    """Security review #12: Phase 0.6 must tighten the ACL on the
    OEM-source token BEFORE reading. Look for /inheritance:r on
    WpxAgentTokenSrc inside the token_staged body's icacls splat."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    # Locate the token_staged body and require the splat to reference both
    # WpxAgentTokenSrc and /inheritance:r (multi-line splat form).
    body_start = text.index("function Invoke-Step-token_staged")
    body_end = text.index("# --- Phase 1: agent_ready", body_start)
    body = text[body_start:body_end]
    assert "$script:WpxAgentTokenSrc" in body
    assert "/inheritance:r" in body


def test_step_functions_phase06_grants_rw_not_bare_r_on_oem_source() -> None:
    """Security review #18 (re-review BLOCKER): the OEM-source ACL grant
    must be (R,W), not bare R. Phase 3 install_complete zeroes this same
    file via [IO.File]::WriteAllBytes before deletion; a read-only ACL
    here would throw UnauthorizedAccessException at zero-write time."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    body_start = text.index("function Invoke-Step-token_staged")
    body_end = text.index("# --- Phase 1: agent_ready", body_start)
    body = text[body_start:body_end]
    # Splat form: a /grant:r line followed by ${user}:(R,W) on the next line.
    assert '${user}:(R,W)' in body, (
        "Phase 0.6 OEM-source grant lost W permission -- Phase 3 zero-write "
        "will throw UnauthorizedAccessException. See security review #18."
    )


def test_step_functions_rdprrap_pin_regex_accepts_digits() -> None:
    """Smoke test 2026-05-10: the pin-file parser used `[a-zA-Z_]+` for
    key names, which silently dropped the `sha256=` line (`256` is not
    in the letter-only group). $cfg.sha256 stayed null, the body wrote
    'pin_incomplete' to stdout (not stderr), and the orchestrator-side
    log only captured stderr -- so the diagnostic was invisible.

    Pin the digit-tolerant pattern (`\\w+` or `[a-zA-Z0-9_]+`) so this
    can't regress."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    rdprrap_start = text.index("# --- Phase 2: rdprrap_installed")
    rdprrap_end = text.index("# --- Phase 2: vbs_launchers", rdprrap_start)
    body = text[rdprrap_start:rdprrap_end]
    # Either \w+ or [a-zA-Z0-9_]+ is acceptable; bare [a-zA-Z_]+ is not.
    assert "[a-zA-Z_]+)=" not in body, (
        "rdprrap pin regex regressed to letter-only key pattern -- sha256= "
        "line will silently drop, breaking every install with "
        "'pin_incomplete'."
    )


def test_step_functions_agent_exec_logs_stdout_on_failure() -> None:
    """Smoke test 2026-05-10: Invoke-WinpodxAgentStep originally logged
    only stderr on failure paths, but Phase 2 step bodies use
    Write-Output (stdout) for their diagnostic messages. The
    rdprrap_installed regression surfaced 'pin_incomplete:...' via
    stdout but install.log showed empty stderr only -- root cause took
    a manual VNC repro to find.

    Pin both stdout and stderr in the agent_exec_failed and
    agent_exec_nonzero log paths so a refactor can't drop either."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    factory_start = text.index("function Invoke-WinpodxAgentStep")
    factory_end = text.index("# --- Phase 2: rdprrap_installed", factory_start)
    factory = text[factory_start:factory_end]
    # Both event names must capture both streams.
    for event in ("agent_exec_failed", "agent_exec_nonzero"):
        chunk_start = factory.index(f"'{event}'")
        # Take a 400-char window after the event name to span the -Extra
        # hashtable.
        window = factory[chunk_start:chunk_start + 400]
        assert "stdout" in window, f"{event} log missing stdout capture"
        assert "stderr" in window, f"{event} log missing stderr capture"


def test_step_functions_phase06_grants_system_and_admins_by_sid() -> None:
    """Production smoke test (2026-05-10): /inheritance:r removes
    inherited ACEs for SYSTEM and Administrators too. If the /grant
    only targets the auto-logon user and that grant fails (icacls error
    swallowed by 2>&1 | Out-Null), the file becomes unreadable to
    everyone -- the actual failure mode observed on the first
    agent-first install.

    Pin the SYSTEM SID and Administrators SID in the grant chain so a
    refactor can't drop them. Use SIDs (not names) to survive locale
    differences (Korean / Japanese / German Windows translates the
    canonical names)."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    body_start = text.index("function Invoke-Step-token_staged")
    body_end = text.index("# --- Phase 1: agent_ready", body_start)
    body = text[body_start:body_end]
    assert "*S-1-5-18:(R,W)" in body, "SYSTEM SID grant missing in token_staged"
    assert "*S-1-5-32-544:(R,W)" in body, (
        "Administrators SID grant missing in token_staged"
    )


def test_step_functions_phase06_checks_lastexitcode_on_icacls() -> None:
    """Production smoke test (2026-05-10): the original code piped icacls
    stderr into Out-Null and never checked $LASTEXITCODE. When the grant
    silently failed, the script proceeded with a now-unreadable file and
    Copy-Item threw "Access is denied" downstream, with no diagnostic
    trail. Pin the LASTEXITCODE check so any refactor that drops it
    fails this test."""
    text = STEP_FUNCTIONS.read_text(encoding="utf-8")
    body_start = text.index("function Invoke-Step-token_staged")
    body_end = text.index("# --- Phase 1: agent_ready", body_start)
    body = text[body_start:body_end]
    assert "$LASTEXITCODE -ne 0" in body, (
        "token_staged must check $LASTEXITCODE on icacls -- silent failure "
        "regression from 2026-05-10 first-install attempt."
    )
    # And the failure path must log + return 1, not warn-and-continue.
    assert "icacls_src_failed" in body
    assert "icacls_dst_failed" in body


def test_watchdog_branches_on_install_complete_marker() -> None:
    """Security review #6: watchdog must branch behaviour on the
    install_complete marker -- 3-cycle hard-exit during install, but
    indefinite respawn with exponential backoff in steady state.
    Pin both the marker constant and the steady backoff schedule."""
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "install_complete.done" in text
    assert "Test-SteadyState" in text
    # Backoff schedule explicit values -- 30s, 60s, 120s, 240s, 300s cap.
    assert "$script:SteadyBackoffSecs = @(30, 60, 120, 240, 300)" in text


def test_watchdog_writes_steady_state_to_separate_log() -> None:
    """Security review #6: steady-state events go to watchdog.log,
    NOT install.log -- avoids unbounded growth of the install-time
    structured stream during long-lived sessions."""
    text = WATCHDOG.read_text(encoding="utf-8")
    assert "$script:WatchdogLog = 'C:\\winpodx\\install-state\\watchdog.log'" in text
    # The mode-aware logger picks WatchdogLog when steady, install.log
    # otherwise. Pin the conditional shape.
    assert "if (Test-SteadyState)" in text
