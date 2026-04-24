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
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from winpodx.core.discovery import (
    DiscoveredApp,
    DiscoveryError,
    _entry_to_discovered,
    _parse_discovery_output,
    _safe_rmtree,
    _slugify_name,
    _sniff_icon_ext,
    discover_apps,
    persist_discovered,
)

from winpodx.core.config import Config

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
    app = DiscoveredApp(
        name="example-app",
        full_name="Example",
        executable="C:\\example.exe",
        icon_bytes=_TINY_PNG,
    )
    persist_discovered([app], target_dir=tmp_path)
    icon = tmp_path / "example-app" / "icon.png"
    assert icon.exists()
    assert icon.read_bytes() == _TINY_PNG


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

    def fake_run(*args, **kwargs):
        # First call (cp) succeeds; second (exec) times out.
        if "cp" in args[0]:
            return subprocess.CompletedProcess(args[0], 0, "", "")
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 120))

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=fake_run),
    ):
        with pytest.raises(DiscoveryError, match="timed out"):
            discover_apps(cfg)


def test_discover_happy_path_roundtrip(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    payload = json.dumps([_valid_entry(name="Calculator")])

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if "cp" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, payload, "")

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=fake_run),
    ):
        apps = discover_apps(cfg)

    assert len(apps) == 1
    assert apps[0].full_name == "Calculator"


def test_discover_nonzero_exit_raises(tmp_path):
    cfg = _make_cfg(backend="podman")
    script = tmp_path / "discover_apps.ps1"
    script.write_text("# stub")

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if "cp" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 42, "", "Get-AppxPackage failed")

    with (
        patch("winpodx.core.discovery.shutil.which", return_value="/usr/bin/podman"),
        patch("winpodx.core.discovery._ps_script_path", return_value=script),
        patch("winpodx.core.discovery.subprocess.run", side_effect=fake_run),
    ):
        with pytest.raises(DiscoveryError, match="rc=42"):
            discover_apps(cfg)
