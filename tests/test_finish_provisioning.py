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

    def fake_discovery(cfg, *, retries, on_progress=None):
        calls["discovery"].append(retries)
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
            retries=6,
            verbose=False,
        )
    )
    assert rc == 0
    assert captured["wait_timeout"] == 3600
    assert captured["require_agent"] is False
    assert captured["with_discovery"] is True
    assert captured["with_reverse_open"] is True
    assert captured["retries"] == 6


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
