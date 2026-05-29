# SPDX-License-Identifier: MIT
"""Tests for ``core.provisioner.finish_provisioning`` (0.6.0 item B).

The unified post-pod-running provisioning chain (wait-ready → agent-settle
→ apply-fixes → discovery → reverse-open) and its ``winpodx provision`` CLI.
Every stage is mocked so no real pod / FreeRDP / agent is touched; the tests
pin each branch + parameter gate.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from winpodx.core import provisioner
from winpodx.core.config import Config
from winpodx.core.provisioner import (
    ProvisionAgentUnavailable,
    finish_provisioning,
)


def _cfg(*, backend: str = "podman", reverse_open: bool = True) -> Config:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.reverse_open.enabled = reverse_open
    return cfg


class _FakeTransport:
    """Stand-in for AgentTransport whose /health is configurable."""

    def __init__(self, available: bool) -> None:
        self._available = available

    def health(self):
        return SimpleNamespace(available=self._available, detail="")


def _patch_stages(
    monkeypatch,
    *,
    wait_ready: bool = True,
    agent_available: bool = True,
    apply_results: dict | None = None,
    discovery_count: int = 7,
    discovery_raises: Exception | None = None,
    reverse_open_raises: Exception | None = None,
):
    """Patch every finish_provisioning stage; return a call-record dict."""
    calls: dict[str, object] = {
        "wait": [],
        "apply": 0,
        "discovery": [],
        "reverse": 0,
        "sleep": 0,
    }

    monkeypatch.setattr(
        provisioner,
        "wait_for_windows_responsive",
        lambda cfg, timeout: calls["wait"].append(timeout) or wait_ready,
    )
    monkeypatch.setattr(
        "winpodx.core.transport.agent.AgentTransport",
        lambda cfg: _FakeTransport(agent_available),
    )
    monkeypatch.setattr(
        provisioner,
        "apply_windows_runtime_fixes",
        lambda cfg: (
            (calls.__setitem__("apply", calls["apply"] + 1))
            or (apply_results if apply_results is not None else {"max_sessions": "ok"})
        ),
    )

    def fake_discovery(cfg, *, retries, require_agent=False, on_progress=None):
        calls["discovery"].append(retries)
        calls.setdefault("discovery_require_agent", []).append(require_agent)
        if discovery_raises is not None:
            raise discovery_raises
        return discovery_count

    monkeypatch.setattr(provisioner, "_run_discovery_with_retry", fake_discovery)

    def fake_reverse(cfg):
        calls["reverse"] = calls["reverse"] + 1
        if reverse_open_raises is not None:
            raise reverse_open_raises

    monkeypatch.setattr(provisioner, "_run_reverse_open", fake_reverse)
    # Make the soft-settle poll's sleep a no-op + counter (tests never block).
    monkeypatch.setattr(
        provisioner.time, "sleep", lambda s: calls.__setitem__("sleep", calls["sleep"] + 1)
    )
    return calls


# --- backend gate --------------------------------------------------------


@pytest.mark.parametrize("backend", ["libvirt", "manual"])
def test_finish_provisioning_skips_non_container_backend(backend, monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(backend=backend))
    assert "backend" in results
    assert "skipped" in results["backend"]
    # No stage ran.
    assert calls["wait"] == []
    assert calls["apply"] == 0
    assert calls["discovery"] == []
    assert calls["reverse"] == 0


# --- stage 1: wait-ready -------------------------------------------------


def test_wait_ready_timeout_short_circuits(monkeypatch):
    calls = _patch_stages(monkeypatch, wait_ready=False)
    results = finish_provisioning(_cfg(), wait_timeout=300)
    assert results["wait_ready"] == "timeout"
    assert calls["wait"] == [300]
    # Nothing downstream runs once RDP never opens.
    assert calls["apply"] == 0
    assert calls["discovery"] == []
    assert calls["reverse"] == 0


def test_wait_timeout_is_forwarded(monkeypatch):
    calls = _patch_stages(monkeypatch)
    finish_provisioning(_cfg(), wait_timeout=1234)
    assert calls["wait"] == [1234]


# --- stage 2: agent settle ----------------------------------------------


def test_require_agent_raises_when_health_down(monkeypatch):
    calls = _patch_stages(monkeypatch, agent_available=False)
    with pytest.raises(ProvisionAgentUnavailable):
        finish_provisioning(_cfg(), require_agent=True)
    # Hard gate fires before apply / discovery / reverse.
    assert calls["apply"] == 0
    assert calls["discovery"] == []
    assert calls["reverse"] == 0


def test_require_agent_ok_when_health_up(monkeypatch):
    _patch_stages(monkeypatch, agent_available=True)
    results = finish_provisioning(_cfg(), require_agent=True)
    assert results["agent_settle"] == "ok"


def test_soft_settle_proceeds_when_agent_never_up(monkeypatch):
    calls = _patch_stages(monkeypatch, agent_available=False)
    results = finish_provisioning(_cfg(), require_agent=False)
    # Soft poll proceeds (no raise); records the not-up state but continues.
    assert results["agent_settle"].startswith("not-up")
    assert calls["apply"] == 1  # downstream stages still ran
    # 30 poll attempts each slept once.
    assert calls["sleep"] == 30


def test_soft_settle_breaks_early_when_agent_up(monkeypatch):
    calls = _patch_stages(monkeypatch, agent_available=True)
    results = finish_provisioning(_cfg(), require_agent=False)
    assert results["agent_settle"] == "ok"
    assert calls["sleep"] == 0  # broke on first poll, no sleeps


# --- stage 3: apply-fixes ------------------------------------------------


def test_apply_fixes_always_runs_and_results_pass_through(monkeypatch):
    custom = {"max_sessions": "ok", "rdp_timeouts": "failed: boom"}
    calls = _patch_stages(monkeypatch, apply_results=custom)
    results = finish_provisioning(_cfg())
    assert calls["apply"] == 1
    assert results["apply_fixes"] == custom


# --- stage 4: discovery --------------------------------------------------


def test_discovery_on_passes_retries_and_count(monkeypatch):
    calls = _patch_stages(monkeypatch, discovery_count=12)
    results = finish_provisioning(_cfg(), with_discovery=True, retries=3)
    assert calls["discovery"] == [3]
    assert results["discovery"] == "12 apps"


def test_discovery_off_is_skipped(monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(), with_discovery=False)
    assert calls["discovery"] == []
    assert results["discovery"] == "skipped"


def test_discovery_failure_is_recorded_not_raised(monkeypatch):
    _patch_stages(monkeypatch, discovery_raises=RuntimeError("agent flaked"))
    results = finish_provisioning(_cfg(), with_discovery=True)
    assert results["discovery"].startswith("failed:")
    # Reverse-open still runs after a discovery failure (best-effort chain).
    assert results["reverse_open"] == "ok"


# --- stage 5: reverse-open ----------------------------------------------


def test_reverse_open_on_when_enabled(monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(reverse_open=True), with_reverse_open=True)
    assert calls["reverse"] == 1
    assert results["reverse_open"] == "ok"


def test_reverse_open_flag_off_skips(monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(reverse_open=True), with_reverse_open=False)
    assert calls["reverse"] == 0
    assert results["reverse_open"] == "skipped"


def test_reverse_open_gated_on_cfg_enabled(monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(reverse_open=False), with_reverse_open=True)
    # Even with with_reverse_open=True, cfg.reverse_open.enabled=False skips.
    assert calls["reverse"] == 0
    assert results["reverse_open"] == "skipped"


def test_reverse_open_failure_recorded(monkeypatch):
    _patch_stages(monkeypatch, reverse_open_raises=RuntimeError("listener died"))
    results = finish_provisioning(_cfg(reverse_open=True), with_reverse_open=True)
    assert results["reverse_open"].startswith("failed:")


# --- progress callback ---------------------------------------------------


def test_on_progress_receives_every_stage(monkeypatch):
    _patch_stages(monkeypatch)
    stages: list[str] = []
    finish_provisioning(_cfg(), on_progress=lambda stage, detail: stages.append(stage))
    # All five stages emit at least one progress event.
    for expected in (
        "wait_ready",
        "agent_settle",
        "apply_fixes",
        "discovery",
        "reverse_open",
    ):
        assert expected in stages


def test_on_progress_exception_is_swallowed(monkeypatch):
    _patch_stages(monkeypatch)

    def boom(stage, detail):
        raise ValueError("callback blew up")

    # A crashing callback must NOT abort provisioning.
    results = finish_provisioning(_cfg(), on_progress=boom)
    assert results["wait_ready"] == "ok"


# --- discovery retry helper (the 6× behaviour) ---------------------------


def test_discovery_retry_succeeds_after_transient_failures(monkeypatch):
    attempts = {"n": 0}

    def flaky(cfg, timeout=180):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("agent transitioning")
        return ["app1", "app2"]

    monkeypatch.setattr("winpodx.core.discovery.discover_apps", flaky)
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda apps: None)
    monkeypatch.setattr("winpodx.cli.app._register_desktop_entries", lambda apps: None)
    monkeypatch.setattr(provisioner.time, "sleep", lambda s: None)

    count = provisioner._run_discovery_with_retry(_cfg(), retries=6)
    assert count == 2
    assert attempts["n"] == 3  # failed twice, succeeded on third


def test_discovery_retry_raises_after_exhausting(monkeypatch):
    def always_fail(cfg, timeout=180):
        raise RuntimeError("never settles")

    monkeypatch.setattr("winpodx.core.discovery.discover_apps", always_fail)
    monkeypatch.setattr("winpodx.core.discovery.persist_discovered", lambda apps: None)
    monkeypatch.setattr("winpodx.cli.app._register_desktop_entries", lambda apps: None)
    monkeypatch.setattr(provisioner.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="never settles"):
        provisioner._run_discovery_with_retry(_cfg(), retries=3)


# --- ProvisionAgentUnavailable is a ProvisionError ----------------------


def test_agent_unavailable_is_provision_error():
    from winpodx.core.provisioner import ProvisionError

    assert issubclass(ProvisionAgentUnavailable, ProvisionError)


# === CLI: winpodx provision ==============================================


def test_provision_cli_help_lists_every_flag(capsys):
    from winpodx.cli.main import cli as main

    with pytest.raises(SystemExit) as exc:
        main(["provision", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in (
        "--wait-timeout",
        "--require-agent",
        "--no-discovery",
        "--no-reverse-open",
        "--retries",
        "--verbose",
    ):
        assert flag in out


def test_provision_cli_maps_flags_to_helper(monkeypatch):
    from winpodx.cli import main as main_mod

    captured: dict = {}

    def fake_finish(cfg, **kwargs):
        captured.update(kwargs)
        return {"wait_ready": "ok", "reverse_open": "skipped"}

    monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: _cfg()))
    monkeypatch.setattr("winpodx.core.provisioner.finish_provisioning", fake_finish)

    rc = main_mod._cmd_provision(
        argparse.Namespace(
            wait_timeout=900,
            require_agent=True,
            no_discovery=True,
            no_reverse_open=True,
            retries=2,
            verbose=False,
        )
    )
    assert rc == 0
    assert captured["wait_timeout"] == 900
    assert captured["require_agent"] is True
    assert captured["with_discovery"] is False
    assert captured["with_reverse_open"] is False
    assert captured["retries"] == 2


def test_provision_cli_defaults_match_install_sh(monkeypatch):
    """No flags == install.sh's post-create defaults (item I AppImage parity)."""
    from winpodx.cli import main as main_mod

    captured: dict = {}

    def fake_finish(cfg, **kwargs):
        captured.update(kwargs)
        return {"wait_ready": "ok"}

    monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: _cfg()))
    monkeypatch.setattr("winpodx.core.provisioner.finish_provisioning", fake_finish)

    rc = main_mod._cmd_provision(
        argparse.Namespace(
            wait_timeout=3600,
            require_agent=False,
            no_discovery=False,
            no_reverse_open=False,
            retries=2,
            verbose=False,
        )
    )
    assert rc == 0
    assert captured["wait_timeout"] == 3600
    assert captured["require_agent"] is False
    assert captured["with_discovery"] is True
    assert captured["with_reverse_open"] is True
    assert captured["retries"] == 2


def test_provision_cli_returns_5_on_agent_unavailable(monkeypatch):
    from winpodx.cli import main as main_mod

    def boom(cfg, **kwargs):
        raise ProvisionAgentUnavailable("agent down")

    monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: _cfg()))
    monkeypatch.setattr("winpodx.core.provisioner.finish_provisioning", boom)

    rc = main_mod._cmd_provision(
        argparse.Namespace(
            wait_timeout=3600,
            require_agent=True,
            no_discovery=False,
            no_reverse_open=False,
            retries=6,
            verbose=False,
        )
    )
    assert rc == 5


def test_provision_cli_returns_4_on_wait_timeout(monkeypatch):
    from winpodx.cli import main as main_mod

    monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: _cfg()))
    monkeypatch.setattr(
        "winpodx.core.provisioner.finish_provisioning",
        lambda cfg, **kwargs: {"wait_ready": "timeout"},
    )

    rc = main_mod._cmd_provision(
        argparse.Namespace(
            wait_timeout=3600,
            require_agent=False,
            no_discovery=False,
            no_reverse_open=False,
            retries=6,
            verbose=False,
        )
    )
    assert rc == 4


def test_provision_cli_rejects_non_container_backend(monkeypatch):
    from winpodx.cli import main as main_mod

    monkeypatch.setattr(
        "winpodx.core.config.Config.load", staticmethod(lambda: _cfg(backend="manual"))
    )
    rc = main_mod._cmd_provision(
        argparse.Namespace(
            wait_timeout=3600,
            require_agent=False,
            no_discovery=False,
            no_reverse_open=False,
            retries=6,
            verbose=False,
        )
    )
    assert rc == 2


# === --create-only removal ===============================================


def test_setup_help_no_longer_lists_create_only(capsys):
    from winpodx.cli.main import cli as main

    with pytest.raises(SystemExit) as exc:
        main(["setup", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--create-only" not in out


def test_setup_rejects_create_only_flag(capsys):
    from winpodx.cli.main import cli as main

    with pytest.raises(SystemExit) as exc:
        main(["setup", "--create-only"])
    # argparse rejects unknown args with exit code 2.
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "create-only" in err


# --- 0.6.0 item B follow-up: behaviors restored after the first-cut blind
# unification regressed them (dynamic wait #126, agent-first #271). ----------


def test_wait_fn_override_used_when_supplied(monkeypatch):
    # The CLI / setup wizard inject a rich (log-streaming) wait via wait_fn.
    # When supplied it must be used INSTEAD of the silent
    # wait_for_windows_responsive (regression: item-B cut ignored it and the
    # fresh-install boot was a silent multi-minute hang).
    calls = _patch_stages(monkeypatch)
    seen: list[int] = []

    def my_wait(cfg, timeout):
        seen.append(timeout)
        return True

    finish_provisioning(_cfg(), wait_timeout=4242, with_reverse_open=False, wait_fn=my_wait)
    assert seen == [4242]
    # The silent wait must NOT have run when wait_fn was supplied.
    assert calls["wait"] == []


def test_silent_wait_used_when_no_wait_fn(monkeypatch):
    calls = _patch_stages(monkeypatch)
    finish_provisioning(_cfg(), wait_timeout=300, with_reverse_open=False, with_discovery=False)
    assert calls["wait"] == [300]


def test_wait_fn_timeout_returns_results_without_downstream(monkeypatch):
    calls = _patch_stages(monkeypatch)
    results = finish_provisioning(_cfg(), wait_fn=lambda cfg, t: False)
    assert results["wait_ready"] == "timeout"
    # wait-ready failed -> apply / discovery never ran.
    assert calls["apply"] == 0
    assert calls["discovery"] == []


def test_require_agent_exports_env_around_apply_and_discovery(monkeypatch):
    # #271: require_agent must export WINPODX_REQUIRE_AGENT=1 so the
    # env-honouring guest-side code (discovery, migrate apply transport)
    # refuses the FreeRDP fallback. The first item-B cut only gated the
    # one-shot settle re-probe, so discovery still fell back to FreeRDP.
    import os

    seen_env: dict[str, str | None] = {}

    def record_env_apply(cfg):
        seen_env["apply"] = os.environ.get("WINPODX_REQUIRE_AGENT")
        return {"max_sessions": "ok"}

    monkeypatch.setattr(provisioner, "apply_windows_runtime_fixes", record_env_apply)

    def record_env_discovery(cfg, *, retries, require_agent=False, on_progress=None):
        seen_env["discovery"] = os.environ.get("WINPODX_REQUIRE_AGENT")
        return 5

    monkeypatch.setattr(provisioner, "_run_discovery_with_retry", record_env_discovery)
    monkeypatch.setattr(provisioner, "_run_reverse_open", lambda cfg: None)
    monkeypatch.setattr(
        "winpodx.core.transport.agent.AgentTransport", lambda cfg: _FakeTransport(True)
    )
    monkeypatch.setattr(provisioner, "wait_for_windows_responsive", lambda cfg, timeout: True)

    monkeypatch.delenv("WINPODX_REQUIRE_AGENT", raising=False)
    finish_provisioning(_cfg(), require_agent=True, with_reverse_open=False)
    assert seen_env["apply"] == "1"
    assert seen_env["discovery"] == "1"
    # Restored (unset) after the chain.
    assert os.environ.get("WINPODX_REQUIRE_AGENT") is None


def test_require_agent_false_leaves_env_untouched(monkeypatch):
    import os

    seen: dict[str, str | None] = {}
    monkeypatch.setattr(
        provisioner,
        "apply_windows_runtime_fixes",
        lambda cfg: (
            seen.__setitem__("env", os.environ.get("WINPODX_REQUIRE_AGENT"))
            or {"max_sessions": "ok"}
        ),
    )
    monkeypatch.setattr(provisioner, "_run_discovery_with_retry", lambda cfg, **k: 0)
    monkeypatch.setattr(provisioner, "_run_reverse_open", lambda cfg: None)
    monkeypatch.setattr(
        "winpodx.core.transport.agent.AgentTransport", lambda cfg: _FakeTransport(True)
    )
    monkeypatch.setattr(provisioner, "wait_for_windows_responsive", lambda cfg, timeout: True)
    monkeypatch.delenv("WINPODX_REQUIRE_AGENT", raising=False)
    finish_provisioning(_cfg(), require_agent=False, with_reverse_open=False)
    assert seen["env"] is None


def test_require_agent_discovery_unavailable_raises_provision_unavailable(monkeypatch):
    # require_agent + discovery's agent_unavailable -> ProvisionAgentUnavailable
    # (caller maps to exit 5 / pending), not a generic "failed" record.
    from winpodx.core.discovery import DiscoveryError

    monkeypatch.setattr(provisioner, "wait_for_windows_responsive", lambda cfg, timeout: True)
    monkeypatch.setattr(
        "winpodx.core.transport.agent.AgentTransport", lambda cfg: _FakeTransport(True)
    )
    monkeypatch.setattr(
        provisioner, "apply_windows_runtime_fixes", lambda cfg: {"max_sessions": "ok"}
    )
    monkeypatch.setattr(provisioner, "_run_reverse_open", lambda cfg: None)

    def boom(cfg, *, retries, require_agent=False, on_progress=None):
        # Mirror _run_discovery_with_retry's own escalation for require_agent.
        if require_agent:
            raise ProvisionAgentUnavailable("agent never came up")
        raise DiscoveryError("x", kind="agent_unavailable")

    monkeypatch.setattr(provisioner, "_run_discovery_with_retry", boom)
    with pytest.raises(ProvisionAgentUnavailable):
        finish_provisioning(_cfg(), require_agent=True, with_reverse_open=False)


def test_discovery_with_retry_escalates_agent_unavailable_when_require_agent(monkeypatch):
    # Unit-test the real _run_discovery_with_retry: with require_agent, a
    # persistent agent_unavailable DiscoveryError escalates to
    # ProvisionAgentUnavailable rather than re-raising the DiscoveryError.
    from winpodx.core import discovery as disc_mod
    from winpodx.core.discovery import DiscoveryError

    monkeypatch.setattr(
        disc_mod,
        "discover_apps",
        lambda cfg, timeout=180: (_ for _ in ()).throw(
            DiscoveryError("agent down", kind="agent_unavailable")
        ),
    )
    monkeypatch.setattr(provisioner.time, "sleep", lambda s: None)
    with pytest.raises(ProvisionAgentUnavailable):
        provisioner._run_discovery_with_retry(_cfg(), retries=2, require_agent=True)


def test_discovery_with_retry_reraises_other_errors_even_with_require_agent(monkeypatch):
    from winpodx.core import discovery as disc_mod
    from winpodx.core.discovery import DiscoveryError

    monkeypatch.setattr(
        disc_mod,
        "discover_apps",
        lambda cfg, timeout=180: (_ for _ in ()).throw(
            DiscoveryError("script broke", kind="script_failed")
        ),
    )
    monkeypatch.setattr(provisioner.time, "sleep", lambda s: None)
    # Non-agent error: re-raised as-is, NOT escalated to ProvisionAgentUnavailable.
    with pytest.raises(DiscoveryError):
        provisioner._run_discovery_with_retry(_cfg(), retries=2, require_agent=True)
