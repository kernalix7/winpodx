# SPDX-License-Identifier: MIT
"""Add/Edit app profile dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from winpodx.core.i18n import tr
from winpodx.gui.theme import (
    BTN_GHOST,
    BTN_PRIMARY,
    FONT_CAPTION,
    INPUT,
    C,
    avatar_color,
)
from winpodx.utils.paths import data_dir
from winpodx.utils.toml_writer import dumps as toml_dumps


class AppProfileDialog(QDialog):
    """Dialog for creating or editing a Windows app profile."""

    def __init__(
        self,
        parent=None,
        *,
        name: str = "",
        full_name: str = "",
        executable: str = "",
        categories: str = "",
        mime_types: str = "",
        edit_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.edit_mode = edit_mode
        self.setWindowTitle(tr("Edit App") if edit_mode else tr("Add App"))
        # Taller than before to fit the per-field helper text + the inline
        # validation message added in the GUI UX overhaul.
        self.setFixedSize(580, 540)
        self.setStyleSheet(f"""
            QDialog {{ background: {C.MANTLE}; }}
            QLabel {{ color: {C.TEXT}; font-size: 13px; }}
            {INPUT}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QFrame()
        color = avatar_color(name or full_name or "new")
        header.setStyleSheet(f"background: {C.CRUST}; border-bottom: 3px solid {color};")
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(28, 20, 28, 20)
        header_l.setSpacing(16)

        letter = (full_name or name or "?")[0].upper()
        self._avatar = QLabel(letter)
        self._avatar.setFixedSize(48, 48)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            f"background: {color}; color: {C.CRUST};"
            f" border-radius: 12px; font-size: 20px; font-weight: bold;"
        )
        header_l.addWidget(self._avatar)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel(tr("Edit App Profile") if edit_mode else tr("New App Profile"))
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 18px; font-weight: bold;")
        title_col.addWidget(title)
        sub = QLabel(tr("Define a Windows application for winpodx"))
        sub.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 12px;")
        title_col.addWidget(sub)
        header_l.addLayout(title_col)
        header_l.addStretch()

        layout.addWidget(header)

        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(28, 24, 28, 24)
        body_l.setSpacing(8)

        form = QGridLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)

        self.input_name = QLineEdit(name)
        self.input_name.setPlaceholderText(tr("e.g. photoshop"))
        if edit_mode:
            self.input_name.setReadOnly(True)
        self.input_name.textChanged.connect(self._update_preview)

        self.input_full_name = QLineEdit(full_name)
        self.input_full_name.setPlaceholderText(tr("e.g. Adobe Photoshop 2024"))
        self.input_full_name.textChanged.connect(self._update_preview)

        self.input_executable = QLineEdit(executable)
        self.input_executable.setPlaceholderText(
            tr(r"e.g. C:\Program Files\Adobe\Photoshop\Photoshop.exe")
        )

        self.input_categories = QLineEdit(categories)
        self.input_categories.setPlaceholderText(tr("e.g. Graphics, 2DGraphics"))

        self.input_mime_types = QLineEdit(mime_types)
        self.input_mime_types.setPlaceholderText(tr("e.g. image/png, image/jpeg"))

        # Per-field inline guidance (Tasks 8 + 9). ``None`` = no helper.
        fields = [
            (tr("Short Name"), self.input_name, None),
            (tr("Display Name"), self.input_full_name, None),
            (
                tr("Executable"),
                self.input_executable,
                tr(r"Full Windows path to the .exe, e.g. C:\Program Files\App\app.exe"),
            ),
            (
                tr("Categories"),
                self.input_categories,
                tr("Optional. Comma-separated freedesktop categories."),
            ),
            (
                tr("MIME Types"),
                self.input_mime_types,
                tr("Optional. Comma-separated MIME types this app can open."),
            ),
        ]
        row = 0
        for label, widget, helper in fields:
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 13px;")
            form.addWidget(lbl, row, 0, Qt.AlignmentFlag.AlignTop)
            form.addWidget(widget, row, 1)
            if helper:
                row += 1
                help_lbl = QLabel(helper)
                help_lbl.setStyleSheet(f"color: {C.OVERLAY0}; font-size: {FONT_CAPTION}px;")
                help_lbl.setWordWrap(True)
                form.addWidget(help_lbl, row, 1)
            row += 1

        body_l.addLayout(form)

        # Inline validation message (Task 8); hidden until a save attempt
        # surfaces a shape problem with the executable path.
        self._validation_lbl = QLabel("")
        self._validation_lbl.setStyleSheet(f"color: {C.PEACH}; font-size: {FONT_CAPTION}px;")
        self._validation_lbl.setWordWrap(True)
        self._validation_lbl.setVisible(False)
        body_l.addWidget(self._validation_lbl)
        body_l.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel = QPushButton(tr("Cancel"))
        cancel.setStyleSheet(BTN_GHOST)
        cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel)

        save = QPushButton(tr("Save") if edit_mode else tr("Create"))
        save.setStyleSheet(BTN_PRIMARY)
        save.clicked.connect(self._on_accept)
        btn_row.addWidget(save)

        body_l.addLayout(btn_row)
        layout.addWidget(body)

        # Snapshot of the initial field values so Cancel can detect edits
        # and confirm before discarding (Task 10).
        self._initial_values = self._current_values()

    def _current_values(self) -> tuple[str, ...]:
        """Current text of every editable field, for dirty-state detection."""
        return (
            self.input_name.text(),
            self.input_full_name.text(),
            self.input_executable.text(),
            self.input_categories.text(),
            self.input_mime_types.text(),
        )

    def _is_dirty(self) -> bool:
        return self._current_values() != self._initial_values

    def _on_cancel(self) -> None:
        """Confirm before discarding unsaved edits (Task 10)."""
        if self._is_dirty():
            reply = QMessageBox.question(
                self,
                tr("Discard changes?"),
                tr("You have unsaved changes. Discard them?"),
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.reject()

    def _update_preview(self) -> None:
        """Update avatar preview as user types."""
        text = self.input_full_name.text() or self.input_name.text() or "?"
        letter = text[0].upper()
        color = avatar_color(self.input_name.text() or self.input_full_name.text() or "new")
        self._avatar.setText(letter)
        self._avatar.setStyleSheet(
            f"background: {color}; color: {C.CRUST};"
            f" border-radius: 12px; font-size: 20px; font-weight: bold;"
        )

    def _on_accept(self) -> None:
        name = self.input_name.text().strip()
        full_name = self.input_full_name.text().strip()
        executable = self.input_executable.text().strip()

        if not name or not full_name or not executable:
            QMessageBox.warning(
                self,
                tr("Missing Fields"),
                tr("Name, Display Name, and Executable are required."),
            )
            return

        import re

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            QMessageBox.warning(
                self,
                tr("Invalid Name"),
                tr("Short name can only contain letters, numbers, dash, and underscore."),
            )
            return

        # Light shape check on the executable (Task 8). It's a Windows path
        # (e.g. C:\Program Files\App\app.exe), so we don't touch the host
        # filesystem -- just sanity-check it looks like a Windows path
        # ending in .exe. A mismatch is a non-blocking warning the user can
        # override, since unusual launchers (.bat, .com, UWP aliases) exist.
        shape_warning = self._executable_shape_warning(executable)
        if shape_warning:
            self._validation_lbl.setText(shape_warning)
            self._validation_lbl.setVisible(True)
            reply = QMessageBox.question(
                self,
                tr("Check the executable path"),
                tr("{warning}\n\nSave anyway?").format(warning=shape_warning),
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        else:
            self._validation_lbl.setVisible(False)

        self.accept()

    @staticmethod
    def _executable_shape_warning(executable: str) -> str:
        """Return a warning if ``executable`` doesn't look like a Windows .exe path.

        Empty string means the shape is fine. Used for the non-blocking save
        validation in :meth:`_on_accept` (Task 8).
        """
        import re

        value = executable.strip()
        if not value:
            return tr("The executable path is empty.")
        # Accept a drive-letter path (C:\...) or a UNC path (\\host\share\...).
        looks_windows = bool(re.match(r"^[a-zA-Z]:\\", value)) or value.startswith("\\\\")
        if not looks_windows:
            return tr(
                r"This doesn't look like a Windows path "
                r"(e.g. C:\Program Files\App\app.exe)."
            )
        if not value.lower().endswith(".exe"):
            return tr("This doesn't end in .exe — double-check the executable.")
        return ""

    def get_result(self) -> dict[str, str | list[str]]:
        """Return the form data as a dict."""
        cats = [c.strip() for c in self.input_categories.text().split(",") if c.strip()]
        mimes = [m.strip() for m in self.input_mime_types.text().split(",") if m.strip()]
        return {
            "name": self.input_name.text().strip(),
            "full_name": self.input_full_name.text().strip(),
            "executable": self.input_executable.text().strip(),
            "categories": cats,
            "mime_types": mimes,
        }


def _validate_app_name(name: str) -> bool:
    """Validate app name is safe for path construction."""
    import re

    return bool(name and re.match(r"^[a-zA-Z0-9_-]+$", name))


def save_app_profile(data: dict) -> Path:
    """Save an app profile to data/apps/{name}/app.toml."""
    name = data["name"]
    if not _validate_app_name(name):
        raise ValueError(f"Invalid app name: {name}")

    app_dir = data_dir() / "apps" / name
    apps_root = data_dir() / "apps"
    if not app_dir.resolve().is_relative_to(apps_root.resolve()):
        raise ValueError(f"Path traversal detected: {name}")

    app_dir.mkdir(parents=True, exist_ok=True)

    toml_path = app_dir / "app.toml"
    # Explicit UTF-8: TOML is UTF-8 by spec; LANG=C would otherwise break non-ASCII.
    toml_path.write_text(toml_dumps(data), encoding="utf-8")
    return toml_path


def delete_app_profile(name: str) -> bool:
    """Delete an app profile directory."""
    import shutil

    if not _validate_app_name(name):
        return False

    app_dir = data_dir() / "apps" / name
    apps_root = data_dir() / "apps"
    if not app_dir.resolve().is_relative_to(apps_root.resolve()):
        return False

    if app_dir.exists():
        shutil.rmtree(app_dir)
        return True
    return False
