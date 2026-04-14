"""Tests for daemon module (lock files, suspend, time sync)."""

from pathlib import Path

from winpodx.core.daemon import cleanup_lock_files


def test_cleanup_lock_files(tmp_path):
    """Lock files should be removed, normal files preserved."""
    # Create a lock file
    lock = tmp_path / "~$test.docx"
    lock.write_text("x")

    # Create a normal file
    normal = tmp_path / "test.docx"
    normal.write_text("real content")

    removed = cleanup_lock_files([tmp_path])

    assert len(removed) == 1
    assert removed[0] == lock
    assert not lock.exists()
    assert normal.exists()


def test_cleanup_ignores_large_files(tmp_path):
    """Files matching lock pattern but >1KB should not be removed."""
    lock = tmp_path / "~$big.docx"
    lock.write_text("x" * 2000)  # > 1KB

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert lock.exists()


def test_cleanup_empty_dir(tmp_path):
    """Should handle empty directories gracefully."""
    removed = cleanup_lock_files([tmp_path])
    assert removed == []


def test_cleanup_nonexistent_dir():
    """Should handle nonexistent directories gracefully."""
    removed = cleanup_lock_files([Path("/nonexistent/path")])
    assert removed == []


def test_cleanup_ignores_symlinks(tmp_path):
    """Symlinks matching lock pattern should NOT be followed or deleted."""
    target = tmp_path / "important.txt"
    target.write_text("important data")

    symlink = tmp_path / "~$evil.docx"
    symlink.symlink_to(target)

    removed = cleanup_lock_files([tmp_path])
    assert len(removed) == 0
    assert target.exists()
    assert symlink.is_symlink()
