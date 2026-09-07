# SPDX-License-Identifier: MIT
"""Security regression: guest-controlled app names must never render as Qt rich text.

Trust boundary: ``AppInfo.full_name`` / ``AppInfo.description`` originate from the
Windows guest's Start-Menu enumeration (``core/discovery`` -> ``DiscoveredApp``).
Discovery only length-bounds them (``_MAX_NAME_LEN = 255``) and strips control
chars for some fields -- it does NOT strip HTML markup. A hostile guest app can
therefore carry a display name like::

    Word <img src="file:///home/user/.config/winpodx/agent_token.txt">

Qt's ``QLabel`` defaults to ``Qt.TextFormat.AutoText``. When the string satisfies
``Qt::mightBeRichText`` (any ``<tag>`` shape does), the label parses it as HTML and
its rich-text engine *resolves* embedded resources -- ``<img src=file://...>`` reads
a local file, ``<a href=...>`` / ``<img src=http://...>`` reach the network (SSRF).
The visible label text is also silently altered (tags stripped), a UI-spoofing risk.

These tests assert the DEFENSIVE contract: every widget that renders a
guest-controlled string must pin ``Qt.TextFormat.PlainText`` (or HTML-escape the
value) so the markup is shown literally and no resource is fetched. They FAIL on
the current code (widgets left at AutoText) and pass once the sinks are hardened.

Covered production sinks:
  * ``_main_window_library_tiles._AppTile`` (Start-menu tile name + tooltip)
  * ``_main_window_library_tiles.make_library_list_tile`` (Applications list row)
  * ``launcher`` Start-flyout rows (``AppEntry.name`` == ``full_name``)

Run: ``QT_QPA_PLATFORM=offscreen pytest tests/test_security_qt_richtext_injection.py``
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from winpodx.core.app import AppInfo  # noqa: E402

# A payload shaped exactly like a hostile guest Start-Menu entry. It contains a
# space, so it takes the wrapping ``QLabel`` branch (not the ASCII/no-space
# ElidingLabel branch) in the compact tile, and it is unambiguously rich text.
_HOSTILE_NAME = 'Word <img src="file:///etc/hostname"> <b>x</b>'
_HOSTILE_DESC = 'Doc editor <a href="http://attacker.example/leak">click</a>'


@pytest.fixture(scope="module")
def _qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _mk_app(full_name: str = _HOSTILE_NAME, description: str = _HOSTILE_DESC) -> AppInfo:
    return AppInfo(
        name="word",  # slug: always _SAFE_NAME_RE-clean
        full_name=full_name,
        executable="C:\\Program Files\\Word\\word.exe",
        description=description,
        source="discovered",
    )


def _rich_labels(widget) -> list[QLabel]:
    """Return QLabels under *widget* that would render text as rich text.

    A label is dangerous when its effective format is not PlainText AND Qt's
    AutoText heuristic would treat its current text as HTML (i.e. parsing the
    text as HTML changes the visible string -> a tag was interpreted).
    """
    dangerous: list[QLabel] = []
    labels = [widget] if isinstance(widget, QLabel) else list(widget.findChildren(QLabel))
    for lbl in labels:
        text = lbl.text()
        if not text or "<" not in text:
            continue
        if lbl.textFormat() == Qt.TextFormat.PlainText:
            continue  # explicitly safe
        doc = QTextDocument()
        doc.setHtml(text)
        if doc.toPlainText() != text:
            dangerous.append(lbl)
    return dangerous


def _tooltip_renders_verbatim(tip: str, original: str) -> bool:
    doc = QTextDocument()
    doc.setHtml(tip)
    return doc.toPlainText() == original and "<img" not in doc.toHtml().split("<body")[-1]


def test_start_tile_name_is_not_rich_text(_qapp):
    from winpodx.gui._main_window_library_tiles import _AppTile

    tile = _AppTile(_mk_app(), on_launch=lambda a: None, on_menu=lambda a, p: None)

    offenders = _rich_labels(tile)
    assert not offenders, (
        "Guest app name rendered as Qt rich text in the Start-menu tile "
        f"({len(offenders)} label(s)). A hostile guest can inject "
        "<img src=file://...> for local file read / SSRF. Pin Qt.TextFormat."
        "PlainText (or html.escape) on the tile name label."
    )


def test_start_tile_tooltip_is_not_rich_text(_qapp):
    from winpodx.gui._main_window_library_tiles import _AppTile

    tile = _AppTile(_mk_app(), on_launch=lambda a: None, on_menu=lambda a, p: None)

    tip = tile.toolTip()
    # QToolTip always interprets rich text; the safe contract for guest data is
    # that what Qt *renders* is the original string, verbatim -- i.e. the value
    # was HTML-escaped so no tag survives as markup and nothing is fetched.
    assert _tooltip_renders_verbatim(tip, _HOSTILE_NAME), (
        "Guest app name set as a tooltip that Qt will render as rich text "
        "(QToolTip auto-detects HTML). Escape the value before setToolTip()."
    )


def test_library_list_row_name_is_not_rich_text(_qapp):
    from winpodx.gui._main_window_library_tiles import make_library_list_tile

    class _Host:
        _select_mode = False
        _selected_names: set[str] = set()

        def _on_tile_checked(self, name, checked): ...
        def _launch_app(self, app): ...
        def _on_edit_app(self, app): ...
        def _on_reset_app(self, app): ...
        def _on_toggle_app_hidden(self, app): ...
        def _on_delete_app(self, app): ...

    row = make_library_list_tile(_Host(), _mk_app())

    offenders = _rich_labels(row)
    assert not offenders, (
        "Guest app name rendered as Qt rich text in the Applications list row "
        f"({len(offenders)} label(s)). Pin Qt.TextFormat.PlainText."
    )


def test_launcher_row_name_is_not_rich_text(_qapp):
    """The Start flyout builds rows from AppEntry.name, which IS full_name."""
    from winpodx.gui import launcher as L

    entry = L.AppEntry(
        filename="word.desktop",
        name=_HOSTILE_NAME,  # discover_apps() sets name = info.full_name or info.name
        exec_="winpodx app run word",
        slug="word",
    )

    # RevealTile is the tile widget used by the launcher Start grid.
    button = L.RevealTile(entry, lambda e: None)

    offenders = _rich_labels(button)
    # The tooltip is also guest-derived (setToolTip(entry.name)).
    tooltip_safe = _tooltip_renders_verbatim(button.toolTip(), _HOSTILE_NAME)

    assert not offenders and tooltip_safe, (
        "Guest app name rendered as Qt rich text in the launcher Start flyout "
        f"(labels={len(offenders)}, tooltip_safe={tooltip_safe}). Pin "
        "Qt.TextFormat.PlainText on the name label and escape the tooltip."
    )
