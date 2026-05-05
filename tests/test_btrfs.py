"""Tests for winpodx.utils.btrfs — Copy-on-Write detection + disable helper."""

from __future__ import annotations

from pathlib import Path
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


class TestDetectStorageFs:
    def test_returns_unknown_for_unsupported_backend(self):
        fs, path = btrfs.detect_storage_fs("libvirt")
        assert fs == "unknown"
        assert path is None

    def test_returns_unknown_when_podman_info_fails(self):
        scripted = [(1, "", "podman not installed")]
        fake, _ = _run_factory(scripted)
        with patch.object(btrfs, "_run", fake):
            fs, path = btrfs.detect_storage_fs("podman")
        assert fs == "unknown"
        assert path is None

    def test_returns_btrfs_when_findmnt_says_btrfs(self, tmp_path):
        # Pretend the graph root exists by pointing at tmp_path.
        scripted = [
            (0, str(tmp_path) + "\n", ""),  # podman info
            (0, "btrfs\n", ""),  # findmnt
        ]
        fake, calls = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            fs, path = btrfs.detect_storage_fs("podman")
        assert fs == "btrfs"
        assert path == tmp_path
        # Sanity: first call was podman info, second was findmnt.
        assert calls[0][:2] == ["podman", "info"]
        assert calls[1][:2] == ["findmnt", "-no"]

    def test_returns_ext4_for_non_btrfs(self, tmp_path):
        scripted = [
            (0, str(tmp_path) + "\n", ""),
            (0, "ext4\n", ""),
        ]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/findmnt"),
        ):
            fs, path = btrfs.detect_storage_fs("podman")
        assert fs == "ext4"
        assert path == tmp_path

    def test_findmnt_missing_returns_unknown(self, tmp_path):
        scripted = [(0, str(tmp_path) + "\n", "")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value=None),
        ):
            fs, path = btrfs.detect_storage_fs("podman")
        assert fs == "unknown"
        assert path == tmp_path  # we still report the path even if fs unknown


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

    def test_returns_none_when_lsattr_missing(self, tmp_path):
        with patch.object(btrfs.shutil, "which", return_value=None):
            assert btrfs.is_cow_disabled(tmp_path) is None

    def test_returns_none_on_lsattr_error(self, tmp_path):
        scripted = [(1, "", "lsattr: Operation not supported")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "_run", fake),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/lsattr"),
        ):
            assert btrfs.is_cow_disabled(tmp_path) is None


class TestDisableCowIfBtrfs:
    def test_skips_when_not_btrfs(self):
        with patch.object(
            btrfs, "detect_storage_fs", return_value=("ext4", Path("/var/lib/containers"))
        ):
            status, _ = btrfs.disable_cow_if_btrfs("podman")
        assert status == "not_btrfs"

    def test_unknown_when_detect_returns_unknown(self):
        with patch.object(btrfs, "detect_storage_fs", return_value=("unknown", None)):
            status, _ = btrfs.disable_cow_if_btrfs("podman")
        assert status == "unknown"

    def test_already_off_when_cow_already_disabled(self, tmp_path):
        with (
            patch.object(btrfs, "detect_storage_fs", return_value=("btrfs", tmp_path)),
            patch.object(btrfs, "is_cow_disabled", return_value=True),
        ):
            status, detail = btrfs.disable_cow_if_btrfs("podman")
        assert status == "already_off"
        assert str(tmp_path) in detail

    def test_failed_when_chattr_missing(self, tmp_path):
        with (
            patch.object(btrfs, "detect_storage_fs", return_value=("btrfs", tmp_path)),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value=None),
        ):
            status, detail = btrfs.disable_cow_if_btrfs("podman")
        assert status == "failed"
        assert "chattr" in detail

    def test_disabled_on_successful_chattr(self, tmp_path):
        scripted = [(0, "", "")]
        fake, calls = _run_factory(scripted)
        with (
            patch.object(btrfs, "detect_storage_fs", return_value=("btrfs", tmp_path)),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/chattr"),
            patch.object(btrfs, "_run", fake),
        ):
            status, detail = btrfs.disable_cow_if_btrfs("podman")
        assert status == "disabled"
        assert str(tmp_path) in detail
        assert calls[0] == ["chattr", "+C", str(tmp_path)]

    def test_failed_when_chattr_returns_nonzero(self, tmp_path):
        scripted = [(1, "", "Operation not permitted\n")]
        fake, _ = _run_factory(scripted)
        with (
            patch.object(btrfs, "detect_storage_fs", return_value=("btrfs", tmp_path)),
            patch.object(btrfs, "is_cow_disabled", return_value=False),
            patch.object(btrfs.shutil, "which", return_value="/usr/bin/chattr"),
            patch.object(btrfs, "_run", fake),
        ):
            status, detail = btrfs.disable_cow_if_btrfs("podman")
        assert status == "failed"
        assert "Operation not permitted" in detail
