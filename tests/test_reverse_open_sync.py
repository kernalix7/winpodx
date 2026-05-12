"""Tests for ``winpodx.reverse_open.sync``.

The agent network call is mocked through ``unittest.mock`` so the
tests don't require a running guest. We verify (a) the snippet
contains the right base64 payloads, (b) ``SyncError`` is raised when
the simulated agent returns rc!=0, (c) the icon collector handles
missing files gracefully.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from winpodx.core.agent import ExecResult
from winpodx.core.config import Config
from winpodx.reverse_open.sync import (
    SyncError,
    _build_sync_script,
    _collect_icons,
    _read_manifest,
    sync_to_guest,
    unregister_on_guest,
)


def _stage(tmp_path: Path, apps: list[dict], icons: dict[str, bytes]) -> Path:
    stage = tmp_path / "stage"
    stage.mkdir()
    manifest = {
        "version": 1,
        "generated_at": "2026-05-11T00:00:00Z",
        "host": {"xdg_current_desktop": ""},
        "apps": apps,
    }
    (stage / "apps.json").write_text(json.dumps(manifest), encoding="utf-8")
    icons_dir = stage / "icons"
    icons_dir.mkdir()
    for slug, data in icons.items():
        (icons_dir / f"{slug}.ico").write_bytes(data)
    return stage


def _kate_entry() -> dict:
    return {
        "slug": "kate",
        "name": "Kate",
        "comment": "",
        "exec_argv": ["/usr/bin/kate", "%F"],
        "icon_name": "kate",
        "mime_types": ["text/plain"],
        "desktop_file": "/x.desktop",
        "is_default_for": [],
    }


# --- _read_manifest ---------------------------------------------------------


def test_read_manifest_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SyncError, match="missing"):
        _read_manifest(tmp_path)


def test_read_manifest_malformed_raises(tmp_path: Path) -> None:
    (tmp_path / "apps.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SyncError, match="parse failed"):
        _read_manifest(tmp_path)


def test_read_manifest_happy(tmp_path: Path) -> None:
    stage = _stage(tmp_path, [_kate_entry()], {})
    manifest = _read_manifest(stage)
    assert manifest["version"] == 1
    assert manifest["apps"][0]["slug"] == "kate"


# --- _collect_icons ---------------------------------------------------------


def test_collect_icons_skips_missing(tmp_path: Path) -> None:
    stage = _stage(
        tmp_path,
        [_kate_entry(), {**_kate_entry(), "slug": "gimp"}],
        {"kate": b"PNG_FAKE"},  # gimp icon missing
    )
    manifest = _read_manifest(stage)
    icons = _collect_icons(stage, manifest)
    assert "kate" in icons
    assert "gimp" not in icons
    assert icons["kate"] == b"PNG_FAKE"


def test_collect_icons_empty_when_no_apps(tmp_path: Path) -> None:
    stage = _stage(tmp_path, [], {})
    manifest = _read_manifest(stage)
    assert _collect_icons(stage, manifest) == {}


# --- _build_sync_script -----------------------------------------------------


_FAKE_HOST_SCRIPTS = {
    "register": "# register stub",
    "unregister": "# unregister stub",
}
_FAKE_SHIM_B64 = base64.b64encode(b"\x4d\x5aFAKE_PE_BYTES").decode("ascii")


def test_build_sync_script_embeds_apps_b64() -> None:
    apps_text = '{"version":1,"apps":[]}'
    script = _build_sync_script(apps_text, {}, _FAKE_HOST_SCRIPTS, _FAKE_SHIM_B64)
    expected = base64.b64encode(apps_text.encode("utf-8")).decode("ascii")
    assert expected in script
    assert "FromBase64String" in script
    assert "register-apps.ps1" in script


def test_build_sync_script_invokes_register_with_shim_exe_flag() -> None:
    """Regression guard: register-apps.ps1 takes -ShimExe (the new
    Rust .exe param) and -BinDir for the per-app hard-link target."""
    script = _build_sync_script("{}", {}, _FAKE_HOST_SCRIPTS, _FAKE_SHIM_B64)
    assert "-ShimExe $shimExe" in script
    assert "-BinDir $binDir" in script
    # No leftover from the prior .ps1-shim era.
    assert "-ShimPath" not in script


def test_build_sync_script_embeds_shim_via_binary_writer() -> None:
    """The Rust .exe must go through Write-BinaryAtomic — UTF-8
    text-decoding a PE binary would corrupt it."""
    script = _build_sync_script("{}", {}, _FAKE_HOST_SCRIPTS, _FAKE_SHIM_B64)
    assert "Write-BinaryAtomic" in script
    assert f"Write-BinaryAtomic $shimExe '{_FAKE_SHIM_B64}'" in script


def test_build_sync_script_embeds_icon_entries() -> None:
    icons = {
        "kate": base64.b64encode(b"PNG").decode("ascii"),
        "gimp": base64.b64encode(b"ICO").decode("ascii"),
    }
    script = _build_sync_script("{}", icons, _FAKE_HOST_SCRIPTS, _FAKE_SHIM_B64)
    # Both slugs and both base64 blobs appear verbatim in the snippet.
    assert "'kate'" in script
    assert "'gimp'" in script
    for blob in icons.values():
        assert blob in script


def test_build_sync_script_sorts_icon_entries() -> None:
    # Sorted order is part of the contract — keeps the rendered
    # snippet stable for diffing across test runs.
    icons = {
        "zebra": base64.b64encode(b"Z").decode("ascii"),
        "alpha": base64.b64encode(b"A").decode("ascii"),
    }
    script = _build_sync_script("{}", icons, _FAKE_HOST_SCRIPTS, _FAKE_SHIM_B64)
    alpha_pos = script.index("'alpha'")
    zebra_pos = script.index("'zebra'")
    assert alpha_pos < zebra_pos


def test_build_sync_script_embeds_both_ps_scripts() -> None:
    """Regression guard: register + unregister must both be
    base64-embedded so the sync layer doesn't depend on dockur having
    staged the OEM bundle."""
    scripts = {
        "register": "# REGISTER_MARKER",
        "unregister": "# UNREGISTER_MARKER",
    }
    rendered = _build_sync_script("{}", {}, scripts, _FAKE_SHIM_B64)
    for body in scripts.values():
        expected = base64.b64encode(body.encode("utf-8")).decode("ascii")
        assert expected in rendered


# --- sync_to_guest ----------------------------------------------------------


def test_sync_to_guest_happy_path(tmp_path: Path) -> None:
    stage = _stage(tmp_path, [_kate_entry()], {"kate": b"FAKEICO"})
    cfg = Config()
    fake_result = ExecResult(rc=0, stdout="registered=1 skipped=0", stderr="")
    with (
        patch("winpodx.reverse_open.sync.AgentClient") as agent_cls,
        patch(
            "winpodx.reverse_open.sync._read_host_shim_exe",
            return_value=b"\x4d\x5aPE_FAKE",
        ),
    ):
        agent_cls.return_value.exec.return_value = fake_result
        result = sync_to_guest(cfg, stage)
    assert result.ok is True
    assert result.pushed_apps == 1
    assert result.pushed_icons == 1


def test_sync_to_guest_propagates_register_failure(tmp_path: Path) -> None:
    stage = _stage(tmp_path, [_kate_entry()], {})
    cfg = Config()
    fake_result = ExecResult(rc=2, stdout="", stderr="bad slug")
    with (
        patch("winpodx.reverse_open.sync.AgentClient") as agent_cls,
        patch(
            "winpodx.reverse_open.sync._read_host_shim_exe",
            return_value=b"\x4d\x5aPE_FAKE",
        ),
    ):
        agent_cls.return_value.exec.return_value = fake_result
        with pytest.raises(SyncError, match="rc=2"):
            sync_to_guest(cfg, stage)


def test_sync_to_guest_surfaces_missing_shim_binary(tmp_path: Path) -> None:
    """If the Rust shim hasn't been built (or wasn't packaged), the
    sync layer must fail loudly rather than ship handlers that point
    at a nonexistent .exe."""
    stage = _stage(tmp_path, [_kate_entry()], {})
    cfg = Config()
    with patch(
        "winpodx.reverse_open.sync._read_host_shim_exe",
        side_effect=SyncError("shim binary missing"),
    ):
        with pytest.raises(SyncError, match="shim binary missing"):
            sync_to_guest(cfg, stage)


# --- unregister_on_guest ----------------------------------------------------


def test_unregister_on_guest_happy(tmp_path: Path) -> None:
    cfg = Config()
    fake_result = ExecResult(rc=0, stdout="progids=3 ext_refs=12", stderr="")
    with patch("winpodx.reverse_open.sync.AgentClient") as agent_cls:
        agent_cls.return_value.exec.return_value = fake_result
        result = unregister_on_guest(cfg)
    assert result.ok is True
    assert result.pushed_apps == 0
    assert result.pushed_icons == 0


def test_unregister_on_guest_propagates_failure() -> None:
    cfg = Config()
    fake_result = ExecResult(rc=4, stdout="", stderr="not staged")
    with patch("winpodx.reverse_open.sync.AgentClient") as agent_cls:
        agent_cls.return_value.exec.return_value = fake_result
        with pytest.raises(SyncError, match="rc=4"):
            unregister_on_guest(cfg)
