"""Tests for core.discovery — dynamic Windows app enumeration.

Covers:
- JSON parsing (happy path, malformed, BOM, object-not-array, truncated flag).
- Entry validation (source allowlist, UWP launch_uri required, length caps,
  base64 decode, oversized icon dropped).
- Slug generation (collision safety, Unicode, unsafe chars).
- persist_discovered (writes app.toml, icon magic-byte dispatch, replace=True
  clears old, path-traversal guard in _safe_rmtree).
- discover_apps end-to-end (subprocess mocking for copy/exec, timeout,
  pod-not-running, script-missing, backend gating).
"""

from __future__ import annotations

import base64
import json
import os
import re
import struct
import subprocess
import zlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from winpodx.core.config import Config
from winpodx.core.discovery import (
    DiscoveredApp,
    DiscoveryError,
    _entry_to_discovered,
    _parse_discovery_output,
    _safe_rmtree,
    _slugify_name,
    _sniff_icon_ext,
    _validate_png_bytes,
    discover_apps,
    persist_discovered,
)

# --- Helpers for Popen-based discover_apps mocking -------------------------


def _build_png(
    width: int = 1,
    height: int = 1,
    *,
    body_extra: bytes = b"",
    corrupt_crc: bool = False,
    real_idat: bool = True,
) -> bytes:
    """Construct a PNG for tests.

    When ``real_idat=True`` (default), produces a genuinely decodable
    RGBA PNG of the requested dimensions — IDAT is properly
    zlib-compressed and each scanline is prefixed with the filter byte.
    Such a PNG passes both ``QImage.loadFromData`` and the stdlib chunk
    walker, which is what the icon-happy-path tests need now that M1
    requires structural validity before persistence.

    When ``real_idat=False``, produces the old structurally-valid-but-
    non-decodable shape (correct magic + IHDR + optional garbage IDAT +
    IEND with valid CRCs) — useful for exercising the stdlib chunk
    walker in isolation (which does not try to decompress IDAT).

    ``body_extra`` is only honored when ``real_idat=False``; in real
    mode the IDAT is computed from the declared dimensions.
    ``corrupt_crc`` flips the IHDR CRC so the stdlib walker rejects it.
    """

    def _chunk(chunk_type: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if corrupt_crc and chunk_type == b"IHDR":
            crc ^= 0xDEADBEEF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

    magic = b"\x89PNG\r\n\x1a\n"
    ihdr_payload = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    png = magic + _chunk(b"IHDR", ihdr_payload)
    if real_idat:
        # Filter=0 per scanline + RGBA zero pixels; zlib-compressed.
        scanline = b"\x00" + (b"\x00\x00\x00\x00" * width)
        raw = scanline * height
        png += _chunk(b"IDAT", zlib.compress(raw))
    elif body_extra:
        png += _chunk(b"IDAT", body_extra)
    png += _chunk(b"IEND", b"")
    return png


def _fake_popen(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """Build a MagicMock Popen that emits fixed bytes then terminates.

    The helper wires up ``fileno()`` on the stdout/stderr attributes so
    core.discovery._run_bounded's ``os.read(fd, ...)`` loop sees a clean
    EOF after the provided bytes. Uses an OS pipe per stream so the
    drain-thread code path is exercised verbatim.
    """
    proc = MagicMock(spec=subprocess.Popen)

    stdout_r, stdout_w = os.pipe()
    stderr_r, stderr_w = os.pipe()
    os.write(stdout_w, stdout)
    os.close(stdout_w)
    os.write(stderr_w, stderr)
    os.close(stderr_w)

    stdout_file = MagicMock()
    stdout_file.fileno.return_value = stdout_r
    stderr_file = MagicMock()
    stderr_file.fileno.return_value = stderr_r

    proc.stdout = stdout_file
    proc.stderr = stderr_file
    proc.returncode = returncode
    proc.poll.return_value = returncode
    proc.wait.return_value = returncode
    proc.kill.return_value = None
    return proc


# --- Fixtures --------------------------------------------------------------

# Minimal PNG header (8-byte magic) that _sniff_icon_ext recognizes.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_TINY_PNG = _PNG_MAGIC + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
_TINY_SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'


def _valid_entry(**overrides) -> dict:
    base = {
        "name": "Example App",
        "path": "C:\\Program Files\\Example\\example.exe",
        "args": "",
        "source": "win32",
        "wm_class_hint": "example",
        "launch_uri": "",
        "icon_b64": "",
    }
    base.update(overrides)
    return base


# --- _sniff_icon_ext -------------------------------------------------------


def test_sniff_icon_ext_png():
    assert _sniff_icon_ext(_TINY_PNG) == "png"


def test_sniff_icon_ext_svg():
    assert _sniff_icon_ext(_TINY_SVG) == "svg"


def test_sniff_icon_ext_svg_with_leading_whitespace():
    # SVG files sometimes have leading whitespace before <?xml or <svg.
    assert _sniff_icon_ext(b"   \n" + _TINY_SVG) == "svg"


def test_sniff_icon_ext_empty():
    assert _sniff_icon_ext(b"") == ""


def test_sniff_icon_ext_unknown_format():
    assert _sniff_icon_ext(b"garbage random bytes") == ""


def test_sniff_icon_ext_jpeg_rejected():
    # Only PNG/SVG are accepted; JPEG (FF D8 FF) is not.
    assert _sniff_icon_ext(b"\xff\xd8\xff\xe0" + b"\x00" * 20) == ""


# --- _slugify_name ---------------------------------------------------------


def test_slugify_basic():
    assert _slugify_name("Microsoft Word") == "microsoft-word"


def test_slugify_collapses_non_alnum():
    assert _slugify_name("Foo   Bar  /  Baz") == "foo-bar-baz"


def test_slugify_strips_leading_trailing_punctuation():
    assert _slugify_name("  ---Foo---  ") == "foo"


def test_slugify_unicode_maps_to_empty_or_dash():
    # Non-ASCII chars get collapsed to '-'; pure-Unicode names resolve to
    # empty slugs (which the caller rejects).
    assert _slugify_name("한글") == ""


def test_slugify_bounds_length():
    assert len(_slugify_name("a" * 200)) <= 64


def test_slugify_rejects_path_traversal():
    # Slashes / backslashes / dots collapse to single '-' and the outer
    # regex enforces [a-zA-Z0-9_-] only; the result is a safe leaf name.
    slug = _slugify_name("../../etc/passwd")
    assert "/" not in slug
    assert ".." not in slug
    assert "\\" not in slug


# --- _entry_to_discovered --------------------------------------------------


def test_entry_happy_path():
    app = _entry_to_discovered(_valid_entry())
    assert app is not None
    assert app.name == "example-app"
    assert app.full_name == "Example App"
    assert app.executable.endswith("example.exe")
    assert app.source == "win32"


def test_entry_missing_name_rejected():
    assert _entry_to_discovered(_valid_entry(name="")) is None
    assert _entry_to_discovered(_valid_entry(name=None)) is None


def test_entry_missing_path_rejected():
    assert _entry_to_discovered(_valid_entry(path="")) is None
    assert _entry_to_discovered(_valid_entry(path=None)) is None


def test_entry_invalid_source_normalized_to_win32():
    app = _entry_to_discovered(_valid_entry(source="app_paths"))
    assert app is not None
    assert app.source == "win32"


def test_entry_valid_uwp_source_preserved():
    app = _entry_to_discovered(
        _valid_entry(
            source="uwp",
            launch_uri="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
            path="C:\\Program Files\\WindowsApps\\Microsoft.WindowsCalculator_xxx",
        )
    )
    assert app is not None
    assert app.source == "uwp"
    assert app.launch_uri


def test_entry_uwp_without_launch_uri_rejected():
    app = _entry_to_discovered(
        _valid_entry(source="uwp", launch_uri="", path="C:\\Program Files\\WindowsApps\\xxx")
    )
    assert app is None


# The guest PS script (scripts/windows/discover_apps.ps1) MUST emit a bare
# `PackageFamilyName!AppId` AUMID as launch_uri — never a `shell:AppsFolder\`
# URI. The host-side FreeRDP builder (src/winpodx/core/rdp.py) prepends the
# prefix itself, so a guest-side prefix produces
# `shell:AppsFolder\shell:AppsFolder\...`. This regex mirrors
# rdp._AUMID_RE so the discovery test suite can assert the contract without
# importing from another subpackage.
_BARE_AUMID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}![A-Za-z0-9._-]{1,64}$")


def test_uwp_launch_uri_is_bare_aumid_end_to_end():
    """Mock a guest JSON payload with a bare AUMID, round-trip through the
    parser, and assert the persisted launch_uri still matches the bare form
    (no `shell:AppsFolder\\` prefix leaks through)."""
    payload = json.dumps(
        [
            _valid_entry(
                name="Calculator",
                source="uwp",
                launch_uri="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App",
                path="C:\\Program Files\\WindowsApps\\Microsoft.WindowsCalculator_xxx",
            )
        ]
    )
    apps = _parse_discovery_output(payload)
    assert len(apps) == 1
    calc = apps[0]
    assert calc.source == "uwp"
    assert calc.launch_uri == "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
    assert "shell:AppsFolder" not in calc.launch_uri
    assert _BARE_AUMID_RE.fullmatch(calc.launch_uri) is not None


def test_entry_oversized_name_rejected():
    app = _entry_to_discovered(_valid_entry(name="x" * 500))
    assert app is None


def test_entry_oversized_path_rejected():
    app = _entry_to_discovered(_valid_entry(path="C:\\" + "x" * 2000))
    assert app is None


def test_entry_icon_base64_decoded():
    icon = _valid_entry(icon_b64=base64.b64encode(_TINY_PNG).decode("ascii"))
    app = _entry_to_discovered(icon)
    assert app is not None
    assert app.icon_bytes == _TINY_PNG


def test_entry_malformed_base64_yields_empty_icon():
    app = _entry_to_discovered(_valid_entry(icon_b64="not base64!!!"))
    assert app is not None
    assert app.icon_bytes == b""


def test_entry_oversized_icon_dropped():
    # >1 MiB decoded payload is silently dropped (entry still accepted).
    big = _TINY_PNG + b"\x00" * (1_048_576 + 1)
    entry = _valid_entry(icon_b64=base64.b64encode(big).decode("ascii"))
    app = _entry_to_discovered(entry)
    assert app is not None
    assert app.icon_bytes == b""


# --- _parse_discovery_output -----------------------------------------------


def test_parse_empty_output_returns_empty_list():
    assert _parse_discovery_output("") == []
    assert _parse_discovery_output("   \n\n  ") == []


def test_parse_happy_path():
    payload = json.dumps([_valid_entry(name="App A"), _valid_entry(name="App B")])
    apps = _parse_discovery_output(payload)
    assert len(apps) == 2
    assert apps[0].full_name == "App A"
    assert apps[1].full_name == "App B"


def test_parse_malformed_json_raises():
    with pytest.raises(DiscoveryError, match="Malformed"):
        _parse_discovery_output("{ not json at all")


def test_parse_array_required_or_single_dict_coerced():
    # A bare object becomes a single-element list per core's parser.
    payload = json.dumps(_valid_entry(name="Solo"))
    apps = _parse_discovery_output(payload)
    assert len(apps) == 1
    assert apps[0].full_name == "Solo"


def test_parse_scalar_json_raises():
    # A scalar (string/int) cannot be coerced and must raise.
    with pytest.raises(DiscoveryError, match="must be an array"):
        _parse_discovery_output('"just a string"')


def test_parse_strips_utf8_bom():
    payload = "﻿" + json.dumps([_valid_entry()])
    apps = _parse_discovery_output(payload)
    assert len(apps) == 1


def test_parse_ignores_non_dict_entries():
    payload = json.dumps([_valid_entry(), "garbage", 42, None, _valid_entry(name="B")])
    apps = _parse_discovery_output(payload)
    assert len(apps) == 2


def test_parse_truncated_flag_respected():
    payload = json.dumps([_valid_entry(), {"_truncated": True}])
    apps = _parse_discovery_output(payload)
    # Truncated marker is consumed, not converted into an app.
    assert len(apps) == 1


def test_parse_max_apps_cap():
    # Feed >500 entries; parser should stop at MAX_APPS.
    entries = [_valid_entry(name=f"App {i}") for i in range(600)]
    apps = _parse_discovery_output(json.dumps(entries))
    assert len(apps) == 500


# --- persist_discovered ----------------------------------------------------


def test_persist_writes_app_toml(tmp_path):
    app = DiscoveredApp(
        name="example-app",
        full_name="Example App",
        executable="C:\\Program Files\\Example\\example.exe",
    )
    written = persist_discovered([app], target_dir=tmp_path)
    assert len(written) == 1
    toml_path = written[0]
    assert toml_path.exists()
    content = toml_path.read_text(encoding="utf-8")
    assert 'name = "example-app"' in content
    assert 'full_name = "Example App"' in content


def test_persist_writes_png_icon(tmp_path):
    # Valid PNG is required post-M1 (magic-only payloads are rejected).
    png = _build_png(width=1, height=1, body_extra=b"\x00" * 4)
    app = DiscoveredApp(
        name="example-app",
        full_name="Example",
        executable="C:\\example.exe",
        icon_bytes=png,
    )
    persist_discovered([app], target_dir=tmp_path)
    icon = tmp_path / "example-app" / "icon.png"
    assert icon.exists()
    assert icon.read_bytes() == png


def test_persist_writes_svg_icon(tmp_path):
    app = DiscoveredApp(
        name="example-app",
        full_name="Example",
        executable="C:\\example.exe",
        icon_bytes=_TINY_SVG,
    )
    persist_discovered([app], target_dir=tmp_path)
    icon = tmp_path / "example-app" / "icon.svg"
    assert icon.exists()


def test_persist_skips_unknown_icon_format(tmp_path):
    app = DiscoveredApp(
        name="example-app",
        full_name="Example",
        executable="C:\\example.exe",
        icon_bytes=b"\xff\xd8\xff\xe0 garbage jpeg",
    )
    persist_discovered([app], target_dir=tmp_path)
    app_dir = tmp_path / "example-app"
    # app.toml is still written but no icon file in a recognized format.
    assert (app_dir / "app.toml").exists()
    assert not (app_dir / "icon.png").exists()
    assert not (app_dir / "icon.svg").exists()
    assert not (app_dir / "icon.jpg").exists()


def test_persist_deduplicates_by_slug(tmp_path):
    # Two apps with the same slug: second should be silently skipped.
    a = DiscoveredApp(name="example-app", full_name="A", executable="C:\\a.exe")
    b = DiscoveredApp(name="example-app", full_name="B", executable="C:\\b.exe")
    written = persist_discovered([a, b], target_dir=tmp_path)
    assert len(written) == 1
    # First-seen wins.
    content = (tmp_path / "example-app" / "app.toml").read_text()
    assert 'full_name = "A"' in content


def test_persist_replace_true_clears_old_entries(tmp_path):
    # Prime the dir with stale content.
    stale = tmp_path / "example-app"
    stale.mkdir()
    (stale / "stale.txt").write_text("leftover")

    app = DiscoveredApp(name="example-app", full_name="Fresh", executable="C:\\e.exe")
    persist_discovered([app], target_dir=tmp_path, replace=True)
    assert not (stale / "stale.txt").exists()
    assert (stale / "app.toml").exists()


def test_persist_replace_false_preserves_old_entries(tmp_path):
    stale_dir = tmp_path / "example-app"
    stale_dir.mkdir()
    (stale_dir / "stale.txt").write_text("preserved")

    app = DiscoveredApp(name="example-app", full_name="Fresh", executable="C:\\e.exe")
    persist_discovered([app], target_dir=tmp_path, replace=False)
    # app.toml still written (overwritten), but stale sibling stays.
    assert (stale_dir / "stale.txt").exists()
    assert (stale_dir / "app.toml").exists()


def test_persist_rejects_unsafe_slug(tmp_path):
    # _SAFE_NAME_RE gatekeeper blocks anything not matching [a-zA-Z0-9_-].
    bad = DiscoveredApp(name="has space", full_name="Bad", executable="C:\\b.exe")
    written = persist_discovered([bad], target_dir=tmp_path)
    assert written == []
    assert not (tmp_path / "has space").exists()


# --- _safe_rmtree ----------------------------------------------------------


def test_safe_rmtree_removes_subpath(tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "file.txt").write_text("x")
    _safe_rmtree(inner, tmp_path)
    assert not inner.exists()


def test_safe_rmtree_refuses_escape(tmp_path, caplog):
    # Construct a path that resolves outside the target root.
    other = tmp_path.parent / f"winpodx-test-escape-{tmp_path.name}"
    other.mkdir()
    (other / "keep.txt").write_text("should survive")
    try:
        _safe_rmtree(other, tmp_path)
        assert other.exists()
        assert (other / "keep.txt").exists()
    finally:
        # Cleanup regardless of test outcome.
        for f in other.iterdir():
            f.unlink()
        other.rmdir()


def test_safe_rmtree_handles_symlink(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "file.txt").write_text("x")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unsupported on this platform")
    _safe_rmtree(link, tmp_path)
    # Symlink is removed without following into the target.
    assert not link.exists()
    assert target.exists()
    assert (target / "file.txt").exists()


# --- discover_apps end-to-end ----------------------------------------------


def _make_cfg(backend: str = "podman") -> Config:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.pod.container_name = "winpodx-test"
    return cfg


def test_discover_rejects_libvirt_backend():
    cfg = _make_cfg(backend="libvirt")
    with pytest.raises(DiscoveryError, match="container backend"):
        discover_apps(cfg)


def test_discover_rejects_manual_backend():
    cfg = _make_cfg(backend="manual")
    with pytest.raises(DiscoveryError, match="container backend"):
        discover_apps(cfg)


def test_discover_requires_runtime_on_path():
    cfg = _make_cfg(backend="podman")
    with patch("winpodx.core.discovery.shutil.which", return_value=None):
        with pytest.raises(DiscoveryError, match="not found on PATH"):
            discover_apps(cfg)


def test_discover_requires_script_file():
    cfg = _make_cfg(backend="podman")
    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch(
            "winpodx.core.discovery._ps_script_path",
            return_value=Path("/nonexistent/discover_apps.ps1"),
        ),
    ):
        with pytest.raises(DiscoveryError, match="Discovery script not found"):
            discover_apps(cfg)


def test_discover_surfaces_pod_not_running(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    cp_err = subprocess.CalledProcessError(
        1, ["podman", "cp"], output="", stderr="no such container"
    )
    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=cp_err),
    ):
        with pytest.raises(DiscoveryError, match="Failed to copy"):
            discover_apps(cfg)


def test_discover_surfaces_timeout(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    def cp_ok(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "")

    # Simulate a child that never naturally terminates. The bounded
    # loop in _run_bounded must kill it once the deadline elapses.
    stuck = _fake_popen(stdout=b"", stderr=b"", returncode=-9)
    stuck.poll.return_value = None  # never finishes on its own

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=cp_ok),
        patch("winpodx.core.discovery.subprocess.Popen", return_value=stuck),
    ):
        with pytest.raises(DiscoveryError, match="timed out") as excinfo:
            discover_apps(cfg, timeout=1)
        assert excinfo.value.kind == "timeout"


def test_discover_happy_path_roundtrip(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    payload = json.dumps([_valid_entry(name="Calculator")]).encode("utf-8")

    def cp_ok(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "")

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=cp_ok),
        patch(
            "winpodx.core.discovery.subprocess.Popen",
            return_value=_fake_popen(stdout=payload, returncode=0),
        ),
    ):
        apps = discover_apps(cfg)

    assert len(apps) == 1
    assert apps[0].full_name == "Calculator"


def test_discover_nonzero_exit_raises(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    def cp_ok(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "")

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=cp_ok),
        patch(
            "winpodx.core.discovery.subprocess.Popen",
            return_value=_fake_popen(stdout=b"", stderr=b"Get-AppxPackage failed", returncode=42),
        ),
    ):
        with pytest.raises(DiscoveryError, match="rc=42") as excinfo:
            discover_apps(cfg)
        assert excinfo.value.kind == "script_failed"


# ---------------------------------------------------------------------------
# core-team additions (v0.1.8 blockers — I1 / I2 / M1 / L1)
# ---------------------------------------------------------------------------


def test_discovery_error_kind_preserved():
    """I1: DiscoveryError must round-trip the `kind` keyword."""
    err = DiscoveryError("boom", kind="script_failed")
    assert str(err) == "boom"
    assert err.kind == "script_failed"

    # Default must also hold so legacy zero-arg construction still works.
    bare = DiscoveryError()
    assert bare.kind == ""

    # And `kind` must be reachable from a raise-and-catch flow.
    with pytest.raises(DiscoveryError) as excinfo:
        raise DiscoveryError("x", kind="timeout")
    assert excinfo.value.kind == "timeout"


def test_discovery_error_kinds_at_all_raise_sites():
    """I1 coverage: every raise site in discovery.py emits a canonical kind."""
    # unsupported_backend
    cfg = _make_cfg(backend="libvirt")
    with pytest.raises(DiscoveryError) as e1:
        discover_apps(cfg)
    assert e1.value.kind == "unsupported_backend"

    # pod_not_running (runtime missing on PATH)
    cfg = _make_cfg(backend="podman")
    with patch("winpodx.core.discovery.shutil.which", return_value=None):
        with pytest.raises(DiscoveryError) as e2:
            discover_apps(cfg)
    assert e2.value.kind == "pod_not_running"

    # script_missing
    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch(
            "winpodx.core.discovery._ps_script_path",
            return_value=Path("/nonexistent/discover_apps.ps1"),
        ),
    ):
        with pytest.raises(DiscoveryError) as e3:
            discover_apps(cfg)
    assert e3.value.kind == "script_missing"

    # bad_json
    with pytest.raises(DiscoveryError) as e4:
        _parse_discovery_output("{ not json")
    assert e4.value.kind == "bad_json"


def test_discovered_app_has_slug_and_icon_path_after_persist(tmp_path):
    """I2: slug + icon_path must be populated post persist_discovered."""
    png = _build_png(width=2, height=2, body_extra=b"\x00" * 4)
    app = DiscoveredApp(
        name="example-app",
        full_name="Example App",
        executable="C:\\Example\\example.exe",
        icon_bytes=png,
    )
    # Before persist: empty contract fields.
    assert app.slug == ""
    assert app.icon_path == ""

    persist_discovered([app], target_dir=tmp_path)

    assert app.slug == "example-app"
    expected_icon = (tmp_path / "example-app" / "icon.png").resolve()
    assert app.icon_path == str(expected_icon)
    assert Path(app.icon_path).is_file()


def test_discovered_app_icon_path_empty_when_no_icon(tmp_path):
    """I2: slug still stamped even when no icon was supplied."""
    app = DiscoveredApp(name="no-icon", full_name="NoIcon", executable="C:\\x.exe")
    persist_discovered([app], target_dir=tmp_path)
    assert app.slug == "no-icon"
    assert app.icon_path == ""


def test_validate_png_rejects_crafted_magic_only_payload():
    """M1: magic bytes + garbage must not be trusted as a PNG."""
    crafted = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert _validate_png_bytes(crafted) is False


def test_validate_png_rejects_empty_bytes():
    assert _validate_png_bytes(b"") is False


def test_validate_png_rejects_wrong_magic():
    assert _validate_png_bytes(b"GIF89a" + b"\x00" * 100) is False


def test_validate_png_stdlib_rejects_crc_corruption():
    """M1: a single-bit flip in IHDR CRC must be caught by the stdlib walker."""
    from winpodx.core.discovery import _validate_png_stdlib

    png = _build_png(corrupt_crc=True)
    assert _validate_png_stdlib(png) is False


def test_validate_png_stdlib_rejects_oversized_dimensions():
    """M1: a 2048x2048 PNG must be rejected even with valid chunks."""
    from winpodx.core.discovery import _validate_png_stdlib

    png = _build_png(width=2048, height=2048)
    assert _validate_png_stdlib(png) is False


def test_validate_png_accepts_real_png():
    """M1: a structurally valid PNG passes validation."""
    png = _build_png(width=1, height=1, body_extra=b"\x00" * 8)
    assert _validate_png_bytes(png) is True


def test_persist_rejects_malformed_png_but_persists_entry(tmp_path):
    """M1 integration: malformed PNG must not abort the run; entry still written."""
    crafted = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    app = DiscoveredApp(
        name="badpng-app",
        full_name="BadPng",
        executable="C:\\b.exe",
        icon_bytes=crafted,
    )
    written = persist_discovered([app], target_dir=tmp_path)
    assert len(written) == 1  # entry persists
    app_dir = tmp_path / "badpng-app"
    assert (app_dir / "app.toml").exists()
    assert not (app_dir / "icon.png").exists()  # icon rejected
    assert app.icon_path == ""


def test_discovery_stdout_cap_triggers_truncated_error(tmp_path):
    """L1: child emitting >HARD_STDOUT_CAP bytes must raise kind=truncated."""
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    def cp_ok(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, "", "")

    from winpodx.core.discovery import HARD_STDOUT_CAP

    # Flood ~65 MiB so the drain loop trips the cap well before completion.
    flood_size = HARD_STDOUT_CAP + (1 * 1024 * 1024)

    class _FloodingProc:
        """Stand-in for subprocess.Popen that floods stdout via an OS pipe."""

        def __init__(self) -> None:
            import threading as _t

            self._stdout_r, self._stdout_w = os.pipe()
            self._stderr_r, self._stderr_w = os.pipe()
            os.close(self._stderr_w)  # no stderr output
            self.returncode: int | None = None
            self._stdout_closed = False

            def _writer() -> None:
                chunk = b"A" * (1024 * 1024)  # 1 MiB per write
                written = 0
                try:
                    while written < flood_size:
                        os.write(self._stdout_w, chunk)
                        written += len(chunk)
                except OSError:
                    # Pipe closed by kill() — expected once the cap trips.
                    return
                finally:
                    try:
                        os.close(self._stdout_w)
                    except OSError:
                        pass

            self._writer_thread = _t.Thread(target=_writer, daemon=True)
            self._writer_thread.start()

            self.stdout = MagicMock()
            self.stdout.fileno.return_value = self._stdout_r
            self.stderr = MagicMock()
            self.stderr.fileno.return_value = self._stderr_r

        def poll(self):
            # Never naturally finishes — the drain cap must be what stops us.
            return None

        def kill(self) -> None:
            self.returncode = -9
            if not self._stdout_closed:
                self._stdout_closed = True
                try:
                    os.close(self._stdout_w)
                except OSError:
                    pass

        def wait(self, timeout=None) -> int:
            if self.returncode is None:
                self.returncode = -9
            return self.returncode

    flooding = _FloodingProc()

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=cp_ok),
        patch("winpodx.core.discovery.subprocess.Popen", return_value=flooding),
    ):
        with pytest.raises(DiscoveryError) as excinfo:
            discover_apps(cfg, timeout=60)
        assert excinfo.value.kind == "truncated"
