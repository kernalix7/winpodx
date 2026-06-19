# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.listener``.

Spawning is mocked via the ``spawn`` constructor argument so no real
subprocess is forked. TOCTOU-safe path resolution is exercised
end-to-end (the listener calls into Phase 1's ``safe_open_unc``).
"""

from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from winpodx.reverse_open.apps_db import AppEntry, AppsDatabase
from winpodx.reverse_open.listener import (
    Listener,
    ListenerConfig,
    _load_json_depth_limited,
    _validate_schema,
)
from winpodx.reverse_open.seen_uuids import SeenUUIDs

# --- helpers ----------------------------------------------------------------


def _incoming(tmp_path: Path) -> Path:
    p = tmp_path / "incoming"
    p.mkdir()
    return p


def _apps_db_with(slug: str, exec_argv: list[str]) -> AppsDatabase:
    entry = AppEntry(
        slug=slug,
        name="Test",
        comment="",
        exec_argv=exec_argv,
        icon_name="",
        mime_types=["text/plain"],
        desktop_file="/x.desktop",
    )
    return AppsDatabase({slug: entry}, "2026-05-11T00:00:00Z")


def _seen(tmp_path: Path) -> SeenUUIDs:
    return SeenUUIDs(tmp_path / "seen.json")


def _write_request(incoming: Path, body: dict, name: str | None = None) -> str:
    uid = name or uuid.uuid4().hex
    (incoming / f"{uid}.json").write_text(json.dumps(body), encoding="utf-8")
    return uid


def _valid_request(target_path: Path) -> dict:
    """Build a schema-valid request that resolves to ``target_path``.

    Phase 1's path layer accepts ``\\\\tsclient\\home\\<rel>`` only.
    ``target_path`` must already live under ``Path.home()`` so the
    listener's share_roots map resolves it cleanly.
    """
    home = Path.home().resolve()
    rel = target_path.resolve().relative_to(home)
    unc = "\\\\tsclient\\home\\" + str(rel).replace("/", "\\")
    return {
        "version": 1,
        "app": "kate",
        "path": unc,
        "ts": "2026-05-11T00:00:00Z",
        "pod_id": None,
    }


@pytest.fixture
def spawn_capture() -> tuple[list, callable]:
    captured: list[tuple[list[str], dict]] = []

    def spawn(argv: list[str], popen_kwargs: dict) -> object:
        captured.append((argv, dict(popen_kwargs)))
        return None

    return captured, spawn


@pytest.fixture
def home_under_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Pin ``Path.home()`` to a fresh tmp directory for path-translation tests."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    yield home


# --- helpers under test ------------------------------------------------------


def test_load_json_depth_limited_rejects_deep() -> None:
    payload = "{}"
    for _ in range(20):
        payload = '{"x":' + payload + "}"
    with pytest.raises(ValueError, match="depth"):
        _load_json_depth_limited(payload, max_depth=8)


def test_load_json_depth_limited_accepts_shallow() -> None:
    payload = json.dumps({"version": 1, "app": "kate"})
    data = _load_json_depth_limited(payload, max_depth=8)
    assert isinstance(data, dict)


def test_validate_schema_rejects_non_object() -> None:
    assert _validate_schema([1, 2]) is not None
    assert _validate_schema(None) is not None


def test_validate_schema_rejects_wrong_version() -> None:
    err = _validate_schema(
        {"version": 99, "app": "kate", "path": "\\\\tsclient\\home\\x", "ts": "t"}
    )
    assert err and "version" in err


def test_validate_schema_accepts_v2_host_origin() -> None:
    err = _validate_schema(
        {
            "version": 2,
            "app": "kate",
            "path": "\\\\tsclient\\home\\x",
            "origin": "host",
            "ts": "t",
        }
    )
    assert err is None


def test_validate_schema_accepts_v2_guest_drive_path() -> None:
    err = _validate_schema(
        {
            "version": 2,
            "app": "kate",
            "path": "C:\\Users\\me\\Desktop\\x.txt",
            "origin": "guest",
            "ts": "t",
        }
    )
    assert err is None


def test_validate_schema_rejects_guest_origin_with_unc_path() -> None:
    # origin=guest must carry a drive path, not a \\tsclient\ UNC.
    err = _validate_schema(
        {
            "version": 2,
            "app": "kate",
            "path": "\\\\tsclient\\home\\x",
            "origin": "guest",
            "ts": "t",
        }
    )
    assert err and "guest path" in err


def test_validate_schema_rejects_bad_origin() -> None:
    err = _validate_schema(
        {
            "version": 2,
            "app": "kate",
            "path": "\\\\tsclient\\home\\x",
            "origin": "elsewhere",
            "ts": "t",
        }
    )
    assert err and "origin" in err


def test_validate_schema_rejects_bad_slug() -> None:
    err = _validate_schema(
        {"version": 1, "app": "Bad Slug", "path": "\\\\tsclient\\home\\x", "ts": "t"}
    )
    assert err and "app" in err


def test_validate_schema_rejects_nul_in_path() -> None:
    err = _validate_schema(
        {"version": 1, "app": "kate", "path": "\\\\tsclient\\home\\x\x00y", "ts": "t"}
    )
    assert err and "NUL" in err


def test_validate_schema_rejects_path_without_tsclient_prefix() -> None:
    err = _validate_schema({"version": 1, "app": "kate", "path": "/etc/passwd", "ts": "t"})
    assert err and "tsclient" in err


def test_validate_schema_accepts_minimal_valid() -> None:
    assert (
        _validate_schema(
            {
                "version": 1,
                "app": "kate",
                "path": "\\\\tsclient\\home\\file.txt",
                "ts": "2026-05-11T00:00:00Z",
                "pod_id": None,
            }
        )
        is None
    )


# --- preflight ---------------------------------------------------------------


def test_preflight_refuses_missing_dir(tmp_path: Path) -> None:
    cfg = ListenerConfig(incoming_dir=tmp_path / "nope", share_roots={})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path))
    with pytest.raises(FileNotFoundError):
        listener.preflight()


def test_preflight_refuses_world_writable_dir(tmp_path: Path) -> None:
    inc = _incoming(tmp_path)
    inc.chmod(0o777)
    cfg = ListenerConfig(incoming_dir=inc, share_roots={})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path))
    with pytest.raises(PermissionError):
        listener.preflight()


def test_preflight_accepts_owner_only(tmp_path: Path) -> None:
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    cfg = ListenerConfig(incoming_dir=inc, share_roots={})
    Listener(cfg, AppsDatabase.empty(), _seen(tmp_path)).preflight()  # no raise


# --- process_pending --------------------------------------------------------


def test_process_pending_drops_oversize_requests(tmp_path: Path, spawn_capture: tuple) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    big = "x" * 200_000
    name = uuid.uuid4().hex
    (inc / f"{name}.json").write_text(big, encoding="utf-8")

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": Path.home()})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    stats = listener.stats_snapshot()
    assert stats.rejected_oversize == 1
    assert captured == []
    assert list(inc.iterdir()) == []


def test_process_pending_rejects_malformed_json(tmp_path: Path, spawn_capture: tuple) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    (inc / f"{uuid.uuid4().hex}.json").write_text("not json", encoding="utf-8")

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": Path.home()})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    stats = listener.stats_snapshot()
    assert stats.rejected_malformed_json == 1
    assert captured == []


def test_process_pending_rejects_unknown_app(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    target = home_under_tmp / "note.txt"
    target.write_text("data", encoding="utf-8")
    _write_request(inc, _valid_request(target))

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    stats = listener.stats_snapshot()
    assert stats.rejected_unknown_app == 1
    assert captured == []


def test_process_pending_rejects_guest_origin_until_mount(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    # origin="guest" (a guest-local C:\ file) is schema-valid but the
    # guest-disk mount isn't wired up yet (#616), so it must be rejected
    # cleanly — NOT spawned, NOT mis-resolved as a host path.
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    _write_request(
        inc,
        {
            "version": 2,
            "app": "kate",
            "path": "C:\\Users\\me\\Desktop\\note.txt",
            "origin": "guest",
            "ts": "2026-06-19T00:00:00Z",
            "pod_id": None,
        },
    )
    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    listener = Listener(cfg, apps_db, _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    stats = listener.stats_snapshot()
    assert stats.rejected_guest_unsupported == 1
    assert stats.accepted == 0
    assert captured == []


def test_process_pending_spawns_on_happy_path(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    target = home_under_tmp / "note.txt"
    target.write_text("data", encoding="utf-8")
    uid = _write_request(inc, _valid_request(target))

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    listener = Listener(cfg, apps_db, _seen(tmp_path), spawn=spawn)
    listener.process_pending()

    stats = listener.stats_snapshot()
    assert stats.accepted == 1
    assert stats.rejected_unknown_app == 0
    assert len(captured) == 1
    argv, popen_kwargs = captured[0]
    assert argv[0] == "/usr/bin/kate"
    # argv[1] is now real_path — the kernel's canonical post-resolve
    # path to the inode, not /proc/self/fd/N. Switched to real_path
    # so D-Bus-handoff apps (Firefox / LibreOffice / Chromium) work:
    # the receiver singleton process doesn't inherit our FD table, so
    # /proc/self/fd/N couldn't be resolved there. The string must
    # contain the original filename, not a /proc/self/fd/ prefix.
    assert "/proc/self/fd/" not in argv[1]
    assert argv[1].endswith("note.txt")
    # No FD inheritance needed any more — popen_kwargs is empty.
    assert popen_kwargs == {}
    # Request file is deleted after the accepted spawn.
    assert not (inc / f"{uid}.json").exists()


def test_process_pending_drops_request_for_missing_target(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    # #425: a request whose target file is gone must be DROPPED, not re-looped
    # forever. safe_open_unc raises ReversePathError on the missing file, the
    # listener catches it and unlinks the request. A second sweep finds nothing.
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    missing = home_under_tmp / "gone.txt"  # deliberately NOT created
    uid = _write_request(inc, _valid_request(missing))

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    listener = Listener(cfg, apps_db, _seen(tmp_path), spawn=spawn)

    listener.process_pending()
    assert not (inc / f"{uid}.json").exists()  # dropped on the first sweep
    listener.process_pending()  # nothing left → no re-loop

    stats = listener.stats_snapshot()
    assert stats.rejected_path == 1
    assert stats.accepted == 0
    assert captured == []


def test_process_pending_rejects_replay(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    target = home_under_tmp / "note.txt"
    target.write_text("data", encoding="utf-8")
    uid = _write_request(inc, _valid_request(target))

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    seen = _seen(tmp_path)
    listener = Listener(cfg, apps_db, seen, spawn=spawn)

    listener.process_pending()
    assert len(captured) == 1

    # Re-write the same UUID and try again.
    captured.clear()
    _write_request(inc, _valid_request(target), name=uid)
    listener.process_pending()
    assert listener.stats_snapshot().rejected_replay == 1
    assert captured == []


def test_process_pending_rejects_path_outside_share_roots(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    body = {
        "version": 1,
        "app": "kate",
        "path": "\\\\tsclient\\evil\\..\\..\\etc\\passwd",
        "ts": "2026-05-11T00:00:00Z",
        "pod_id": None,
    }
    _write_request(inc, body)
    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": home_under_tmp})
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    listener = Listener(cfg, apps_db, _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    assert listener.stats_snapshot().rejected_path == 1
    assert captured == []


def test_process_pending_skips_non_matching_filenames(tmp_path: Path, spawn_capture: tuple) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    # .tmp suffix → not picked up.
    (inc / "abc.json.tmp").write_text(json.dumps({"version": 1}), encoding="utf-8")
    # Non-hex name → not picked up.
    (inc / "hello.json").write_text(json.dumps({"version": 1}), encoding="utf-8")

    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": Path.home()})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    stats = listener.stats_snapshot()
    assert stats.accepted == 0
    assert stats.rejected_oversize == 0
    assert stats.rejected_malformed_json == 0
    # Both untouched.
    assert (inc / "abc.json.tmp").exists()
    assert (inc / "hello.json").exists()


def test_in_flight_cap_only_processes_oldest_n(
    tmp_path: Path, home_under_tmp: Path, spawn_capture: tuple
) -> None:
    captured, spawn = spawn_capture
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    target = home_under_tmp / "note.txt"
    target.write_text("data", encoding="utf-8")
    for _ in range(5):
        _write_request(inc, _valid_request(target))

    cfg = ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": home_under_tmp},
        max_in_flight=3,
    )
    apps_db = _apps_db_with("kate", ["/usr/bin/kate", "%f"])
    listener = Listener(cfg, apps_db, _seen(tmp_path), spawn=spawn)
    listener.process_pending()
    # 3 accepted, 2 over-cap.
    stats = listener.stats_snapshot()
    assert stats.accepted == 3
    assert stats.rejected_in_flight == 2


# --- janitor -----------------------------------------------------------------


def test_janitor_removes_stale_files(tmp_path: Path) -> None:
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    stale = inc / f"{uuid.uuid4().hex}.json"
    stale.write_text("{}", encoding="utf-8")
    old_mtime = time.time() - 3600
    os.utime(stale, (old_mtime, old_mtime))

    cfg = ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": Path.home()},
        janitor_age_seconds=300,
    )
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path))
    listener._maybe_run_janitor()
    assert not stale.exists()
    assert listener.stats_snapshot().janitor_removed == 1


def test_janitor_keeps_fresh_files(tmp_path: Path) -> None:
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    fresh = inc / f"{uuid.uuid4().hex}.json"
    fresh.write_text("{}", encoding="utf-8")
    cfg = ListenerConfig(
        incoming_dir=inc,
        share_roots={"home": Path.home()},
        janitor_age_seconds=300,
    )
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path))
    listener._maybe_run_janitor()
    assert fresh.exists()


# --- stat the security boundary ---------------------------------------------


def test_preflight_complains_when_dir_owned_by_other(tmp_path: Path) -> None:
    inc = _incoming(tmp_path)
    inc.chmod(0o700)
    cfg = ListenerConfig(incoming_dir=inc, share_roots={"home": Path.home()})
    listener = Listener(cfg, AppsDatabase.empty(), _seen(tmp_path))
    # Monkey-patch geteuid so the dir's stat looks owned by someone else.
    import winpodx.reverse_open.listener as listener_mod

    real_geteuid = listener_mod.os.geteuid

    listener_mod.os.geteuid = lambda: real_geteuid() + 1
    try:
        with pytest.raises(PermissionError):
            listener.preflight()
    finally:
        listener_mod.os.geteuid = real_geteuid


def test_filemode_permission_helper() -> None:
    # Self-check: stat.filemode formatting matches what we expect to
    # surface in the PermissionError message.
    assert stat.filemode(0o775).endswith("xrwxr-x")
