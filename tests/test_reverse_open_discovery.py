# SPDX-License-Identifier: MIT
"""Tests for ``winpodx.reverse_open.discovery``.

The XDG_* env vars are isolated per-test by ``conftest._isolate_xdg_and_home``,
so each test that writes ``.desktop`` files into ``XDG_DATA_HOME/applications``
or ``XDG_DATA_DIRS/<dir>/applications`` gets a clean tree.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from winpodx.reverse_open.discovery import (
    LinuxApp,
    _exec_is_safe,
    _is_displayed,
    _strip_field_codes,
    discover_apps,
    slug_for_desktop,
)


@pytest.fixture(autouse=True)
def _isolate_xdg_data_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Override XDG_DATA_DIRS so discover_apps doesn't see /usr/share.

    The conftest's autouse fixture only redirects XDG_DATA_HOME plus
    the other ``XDG_*_HOME`` vars; the system path list
    ``XDG_DATA_DIRS`` (which defaults to ``/usr/local/share:/usr/share``
    when unset) still pointed at the real host, so every
    discover_apps() call returned the test app PLUS every legit app
    installed on the developer's machine. Point both system paths at
    an empty dir per-test.
    """
    empty = tmp_path / "_empty_xdg_dirs"
    empty.mkdir()
    monkeypatch.setenv("XDG_DATA_DIRS", str(empty))
    # XDG_CURRENT_DESKTOP is read by _current_desktops(); clear it so
    # OnlyShowIn / NotShowIn filters don't accidentally fire.
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)


def _xdg_apps_dir() -> Path:
    base = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _write_desktop(name: str, body: str) -> Path:
    path = _xdg_apps_dir() / name
    path.write_text(body, encoding="utf-8")
    return path


# --- slug derivation ---------------------------------------------------------


def test_slug_for_desktop_simple() -> None:
    assert slug_for_desktop(Path("/x/kate.desktop")) == "kate"


def test_slug_for_desktop_dotted_reverse_dns() -> None:
    assert slug_for_desktop(Path("/usr/share/applications/org.kde.kate.desktop")) == "org-kde-kate"


def test_slug_for_desktop_uppercase_and_punctuation_stripped() -> None:
    assert slug_for_desktop(Path("/x/Some_Weird+name.desktop")) == "someweirdname"


# --- field code stripping ----------------------------------------------------


def test_strip_field_codes_keeps_f_u_and_drops_others() -> None:
    argv = ["kate", "%c", "%f", "%k", "%i", "%U", "--flag"]
    assert _strip_field_codes(argv) == ["kate", "%f", "%U", "--flag"]


def test_strip_field_codes_preserves_args_starting_with_percent_substring() -> None:
    # Only WHOLE-token field codes are stripped. A token like "%foo"
    # isn't a field code per the spec, so it survives. (Discovery
    # never substitutes anything but the four spec codes, so this is
    # the right semantic.)
    argv = ["weird-app", "%foo", "%f"]
    assert _strip_field_codes(argv) == ["weird-app", "%foo", "%f"]


# --- Hidden / NoDisplay / OnlyShowIn / NotShowIn -----------------------------


def test_is_displayed_hidden_excludes() -> None:
    assert _is_displayed({"Hidden": "true"}, frozenset()) is False


def test_is_displayed_nodisplay_excludes() -> None:
    assert _is_displayed({"NoDisplay": "true"}, frozenset()) is False


def test_is_displayed_only_show_in_matches() -> None:
    entry = {"OnlyShowIn": "KDE;GNOME"}
    assert _is_displayed(entry, frozenset({"KDE"})) is True
    assert _is_displayed(entry, frozenset({"XFCE"})) is False


def test_is_displayed_not_show_in_excludes() -> None:
    entry = {"NotShowIn": "KDE"}
    assert _is_displayed(entry, frozenset({"KDE"})) is False
    assert _is_displayed(entry, frozenset({"GNOME"})) is True


def test_is_displayed_no_filter_when_desktops_empty() -> None:
    # XDG_CURRENT_DESKTOP unset: treat all OnlyShowIn / NotShowIn as
    # no-ops (match gnome-shell's behaviour).
    entry = {"OnlyShowIn": "KDE", "NotShowIn": "GNOME"}
    assert _is_displayed(entry, frozenset()) is True


# --- Exec sanitisation -------------------------------------------------------


def test_exec_is_safe_rejects_shell_metacharacters() -> None:
    assert _exec_is_safe("kate %f && rm -rf /", ["kate", "%f", "&&", "rm"]) is False
    assert _exec_is_safe("kate $(whoami) %f", ["kate", "$(whoami)", "%f"]) is False
    assert _exec_is_safe("kate %f | tee log", ["kate", "%f", "|", "tee"]) is False


def test_exec_is_safe_rejects_wrapper_prefixes() -> None:
    assert _exec_is_safe("wine notepad.exe", ["wine", "notepad.exe"]) is False
    assert _exec_is_safe("winapps run kate", ["winapps", "run", "kate"]) is False
    assert _exec_is_safe("winpodx app run kate", ["winpodx", "app", "run"]) is False


def test_exec_is_safe_accepts_clean_invocation() -> None:
    assert _exec_is_safe("/usr/bin/kate %f", ["/usr/bin/kate", "%f"]) is True


# --- discover_apps integration ------------------------------------------------


def _kate_entry(extra: str = "") -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Kate\n"
        "Comment=Advanced text editor\n"
        "Exec=/usr/bin/kate %F\n"
        "Icon=kate\n"
        "MimeType=text/plain;text/xml;\n"
        f"{extra}"
    )


def test_discover_apps_returns_basic_app() -> None:
    _write_desktop("org.kde.kate.desktop", _kate_entry())
    apps = discover_apps()
    assert len(apps) == 1
    app = apps[0]
    assert isinstance(app, LinuxApp)
    assert app.slug == "org-kde-kate"
    assert app.name == "Kate"
    assert app.exec_argv == ["/usr/bin/kate", "%F"]
    assert app.icon_name == "kate"
    assert app.mime_types == ["text/plain", "text/xml"]


def test_discover_apps_skips_entries_without_mime() -> None:
    _write_desktop(
        "settings.desktop",
        "[Desktop Entry]\nType=Application\nName=Settings\nExec=settings\n",
    )
    assert discover_apps() == []


def test_discover_apps_drops_records_reason() -> None:
    # #594: the `drops` out-param records every scanned .desktop that wasn't
    # returned, with a human reason — the data behind `host-open refresh -v`.
    p = _write_desktop(
        "settings.desktop",
        "[Desktop Entry]\nType=Application\nName=Settings\nExec=settings\n",
    )
    drops: list = []
    apps = discover_apps(drops=drops)
    assert apps == []
    assert (p, "no MimeType= (handles no file type / URL scheme)") in drops


def test_discover_apps_drops_empty_when_app_kept() -> None:
    _write_desktop("org.kde.kate.desktop", _kate_entry())
    drops: list = []
    apps = discover_apps(drops=drops)
    assert len(apps) == 1
    assert drops == []


def test_discover_apps_skips_nodisplay_by_default() -> None:
    _write_desktop("kate.desktop", _kate_entry("NoDisplay=true\n"))
    assert discover_apps() == []


def test_discover_apps_include_nodisplay_opts_in() -> None:
    _write_desktop("kate.desktop", _kate_entry("NoDisplay=true\n"))
    apps = discover_apps(include_nodisplay=True)
    assert [a.slug for a in apps] == ["kate"]


def test_discover_apps_skips_hidden_even_with_include_nodisplay() -> None:
    _write_desktop("kate.desktop", _kate_entry("Hidden=true\n"))
    assert discover_apps(include_nodisplay=True) == []


def test_discover_apps_rejects_shell_exec() -> None:
    _write_desktop(
        "bad.desktop",
        "[Desktop Entry]\nType=Application\nName=Bad\n"
        "Exec=rm -rf %f && echo gotcha\n"
        "MimeType=text/plain;\n",
    )
    assert discover_apps() == []


def test_discover_apps_excludes_wine_wrapper() -> None:
    _write_desktop(
        "wine-notepad.desktop",
        "[Desktop Entry]\nType=Application\nName=Notepad\n"
        "Exec=wine notepad.exe %f\n"
        "MimeType=text/plain;\n",
    )
    assert discover_apps() == []


def test_discover_apps_excludes_winpodx_self_generated_by_name_prefix() -> None:
    _write_desktop(
        "winpodx-windows-notepad.desktop",
        "[Desktop Entry]\nType=Application\nName=Windows: Notepad\n"
        "Exec=/usr/bin/winpodx-run app run notepad\n"
        "MimeType=text/plain;\n",
    )
    assert discover_apps() == []


def test_discover_apps_basename_shadowing(monkeypatch: pytest.MonkeyPatch) -> None:
    # XDG_DATA_HOME/applications wins over XDG_DATA_DIRS entries by
    # basename — matches xdg-open semantics.
    _write_desktop("kate.desktop", _kate_entry("Name=Kate-User\n"))

    system_dir = Path(os.environ["XDG_DATA_HOME"]).parent / "system" / "applications"
    system_dir.mkdir(parents=True)
    (system_dir / "kate.desktop").write_text(_kate_entry("Name=Kate-System\n"), encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_DIRS", str(system_dir.parent))

    apps = discover_apps()
    assert len(apps) == 1
    assert apps[0].name == "Kate-User"


def test_discover_apps_default_handlers_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    _write_desktop("org.kde.kate.desktop", _kate_entry())
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    (config_home / "mimeapps.list").write_text(
        "[Default Applications]\ntext/plain=org.kde.kate.desktop;\n",
        encoding="utf-8",
    )
    apps = discover_apps()
    assert apps[0].is_default_for == ["text/plain"]


def test_discover_apps_picks_up_system_level_kde_mimeapps_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Defaults set in a system-wide ``<desktop>-mimeapps.list``
    (e.g. ``/usr/share/applications/kde-mimeapps.list``) must be
    honoured. This is where distro packaging puts per-DE defaults —
    KDE Plasma installs typically ship a kde-mimeapps.list with
    ``text/plain=org.kde.kate.desktop`` that the user inherits without
    any per-user mimeapps.list.

    Regression guard for the v0.4.6 smoke finding where only 6 of 95
    discovered apps had ``is_default_for`` populated because the
    candidate walk stopped at $XDG_CONFIG_HOME / $XDG_DATA_HOME.
    """
    _write_desktop("org.kde.kate.desktop", _kate_entry())

    # Stand up a fake XDG_DATA_DIRS entry containing a system-wide
    # kde-mimeapps.list with the default. We DO NOT write any
    # user-level mimeapps.list — that's the point: the default lives
    # purely in the system file and discovery must still find it.
    sys_share = tmp_path / "share"
    sys_apps = sys_share / "applications"
    sys_apps.mkdir(parents=True)
    (sys_apps / "kde-mimeapps.list").write_text(
        "[Default Applications]\ntext/plain=org.kde.kate.desktop\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_DATA_DIRS", str(sys_share))
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")

    apps = discover_apps()
    kate = next((a for a in apps if a.slug == "org-kde-kate"), None)
    assert kate is not None
    assert kate.is_default_for == ["text/plain"]


def test_discover_apps_extra_dirs_after_xdg() -> None:
    _write_desktop("kate.desktop", _kate_entry())
    extra = Path(os.environ["XDG_DATA_HOME"]).parent / "extra" / "apps"
    extra.mkdir(parents=True)
    (extra / "gimp.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=GIMP\n"
        "Exec=/usr/bin/gimp %F\nIcon=gimp\nMimeType=image/png;\n",
        encoding="utf-8",
    )
    apps = discover_apps(extra_dirs=[extra])
    slugs = sorted(a.slug for a in apps)
    assert slugs == ["gimp", "kate"]


def test_discover_apps_rejects_unbalanced_quotes_in_exec() -> None:
    _write_desktop(
        "broken.desktop",
        "[Desktop Entry]\nType=Application\nName=Broken\n"
        'Exec=editor "unbalanced %f\n'
        "MimeType=text/plain;\n",
    )
    assert discover_apps() == []
