# SPDX-License-Identifier: MIT
"""Safely read display-only installation progress from dockur's VNC endpoint."""

from __future__ import annotations

import http.client
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser

_MAX_BODY_BYTES = 64 * 1024
_MAX_TEXT_CHARS = 512
_REQUEST_TIMEOUT_SECONDS = 1.0
_STALE_SECONDS = 60.0
_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclass(frozen=True)  # Python 3.9 uses the manual slots below.
class DockurProgress:
    """Normalized text exposed by dockur's progress document."""

    # Python 3.9 lacks dataclass(slots=True), so declare slots explicitly.
    __slots__ = ("text", "is_loading")

    text: str
    is_loading: bool


class _Target:
    __slots__ = ("closed", "depth", "is_loading", "parts", "tag")

    def __init__(self, tag: str, depth: int, is_loading: bool) -> None:
        self.tag = tag
        self.depth = depth
        self.is_loading = is_loading
        self.parts: list[str] = []
        self.closed = False


class _ProgressParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.info_targets: list[_Target] = []
        self.loading_targets: list[_Target] = []
        self.plain_parts: list[str] = []
        self.saw_tag = False
        self.stack: list[str] = []
        self.malformed = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.saw_tag = True
        attribute_values: dict[str, list[str | None]] = {}
        for name, value in attrs:
            attribute_values.setdefault(name, []).append(value)
        if any(len(values) != 1 for values in attribute_values.values()):
            self.malformed = True

        element_id = attribute_values.get("id", [None])[0]
        class_value = attribute_values.get("class", [None])[0]
        classes = class_value.split() if class_value is not None else []
        is_loading = "loading" in classes
        target = _Target(tag, len(self.stack), is_loading)
        if element_id == "info":
            self.info_targets.append(target)
        elif tag == "p" and is_loading:
            self.loading_targets.append(target)

        if tag not in _VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1] != tag:
            self.malformed = True
            return
        depth = len(self.stack) - 1
        self.stack.pop()
        for target in (*self.info_targets, *self.loading_targets):
            if target.tag == tag and target.depth == depth and not target.closed:
                target.closed = True

    def handle_data(self, data: str) -> None:
        if "script" in self.stack or "style" in self.stack:
            return
        depth = len(self.stack)
        if depth == 0:
            self.plain_parts.append(data)
        for target in (*self.info_targets, *self.loading_targets):
            if not target.closed and depth > target.depth:
                target.parts.append(data)

    def valid(self) -> bool:
        targets = (*self.info_targets, *self.loading_targets)
        return not self.malformed and not self.stack and all(target.closed for target in targets)


def parse_dockur_progress(body: bytes) -> DockurProgress | None:
    """Parse a bounded UTF-8 dockur response into normalized progress."""
    if not body or len(body) > _MAX_BODY_BYTES:
        return None
    try:
        document = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

    parser = _ProgressParser()
    try:
        parser.feed(document)
        parser.close()
    except ValueError:
        return None
    if not parser.valid() or len(parser.info_targets) > 1:
        return None
    if parser.info_targets:
        target = parser.info_targets[0]
        parts = target.parts
        is_loading = target.is_loading
    elif len(parser.loading_targets) == 1:
        target = parser.loading_targets[0]
        parts = target.parts
        is_loading = target.is_loading
    elif not parser.saw_tag and "<" not in document and ">" not in document:
        parts = parser.plain_parts
        is_loading = False
    else:
        return None

    text = " ".join("".join(parts).split())
    if (
        not text
        or len(text) > _MAX_TEXT_CHARS
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in text)
    ):
        return None
    return DockurProgress(text=text, is_loading=is_loading)


class DockurProgressReader:
    """Poll dockur progress and suppress values unchanged for 60 seconds."""

    def __init__(self, vnc_port: int) -> None:
        self._vnc_port = vnc_port
        self._last_progress: DockurProgress | None = None
        self._first_seen_at: float | None = None

    def poll(self) -> DockurProgress | None:
        """Fetch one progress document, returning None when unavailable or stale."""
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                self._vnc_port,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        except (OSError, ValueError, http.client.HTTPException):
            return None

        expired = threading.Event()

        def expire_request() -> None:
            expired.set()
            try:
                connection.close()
            except (OSError, http.client.HTTPException):
                return

        deadline = threading.Timer(_REQUEST_TIMEOUT_SECONDS, expire_request)
        deadline.daemon = True
        deadline.start()
        close_failed = False
        try:
            connection.request("GET", "/msg.html")
            response = connection.getresponse()
            if response.status != 200:
                return None
            content_encoding = response.getheader("Content-Encoding")
            if content_encoding is not None and content_encoding.strip().lower() not in (
                "",
                "identity",
            ):
                return None
            body = response.read(_MAX_BODY_BYTES + 1)
        except (OSError, ValueError, http.client.HTTPException):
            return None
        finally:
            deadline.cancel()
            deadline.join()
            try:
                connection.close()
            except (OSError, http.client.HTTPException):
                close_failed = True

        if expired.is_set() or close_failed:
            return None

        progress = parse_dockur_progress(body)
        if progress is None:
            return None
        now = time.monotonic()
        if progress != self._last_progress:
            self._last_progress = progress
            self._first_seen_at = now
            return progress
        if self._first_seen_at is None or now - self._first_seen_at >= _STALE_SECONDS:
            return None
        return progress
