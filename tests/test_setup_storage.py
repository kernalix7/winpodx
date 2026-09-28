# SPDX-License-Identifier: MIT
"""Red tests for the planned Qt-free storage/ISO validator.

Planned module: ``winpodx.setup_wizard.storage`` (#655).

Contract under test::

    storage.validate_storage_choices(storage, iso, *, existing=None) -> StorageChoices

``storage`` / ``iso`` are raw user choices (``str | os.PathLike | None``),
normalized to absolute ``pathlib.Path | None``. ``existing`` is a
``ExistingInstall(storage=None, named_volume=None)`` when a guest install
already exists, else ``None``. Invalid input raises the typed
``StorageValidationError`` (``.key``, ``.path``) instead of ``SystemExit``, and
the validator never writes to the filesystem.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from winpodx.setup_wizard import storage


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in root.rglob("*")
    }


def test_validate_error_is_typed_not_system_exit() -> None:
    assert issubclass(storage.StorageValidationError, Exception)
    assert not issubclass(storage.StorageValidationError, SystemExit)


def test_storage_choices_are_immutable() -> None:
    choices = storage.StorageChoices(storage=None, iso=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(choices, "storage", Path("/tmp"))
    assert dataclasses.is_dataclass(storage.StorageChoices)


def test_validate_normalizes_expanded_absolute_storage_and_iso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")

    choices = storage.validate_storage_choices("~/winpodx-store", str(iso))

    assert choices.storage == tmp_path / "winpodx-store"
    assert choices.storage is not None and choices.storage.is_absolute()
    assert choices.iso == iso
    assert not (tmp_path / "winpodx-store").exists()


def test_validate_blank_choices_normalize_to_none() -> None:
    choices = storage.validate_storage_choices("", "")

    assert choices.storage is None
    assert choices.iso is None


@pytest.mark.parametrize("selected", ["relative/path", "storage"])
def test_validate_rejects_relative_storage(
    selected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(selected, None)

    assert exc.value.key == "relative"
    assert not (tmp_path / selected).exists()


def test_validate_rejects_symlink_storage(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(link, None)

    assert exc.value.key == "symlink"
    assert link.is_symlink()
    assert real.is_dir()


def test_validate_rejects_occupied_storage_without_touching_contents(tmp_path: Path) -> None:
    target = tmp_path / "occupied"
    target.mkdir()
    guest = target / "windows.img"
    guest.write_bytes(b"guest data")

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(target, None)

    assert exc.value.key == "occupied"
    assert exc.value.path == target
    assert guest.read_bytes() == b"guest data"


def test_validate_rejects_storage_under_file_ancestor(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(blocker / "storage", None)

    assert exc.value.key == "unwritable"
    assert blocker.is_file()


def test_validate_rejects_unwritable_ancestor(tmp_path: Path) -> None:
    ancestor = tmp_path / "denied"
    ancestor.mkdir()

    with patch("winpodx.setup_wizard.storage.os.access", return_value=False):
        with pytest.raises(storage.StorageValidationError) as exc:
            storage.validate_storage_choices(ancestor / "storage", None)

    assert exc.value.key == "unwritable"


def test_validate_reports_inaccessible_storage_on_oserror(tmp_path: Path) -> None:
    target = tmp_path / "locked"
    target.mkdir()

    with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
        with pytest.raises(storage.StorageValidationError) as exc:
            storage.validate_storage_choices(target, None)

    assert exc.value.key == "inaccessible"


def test_validate_rejects_missing_iso(tmp_path: Path) -> None:
    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(None, tmp_path / "missing.iso")

    assert exc.value.key == "iso_not_file"


def test_validate_rejects_directory_iso(tmp_path: Path) -> None:
    directory = tmp_path / "folder.iso"
    directory.mkdir()

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(None, directory)

    assert exc.value.key == "iso_not_file"
    assert directory.is_dir()


def test_validate_rejects_unreadable_iso(tmp_path: Path) -> None:
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")

    with patch.object(Path, "open", side_effect=PermissionError("denied")):
        with pytest.raises(storage.StorageValidationError) as exc:
            storage.validate_storage_choices(None, iso)

    assert exc.value.key == "iso_unreadable"
    assert iso.read_bytes() == b"iso fixture"


def test_validate_accepts_readable_iso(tmp_path: Path) -> None:
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")

    choices = storage.validate_storage_choices(None, iso)

    assert choices.storage is None
    assert choices.iso == iso
    assert iso.read_bytes() == b"iso fixture"


def test_validate_rejects_changed_storage_when_install_exists(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    guest = current / "windows.img"
    guest.write_bytes(b"guest data")
    elsewhere = tmp_path / "elsewhere"

    existing = storage.ExistingInstall(storage=current)
    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(elsewhere, None, existing=existing)

    assert exc.value.key == "existing_storage"
    assert not elsewhere.exists()
    assert guest.read_bytes() == b"guest data"


def test_validate_rejects_iso_when_install_exists(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    iso = tmp_path / "windows.iso"
    iso.write_bytes(b"iso fixture")

    existing = storage.ExistingInstall(storage=current)
    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(None, iso, existing=existing)

    assert exc.value.key == "existing_iso"
    assert iso.read_bytes() == b"iso fixture"


def test_validate_rejects_new_storage_when_named_volume_exists(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    existing = storage.ExistingInstall(named_volume="winpodx-data")

    with pytest.raises(storage.StorageValidationError) as exc:
        storage.validate_storage_choices(elsewhere, None, existing=existing)

    assert exc.value.key == "existing_storage"
    assert not elsewhere.exists()


def test_validate_allows_current_storage_when_install_exists(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.mkdir()
    (current / "windows.img").write_bytes(b"guest data")
    existing = storage.ExistingInstall(storage=current)

    choices = storage.validate_storage_choices(current, None, existing=existing)

    assert choices.storage == current
    assert choices.iso is None


def test_validate_never_mutates_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "windows.img").write_bytes(b"guest data")
    before = _snapshot(tmp_path)

    storage.validate_storage_choices("~/fresh-store", None)
    for storage_choice, iso in (
        (occupied, None),
        ("relative/path", None),
        (None, tmp_path / "missing.iso"),
    ):
        with pytest.raises(storage.StorageValidationError):
            storage.validate_storage_choices(storage_choice, iso)

    assert _snapshot(tmp_path) == before
    assert not (tmp_path / "fresh-store").exists()
