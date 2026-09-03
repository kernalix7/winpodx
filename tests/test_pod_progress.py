from __future__ import annotations

import dataclasses
from typing import Final

import pytest

from winpodx.core.dockur_progress import (
    DockurProgress,
    parse_dockur_progress,
)

MAX_BODY_BYTES: Final = 64 * 1024
VNC_PORT: Final = 8006


def test_dockur_progress_is_frozen() -> None:
    # Given
    progress = DockurProgress(text="Installing Windows", is_loading=True)

    # When / Then
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(progress, "text", "Changed")


def test_parse_v605_loading_html_normalizes_machine_status() -> None:
    # Given
    body = b"""<!doctype html><html><head><title>Windows</title></head><body>
        <main><p class="loading">Downloading <strong>Windows&nbsp;11</strong>
        &amp; preparing <span>drivers</span></p></main></body></html>"""

    # When
    result = parse_dockur_progress(body)

    # Then
    assert result == DockurProgress(
        text="Downloading Windows 11 & preparing drivers",
        is_loading=True,
    )


def test_parse_v605_bare_completion_text() -> None:
    # Given: qemux/qemu 7.48 writes escaped bare text when the message is not loading.
    body = b"The virtual machine was booted successfully.\n"

    # When
    result = parse_dockur_progress(body)

    # Then
    assert result == DockurProgress(
        text="The virtual machine was booted successfully.",
        is_loading=False,
    )


def test_parse_prefers_unique_info_target_over_loading() -> None:
    # Given
    body = b"""<html><body>
        <p class="loading">Old loading status</p>
        <section id="info">Windows <b>is&nbsp;ready</b> &amp; reachable</section>
        </body></html>"""

    # When
    result = parse_dockur_progress(body)

    # Then
    assert result == DockurProgress(text="Windows is ready & reachable", is_loading=False)


@pytest.mark.parametrize(
    "control",
    ["\x00", "\x1b", "\x7f", "\x80", "\x9f"],
    ids=["nul", "esc", "del", "c1-80", "c1-9f"],
)
def test_parse_rejects_control_characters_in_text(control: str) -> None:
    body = f'<p class="loading">safe{control}text</p>'.encode("utf-8")
    assert parse_dockur_progress(body) is None


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html><body></body></html>",
        b'<p class="loading">invalid \xff</p>',
        b'<p class="loading">x</p>' + (b" " * MAX_BODY_BYTES),
        b'<p class="loading">a</p><p class="loading">b</p>',
        b'<div id="info">a</div><div id="info">b</div>',
        b'<p class="loading">never closed',
        b'<div id="info">never closed',
        b'<p class="loading">' + (b"x" * 513) + b"</p>",
    ],
    ids=[
        "empty",
        "missing-target",
        "invalid-utf8",
        "oversized-body",
        "duplicate-loading",
        "duplicate-info",
        "unclosed-loading",
        "unclosed-info",
        "oversized-text",
    ],
)
def test_parse_rejects_untrusted_or_ambiguous_html(body: bytes) -> None:
    # Given / When
    result = parse_dockur_progress(body)

    # Then
    assert result is None
