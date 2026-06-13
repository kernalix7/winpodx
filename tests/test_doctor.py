# SPDX-License-Identifier: MIT
"""Tests for ``winpodx doctor`` (#255 PR 6)."""

from __future__ import annotations

import argparse

import pytest

from winpodx.cli import doctor
from winpodx.cli.doctor import (
    Finding,
    _check_autostart_entry,
    _check_config_state,
    _check_freerdp,
    _check_initialized_flag,
    _check_kvm,
    _check_pending_setup,
    handle_doctor,
)
from winpodx.core.config import Config
from winpodx.utils.deps import DepCheck


def _stub_new_checks(monkeypatch):
    """Neutralise the 0.6.0 remediable checks so the legacy report tests
    stay deterministic regardless of host state (apps, run dir, pod)."""
    monkeypatch.setattr(doctor, "_check_stale_locks", lambda: Finding("ok", "locks"))
    monkeypatch.setattr(doctor, "_check_missing_desktop_entries", lambda: Finding("ok", "entries"))
    monkeypatch.setattr(doctor, "_check_agent_health", lambda: None)
    monkeypatch.setattr(doctor, "_check_oem_drift", lambda: None)


class TestFindingFormatting:
    def test_severity_tags(self):
        assert Finding("ok", "x").severity_tag().strip() == "[OK]"
        assert Finding("warn", "x").severity_tag().strip() == "[WARN]"
        assert Finding("fail", "x").severity_tag().strip() == "[FAIL]"


class TestPendingCheck:
    def test_no_pending_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        f = _check_pending_setup()
        assert f.severity == "ok"

    def test_pending_marker_returns_warn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cd = tmp_path / "winpodx"
        cd.mkdir(parents=True)
        (cd / ".pending_setup").write_text("wait_ready\ndiscovery\n")
        f = _check_pending_setup()
        assert f.severity == "warn"
        assert "2 item" in f.title


class TestAutostartCheck:
    def test_no_autostart_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        f = _check_autostart_entry()
        assert f.severity == "ok"

    def test_autostart_missing_binary_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        autostart = tmp_path / "autostart" / "winpodx-tray.desktop"
        autostart.parent.mkdir(parents=True)
        autostart.write_text("[Desktop Entry]\nExec=winpodx tray\n")
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_autostart_entry()
        assert f.severity == "fail"


class TestInitializedCheck:
    def test_initialized_true_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.pod.initialized = True
        cfg.save()
        f = _check_initialized_flag()
        assert f.severity == "ok"

    def test_initialized_false_returns_warn(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()  # initialized = False
        f = _check_initialized_flag()
        assert f.severity == "warn"


class TestConfigStateCheck:
    def test_neither_present_warns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_config_state()
        assert f.severity == "warn"
        assert "not installed" in f.title

    def test_config_only_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()
        monkeypatch.setattr("shutil.which", lambda _name: None)
        f = _check_config_state()
        assert f.severity == "fail"
        assert "binary not on PATH" in f.title

    def test_binary_only_warns(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("shutil.which", lambda _name: "/fake/winpodx")
        f = _check_config_state()
        assert f.severity == "warn"
        assert "config missing" in f.title

    def test_both_present_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()
        monkeypatch.setattr("shutil.which", lambda _name: "/fake/winpodx")
        f = _check_config_state()
        assert f.severity == "ok"


class TestKvmCheck:
    def test_present_returns_ok(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.exists", lambda self: str(self) == "/dev/kvm")
        f = _check_kvm()
        assert f.severity == "ok"

    def test_missing_returns_fail(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.exists", lambda self: False)
        f = _check_kvm()
        assert f.severity == "fail"


class TestCheckFreerdp:
    """#546: warn (not fail) on old FreeRDP 3.x with broken RAIL window mapping."""

    def _patch(self, monkeypatch, *, found=True, version="3.5.1"):
        monkeypatch.setattr(
            "winpodx.utils.deps.check_freerdp",
            lambda: DepCheck(name="xfreerdp", found=found, path="/usr/bin/xfreerdp3"),
        )

        class _Res:
            stdout = f"This is FreeRDP version {version} (...)\n" if version else ""
            stderr = ""

        monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _Res())

    def test_old_3x_warns(self, monkeypatch):
        self._patch(monkeypatch, version="3.5.1")
        f = _check_freerdp()
        assert f.severity == "warn"
        assert "546" in f.title and "3.5.1" in f.title

    def test_floor_version_is_ok(self, monkeypatch):
        self._patch(monkeypatch, version="3.6.0")
        assert _check_freerdp().severity == "ok"

    def test_newer_3x_is_ok(self, monkeypatch):
        self._patch(monkeypatch, version="3.10.2")
        assert _check_freerdp().severity == "ok"

    def test_unparseable_version_is_ok(self, monkeypatch):
        # No version string -> can't judge -> don't warn (binary exists).
        self._patch(monkeypatch, version="")
        assert _check_freerdp().severity == "ok"

    def test_missing_fails(self, monkeypatch):
        self._patch(monkeypatch, found=False)
        assert _check_freerdp().severity == "fail"


class TestHandleDoctor:
    def test_exits_zero_on_no_fail(self, tmp_path, monkeypatch, capsys):
        """All checks return OK or WARN -> exit 0."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _stub_new_checks(monkeypatch)
        # Stub every check to OK to keep the test independent of host state.
        ok = Finding("ok", "stub")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: ok)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        handle_doctor(argparse.Namespace())  # no sys.exit -> success
        out = capsys.readouterr().out
        assert "all checks passed" in out

    def test_exits_one_on_fail(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        _stub_new_checks(monkeypatch)
        fail = Finding("fail", "stub fail", suggestion="do something")
        ok = Finding("ok", "stub ok")
        for name in (
            "_check_install_source",
            "_check_freerdp",
            "_check_kvm",
            "_check_config_state",
            "_check_pending_setup",
            "_check_autostart_entry",
            "_check_initialized_flag",
        ):
            monkeypatch.setattr(f"winpodx.cli.doctor.{name}", lambda: fail)
        monkeypatch.setattr("winpodx.cli.doctor._check_container_backend", lambda: [ok])
        monkeypatch.setattr("winpodx.cli.doctor._check_container_health", lambda: [ok])

        with pytest.raises(SystemExit) as exc:
            handle_doctor(argparse.Namespace())
        assert exc.value.code == 1


# -----------------------------------------------------------------------
# 0.6.0 item K: --fix auto-remediation.
# -----------------------------------------------------------------------


def _ns(**kw):
    base = {"json": False, "quick": False, "fix": False}
    base.update(kw)
    return argparse.Namespace(**base)


class TestFindingFixId:
    def test_fix_id_defaults_none(self):
        assert Finding("ok", "x").fix_id is None
        assert Finding("warn", "x", fix_id="stale_locks").fix_id == "stale_locks"


class TestFixerRegistry:
    def test_every_emitted_fix_id_has_fixer_and_reprobe(self):
        emitted = {"stale_locks", "missing_desktop_entries", "dead_agent", "oem_drift"}
        assert emitted <= set(doctor._FIXERS)
        assert emitted <= set(doctor._REPROBES)

    def test_registered_callables(self):
        for fn in doctor._FIXERS.values():
            assert callable(fn)
        for fn in doctor._REPROBES.values():
            assert callable(fn)


def _seed_run_dir(monkeypatch, tmp_path, files):
    """Point data_dir() at tmp_path and write the given {name: pid} cprocs."""
    monkeypatch.setattr("winpodx.utils.paths.data_dir", lambda: tmp_path)
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    for name, pid in files.items():
        (run / name).write_text(str(pid), encoding="utf-8")
    return run


class TestStaleLockFixer:
    def test_dead_vs_live_pid_detection(self, monkeypatch, tmp_path):
        run = _seed_run_dir(monkeypatch, tmp_path, {"dead.cproc": 999999, "live.cproc": 1234})
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: pid == 1234)
        dead = doctor._dead_lock_files()
        assert {p.name for p in dead} == {"dead.cproc"}
        assert (run / "live.cproc").exists()

    def test_fix_purges_only_dead(self, monkeypatch, tmp_path):
        run = _seed_run_dir(monkeypatch, tmp_path, {"dead.cproc": 999999, "live.cproc": 1234})
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: pid == 1234)
        ok, msg = doctor._fix_stale_locks()
        assert ok
        assert "purged 1" in msg
        assert not (run / "dead.cproc").exists()
        assert (run / "live.cproc").exists()

    def test_fix_idempotent_noop_when_clean(self, monkeypatch, tmp_path):
        _seed_run_dir(monkeypatch, tmp_path, {"live.cproc": 1234})
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: True)
        ok, msg = doctor._fix_stale_locks()
        assert ok
        assert "no stale lock files" in msg

    def test_check_flags_dead_with_fix_id(self, monkeypatch, tmp_path):
        _seed_run_dir(monkeypatch, tmp_path, {"dead.cproc": 999999})
        monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", lambda pid: False)
        f = doctor._check_stale_locks()
        assert f.severity == "warn"
        assert f.fix_id == "stale_locks"

    def test_check_ok_when_no_run_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("winpodx.utils.paths.data_dir", lambda: tmp_path)
        f = doctor._check_stale_locks()
        assert f.severity == "ok"


class _FakeApp:
    def __init__(self, name, mime_types=None, hidden=False):
        self.name = name
        self.full_name = name
        self.mime_types = mime_types or []
        self.hidden = hidden


class _ExistsPath:
    def __init__(self, exists):
        self._exists = exists

    def exists(self):
        return self._exists


class TestMissingDesktopEntryFixer:
    def test_detects_missing(self, monkeypatch):
        apps = [_FakeApp("alpha"), _FakeApp("beta")]
        monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: apps)
        present = {"alpha"}
        monkeypatch.setattr(
            doctor, "_desktop_entry_path", lambda app: _ExistsPath(app.name in present)
        )
        missing = doctor._apps_missing_desktop_entries()
        assert [a.name for a in missing] == ["beta"]

    def test_hidden_apps_not_flagged_missing(self, monkeypatch):
        """A hidden app legitimately has no .desktop entry; doctor must not
        report it as missing (which --fix would re-register = un-hide) (#535)."""
        apps = [_FakeApp("alpha"), _FakeApp("hiddenapp", hidden=True)]
        monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: apps)
        # neither has a .desktop on disk
        monkeypatch.setattr(doctor, "_desktop_entry_path", lambda app: _ExistsPath(False))
        missing = doctor._apps_missing_desktop_entries()
        assert [a.name for a in missing] == ["alpha"]  # hidden one skipped

    def test_fix_reregisters(self, monkeypatch):
        apps = [_FakeApp("beta", mime_types=["text/plain"])]
        monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: apps)
        monkeypatch.setattr(doctor, "_desktop_entry_path", lambda app: _ExistsPath(False))
        installed = []
        monkeypatch.setattr(
            "winpodx.desktop.entry.install_desktop_entry",
            lambda app: installed.append(app.name),
        )
        monkeypatch.setattr("winpodx.desktop.mime.register_mime_types", lambda app: None)
        monkeypatch.setattr("winpodx.desktop.icons.update_icon_cache", lambda: None)
        ok, msg = doctor._fix_missing_desktop_entries()
        assert ok
        assert installed == ["beta"]
        assert "re-registered 1" in msg

    def test_fix_noop_when_all_present(self, monkeypatch):
        monkeypatch.setattr("winpodx.core.app.list_available_apps", lambda: [_FakeApp("alpha")])
        monkeypatch.setattr(doctor, "_desktop_entry_path", lambda app: _ExistsPath(True))
        called = []
        monkeypatch.setattr(
            "winpodx.desktop.entry.install_desktop_entry",
            lambda app: called.append(app.name),
        )
        ok, msg = doctor._fix_missing_desktop_entries()
        assert ok
        assert called == []
        assert "no missing desktop entries" in msg

    def test_check_flags_with_fix_id(self, monkeypatch):
        monkeypatch.setattr(doctor, "_apps_missing_desktop_entries", lambda: [_FakeApp("beta")])
        f = doctor._check_missing_desktop_entries()
        assert f.severity == "warn"
        assert f.fix_id == "missing_desktop_entries"


class TestDeadAgentFixer:
    """--fix must DISPATCH to the keep-alive kick; the guest call is mocked."""

    def test_dispatches_keepalive_kick(self, monkeypatch):
        cfg = argparse.Namespace(pod=argparse.Namespace(backend="podman"))
        monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: cfg))
        calls = {}

        def fake_transport(c, ps, *, timeout=60, description=""):
            calls["ps"] = ps
            return Finding("ok", "x")  # any object; result unused

        monkeypatch.setattr("winpodx.core.windows_exec.run_via_transport", fake_transport)
        monkeypatch.setattr("winpodx.core.guest_sync._wait_agent_back", lambda c, **kw: True)
        ok, msg = doctor._fix_dead_agent()
        assert ok
        assert "WinpodxAgentKeepAlive" in calls["ps"]
        assert "Start-ScheduledTask" in calls["ps"]

    def test_reports_still_down_when_health_never_returns(self, monkeypatch):
        cfg = argparse.Namespace(pod=argparse.Namespace(backend="podman"))
        monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: cfg))
        monkeypatch.setattr(
            "winpodx.core.windows_exec.run_via_transport",
            lambda c, ps, **kw: Finding("ok", "x"),
        )
        monkeypatch.setattr("winpodx.core.guest_sync._wait_agent_back", lambda c, **kw: False)
        ok, _ = doctor._fix_dead_agent()
        assert not ok


class TestOemDriftFixer:
    """--fix must DISPATCH to guest_sync.maybe_autosync; the call is mocked."""

    def test_dispatches_maybe_autosync(self, monkeypatch):
        cfg = argparse.Namespace(pod=argparse.Namespace(backend="podman"))
        monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: cfg))
        calls = {}

        def fake_autosync(c):
            calls["cfg"] = c
            return True

        monkeypatch.setattr("winpodx.core.guest_sync.maybe_autosync", fake_autosync)
        ok, msg = doctor._fix_oem_drift()
        assert ok
        assert calls["cfg"] is cfg
        assert "synced" in msg

    def test_reports_already_current(self, monkeypatch):
        cfg = argparse.Namespace(pod=argparse.Namespace(backend="podman"))
        monkeypatch.setattr("winpodx.core.config.Config.load", staticmethod(lambda: cfg))
        monkeypatch.setattr("winpodx.core.guest_sync.maybe_autosync", lambda c: False)
        ok, msg = doctor._fix_oem_drift()
        assert ok
        assert "already current" in msg


def _all_ok_legacy(monkeypatch):
    monkeypatch.setattr(doctor, "_check_install_source", lambda: Finding("ok", "src"))
    monkeypatch.setattr(doctor, "_check_freerdp", lambda: Finding("ok", "frdp"))
    monkeypatch.setattr(doctor, "_check_kvm", lambda: Finding("ok", "kvm"))
    monkeypatch.setattr(doctor, "_check_container_backend", lambda: [Finding("ok", "be")])
    monkeypatch.setattr(doctor, "_check_config_state", lambda: Finding("ok", "cfg"))
    monkeypatch.setattr(doctor, "_check_pending_setup", lambda: Finding("ok", "pending"))
    monkeypatch.setattr(doctor, "_check_autostart_entry", lambda: Finding("ok", "auto"))
    monkeypatch.setattr(doctor, "_check_initialized_flag", lambda: Finding("ok", "init"))
    monkeypatch.setattr(doctor, "_check_container_health", lambda: [])
    monkeypatch.setattr(doctor, "_check_agent_health", lambda: None)
    monkeypatch.setattr(doctor, "_check_oem_drift", lambda: None)
    monkeypatch.setattr(doctor, "_check_missing_desktop_entries", lambda: Finding("ok", "e"))


class TestHandleDoctorFix:
    def test_fix_dispatches_registered_fixer(self, capsys, monkeypatch):
        _all_ok_legacy(monkeypatch)
        monkeypatch.setattr(
            doctor,
            "_check_stale_locks",
            lambda: Finding("warn", "stale", fix_id="stale_locks"),
        )
        dispatched = []
        monkeypatch.setitem(
            doctor._FIXERS,
            "stale_locks",
            lambda: dispatched.append(1) or (True, "purged 1"),
        )
        monkeypatch.setitem(doctor._REPROBES, "stale_locks", lambda: Finding("ok", "clean"))
        handle_doctor(_ns(fix=True))
        out = capsys.readouterr().out
        assert dispatched == [1]
        assert "[fixed]" in out

    def test_fix_reports_no_autofix_for_unknown(self, capsys, monkeypatch):
        _all_ok_legacy(monkeypatch)
        monkeypatch.setattr(doctor, "_check_stale_locks", lambda: Finding("ok", "locks"))
        # A warn with NO fix_id -> "no auto-fix available".
        monkeypatch.setattr(doctor, "_check_freerdp", lambda: Finding("warn", "frdp slow"))
        handle_doctor(_ns(fix=True))
        out = capsys.readouterr().out
        assert "no auto-fix available" in out

    def test_fix_still_failing_keeps_exit_1(self, monkeypatch):
        _all_ok_legacy(monkeypatch)
        monkeypatch.setattr(
            doctor,
            "_check_stale_locks",
            lambda: Finding("fail", "broken", fix_id="stale_locks"),
        )
        monkeypatch.setitem(doctor._FIXERS, "stale_locks", lambda: (False, "could not fix"))
        monkeypatch.setitem(
            doctor._REPROBES, "stale_locks", lambda: Finding("fail", "still broken")
        )
        with pytest.raises(SystemExit) as exc:
            handle_doctor(_ns(fix=True))
        assert exc.value.code == 1
