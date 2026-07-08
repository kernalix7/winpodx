# SPDX-License-Identifier: MIT
"""Canonical URL-scheme policy (#421 / #694)."""

from __future__ import annotations

import pytest

from winpodx.core.url_schemes import (
    DANGEROUS_SCHEMES,
    is_safe_scheme,
    sanitize_url_arg,
    url_scheme_of,
)


@pytest.mark.parametrize(
    "s", ["mailto", "https", "http", "slack", "vnc", "tel", "zoommtg", "webcal"]
)
def test_is_safe_scheme_accepts_common(s: str) -> None:
    assert is_safe_scheme(s)
    assert is_safe_scheme(s.upper())  # case-insensitive
    assert is_safe_scheme(s + ":")  # trailing colon tolerated


@pytest.mark.parametrize("s", sorted(DANGEROUS_SCHEMES))
def test_is_safe_scheme_rejects_denylist(s: str) -> None:
    assert not is_safe_scheme(s)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1abc",
        "a b",
        "has:colon",
        "way-too-long-scheme-name-that-exceeds-the-thirty-two-char-cap",
    ],
)
def test_is_safe_scheme_rejects_malformed(bad: str) -> None:
    assert not is_safe_scheme(bad)


def test_url_scheme_of_routes_urls_not_paths() -> None:
    assert url_scheme_of("mailto:a@b.com") == "mailto"
    assert url_scheme_of("https://example.com/x") == "https"
    assert url_scheme_of("SLACK://team") == "slack"
    # file paths + file: URIs are NOT routed (file: is denylisted -> UNC path)
    assert url_scheme_of("/home/me/doc.txt") is None
    assert url_scheme_of("file:///home/me/doc.txt") is None
    assert url_scheme_of("relative/path") is None
    # dangerous schemes are not routed
    assert url_scheme_of("javascript:alert(1)") is None
    assert url_scheme_of("data:text/html,x") is None


def test_sanitize_neutralises_app_cmd_injection() -> None:
    # comma splits FreeRDP3 /app: sub-keys; double-quote wraps the cmd value;
    # CR/LF/control chars could smuggle extra argv.
    out = sanitize_url_arg('slack://team,chan"x\r\n\tbell\x07')
    assert "," not in out
    assert '"' not in out
    assert "\r" not in out and "\n" not in out and "\t" not in out
    assert "\x07" not in out
    # the rest of the URL survives
    assert out.startswith("slack://team")
