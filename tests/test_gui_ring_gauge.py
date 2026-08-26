# SPDX-License-Identifier: MIT
"""Tests for gui._ring_gauge — the dashboard's custom-painted RingGauge and StatBar.

``paintEvent`` is exercised for real by rendering the widget into a QPixmap under the
offscreen platform, so the drawing code runs instead of being mocked away. Where a
value drives the drawing, the test renders twice and asserts the pixels differ.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from winpodx.gui._ring_gauge import RingGauge, StatBar, _qcolor  # noqa: E402


def _ensure_qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _render(widget, width: int = 160, height: int = 160) -> bytes:
    from PySide6.QtGui import QPixmap

    widget.resize(width, height)
    pixmap = QPixmap(width, height)
    pixmap.fill()
    widget.render(pixmap)
    image = pixmap.toImage()
    return bytes(image.constBits())


# --- _qcolor --------------------------------------------------------------


def test_qcolor_parses_hex_and_keeps_full_alpha_by_default() -> None:
    _ensure_qapp()
    color = _qcolor("#ff8800")

    assert (color.red(), color.green(), color.blue()) == (255, 136, 0)
    assert color.alphaF() == pytest.approx(1.0)


def test_qcolor_applies_fractional_alpha() -> None:
    _ensure_qapp()

    assert _qcolor("#ffffff", 0.25).alphaF() == pytest.approx(0.25, abs=0.01)


# --- RingGauge ------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [(150.0, 100.0), (-20.0, 0.0), (42.5, 42.5), (0.0, 0.0), (100.0, 100.0)],
)
def test_ring_clamps_percentage_into_range(given: float, expected: float) -> None:
    _ensure_qapp()
    gauge = RingGauge("RAM", "#89b4fa")

    gauge.set_value(given, "x")

    assert gauge._pct == pytest.approx(expected)


def test_ring_accepts_none_for_the_unavailable_state() -> None:
    _ensure_qapp()
    gauge = RingGauge("RAM", "#89b4fa")

    gauge.set_value(None, "n/a")

    assert gauge._pct is None
    assert gauge._center_text == "n/a"


def test_ring_coerces_an_int_percentage_to_float() -> None:
    _ensure_qapp()
    gauge = RingGauge("CPU", "#89b4fa")

    gauge.set_value(37, "37%")

    assert isinstance(gauge._pct, float)


def test_ring_size_hint_is_at_least_its_minimum() -> None:
    _ensure_qapp()
    gauge = RingGauge("CPU", "#89b4fa")

    hint = gauge.sizeHint()

    assert hint.width() == gauge.minimumWidth()
    assert hint.height() == gauge.minimumHeight()


def test_ring_paints_without_a_value() -> None:
    _ensure_qapp()
    gauge = RingGauge("Disk", "#a6e3a1")
    gauge.set_value(None, "--")

    assert len(_render(gauge)) > 0


def test_ring_arc_actually_reflects_the_value() -> None:
    _ensure_qapp()
    gauge = RingGauge("Disk", "#a6e3a1")

    gauge.set_value(0, "0%")
    empty = _render(gauge)
    gauge.set_value(100, "0%")
    full = _render(gauge)

    assert empty != full


def test_ring_center_text_is_drawn() -> None:
    _ensure_qapp()
    gauge = RingGauge("Disk", "#a6e3a1")

    gauge.set_value(50, "50%")
    with_text = _render(gauge)
    gauge.set_value(50, "")
    without_text = _render(gauge)

    assert with_text != without_text


# --- StatBar --------------------------------------------------------------


@pytest.mark.parametrize(("given", "expected"), [(180.0, 100.0), (-5.0, 0.0), (60.0, 60.0)])
def test_statbar_clamps_percentage_into_range(given: float, expected: float) -> None:
    _ensure_qapp()
    bar = StatBar("Disk", "#f9e2af")

    bar.set_value(given, "1 / 2 GB")

    assert bar._pct == pytest.approx(expected)


def test_statbar_accepts_none_and_keeps_detail_text() -> None:
    _ensure_qapp()
    bar = StatBar("Disk", "#f9e2af")

    bar.set_value(None, "n/a")

    assert bar._pct is None
    assert bar._detail == "n/a"


def test_statbar_exposes_and_clears_critical_accessibility_state(monkeypatch) -> None:
    _ensure_qapp()
    import winpodx.gui._ring_gauge as gauge_mod

    monkeypatch.setattr(gauge_mod, "tr", lambda text: f"T<{text}>", raising=False)
    bar = StatBar(
        "Disk C:",
        "#89b4fa",
        critical_color="#f38ba8",
        critical_pct=85,
    )

    assert bar.accessibleName() == "Disk C:"

    bar.set_value(84, "54 / 64 GB")
    assert "54 / 64 GB" in bar.accessibleDescription()
    assert "T<WARNING>" not in bar.accessibleDescription()

    for pct in (85, 96):
        bar.set_value(pct, "61 / 64 GB")
        assert "61 / 64 GB" in bar.accessibleDescription()
        assert "T<WARNING>" in bar.accessibleDescription()

    bar.set_value(40, "26 / 64 GB")
    assert "T<WARNING>" not in bar.accessibleDescription()
    bar.set_value(None, "n/a")
    assert bar.accessibleDescription() == "n/a"


def test_statbar_size_hint_matches_its_minimum() -> None:
    _ensure_qapp()
    bar = StatBar("Disk", "#f9e2af")

    hint = bar.sizeHint()

    assert hint.width() == bar.minimumWidth()
    assert hint.height() == bar.minimumHeight()


def test_statbar_fill_actually_reflects_the_value() -> None:
    _ensure_qapp()
    bar = StatBar("Disk", "#f9e2af")

    bar.set_value(0, "0 / 100 GB")
    empty = _render(bar, width=240, height=60)
    bar.set_value(100, "0 / 100 GB")
    full = _render(bar, width=240, height=60)

    assert empty != full


def test_statbar_detail_text_is_drawn() -> None:
    _ensure_qapp()
    bar = StatBar("Disk", "#f9e2af")

    bar.set_value(40, "40 / 100 GB")
    with_detail = _render(bar, width=240, height=60)
    bar.set_value(40, "")
    without_detail = _render(bar, width=240, height=60)

    assert with_detail != without_detail
