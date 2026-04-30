"""Tests for winpodx.core.checks — health probes feed `winpodx check` + GUI."""

from __future__ import annotations

import pytest

from winpodx.core import checks
from winpodx.core.checks import Probe


def test_probe_dataclass_is_immutable():
    p = Probe(name="x", status="ok", detail="d", duration_ms=1)
    with pytest.raises(Exception):
        p.status = "fail"  # type: ignore[misc]


def test_probe_is_ok_property():
    assert Probe("x", "ok", "", 0).is_ok
    assert not Probe("x", "warn", "", 0).is_ok
    assert not Probe("x", "fail", "", 0).is_ok
    assert not Probe("x", "skip", "", 0).is_ok


def test_overall_all_ok():
    probes = [Probe("a", "ok", "", 0), Probe("b", "ok", "", 0)]
    assert checks.overall(probes) == "ok"


def test_overall_warn_wins_over_ok():
    probes = [Probe("a", "ok", "", 0), Probe("b", "warn", "", 0)]
    assert checks.overall(probes) == "warn"


def test_overall_fail_wins_over_everything():
    probes = [Probe("a", "ok", "", 0), Probe("b", "warn", "", 0), Probe("c", "fail", "", 0)]
    assert checks.overall(probes) == "fail"


def test_overall_skip_treated_as_neutral():
    """A skip should not pull the verdict away from ok."""
    probes = [Probe("a", "ok", "", 0), Probe("b", "skip", "", 0)]
    assert checks.overall(probes) == "ok"


def test_overall_empty_iterable():
    assert checks.overall([]) == "ok"


def test_probe_never_raises_on_internal_exception():
    """The _timed wrapper must catch any exception and surface as fail.

    A buggy probe should NOT take down `winpodx check` for the whole
    user — kernalix7's UI freezing on a transient AgentClient bug was
    exactly this class of regression.
    """

    def boom(_cfg) -> Probe:
        raise RuntimeError("simulated")

    # The probe wrapper inside checks.py wraps each function; for an
    # external function we apply _timed directly to verify the contract.
    status, detail, ms = checks._timed(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert status == "fail"
    assert "boom" in detail or "RuntimeError" in detail
    assert ms >= 0


def test_run_all_returns_one_probe_per_registered_function(monkeypatch):
    """run_all must call every probe in PROBES and return a Probe for each."""
    fake_cfg = object()
    fake_probes = [Probe(f"p{i}", "ok", f"detail-{i}", i) for i, _ in enumerate(checks.PROBES)]

    monkeypatch.setattr(checks, "PROBES", tuple(lambda _cfg, n=p: n for p in fake_probes))
    out = checks.run_all(fake_cfg)
    assert len(out) == len(fake_probes)
    assert all(isinstance(p, Probe) for p in out)


def test_probe_pod_running_handles_pod_status_failure(monkeypatch):
    """If pod_status raises, the probe must return fail, not propagate."""
    import winpodx.core.pod as pod_mod

    def boom(_cfg):
        raise RuntimeError("backend gone")

    monkeypatch.setattr(pod_mod, "pod_status", boom)
    out = checks.probe_pod_running(_FakeCfg())
    assert out.name == "pod_running"
    assert out.status == "fail"
    assert "backend gone" in out.detail or "RuntimeError" in out.detail


def test_probe_password_age_skip_when_disabled():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 0
    out = checks.probe_password_age(cfg)
    assert out.status == "skip"


def test_probe_password_age_warn_when_no_timestamp():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 7
    cfg.rdp.password_updated = ""
    out = checks.probe_password_age(cfg)
    assert out.status == "warn"


def test_probe_password_age_warn_when_overdue():
    cfg = _FakeCfg()
    cfg.rdp.password_max_age = 1
    cfg.rdp.password_updated = "2020-01-01T00:00:00+00:00"
    out = checks.probe_password_age(cfg)
    assert out.status == "warn"
    assert "overdue" in out.detail


def test_probe_apps_discovered_warn_on_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("winpodx.core.discovery.discovered_apps_dir", lambda: tmp_path / "missing")
    out = checks.probe_apps_discovered(_FakeCfg())
    assert out.status == "warn"
    assert "no apps" in out.detail.lower()


def test_probe_apps_discovered_ok_with_app_subdirs(tmp_path, monkeypatch):
    apps_dir = tmp_path / "discovered"
    apps_dir.mkdir()
    for slug in ("notepad", "msedge"):
        (apps_dir / slug).mkdir()
        (apps_dir / slug / "app.toml").write_text("name = '" + slug + "'\n")
    monkeypatch.setattr("winpodx.core.discovery.discovered_apps_dir", lambda: apps_dir)
    out = checks.probe_apps_discovered(_FakeCfg())
    assert out.status == "ok"
    assert "2 app(s)" in out.detail


# --- Helpers ---


class _FakeCfg:
    """Minimal stand-in for Config — only the fields probes touch."""

    class _Pod:
        backend = "podman"

    class _Rdp:
        ip = "127.0.0.1"
        port = 3390
        password = "x"
        password_max_age = 0
        password_updated = ""

    def __init__(self):
        self.pod = self._Pod()
        self.rdp = self._Rdp()
