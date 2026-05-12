"""Tests for ``winpodx.reverse_open.paths``.

Phase 1 PR 1 (issue #48): UNC -> POSIX translation, strict subtree
check, and TOCTOU-safe spawn helper.

The translation function is the trust boundary between guest-supplied
input and host-side execution; every input either returns a valid
Path under a share root or raises ``ReversePathError``. These tests
exercise both the happy paths and the security-relevant rejections.

The atomic ``safe_open_unc`` context manager replaces the older
translate-then-open_for_spawn split. It validates against
``/proc/self/fd/N`` *after* the FD is acquired, so an attacker cannot
swap a symlink between validation and open. The three swap-attack
tests below exercise that property end-to-end.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from winpodx.reverse_open.paths import (
    ReversePathError,
    SafeFile,
    is_relative_to,
    safe_open_unc,
    translate_unc_to_posix,
)

# ---- translate_unc_to_posix: happy paths --------------------------


def test_happy_path_home(tmp_path):
    """``\\tsclient\\home\\foo`` resolves under the home share root."""
    target = tmp_path / "foo"
    target.touch()
    share_roots = {"home": tmp_path}

    result = translate_unc_to_posix(r"\\tsclient\home\foo", share_roots)

    assert result == (tmp_path / "foo").resolve()


def test_happy_path_media_nested(tmp_path):
    """``\\tsclient\\media\\USB\\x`` resolves under media share root."""
    media_root = tmp_path / "media"
    media_root.mkdir()
    (media_root / "USB").mkdir()
    (media_root / "USB" / "x").touch()
    share_roots = {"media": media_root}

    result = translate_unc_to_posix(r"\\tsclient\media\USB\x", share_roots)

    assert result == (media_root / "USB" / "x").resolve()


def test_happy_path_custom_drive(tmp_path):
    """A user-configured ``/drive:work,/path`` resolves correctly."""
    work_root = tmp_path / "work"
    work_root.mkdir()
    (work_root / "report.pdf").touch()
    share_roots = {"work": work_root}

    result = translate_unc_to_posix(r"\\tsclient\work\report.pdf", share_roots)

    assert result == (work_root / "report.pdf").resolve()


def test_happy_path_unicode(tmp_path):
    """Unicode (Hangul) path components survive translation."""
    sub = tmp_path / "한글"
    sub.mkdir()
    (sub / "notes.txt").touch()
    share_roots = {"home": tmp_path}

    result = translate_unc_to_posix(r"\\tsclient\home\한글\notes.txt", share_roots)

    assert result == (sub / "notes.txt").resolve()


def test_case_insensitive_prefix_and_share(tmp_path):
    """``\\TSCLIENT\\HOME\\foo`` matches ``home`` share case-insensitively."""
    (tmp_path / "foo").touch()
    share_roots = {"home": tmp_path}

    result = translate_unc_to_posix(r"\\TSCLIENT\HOME\foo", share_roots)

    assert result == (tmp_path / "foo").resolve()


def test_mixed_case_share_name(tmp_path):
    """``Home`` (mixed case) still matches ``home`` share."""
    (tmp_path / "foo").touch()
    share_roots = {"home": tmp_path}

    result = translate_unc_to_posix(r"\\tsclient\Home\foo", share_roots)

    assert result == (tmp_path / "foo").resolve()


def test_nonexistent_target_still_resolves(tmp_path):
    """``strict=False`` resolution: missing leaf still translates.

    The listener checks existence separately with a friendlier error,
    so the translator only validates path *shape*, not file presence.
    """
    share_roots = {"home": tmp_path}

    result = translate_unc_to_posix(r"\\tsclient\home\does_not_exist.xml", share_roots)

    assert result == (tmp_path / "does_not_exist.xml").resolve()


# ---- translate_unc_to_posix: traversal & symlink escape -----------


def test_traversal_escapes_share_root(tmp_path):
    """``..`` walking out of the share root is rejected post-resolve."""
    share_roots = {"home": tmp_path}

    with pytest.raises(ReversePathError, match="escapes share root"):
        translate_unc_to_posix(r"\\tsclient\home\..\..\etc\passwd", share_roots)


def test_symlink_escape_rejected(tmp_path):
    """A symlink in the share that points outside the root is rejected."""
    share_roots = {"home": tmp_path}
    escape_link = tmp_path / "escape"
    escape_link.symlink_to("/etc")

    with pytest.raises(ReversePathError, match="escapes share root"):
        translate_unc_to_posix(r"\\tsclient\home\escape\passwd", share_roots)


def test_proc_denylist_post_resolve():
    """A share root pointing at /proc is caught by the denylist.

    Artificial -- the live cfg doesn't allow ``/drive:proc,/proc`` --
    but defence-in-depth: even if some future code path admitted such
    a config, the post-resolve denylist still rejects.
    """
    share_roots = {"proc": Path("/proc")}

    with pytest.raises(ReversePathError, match="system denylist root"):
        translate_unc_to_posix(r"\\tsclient\proc\self\status", share_roots)


# ---- translate_unc_to_posix: input validation ---------------------


def test_empty_input(tmp_path):
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="empty"):
        translate_unc_to_posix("", share_roots)


def test_nul_byte_input(tmp_path):
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="NUL"):
        translate_unc_to_posix("\\\\tsclient\\home\\foo\0bar", share_roots)


def test_non_string_input(tmp_path):
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="must be str"):
        translate_unc_to_posix(b"\\\\tsclient\\home\\foo", share_roots)  # type: ignore[arg-type]


def test_non_string_none(tmp_path):
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="must be str"):
        translate_unc_to_posix(None, share_roots)  # type: ignore[arg-type]


def test_bare_share_root_no_path(tmp_path):
    """``\\tsclient\\home`` with no trailing path is rejected."""
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="bare share root"):
        translate_unc_to_posix(r"\\tsclient\home", share_roots)


def test_bare_share_root_trailing_separator(tmp_path):
    """``\\tsclient\\home\\`` with empty rest is rejected."""
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="bare share root"):
        translate_unc_to_posix("\\\\tsclient\\home\\", share_roots)


def test_bare_prefix_only(tmp_path):
    """``\\tsclient\\`` (no share name at all) is rejected."""
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="bare share root"):
        translate_unc_to_posix("\\\\tsclient\\", share_roots)


def test_forward_slash_variant_rejected(tmp_path):
    """``//tsclient/home/foo`` is NOT a valid UNC prefix -- rejected.

    Windows hands the shim backslash-form UNCs verbatim. Forward
    slashes would only appear in hand-crafted requests, which we
    treat as malicious.
    """
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="not under"):
        translate_unc_to_posix("//tsclient/home/foo", share_roots)


def test_unknown_share_name_lists_known(tmp_path):
    """Unknown share name error message includes the known shares."""
    share_roots = {"home": tmp_path, "media": tmp_path}
    with pytest.raises(ReversePathError) as excinfo:
        translate_unc_to_posix(r"\\tsclient\bogus\x", share_roots)
    msg = str(excinfo.value)
    assert "bogus" in msg
    assert "home" in msg
    assert "media" in msg


def test_not_under_tsclient_prefix(tmp_path):
    """Random non-UNC string rejected with a 'not under' message."""
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="not under"):
        translate_unc_to_posix("C:\\Users\\foo\\file.txt", share_roots)


def test_empty_share_roots_rejected():
    """An empty share table refuses every path -- no legal target exists."""
    with pytest.raises(ReversePathError, match="no share roots configured"):
        translate_unc_to_posix(r"\\tsclient\home\foo", {})


def test_non_ascii_share_name_rejected(tmp_path):
    """Non-ASCII share name is treated as malicious / misconfigured."""
    share_roots = {"home": tmp_path}
    with pytest.raises(ReversePathError, match="non-ASCII share name"):
        translate_unc_to_posix("\\\\tsclient\\한글\\foo", share_roots)


# ---- is_relative_to ----------------------------------------------


def test_is_relative_to_subtree():
    assert is_relative_to(Path("/a/b/c"), Path("/a/b"))


def test_is_relative_to_equal():
    assert is_relative_to(Path("/a/b"), Path("/a/b"))


def test_is_relative_to_parent_of_root_is_false():
    """Parent of root must NOT be considered "under" root.

    This is the strict-semantics property the security check relies
    on. Prior to 3.12, ``Path.is_relative_to`` always returned False
    for parent-of-root; 3.12 added ``walk_up`` semantics that we
    explicitly opt out of.
    """
    assert not is_relative_to(Path("/a"), Path("/a/b"))


def test_is_relative_to_sibling_is_false():
    assert not is_relative_to(Path("/a/c"), Path("/a/b"))


def test_is_relative_to_unrelated_is_false():
    assert not is_relative_to(Path("/x/y"), Path("/a/b"))


# ---- safe_open_unc / SafeFile -----------------------------------


def _fd_count() -> int:
    """Count currently open FDs in this process via /proc/self/fd."""
    return len(os.listdir("/proc/self/fd"))


def test_safe_open_unc_yields_safefile_with_proc_path(tmp_path):
    """``safe_open_unc`` yields a SafeFile whose ``proc_path`` is
    ``/proc/self/fd/N`` and whose fd is closed after context exit.
    """
    target = tmp_path / "f"
    target.write_text("hello")
    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\f", share_roots) as safe:
        assert isinstance(safe, SafeFile)
        assert safe.fd >= 0
        assert safe.proc_path == Path("/proc/self/fd") / str(safe.fd)
        # While inside the context the proc_path should resolve to a
        # readable inode -- the kernel keeps the inode pinned via fd.
        assert safe.proc_path.exists()
        captured_fd = safe.fd

    # After context exit the fd is closed; closing again is a no-op
    # but os.fstat on the original fd should now fail with EBADF.
    with pytest.raises(OSError):
        os.fstat(captured_fd)


def test_safe_open_unc_nonexistent_raises_oserror(tmp_path):
    """Opening a path that doesn't exist surfaces OSError.

    The listener catches and logs INFO "target file does not exist"
    instead of WARNING -- distinct from a translation failure, which
    is a security event.
    """
    share_roots = {"home": tmp_path}
    before = _fd_count()
    with pytest.raises(OSError):
        with safe_open_unc(r"\\tsclient\home\does_not_exist", share_roots):
            pass
    # OSError on os.open() means the FD was never allocated, so the
    # count is unchanged.
    assert _fd_count() == before


def test_safe_open_unc_safefile_close_idempotent(tmp_path):
    """``SafeFile.close()`` swallows EBADF from a double-close."""
    target = tmp_path / "f"
    target.touch()
    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\f", share_roots) as safe:
        safe.close()
        # A second close inside or after the context must not raise.
        safe.close()


def test_safe_open_unc_closes_fd_on_inner_exception(tmp_path):
    """An exception raised inside the with-block still closes the fd."""
    target = tmp_path / "f"
    target.touch()
    share_roots = {"home": tmp_path}

    captured: dict[str, int] = {}

    class _Sentinel(Exception):
        pass

    with pytest.raises(_Sentinel):
        with safe_open_unc(r"\\tsclient\home\f", share_roots) as safe:
            captured["fd"] = safe.fd
            raise _Sentinel("boom")

    with pytest.raises(OSError):
        os.fstat(captured["fd"])


def test_safe_open_unc_rejects_traversal(tmp_path):
    """``..`` traversal is rejected by the readlink-based check.

    Set up: a sibling directory next to ``tmp_path`` containing a
    real file, so the candidate path *opens successfully* -- the
    failure must come from the readlink-vs-share-root check, not
    from a missing target.
    """
    sibling = tmp_path.parent / (tmp_path.name + "_sibling")
    sibling.mkdir(exist_ok=True)
    victim = sibling / "victim"
    victim.write_text("secret")

    share_roots = {"home": tmp_path}
    before = _fd_count()
    with pytest.raises(ReversePathError, match="escapes share root"):
        with safe_open_unc(
            f"\\\\tsclient\\home\\..\\{sibling.name}\\victim",
            share_roots,
        ):
            pass
    assert _fd_count() == before


def test_safe_open_unc_rejects_symlink_leaf(tmp_path):
    """A symlink leaf is rejected, regardless of where it points.

    ``O_PATH | O_NOFOLLOW`` (unlike plain ``O_NOFOLLOW``) does NOT
    raise ELOOP for a symlink leaf -- it opens the symlink itself,
    so ``readlink('/proc/self/fd/N')`` would return the symlink's
    own path, which is inside the share root by construction. The
    extra ``S_ISLNK`` check on the resulting FD is what catches the
    case and raises ``ReversePathError``.
    """
    share_roots = {"home": tmp_path}
    escape_link = tmp_path / "escape"
    escape_link.symlink_to("/etc/passwd")

    before = _fd_count()
    with pytest.raises(ReversePathError, match="symlink leaf"):
        with safe_open_unc(r"\\tsclient\home\escape", share_roots):
            pass
    assert _fd_count() == before


def test_safe_open_unc_rejects_symlink_leaf_pointing_inside(tmp_path):
    """Even a symlink whose target is inside the share is rejected.

    Defence-in-depth: a symlink leaf is always refused, so a guest
    can't use a same-share symlink as a stepping stone for a later
    swap attack.
    """
    share_roots = {"home": tmp_path}
    real = tmp_path / "real"
    real.write_text("ok")
    inside_link = tmp_path / "inside_link"
    inside_link.symlink_to(real)

    before = _fd_count()
    with pytest.raises(ReversePathError, match="symlink leaf"):
        with safe_open_unc(r"\\tsclient\home\inside_link", share_roots):
            pass
    assert _fd_count() == before


def test_safe_open_unc_empty_share_roots(tmp_path):
    """Empty share-roots dict refuses every path."""
    with pytest.raises(ReversePathError, match="no share roots configured"):
        with safe_open_unc(r"\\tsclient\home\foo", {}):
            pass


def test_safe_popen_kwargs_returns_empty(tmp_path):
    """``SafeFile.popen_kwargs()`` returns an empty dict.

    The listener now hands ``real_path`` (the kernel's canonical path)
    to the spawned child instead of the FD-bound ``proc_path``, so no
    FD inheritance is needed. See SafeFile docstring for why we made
    this trade (Firefox / LibreOffice D-Bus handoff workaround).
    """
    target = tmp_path / "f"
    target.touch()
    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\f", share_roots) as safe:
        assert safe.popen_kwargs() == {}


def test_safe_open_unc_exposes_real_path(tmp_path):
    """SafeFile.real_path is the kernel's canonical post-resolve path.

    This is what the listener hands to the child process — opaque,
    no FD inheritance required, works for D-Bus-handoff apps like
    Firefox where /proc/self/fd/N would fail in the receiver process.
    """
    target = tmp_path / "f"
    target.write_text("hello")
    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\f", share_roots) as safe:
        # real_path resolves to the validated inode and contains the
        # same bytes we'd see via the FD-bound proc_path.
        assert safe.real_path.is_file()
        assert safe.real_path.read_text() == "hello"
        # It's also distinct from /proc/self/fd/N — the two are kept
        # as separate fields for callers with different needs.
        assert "/proc/self/fd/" not in str(safe.real_path)


# ---- Symlink-swap-during-window attack tests ---------------------
#
# These three tests exercise the property the new atomic flow is
# designed to guarantee: a guest cannot redirect the spawn target by
# swapping a symlink at any point between validation and use, because
# validation is performed on the kernel's authoritative readlink of
# the already-acquired FD, not on a string we re-resolve later.


def test_swap_attack_leaf_symlink_after_open_targets_original_inode(tmp_path):
    """Validation succeeds, attacker swaps the leaf symlink, the FD
    still points at the original inode.

    Setup: ``link`` is a regular file (not a symlink, so ``O_NOFOLLOW``
    accepts it). Inside the context we ``unlink`` it and recreate it
    as a symlink to ``decoy``. The kernel's view through
    ``/proc/self/fd/N`` continues to be the original inode -- proven
    by reading via ``proc_path`` and getting the original bytes.
    """
    real_target = tmp_path / "link"
    real_target.write_text("original")
    decoy = tmp_path / "decoy"
    decoy.write_text("attacker")
    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\link", share_roots) as safe:
        # Attacker rewrites the on-disk path to a symlink pointing
        # at the decoy.
        real_target.unlink()
        real_target.symlink_to(decoy)

        # The kernel's FD-backed view still points at the inode we
        # validated -- reading via /proc/self/fd/N yields the
        # original content, not the decoy.
        with safe.proc_path.open("rb") as f:
            data = f.read()
    assert data == b"original"


def test_swap_attack_non_leaf_component_after_open_keeps_inode(tmp_path):
    """Validation succeeds, attacker swaps a non-leaf directory
    component, the FD still points at the original inode.

    Setup: ``inner/leaf`` exists. After we open it, the attacker
    renames ``inner`` aside and replaces it with a directory
    containing a different ``leaf``. The on-disk path now resolves
    to a different file, but the FD we hold pins the *original*
    inode -- proven by reading the original bytes via ``proc_path``.
    """
    inner = tmp_path / "inner"
    inner.mkdir()
    leaf = inner / "leaf"
    leaf.write_text("original")

    decoy_inner = tmp_path / "decoy_inner"
    decoy_inner.mkdir()
    (decoy_inner / "leaf").write_text("attacker")

    share_roots = {"home": tmp_path}

    with safe_open_unc(r"\\tsclient\home\inner\leaf", share_roots) as safe:
        # Attacker swaps the parent directory under us.
        inner.rename(tmp_path / "inner_old")
        decoy_inner.rename(inner)

        # On-disk resolution now hits the attacker's file:
        assert (tmp_path / "inner" / "leaf").read_text() == "attacker"

        # But the FD we hold still points at the original inode.
        with safe.proc_path.open("rb") as f:
            data = f.read()
    assert data == b"original"


def test_swap_attack_invalid_path_does_not_leak_fd(tmp_path):
    """A path that fails validation inside ``safe_open_unc`` must
    never leak an FD.

    Counts ``/proc/self/fd`` entries before and after the attempted
    open: the count must be unchanged whether the failure was a
    parse error (no os.open call) or a readlink-validation rejection
    (FD acquired then closed in the except block).
    """
    # Set up: a symlink whose target is OUTSIDE the share root.
    # Using a regular file at a target outside tmp_path would also
    # work, but the cleanest case is a real file inside a directory
    # that the open() succeeds on, then validation rejects via the
    # readlink check.
    outside = tmp_path.parent / "outside_share"
    outside.mkdir(exist_ok=True)
    target_outside = outside / "victim"
    target_outside.write_text("secret")

    # Build a share root that's a directory containing a symlink to
    # a directory outside the share.
    share = tmp_path / "share"
    share.mkdir()
    # Bind-mount-like path: an inner directory that's a symlink to
    # somewhere outside. O_NOFOLLOW only blocks the *leaf*; here the
    # leaf is "victim" (a real file), and "escape" is a non-leaf
    # symlink, which os.open() will follow even with O_NOFOLLOW.
    (share / "escape").symlink_to(outside)
    share_roots = {"home": share}

    before = _fd_count()
    with pytest.raises(ReversePathError):
        with safe_open_unc(r"\\tsclient\home\escape\victim", share_roots):
            pass
    after = _fd_count()
    assert after == before, (
        f"FD leak: {before} open before, {after} after. "
        f"safe_open_unc must close the FD when readlink-validation rejects."
    )

    # And the same property holds for parse-time rejections, where
    # os.open is never called.
    before = _fd_count()
    with pytest.raises(ReversePathError):
        with safe_open_unc("not-a-unc-path", share_roots):
            pass
    assert _fd_count() == before


# ---- Hypothesis property test ------------------------------------


@pytest.mark.hypothesis
def test_property_translate_total_function(tmp_path):
    """For arbitrary text input the translator either returns a Path
    under the share root or raises ReversePathError -- never crashes
    with another exception, never returns a path outside the root.
    """
    pytest.importorskip("hypothesis")
    from hypothesis import given, settings
    from hypothesis import strategies as st

    share_root = tmp_path
    share_roots = {"home": share_root}
    resolved_root = share_root.resolve()

    @given(st.text(max_size=200))
    @settings(max_examples=200, deadline=None)
    def _check(s: str) -> None:
        try:
            result = translate_unc_to_posix(s, share_roots)
        except ReversePathError:
            return
        # If it returned, it must be a Path under the share root.
        assert isinstance(result, Path)
        assert is_relative_to(result, resolved_root), (
            f"translator returned {result!r} not under {resolved_root!r} for input {s!r}"
        )

    _check()
