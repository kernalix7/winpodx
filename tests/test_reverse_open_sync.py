# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.sync``.

The agent network call is mocked through ``unittest.mock`` so the
tests don't require a running guest. We verify (a) the snippet
contains the right base64 payloads, (b) ``SyncError`` is raised when
the simulated agent returns rc!=0, (c) the icon collector handles
missing files gracefully, and (d) the host license/notice/provenance
payload is copied to the guest alongside the staged binaries with the
original bytes preserved (legal L3).
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from winpodx.core.agent import ExecResult
from winpodx.core.config import Config
from winpodx.reverse_open.sync import (
    SyncError,
    _build_sync_script,
    _collect_icons,
    _notice_entry_line,
    _read_host_notices,
    _read_manifest,
    _SyncPayload,
    _validate_guest_rel,
    sync_to_guest,
    unregister_on_guest,
)
from winpodx.utils.paths import bundle_dir


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
_FAKE_RCEDIT_B64 = base64.b64encode(b"\x4d\x5aFAKE_RCEDIT_PE_BYTES").decode("ascii")

# Synthetic notice payload used for the renderer-level tests so they don't
# depend on the (large) checked-in rust-std copyright inventory.
_FAKE_NOTICES_BYTES = {
    "LICENSE": b"MIT_TEXT",
    "licenses/getrandom-0.2.17/LICENSE-MIT": b"GETRANDOM_MIT",
}
_FAKE_NOTICES_B64 = {
    rel: base64.b64encode(data).decode("ascii") for rel, data in _FAKE_NOTICES_BYTES.items()
}

_B64_LITERAL_RE = re.compile(r"b64 = '([A-Za-z0-9+/=]+)'")
_GUEST_BIN_LITERAL = r"$binDir = 'C:\Users\Public\winpodx\reverse-open\bin'"


def _payload(
    apps_text: str = "{}",
    icons: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    shim: str = _FAKE_SHIM_B64,
    rcedit: str = _FAKE_RCEDIT_B64,
    notices: dict[str, str] | None = None,
) -> _SyncPayload:
    return _SyncPayload(
        apps_json_text=apps_text,
        icons_b64=icons or {},
        host_scripts=scripts or _FAKE_HOST_SCRIPTS,
        shim_b64=shim,
        rcedit_b64=rcedit,
        notices_b64=notices or {},
    )


def test_build_sync_script_embeds_apps_b64() -> None:
    apps_text = '{"version":1,"apps":[]}'
    script = _build_sync_script(_payload(apps_text=apps_text))
    expected = base64.b64encode(apps_text.encode("utf-8")).decode("ascii")
    assert expected in script
    assert "FromBase64String" in script
    assert "register-apps.ps1" in script


def test_build_sync_script_invokes_register_with_shim_exe_flag() -> None:
    """Regression guard: register-apps.ps1 takes -ShimExe (the source
    .exe copied per-slug) and -RcEditExe (used to embed per-slug
    icons into each copy) and -BinDir for the per-app .exe target."""
    script = _build_sync_script(_payload())
    assert "-ShimExe $shimExe" in script
    assert "-RcEditExe $rcEditExe" in script
    assert "-BinDir $binDir" in script
    # No leftover from the prior .ps1-shim era.
    assert "-ShimPath" not in script


def test_build_sync_script_embeds_shim_via_binary_writer() -> None:
    """The Rust .exe must go through Write-BinaryAtomic — UTF-8
    text-decoding a PE binary would corrupt it."""
    script = _build_sync_script(_payload())
    assert "Write-BinaryAtomic" in script
    assert f"Write-BinaryAtomic $shimExe '{_FAKE_SHIM_B64}'" in script


def test_build_sync_script_embeds_rcedit_via_binary_writer() -> None:
    """rcedit.exe is a PE too — same binary-writer path as the shim."""
    script = _build_sync_script(_payload())
    assert f"Write-BinaryAtomic $rcEditExe '{_FAKE_RCEDIT_B64}'" in script


def test_build_sync_script_embeds_icon_entries() -> None:
    icons = {
        "kate": base64.b64encode(b"PNG").decode("ascii"),
        "gimp": base64.b64encode(b"ICO").decode("ascii"),
    }
    script = _build_sync_script(_payload(icons=icons))
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
    script = _build_sync_script(_payload(icons=icons))
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
    rendered = _build_sync_script(_payload(scripts=scripts))
    for body in scripts.values():
        expected = base64.b64encode(body.encode("utf-8")).decode("ascii")
        assert expected in rendered


# --- notices: renderer contract --------------------------------------------


def test_build_sync_script_stages_notices_into_bin_dir() -> None:
    """Notices are staged into the SAME guest bin dir as the per-app
    shims, via the byte-preserving writer, with parent dirs created for
    the nested licence tree."""
    script = _build_sync_script(_payload(notices=_FAKE_NOTICES_B64))
    assert _GUEST_BIN_LITERAL in script
    assert "$noticeEntries = @(" in script
    assert "$dst = Join-Path $binDir $n.rel" in script
    assert "Write-BinaryAtomic $dst $n.b64" in script
    # Parent-directory creation supports the nested licenses/ tree.
    assert "Split-Path -Parent $Path" in script
    assert "New-Item -ItemType Directory" in script


def test_build_sync_script_preserves_nested_notice_paths() -> None:
    script = _build_sync_script(_payload(notices=_FAKE_NOTICES_B64))
    assert "licenses\\getrandom-0.2.17\\LICENSE-MIT" in script
    assert "'LICENSE'" in script


def test_build_sync_script_notice_bytes_decode_exact() -> None:
    """Every embedded notice blob decodes back to the original bytes —
    base64 + Write-BinaryAtomic never UTF-8-normalises binary content."""
    script = _build_sync_script(_payload(notices=_FAKE_NOTICES_B64))
    embedded = set(_B64_LITERAL_RE.findall(script))
    for rel, data in _FAKE_NOTICES_BYTES.items():
        blob = _FAKE_NOTICES_B64[rel]
        assert blob in embedded
        assert base64.b64decode(blob) == data


# --- notices: real host bundle ---------------------------------------------


def test_read_host_notices_byte_equality() -> None:
    """The reader returns every required notice straight from the host
    bundle, byte-for-byte, including the nested licence tree."""
    base = bundle_dir()
    notices = _read_host_notices()

    for name in ("LICENSE", "LICENSE-rcedit.txt", "THIRD_PARTY_NOTICES.txt", "BUILDINFO.json"):
        assert name in notices, f"required notice {name} was not staged"

    assert notices["LICENSE"] == (base / "LICENSE").read_bytes()
    shim_bin = base / "config" / "oem" / "reverse-open" / "shim" / "bin"
    assert notices["LICENSE-rcedit.txt"] == (shim_bin / "LICENSE-rcedit.txt").read_bytes()
    assert notices["THIRD_PARTY_NOTICES.txt"] == (shim_bin / "THIRD_PARTY_NOTICES.txt").read_bytes()
    assert notices["BUILDINFO.json"] == (shim_bin / "BUILDINFO.json").read_bytes()

    nested = sorted(k for k in notices if k.startswith("licenses/"))
    assert nested, "nested licence payload must be retained"
    for rel in nested:
        assert notices[rel] == (shim_bin / rel).read_bytes()


def test_read_host_notices_missing_raises_broken_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bundle with no notices is a broken bundle — fail loudly, never
    sync handlers without the required licence copies."""
    for marker in ("scripts", "config", "data"):
        (tmp_path / marker).mkdir()
    monkeypatch.setenv("WINPODX_BUNDLE_DIR", str(tmp_path))
    with pytest.raises(SyncError, match="notice"):
        _read_host_notices()


def test_read_host_notices_missing_licenses_dir_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Top-level files present but the nested licence tree gone is still
    an incomplete notice payload."""
    for marker in ("scripts", "config", "data"):
        (tmp_path / marker).mkdir()
    bin_dir = tmp_path / "config" / "oem" / "reverse-open" / "shim" / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / "LICENSE").write_bytes(b"MIT")
    for name in ("LICENSE-rcedit.txt", "THIRD_PARTY_NOTICES.txt", "BUILDINFO.json"):
        (bin_dir / name).write_bytes(b"x")
    monkeypatch.setenv("WINPODX_BUNDLE_DIR", str(tmp_path))
    with pytest.raises(SyncError, match="notice"):
        _read_host_notices()


def test_real_notice_payload_stays_within_request_budget() -> None:
    """The guest /exec request body is finite, so the fully-expanded
    notice payload must stay comfortably below the transport ceiling."""
    notices = _read_host_notices()
    b64 = {rel: base64.b64encode(data).decode("ascii") for rel, data in notices.items()}
    script = _build_sync_script(_payload(notices=b64))
    assert len(script.encode("utf-8")) < 8 * 1024 * 1024


def test_real_notice_payload_decodes_to_original_bytes() -> None:
    notices = _read_host_notices()
    b64 = {rel: base64.b64encode(data).decode("ascii") for rel, data in notices.items()}
    script = _build_sync_script(_payload(notices=b64))
    embedded = set(_B64_LITERAL_RE.findall(script))
    for rel, data in notices.items():
        assert b64[rel] in embedded
        assert base64.b64decode(b64[rel]) == data


# --- notices: destination-path safety ---------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["", "/abs", "../escape", "a/../../b", "a/./b", "a\\b", "C:x", " licenses/x"],
)
def test_validate_guest_rel_rejects_traversal(bad: str) -> None:
    with pytest.raises(SyncError, match="unsafe"):
        _validate_guest_rel(bad)


def test_validate_guest_rel_accepts_nested() -> None:
    rel = "licenses/rust-std-1.99.0-nightly/licenses/MIT.txt"
    assert _validate_guest_rel(rel) == rel


def test_notice_entry_line_quotes_powershell_literal() -> None:
    line = _notice_entry_line("licenses/o'brien/LICENSE", "QUJD")
    assert "rel = 'licenses\\o''brien\\LICENSE'" in line
    assert "b64 = 'QUJD'" in line


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
        patch(
            "winpodx.reverse_open.sync._read_host_rcedit_exe",
            return_value=b"\x4d\x5aRCEDIT_FAKE",
        ),
        patch(
            "winpodx.reverse_open.sync._read_host_notices",
            return_value=_FAKE_NOTICES_BYTES,
        ),
    ):
        agent_cls.return_value.exec.return_value = fake_result
        result = sync_to_guest(cfg, stage)
    assert result.ok is True
    assert result.pushed_apps == 1
    assert result.pushed_icons == 1


def test_sync_to_guest_stages_notices_and_keeps_registration(tmp_path: Path) -> None:
    """The end-to-end snippet copies the notice payload AND still runs
    normal registration with the shim / rcedit flags unchanged."""
    stage = _stage(tmp_path, [_kate_entry()], {"kate": b"FAKEICO"})
    cfg = Config()
    fake_result = ExecResult(rc=0, stdout="registered=1 skipped=0", stderr="")
    captured: dict[str, str] = {}

    def _exec(script: str, timeout: int | None = None) -> ExecResult:
        captured["script"] = script
        return fake_result

    with (
        patch("winpodx.reverse_open.sync.AgentClient") as agent_cls,
        patch("winpodx.reverse_open.sync._read_host_shim_exe", return_value=b"\x4d\x5aPE"),
        patch("winpodx.reverse_open.sync._read_host_rcedit_exe", return_value=b"\x4d\x5aRC"),
        patch(
            "winpodx.reverse_open.sync._read_host_notices",
            return_value=_FAKE_NOTICES_BYTES,
        ),
    ):
        agent_cls.return_value.exec.side_effect = _exec
        result = sync_to_guest(cfg, stage)

    assert result.ok is True
    script = captured["script"]
    assert _GUEST_BIN_LITERAL in script
    assert "$dst = Join-Path $binDir $n.rel" in script
    for rel, data in _FAKE_NOTICES_BYTES.items():
        assert base64.b64encode(data).decode("ascii") in script
    # Normal registration path is unchanged.
    assert "-ShimExe $shimExe" in script
    assert "-RcEditExe $rcEditExe" in script
    assert "-BinDir $binDir" in script


def test_sync_to_guest_missing_notice_aborts_before_guest(tmp_path: Path) -> None:
    stage = _stage(tmp_path, [_kate_entry()], {})
    cfg = Config()
    with (
        patch("winpodx.reverse_open.sync.AgentClient") as agent_cls,
        patch(
            "winpodx.reverse_open.sync._read_host_notices",
            side_effect=SyncError("bundle notice missing"),
        ),
    ):
        with pytest.raises(SyncError, match="bundle notice missing"):
            sync_to_guest(cfg, stage)
    agent_cls.assert_not_called()


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
        patch(
            "winpodx.reverse_open.sync._read_host_rcedit_exe",
            return_value=b"\x4d\x5aRCEDIT_FAKE",
        ),
        patch(
            "winpodx.reverse_open.sync._read_host_notices",
            return_value=_FAKE_NOTICES_BYTES,
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
