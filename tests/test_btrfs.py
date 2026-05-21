# SPDX-License-Identifier: MIT
"""Tests for winpodx.utils.btrfs — per-path Copy-on-Write helper.

The module deliberately does NOT operate on podman's graph root; tests
verify behaviour against the explicit path the caller provides (the
winpodx bind-mount storage directory).
"""

from __future__ import annotations

from unittest.mock import patch

from winpodx.utils import btrfs


def _run_factory(scripted):
    """Build a fake _run that pops scripted (rc, stdout, stderr) tuples in order."""
    calls = []

    def fake(cmd, timeout=5.0):
        calls.append(cmd)
        if not scripted:
            return -1, "", "no more scripted responses"
        return scripted.pop(0)

    return fake, calls


class TestDetectPathFs:
    def test_returns_btrfs_when_findmnt_says_btrfs(self, tmp_path):
        scripted = [(0, "btrfs\n", "")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            assert btrfs.detect_path_fs(tmp_path) == "btrfs"

    def test_returns_ext4_for_non_btrfs(self, tmp_path):
        scripted = [(0, "ext4\n", "")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            assert btrfs.detect_path_fs(tmp_path) == "ext4"

    def test_returns_unknown_when_findmnt_missing(self, tmp_path):
        with patch.object(btrfs.shutil, "which", return_value=None):
            assert btrfs.detect_path_fs(tmp_path) == "unknown"

    def test_returns_unknown_when_findmnt_errors(self, tmp_path):
        scripted = [(1, "", "findmnt: ...")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            assert btrfs.detect_path_fs(tmp_path) == "unknown"

    def test_walks_up_to_existing_parent_for_nonexistent_path(self, tmp_path):
        """Regression for kernalix7's 2026-05-06 silent-NoCoW bug:
        ``findmnt --target`` on a non-existent path returns rc=1 with
        empty stdout on opensuse Tumbleweed — the auto-migration
        plan_migration call computes ``target_fs = detect_path_fs(
        ~/.local/share/winpodx/storage)`` BEFORE the dir is mkdir'd,
        so a naive findmnt call returned 'unknown' there, set
        ``chattr_will_run=False``, and silently skipped the entire
        NoCoW path. The fix walks the parent chain until it hits an
        existing dir (always succeeds — `/` exists), so callers get
        the fs that will contain `path` once it's materialised.
        """
        scripted = [(0, "btrfs\n", "")]
        fake, calls = _run_factory(scripted)
        nonexistent = tmp_path / "nope" / "still_not_here" / "leaf"
        # Guard rail: the leaf and its first ancestor really don't exist.
        assert not nonexistent.exists()
        assert not nonexistent.parent.exists()
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            assert btrfs.detect_path_fs(nonexistent) == "btrfs"
        # The probe should have walked up to tmp_path (the nearest
        # existing ancestor), NOT the original non-existent leaf.
        assert calls, "findmnt was not invoked"
        last_target = calls[-1][-1]
        assert last_target == str(tmp_path), (
            f"expected probe to walk up to {tmp_path}, got {last_target!r}"
        )

    def test_walk_up_terminates_at_root_when_nothing_exists(self, tmp_path, monkeypatch):
        """Loop bound: a pathological symlink loop or absurd path depth
        must not spin forever. Walk-up has a 64-step ceiling; well past
        that, we fall through to whatever findmnt says about the
        original path. Sanity: a 200-segment path doesn't hang."""
        scripted = [(0, "btrfs\n", "")]
        fake, _ = _run_factory(scripted)
        deep = tmp_path
        for i in range(200):
            deep = deep / f"seg{i}"
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            # Should return SOMETHING quickly (either btrfs from the
            # scripted response or unknown if probing fell off the ceiling).
            result = btrfs.detect_path_fs(deep)
        assert result in {"btrfs", "unknown"}


class TestIsCowDisabled:
    def test_returns_true_when_C_flag_present(self, tmp_path):
        scripted = [(0, "----C-------------- " + str(tmp_path) + "\n", "")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/lsattr"),
        ):
            assert btrfs.is_cow_disabled(tmp_path) is True

    def test_returns_false_when_C_flag_absent(self, tmp_path):
        scripted = [(0, "------------------- " + str(tmp_path) + "\n", "")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/lsattr"),
        ):
            assert btrfs.is_cow_disabled(tmp_path) is False

    def test_returns_none_when_path_missing(self, tmp_path):
        gone = tmp_path / "does-not-exist"
        with patch.object(btrfs.shutil, "which", return_value="/usr/bin/lsattr"):
            assert btrfs.is_cow_disabled(gone) is None


class TestDisableCowOnPath:
    def test_path_missing_returns_path_missing(self, tmp_path):
        status, _ = btrfs.disable_cow_on_path(tmp_path / "absent")
        assert status == "path_missing"

    def test_skips_when_not_btrfs(self, tmp_path):
        with patch.object(btrfs, "detect_path_fs", return_value="ext4"):
            status, detail = btrfs.disable_cow_on_path(tmp_path)
        assert status == "not_btrfs"
        assert "ext4" in detail

    def test_unknown_fs_when_findmnt_unavailable(self, tmp_path):
        with patch.object(btrfs, "detect_path_fs", return_value="unknown"):
            status, _ = btrfs.disable_cow_on_path(tmp_path)
        assert status == "unknown_fs"

    def test_already_off_when_cow_already_disabled(self, tmp_path):
        with (
            patch.object(btrfs, "detect_path_fs", return_value="btrfs"),
            patch.object(btrfs, "is_cow_disabled", return_value=True),
        ):
            status, _ = btrfs.disable_cow_on_path(tmp_path)
        assert status == "already_off"

    def test_failed_when_chattr_missing(self, tmp_path):
        with (
            patch.object(btrfs, "detect_path_fs", return_value="btrfs"),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value=None),
        ):
            status, detail = btrfs.disable_cow_on_path(tmp_path)
        assert status == "failed"
        assert "chattr" in detail

    def test_disabled_on_successful_chattr(self, tmp_path):
        scripted = [(0, "", "")]
        fake, calls = _run_factory(scripted)
        with (
            patch.object(btrfs, "detect_path_fs", return_value="btrfs"),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/chattr"),
            patch.object(btrfs, "_run", fake),
        ):
            status, _ = btrfs.disable_cow_on_path(tmp_path)
        assert status == "disabled"
        assert calls[0] == ["chattr", "+C", str(tmp_path)]

    def test_failed_when_chattr_returns_nonzero(self, tmp_path):
        scripted = [(1, "", "Operation not permitted\n")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "detect_path_fs", return_value="btrfs"),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/chattr"),
            patch.object(btrfs, "_run", fake),
        ):
            status, detail = btrfs.disable_cow_on_path(tmp_path)
        assert status == "failed"
        assert "Operation not permitted" in detail


class TestDoesNotTouchGraphRoot:
    """Regression: this module must NOT have a 'detect_storage_fs' or
    similar helper that calls `podman info --format '{{.Store.GraphRoot}}'`.
    The earlier design (PR #124) did, and it was rejected because it
    affected every future podman volume on the host. The module's public
    surface is path-only.
    """

    def test_module_does_not_export_graph_root_helper(self):
        for forbidden in ("detect_storage_fs", "disable_cow_if_btrfs"):
            assert not hasattr(btrfs, forbidden), (
                f"btrfs.py must not expose {forbidden!r} — would re-introduce "
                f"the graph-root chattr behaviour we explicitly rejected"
            )
