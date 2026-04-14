"""Tests for desktop entry generation."""

from winpodx.core.app import AppInfo
from winpodx.desktop.entry import DESKTOP_TEMPLATE


def test_desktop_template():
    app = AppInfo(
        name="word",
        full_name="Microsoft Word",
        executable="C:\\Program Files\\Office\\WINWORD.EXE",
        categories=["Office", "WordProcessor"],
        mime_types=["application/msword"],
    )

    content = DESKTOP_TEMPLATE.format(
        full_name=app.full_name,
        name=app.name,
        icon_name=f"winpodx-{app.name}",
        categories=";".join(app.categories) + ";",
        mime_types=";".join(app.mime_types) + ";",
        wm_class="winword",
    )

    assert "Name=Microsoft Word" in content
    assert "Exec=winpodx app run word %F" in content
    assert "Icon=winpodx-word" in content
    assert "Categories=Office;WordProcessor;" in content
    assert "MimeType=application/msword;" in content
    assert "StartupWMClass=winword" in content
