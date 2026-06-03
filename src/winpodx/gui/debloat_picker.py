# SPDX-License-Identifier: MIT
"""Qt picker dialog for ``winpodx debloat`` selection (#247 phase 3).

Surfaces every catalog item as a checkbox with a risk badge + tooltip,
plus a preset radio group that seeds the checkbox state. The user can
freely toggle any checkbox after picking a preset; doing so flips the
radio to ``Custom``. On Apply the selected names are returned to the
caller, which is responsible for actually running the orchestrator
payload via ``run_via_transport`` -- the dialog itself is pure UI.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.debloat import DebloatCatalog
from winpodx.core.i18n import tr
from winpodx.gui.theme import (
    BTN_PRIMARY,
    BTN_SECONDARY,
    CHECKBOX,
    DIALOG,
    RADIO,
    SCROLL_AREA,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    C,
)

# Color tokens for the risk badge. Pulled inline rather than imported
# from ``winpodx.gui.theme`` so this module stays usable from tests /
# standalone screenshots without the rest of the theme infrastructure
# (the theme module pulls in palette globals + Qt-specific helpers
# that aren't needed for a leaf dialog).
_RISK_COLOR = {"low": C.GREEN, "medium": C.PEACH, "high": C.RED}

# Hover text for each risk badge, mirroring the legend.
_RISK_TOOLTIP = {
    "low": "LOW — generally safe to apply.",
    "medium": "MEDIUM — changes behavior; review before applying.",
    "high": "HIGH — may break features or remove apps.",
}

# Plain-language description of what each named preset does. Keyed by the
# lowercase preset name from the catalog; presets the catalog ships that
# aren't listed here fall back to a generic "N item(s)" line so a future
# catalog addition still renders sensibly.
_PRESET_DESCRIPTIONS = {
    "normal": "Normal — disable telemetry, ads, and suggestions. Generally safe.",
    "aggressive": (
        "Aggressive — Normal plus removing built-in apps (e.g. OneDrive). "
        "Frees more but may break some features."
    ),
    "custom": "Custom — your own hand-picked set; nothing is assumed.",
}


class DebloatPickerDialog(QDialog):
    """Modal item picker driven by a ``DebloatCatalog``.

    Usage:

        dialog = DebloatPickerDialog(catalog, parent=window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            apply_undo = dialog.run_undo()
            names = dialog.selected_items()
            # ... run build_run_script / build_undo_script over `names`

    Emits :attr:`apply_requested` when the user clicks Apply, after the
    dialog has been accepted. The signal carries ``(selected_names,
    run_undo_flag)`` so callers can wire it directly to a worker
    thread without re-querying the dialog afterwards.
    """

    apply_requested = Signal(list, bool)

    def __init__(
        self,
        catalog: DebloatCatalog,
        *,
        initial_preset: str = "normal",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog = catalog
        self._suppress_recompute = False
        self._run_undo = False

        # ``_item_boxes`` is the source of truth -- ordered insertion
        # mirrors catalog declaration order so visual rows match the
        # underlying TOML layout (and so the user sees the same order
        # the CLI's --list output prints).
        self._item_boxes: dict[str, QCheckBox] = {}

        self.setWindowTitle(tr("Debloat picker"))
        self.setMinimumWidth(560)
        self.setModal(True)
        self.setStyleSheet(DIALOG + CHECKBOX + RADIO + SCROLL_AREA)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(SPACE_M)

        # --- Title + subtitle --------------------------------------------
        title = QLabel(tr("Debloat picker"))
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {C.TEXT};")
        outer.addWidget(title)

        subtitle = QLabel(
            tr(
                "Pick a preset and (optionally) tweak the per-item checkboxes. "
                "Selected items run inside the Windows guest via the agent "
                "transport. Items tagged (one-way) cannot be undone — they have "
                "no reverse script."
            )
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 12px;")
        outer.addWidget(subtitle)

        # Risk-scale legend so the per-item badges read clearly.
        legend = QLabel(
            tr("Risk: ")
            + f'<span style="color:{_RISK_COLOR["low"]}">'
            + tr("LOW = generally safe")
            + "</span>  ·  "
            + f'<span style="color:{_RISK_COLOR["medium"]}">'
            + tr("MEDIUM = changes behavior")
            + "</span>  ·  "
            + f'<span style="color:{_RISK_COLOR["high"]}">'
            + tr("HIGH = may break features")
            + "</span>"
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        legend.setWordWrap(True)
        legend.setStyleSheet(f"font-size: 11px; color: {C.SUBTEXT0};")
        outer.addWidget(legend)

        # --- Preset radio group ------------------------------------------
        preset_row = QHBoxLayout()
        preset_row.setSpacing(SPACE_L)
        preset_label = QLabel(tr("Preset:"))
        preset_label.setStyleSheet(f"font-weight: 500; color: {C.SUBTEXT0};")
        preset_row.addWidget(preset_label)

        self._preset_group = QButtonGroup(self)
        self._preset_buttons: dict[str, QRadioButton] = {}
        for preset_name in catalog.preset_names:
            btn = QRadioButton(preset_name.capitalize())
            btn.toggled.connect(self._on_preset_toggled)
            self._preset_group.addButton(btn)
            self._preset_buttons[preset_name] = btn
            preset_row.addWidget(btn)

        self._custom_button = QRadioButton(tr("Custom"))
        # ``Custom`` isn't a real catalog preset -- it just disables the
        # seed-from-preset behaviour. We do NOT connect its toggled
        # signal because flipping to Custom should leave the current
        # checkbox state alone; the only path that lands on Custom is
        # the user toggling a checkbox, which we drive directly.
        self._preset_group.addButton(self._custom_button)
        preset_row.addWidget(self._custom_button)
        preset_row.addStretch()

        outer.addLayout(preset_row)

        # Plain-language description of the currently-selected preset,
        # refreshed by the preset/custom toggles.
        self._preset_desc = QLabel("")
        self._preset_desc.setWordWrap(True)
        self._preset_desc.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 11px;")
        outer.addWidget(self._preset_desc)

        # --- Item list ---------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.StyledPanel)
        content = QWidget()
        items_layout = QVBoxLayout(content)
        items_layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        items_layout.setSpacing(SPACE_S)

        for item in catalog.items.values():
            row = QHBoxLayout()
            row.setSpacing(SPACE_S)

            box = QCheckBox()
            box.toggled.connect(self._on_item_toggled)
            self._item_boxes[item.name] = box
            row.addWidget(box)

            badge = QLabel(item.risk.upper())
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedWidth(60)
            badge.setToolTip(_RISK_TOOLTIP.get(item.risk, ""))
            badge.setStyleSheet(
                f"background: {_RISK_COLOR.get(item.risk, '#888')};"
                f" color: {C.CRUST}; border-radius: 6px;"
                " padding: 2px 4px; font-size: 11px; font-weight: 500;"
            )
            row.addWidget(badge)

            label = QLabel(item.label)
            # Full purpose on hover so every item is explainable even when the
            # short label can't carry it.
            label.setToolTip(item.description)
            label.setStyleSheet(f"font-size: 13px; color: {C.TEXT};")
            row.addWidget(label, 1)

            one_way = QLabel(tr("(one-way)")) if not item.is_reversible else QLabel("")
            if not item.is_reversible:
                one_way.setToolTip(tr("Cannot be undone — this item has no reverse script."))
            one_way.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 11px;")
            row.addWidget(one_way)

            items_layout.addLayout(row)

        items_layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # --- Footer (count + buttons) ------------------------------------
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 12px;")
        outer.addWidget(self._count_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Apply
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setStyleSheet(BTN_PRIMARY)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setStyleSheet(BTN_SECONDARY)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._on_apply)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        # --- Initial state ----------------------------------------------
        initial = initial_preset if initial_preset in catalog.presets else "normal"
        self._preset_buttons[initial].setChecked(True)
        # ``setChecked(True)`` above already triggered ``_on_preset_toggled``,
        # which seeded the checkboxes and updated the count label.

    # -----------------------------------------------------------------
    # Selection helpers (public API for callers).
    # -----------------------------------------------------------------

    def selected_items(self) -> list[str]:
        """Names of the currently-checked items, in catalog order."""
        return [name for name, box in self._item_boxes.items() if box.isChecked()]

    def run_undo(self) -> bool:
        """True when the dialog was accepted with Undo intent. P3 always
        applies; #247 P3 follow-up will add an Undo mode toggle."""
        return self._run_undo

    # -----------------------------------------------------------------
    # Internal slot handlers.
    # -----------------------------------------------------------------

    def _on_preset_toggled(self, checked: bool) -> None:
        if not checked or self._suppress_recompute:
            return
        sender = self.sender()
        for preset_name, btn in self._preset_buttons.items():
            if btn is sender:
                members = set(self._catalog.items_for_preset(preset_name))
                self._set_checkboxes_to(members)
                self._update_preset_desc(preset_name)
                break

    def _update_preset_desc(self, preset_name: str) -> None:
        """Refresh the plain-language description for the active preset."""
        desc = _PRESET_DESCRIPTIONS.get(preset_name.lower())
        if desc is None:
            count = len(self._catalog.items_for_preset(preset_name))
            desc = tr("{name} — preset with {count} item(s).").format(
                name=preset_name.capitalize(), count=count
            )
        else:
            desc = tr(desc)
        self._preset_desc.setText(desc)

    def _set_checkboxes_to(self, members: set[str]) -> None:
        """Apply ``members`` as the checked set without re-firing toggles
        back into the preset radio (which would flip it to Custom)."""
        self._suppress_recompute = True
        try:
            for name, box in self._item_boxes.items():
                box.setChecked(name in members)
        finally:
            self._suppress_recompute = False
        self._refresh_count()

    def _on_item_toggled(self, _checked: bool) -> None:
        if self._suppress_recompute:
            return
        # User edited the selection directly -> we're no longer on a
        # named preset. Flip radio to Custom; the radio's signal is
        # not connected for Custom so this won't recursively mutate
        # checkboxes.
        if not self._custom_button.isChecked():
            self._suppress_recompute = True
            try:
                self._custom_button.setChecked(True)
            finally:
                self._suppress_recompute = False
            self._update_preset_desc("custom")
        self._refresh_count()

    def _refresh_count(self) -> None:
        total = len(self._item_boxes)
        chosen = len(self.selected_items())
        self._count_label.setText(
            tr("Selected: {chosen} of {total}").format(chosen=chosen, total=total)
        )

    def _on_apply(self) -> None:
        names = self.selected_items()
        if not names:
            # Nothing selected -- treat Apply as a no-op + reject so
            # callers don't fire an empty run_via_transport payload.
            self.reject()
            return
        self.apply_requested.emit(names, self._run_undo)
        self.accept()
