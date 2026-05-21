# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.seen_uuids``.

Phase 1 PR 1 of the reverse-open feature (#48). Covers the persistent
ring buffer used by the listener to reject replayed request UUIDs.
"""

from __future__ import annotations

import os
import stat
import uuid as uuid_mod
from pathlib import Path

import pytest

from winpodx.reverse_open.seen_uuids import (
    DEFAULT_MAX_SIZE,
    SeenUUIDs,
    default_seen_uuids_path,
)


def _u() -> str:
    """Convenience: return a fresh canonical UUID string."""
    return str(uuid_mod.uuid4())


# ---- core add / has -------------------------------------------------


def test_add_then_has_returns_true(tmp_path: Path) -> None:
    """A UUID that was added must be reported by ``has``."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids")
    u = _u()
    ring.add(u)
    assert ring.has(u) is True


def test_has_unknown_uuid_returns_false(tmp_path: Path) -> None:
    """A UUID not yet seen must not be reported as seen."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids")
    assert ring.has(_u()) is False


def test_has_non_string_returns_false(tmp_path: Path) -> None:
    """``has`` must never crash on garbage input — listener calls it
    on every request and a raise here would DoS the dispatcher."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids")
    assert ring.has(None) is False  # type: ignore[arg-type]
    assert ring.has(12345) is False  # type: ignore[arg-type]
    assert ring.has(b"bytes") is False  # type: ignore[arg-type]


def test_re_add_is_idempotent(tmp_path: Path) -> None:
    """Re-adding the same UUID must not duplicate or reorder it."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids", max_size=3)
    u1, u2, u3 = _u(), _u(), _u()
    ring.add(u1)
    ring.add(u2)
    ring.add(u1)  # re-add: must not push u1 to the front
    ring.add(u3)
    # Capacity 3, three distinct UUIDs added, all should still be present.
    assert ring.has(u1) is True
    assert ring.has(u2) is True
    assert ring.has(u3) is True
    assert len(ring) == 3


# ---- capacity (FIFO) -----------------------------------------------


def test_default_max_size_is_1000() -> None:
    """The published default for ``max_size`` is 1000."""
    assert DEFAULT_MAX_SIZE == 1000


def test_capacity_enforced_default_1000(tmp_path: Path) -> None:
    """After 1000 adds + 1 more, the first added must be evicted."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids")  # default 1000
    uuids = [_u() for _ in range(1000)]
    for u in uuids:
        ring.add(u)
    assert ring.has(uuids[0]) is True

    # One more push — the oldest (uuids[0]) must fall out.
    extra = _u()
    ring.add(extra)
    assert ring.has(uuids[0]) is False
    assert ring.has(uuids[1]) is True
    assert ring.has(extra) is True
    assert len(ring) == 1000


def test_max_size_10_honored(tmp_path: Path) -> None:
    """``max_size=10`` must constrain the ring to 10 entries (no hardcoded 1000)."""
    ring = SeenUUIDs(path=tmp_path / ".seen-uuids", max_size=10)
    uuids = [_u() for _ in range(15)]
    for u in uuids:
        ring.add(u)
    # First 5 evicted, last 10 retained.
    for evicted in uuids[:5]:
        assert ring.has(evicted) is False
    for kept in uuids[5:]:
        assert ring.has(kept) is True
    assert len(ring) == 10


def test_max_size_must_be_positive(tmp_path: Path) -> None:
    """``max_size <= 0`` is a programming error and must raise."""
    with pytest.raises(ValueError):
        SeenUUIDs(path=tmp_path / ".seen-uuids", max_size=0)
    with pytest.raises(ValueError):
        SeenUUIDs(path=tmp_path / ".seen-uuids", max_size=-1)


# ---- persistence across instances ----------------------------------


def test_persistence_across_instances(tmp_path: Path) -> None:
    """Instance B at the same path must see what instance A added."""
    p = tmp_path / ".seen-uuids"
    a = SeenUUIDs(path=p)
    u = _u()
    a.add(u)

    b = SeenUUIDs(path=p)
    assert b.has(u) is True


def test_persistence_preserves_fifo_order(tmp_path: Path) -> None:
    """Reload must preserve insertion order so subsequent overflows
    evict the *original* oldest entries, not whichever order Python's
    set iteration happens to land in."""
    p = tmp_path / ".seen-uuids"
    a = SeenUUIDs(path=p, max_size=3)
    u1, u2, u3 = _u(), _u(), _u()
    a.add(u1)
    a.add(u2)
    a.add(u3)

    # Reload, then push one more — the eviction must be u1 (oldest).
    b = SeenUUIDs(path=p, max_size=3)
    assert len(b) == 3
    u4 = _u()
    b.add(u4)
    assert b.has(u1) is False
    assert b.has(u2) is True
    assert b.has(u3) is True
    assert b.has(u4) is True


def test_load_truncates_to_max_size_when_shrunk(tmp_path: Path) -> None:
    """If the file holds more entries than the new ``max_size``
    (operator shrank capacity between runs), the newest are kept."""
    p = tmp_path / ".seen-uuids"
    a = SeenUUIDs(path=p, max_size=10)
    uuids = [_u() for _ in range(10)]
    for u in uuids:
        a.add(u)

    # Reopen with a smaller capacity. The newest 3 should survive,
    # the oldest 7 should be dropped.
    b = SeenUUIDs(path=p, max_size=3)
    assert len(b) == 3
    for evicted in uuids[:7]:
        assert b.has(evicted) is False
    for kept in uuids[-3:]:
        assert b.has(kept) is True


# ---- malformed on-disk entries -------------------------------------


def test_corrupted_file_does_not_crash_load(tmp_path: Path) -> None:
    """Non-UUID lines / comments must be silently dropped on load,
    not raise — a hand-edited or partly-overwritten file shouldn't
    knock the listener out."""
    p = tmp_path / ".seen-uuids"
    valid = _u()
    p.write_text(
        "\n".join(
            [
                "# this is a comment from a curious user",
                "",
                "not-a-uuid",
                valid,
                "12345",
                "  ",  # whitespace-only
                "deadbeef",  # bare hex without UUID format
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    ring = SeenUUIDs(path=p)
    assert ring.has(valid) is True
    assert len(ring) == 1


def test_completely_garbage_file_loads_as_empty(tmp_path: Path) -> None:
    """A totally corrupted file must load to an empty ring without raising."""
    p = tmp_path / ".seen-uuids"
    p.write_text("\x00\x01\x02 random binary garbage\n!!!\n", encoding="utf-8")
    ring = SeenUUIDs(path=p)
    assert len(ring) == 0


# ---- input validation ----------------------------------------------


def test_add_rejects_empty_string(tmp_path: Path) -> None:
    """Empty ``add`` input must raise and not write garbage."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    with pytest.raises(ValueError):
        ring.add("")
    # Nothing should have been persisted.
    assert not p.exists()


def test_add_rejects_whitespace(tmp_path: Path) -> None:
    """Whitespace-only ``add`` input must raise and not write."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    with pytest.raises(ValueError):
        ring.add("   ")
    assert not p.exists()


def test_add_rejects_non_uuid(tmp_path: Path) -> None:
    """Non-UUID strings must raise and not write."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    with pytest.raises(ValueError):
        ring.add("not-a-uuid")
    with pytest.raises(ValueError):
        ring.add("12345")
    assert not p.exists()


def test_add_rejects_non_string(tmp_path: Path) -> None:
    """Non-string ``add`` input must raise and not write."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    with pytest.raises(ValueError):
        ring.add(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ring.add(12345)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ring.add(b"bytes-not-str")  # type: ignore[arg-type]
    assert not p.exists()


# ---- on-disk file properties ---------------------------------------


def test_file_mode_is_0600_after_add(tmp_path: Path) -> None:
    """The persisted file must be mode 0600 — not group/world readable."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    ring.add(_u())
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_parent_dir_created_automatically(tmp_path: Path) -> None:
    """If the parent dir does not exist, ``add`` must create it."""
    p = tmp_path / "deep" / "nested" / "missing" / ".seen-uuids"
    assert not p.parent.exists()
    ring = SeenUUIDs(path=p)
    ring.add(_u())
    assert p.parent.exists()
    assert p.exists()


def test_atomic_update_no_orphan_tmp_file(tmp_path: Path) -> None:
    """After ``add``, no ``.tmp`` should be left behind."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    for _ in range(5):
        ring.add(_u())
    tmp_file = p.with_name(p.name + ".tmp")
    assert not tmp_file.exists()


def test_on_disk_format_one_uuid_per_line(tmp_path: Path) -> None:
    """One UUID per line, newest at the bottom."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p, max_size=5)
    uuids = [_u() for _ in range(3)]
    for u in uuids:
        ring.add(u)
    lines = [line for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines == uuids


# ---- default path --------------------------------------------------


def test_default_path_under_xdg_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The default path must follow XDG and live under
    ``~/.local/share/winpodx/reverse-open/.seen-uuids``."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    expected = tmp_path / "winpodx" / "reverse-open" / ".seen-uuids"
    assert default_seen_uuids_path() == expected


def test_default_path_used_when_none_passed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``SeenUUIDs()`` with no path arg must derive the XDG default."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    ring = SeenUUIDs()
    expected = tmp_path / "winpodx" / "reverse-open" / ".seen-uuids"
    assert ring.path == expected


# ---- replay scenario -----------------------------------------------


def test_replay_scenario_end_to_end(tmp_path: Path) -> None:
    """Full replay-defence flow:

    1. Listener processes UUID U, calls ``add(U)``.
    2. Listener restarts; second instance loads the ring from disk.
    3. Guest re-submits the same U; ``has(U)`` must be True.
    """
    p = tmp_path / ".seen-uuids"
    listener_a = SeenUUIDs(path=p)
    u = _u()
    listener_a.add(u)
    # ... listener crashes, file remains.

    listener_b = SeenUUIDs(path=p)
    # Second incoming request with the same UUID -> rejected.
    assert listener_b.has(u) is True


# ---- file permissions edge case ------------------------------------


def test_parent_dir_mode_is_user_only(tmp_path: Path) -> None:
    """The auto-created parent dir should be 0700-ish (user-only).
    We assert it's not group-or-world *writable* — exact perms vary
    by umask, but the dir must not let other users drop tombstones in."""
    p = tmp_path / "fresh-parent" / ".seen-uuids"
    ring = SeenUUIDs(path=p)
    ring.add(_u())
    parent_mode = stat.S_IMODE(p.parent.stat().st_mode)
    # No group write, no world write.
    assert parent_mode & stat.S_IWGRP == 0
    assert parent_mode & stat.S_IWOTH == 0


def test_multiple_adds_share_same_atomic_replace(tmp_path: Path) -> None:
    """Sanity check: many sequential adds maintain the on-disk file
    in a coherent state at every step (no torn writes visible to a
    re-opened reader)."""
    p = tmp_path / ".seen-uuids"
    ring = SeenUUIDs(path=p, max_size=50)
    uuids: list[str] = []
    for _ in range(50):
        u = _u()
        uuids.append(u)
        ring.add(u)
        # Re-read every step; the file must always be a valid,
        # parseable, increasing prefix of the eventual contents.
        reload = SeenUUIDs(path=p, max_size=50)
        assert reload.has(u) is True
        assert len(reload) == len(uuids)


# ---- file mode preserved on update ---------------------------------


def test_file_mode_preserved_across_updates(tmp_path: Path) -> None:
    """Even after many ``add`` calls (each rewriting the file), mode
    stays 0600 — not surfaced from a permissive umask on subsequent
    creates."""
    # Force a permissive umask so an os.open without explicit mode
    # would land on 0644 / 0666.
    old_umask = os.umask(0o000)
    try:
        p = tmp_path / ".seen-uuids"
        ring = SeenUUIDs(path=p)
        for _ in range(5):
            ring.add(_u())
        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode == 0o600, f"expected 0600 under umask 0, got {oct(mode)}"
    finally:
        os.umask(old_umask)
