# SPDX-License-Identifier: MIT
"""Unit tests for guest-sync host-side logic.

The guest-mutating steps (OEM delivery, urlacl, agent restart over /exec)
are covered by the real-Windows smoke gate, not here. These tests pin the
version-stamp comparison, the parse, and the trigger gating."""

from __future__ import annotations

import json

import pytest

from winpodx.core.config import Config
from winpodx.core.guest_sync import (
    GuestVersion,
    guest_sync_needed,
    host_version,
    maybe_autosync,
)


def test_host_version_shape() -> None:
    hv = host_version()
    assert isinstance(hv, GuestVersion)
    assert hv.winpodx  # non-empty
    assert hv.oem_bundle  # non-empty


def test_guest_version_equality() -> None:
    a = GuestVersion(winpodx="0.5.8", oem_bundle="25")
    assert a == GuestVersion(winpodx="0.5.8", oem_bundle="25")
    assert a != GuestVersion(winpodx="0.5.9", oem_bundle="25")
    assert a != GuestVersion(winpodx="0.5.8", oem_bundle="26")


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.pod.guest_autosync = True
    return cfg


def test_guest_sync_needed_when_stamp_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: None)
    assert guest_sync_needed(_cfg()) is True


def test_guest_sync_needed_when_older(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "winpodx.core.guest_sync.read_guest_version",
        lambda cfg: GuestVersion(winpodx="0.0.1", oem_bundle="1"),
    )
    assert guest_sync_needed(_cfg()) is True


def test_guest_sync_not_needed_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.read_guest_version", lambda cfg: host_version())
    assert guest_sync_needed(_cfg()) is False


def test_maybe_autosync_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    cfg.pod.guest_autosync = False
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run when disabled"),
    )
    assert maybe_autosync(cfg) is False


def test_maybe_autosync_skips_when_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.guest_sync_needed", lambda cfg: False)
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run when current"),
    )
    assert maybe_autosync(_cfg()) is False


def test_maybe_autosync_runs_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("winpodx.core.guest_sync.guest_sync_needed", lambda cfg: True)
    calls = {"n": 0}
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1),
    )
    assert maybe_autosync(_cfg()) is True
    assert calls["n"] == 1


def test_maybe_autosync_skips_unsupported_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _cfg()
    cfg.pod.backend = "libvirt"
    monkeypatch.setattr(
        "winpodx.core.guest_sync.sync_guest",
        lambda *a, **k: pytest.fail("sync should not run on libvirt"),
    )
    assert maybe_autosync(cfg) is False


def test_read_guest_version_parses_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    from winpodx.core import guest_sync

    class _R:
        ok = True
        stdout = json.dumps({"winpodx": "0.5.8", "oem_bundle": "25"})
        stderr = ""
        rc = 0

    monkeypatch.setattr(guest_sync, "run_in_windows", lambda *a, **k: _R(), raising=False)
    # run_in_windows is imported inside the function; patch the source module.
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", lambda *a, **k: _R())
    gv = guest_sync.read_guest_version(_cfg())
    assert gv == GuestVersion(winpodx="0.5.8", oem_bundle="25")


def test_config_guest_autosync_default_and_coerce() -> None:
    cfg = Config()
    assert cfg.pod.guest_autosync is True
    cfg.pod.guest_autosync = "nope"  # type: ignore[assignment]
    cfg.pod.__post_init__()
    assert cfg.pod.guest_autosync is True
