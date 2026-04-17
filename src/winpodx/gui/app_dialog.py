"""Add/Edit app profile dialog.

Allows users to create and edit app profiles (TOML definitions)
through a GUI instead of manually editing files.
"""

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

from winpodx.gui.theme import (
    BTN_GHOST,
    BTN_PRIMARY,
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
        self.setWindowTitle("Edit App" if edit_mode else "Add App")
        self.setFixedSize(580, 460)
        self.setStyleSheet(f"""
            QDialog {{ background: {C.MANTLE}; }}
            QLabel {{ color: {C.TEXT}; font-size: 13px; }}
            {INPUT}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── Header with color stripe and preview avatar ──
        header = QFrame()
        color = avatar_color(name or full_name or "new")
        header.setStyleSheet(f"background: {C.CRUST}; border-bottom: 3px solid {color};")
        header_l = QHBoxLayout(header)
        header_l.setContentsMargins(28, 20, 28, 20)
        header_l.setSpacing(16)

        # Preview avatar
        letter = (full_name or name or "?")[0].upper()
        self._avatar = QLabel(letter)
        self._avatar.setFixedSize(48, 48)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            f"background: {color}; color: {C.CRUST};"
            f" border-radius: 12px; font-size: 20px; font-weight: bold;"
        )
        header_l.addWidget(self._avatar)

        # Title + subtitle
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        title = QLabel("Edit App Profile" if edit_mode else "New App Profile")
        title.setStyleSheet(f"color: {C.TEXT}; font-size: 18px; font-weight: bold;")
        title_col.addWidget(title)
        sub = QLabel("Define a Windows application for winpodx")
        sub.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 12px;")
        title_col.addWidget(sub)
        header_l.addLayout(title_col)
        header_l.addStretch()

        layout.addWidget(header)

        # ── Form body ──
        body = QWidget()
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(28, 24, 28, 24)
        body_l.setSpacing(8)

        form = QGridLayout()
        form.setVerticalSpacing(10)
        form.setHorizontalSpacing(12)

        self.input_name = QLineEdit(name)
        self.input_name.setPlaceholderText("e.g. photoshop")
        if edit_mode:
            self.input_name.setReadOnly(True)
        self.input_name.textChanged.connect(self._update_preview)

        self.input_full_name = QLineEdit(full_name)
        self.input_full_name.setPlaceholderText("e.g. Adobe Photoshop 2024")
        self.input_full_name.textChanged.connect(self._update_preview)

        self.input_executable = QLineEdit(executable)
        self.input_executable.setPlaceholderText(
            r"e.g. C:\Program Files\Adobe\Photoshop\Photoshop.exe"
        )

        self.input_categories = QLineEdit(categories)
        self.input_categories.setPlaceholderText("e.g. Graphics, 2DGraphics")

        self.input_mime_types = QLineEdit(mime_types)
        self.input_mime_types.setPlaceholderText("e.g. image/png, image/jpeg")

        fields = [
            ("Short Name", self.input_name),
            ("Display Name", self.input_full_name),
            ("Executable", self.input_executable),
            ("Categories", self.input_categories),
            ("MIME Types", self.input_mime_types),
        ]
        for row, (label, widget) in enumerate(fields):
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {C.SUBTEXT0}; font-size: 13px;")
            form.addWidget(lbl, row, 0)
            form.addWidget(widget, row, 1)

        body_l.addLayout(form)

        # Help
        help_lbl = QLabel(
            "Executable: full Windows path to the .exe file.  "
            "Categories/MIME: comma-separated or leave empty."
        )
        help_lbl.setStyleSheet(f"color: {C.OVERLAY0}; font-size: 11px;")
        help_lbl.setWordWrap(True)
        body_l.addWidget(help_lbl)
        body_l.addStretch()

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel = QPushButton("Cancel")
        cancel.setStyleSheet(BTN_GHOST)
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(cancel)

        save = QPushButton("Save" if edit_mode else "Create")
        save.setStyleSheet(BTN_PRIMARY)
        save.clicked.connect(self._on_accept)
        btn_row.addWidget(save)

        body_l.addLayout(btn_row)
        layout.addWidget(body)

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
                "Missing Fields",
                "Name, Display Name, and Executable are required.",
            )
            return

        import re

        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            QMessageBox.warning(
                self,
                "Invalid Name",
                "Short name can only contain letters, numbers, dash, and underscore.",
            )
            return

        self.accept()

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
    # Verify resolved path stays under apps dir
    apps_root = data_dir() / "apps"
    if not app_dir.resolve().is_relative_to(apps_root.resolve()):
        raise ValueError(f"Path traversal detected: {name}")

    app_dir.mkdir(parents=True, exist_ok=True)

    toml_path = app_dir / "app.toml"
    # Explicit UTF-8: TOML is UTF-8 by spec and users commonly enter non-ASCII
    # ``full_name`` values (e.g. Korean "한글 메모장"). Under LANG=C the default
    # locale encoding is ASCII and write_text() would raise UnicodeEncodeError.
    toml_path.write_text(toml_dumps(data), encoding="utf-8")
    return toml_path


def delete_app_profile(name: str) -> bool:
    """Delete an app profile directory."""
    import shutil

    if not _validate_app_name(name):
        return False

    app_dir = data_dir() / "apps" / name
    # Verify resolved path stays under apps dir
    apps_root = data_dir() / "apps"
    if not app_dir.resolve().is_relative_to(apps_root.resolve()):
        return False

    if app_dir.exists():
        shutil.rmtree(app_dir)
        return True
    return False
