"""Tests for ``winpodx host-open`` CLI subcommands.

Invokes the dispatcher through ``winpodx.cli.main.main`` so the
parser + handler wiring is exercised end-to-end. XDG isolation comes
from ``conftest._isolate_xdg_and_home``; we write fake ``.desktop``
files under ``$XDG_DATA_HOME/applications`` and assert on the staged
``apps.json`` + the persisted ``winpodx.toml``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from winpodx.cli.main import cli as _cli_entry
from winpodx.core.config import Config

pytest.importorskip("PIL")  # convert_to_ico needs Pillow


@pytest.fixture(autouse=True)
def _isolate_xdg_data_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Pin XDG_DATA_DIRS to an empty dir so the host's /usr/share doesn't leak."""
    empty = tmp_path / "_empty_xdg_dirs"
    empty.mkdir()
    monkeypatch.setenv("XDG_DATA_DIRS", str(empty))
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)


def cli_main(argv: list[str]) -> int:
    """Invoke the top-level CLI and translate ``SystemExit`` back to ``int``.

    ``_dispatch`` wraps every host-open invocation in ``sys.exit(...)`` so
    the dispatcher matches the convention used elsewhere (``migrate``,
    ``check``). Tests want a plain return code, so we trap the
    ``SystemExit`` here and surface its ``code`` value.
    """
    try:
        _cli_entry(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _xdg_apps() -> Path:
    base = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_app(name: str, body: str) -> None:
    (_xdg_apps() / name).write_text(body, encoding="utf-8")


_KATE = (
    "[Desktop Entry]\n"
    "Type=Application\n"
    "Name=Kate\n"
    "Exec=/usr/bin/kate %F\n"
    "Icon=kate\n"
    "MimeType=text/plain;text/xml;\n"
)
_GIMP = (
    "[Desktop Entry]\n"
    "Type=Application\n"
    "Name=GIMP\n"
    "Exec=/usr/bin/gimp %F\n"
    "Icon=gimp\n"
    "MimeType=image/png;image/jpeg;\n"
)


# --- status -------------------------------------------------------------------


def test_status_default_enabled(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "status"])
    out = capsys.readouterr().out
    assert "enabled" in out
    assert "0 slug(s)" in out  # allowlist
    assert "(none — run `winpodx host-open refresh`)" in out


def test_status_json(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "status", "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["enabled"] is True
    assert payload["allowlist"] == []
    assert payload["cache"]["exists"] is False


# --- enable / disable ---------------------------------------------------------


def test_enable_when_already_default_on(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "enable"])
    assert "already enabled" in capsys.readouterr().out
    cfg = Config.load()
    assert cfg.reverse_open.enabled is True


def test_disable_then_re_enable(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "disable"])
    cfg = Config.load()
    assert cfg.reverse_open.enabled is False
    assert "disabled" in capsys.readouterr().out

    cli_main(["host-open", "enable"])
    cfg = Config.load()
    assert cfg.reverse_open.enabled is True


# --- add / remove -------------------------------------------------------------


def test_add_to_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "add", "kate"])
    out = capsys.readouterr().out
    assert "added kate" in out
    cfg = Config.load()
    assert "kate" in cfg.reverse_open.allowlist


def test_add_to_denylist_with_flag(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "add", "evil-app", "--deny"])
    capsys.readouterr()
    cfg = Config.load()
    assert "evil-app" in cfg.reverse_open.denylist
    assert "evil-app" not in cfg.reverse_open.allowlist


def test_add_invalid_slug_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["host-open", "add", "Bad Slug!"])
    err = capsys.readouterr().err
    assert rc != 0 if rc is not None else True
    assert "not a valid" in err


def test_add_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "add", "kate"])
    capsys.readouterr()
    cli_main(["host-open", "add", "kate"])
    assert "already present" in capsys.readouterr().out


def test_add_to_allowlist_removes_from_denylist(
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli_main(["host-open", "add", "kate", "--deny"])
    capsys.readouterr()
    cli_main(["host-open", "add", "kate"])
    cfg = Config.load()
    assert "kate" in cfg.reverse_open.allowlist
    assert "kate" not in cfg.reverse_open.denylist


def test_remove_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "add", "kate"])
    capsys.readouterr()
    cli_main(["host-open", "remove", "kate"])
    cfg = Config.load()
    assert "kate" not in cfg.reverse_open.allowlist


def test_remove_missing_slug_is_noop(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "remove", "not-there"])
    assert "not present" in capsys.readouterr().out


# --- refresh ------------------------------------------------------------------


def test_refresh_writes_manifest_and_icons(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _write_app("kate.desktop", _KATE)
    _write_app("gimp.desktop", _GIMP)

    cli_main(["host-open", "refresh"])
    out = capsys.readouterr().out
    assert "Discovered 2 apps" in out

    manifest_path = Path(os.environ["XDG_DATA_HOME"]) / "winpodx" / "reverse-open" / "apps.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    slugs = sorted(a["slug"] for a in manifest["apps"])
    assert slugs == ["gimp", "kate"]

    icons_dir = manifest_path.parent / "icons"
    assert (icons_dir / "kate.ico").is_file()
    assert (icons_dir / "gimp.ico").is_file()


def test_refresh_json_summary(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "refresh", "--json", "--skip-icons"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["discovered"] == 1
    assert payload["kept"] == 1
    assert payload["skipped"] == []
    # --skip-icons makes the icon counters zero (we never enter the
    # conversion loop).
    assert payload["icons_real"] == 0
    assert payload["icons_placeholder"] == 0


def test_refresh_respects_denylist(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    _write_app("gimp.desktop", _GIMP)
    cli_main(["host-open", "add", "gimp", "--deny"])
    capsys.readouterr()

    cli_main(["host-open", "refresh", "--json", "--skip-icons"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["discovered"] == 2
    assert payload["kept"] == 1
    assert any(s["slug"] == "gimp" and s["reason"] == "denylist" for s in payload["skipped"])


def test_refresh_respects_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    _write_app("gimp.desktop", _GIMP)
    cli_main(["host-open", "add", "kate"])
    capsys.readouterr()

    cli_main(["host-open", "refresh", "--json", "--skip-icons"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["kept"] == 1
    assert any(
        s["slug"] == "gimp" and s["reason"] == "not-in-allowlist" for s in payload["skipped"]
    )


# --- list ---------------------------------------------------------------------


def test_list_scan(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "list"])
    out = capsys.readouterr().out
    assert "kate" in out


def test_list_cached_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["host-open", "list", "--cached"])
    err = capsys.readouterr().err
    assert "no cached manifest" in err
    assert rc != 0


def test_list_cached_reads_manifest(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "refresh", "--skip-icons"])
    capsys.readouterr()
    cli_main(["host-open", "list", "--cached"])
    out = capsys.readouterr().out
    assert "Cached manifest" in out
    assert "kate" in out


def test_list_json_scan(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "list", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "scan"
    assert payload["apps"][0]["slug"] == "kate"


# --- subcommand dispatch -----------------------------------------------------


def test_no_subcommand_errors_helpfully(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli_main(["host-open"])
    err = capsys.readouterr().err
    assert "missing subcommand" in err
    assert rc != 0


# --- daemon lifecycle subcommands -------------------------------------------


def test_daemon_status_when_not_running(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "daemon-status"])
    out = capsys.readouterr().out
    assert "not running" in out


def test_daemon_status_json(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "daemon-status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["running"] is False
    assert payload["pid"] is None
    assert payload["pid_file"].endswith("reverse-open.pid")


def test_stop_listener_when_not_running(capsys: pytest.CaptureFixture[str]) -> None:
    cli_main(["host-open", "stop-listener"])
    out = capsys.readouterr().out
    assert "not running" in out


def test_start_then_stop_then_status(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "refresh", "--skip-icons"])
    capsys.readouterr()

    cli_main(["host-open", "start-listener"])
    out = capsys.readouterr().out
    assert "pid" in out

    cli_main(["host-open", "daemon-status"])
    assert "running (pid " in capsys.readouterr().out

    cli_main(["host-open", "stop-listener"])
    assert "stopped" in capsys.readouterr().out

    cli_main(["host-open", "daemon-status"])
    assert "not running" in capsys.readouterr().out


def test_refresh_signals_running_daemon(capsys: pytest.CaptureFixture[str]) -> None:
    _write_app("kate.desktop", _KATE)
    cli_main(["host-open", "refresh", "--skip-icons"])
    capsys.readouterr()
    cli_main(["host-open", "start-listener"])
    capsys.readouterr()

    try:
        cli_main(["host-open", "refresh", "--skip-icons"])
        out = capsys.readouterr().out
        assert "SIGHUP" in out
    finally:
        cli_main(["host-open", "stop-listener"])
        capsys.readouterr()
