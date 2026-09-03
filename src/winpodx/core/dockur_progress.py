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
    __slots__ = ("closed", "depth", "is_loading", "parts", "tag", "text_chars")

    def __init__(self, tag: str, depth: int, is_loading: bool) -> None:
        self.tag = tag
        self.depth = depth
        self.is_loading = is_loading
        self.parts: list[str] = []
        self.text_chars = 0
        self.closed = False

    def append(self, data: str) -> bool:
        remaining = _MAX_TEXT_CHARS + 1 - self.text_chars
        if remaining > 0:
            part = data[:remaining]
            self.parts.append(part)
            self.text_chars += len(part)
        return len(data) <= remaining


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
        if element_id == "info":
            if self.info_targets:
                self.malformed = True
            else:
                self.info_targets.append(_Target(tag, len(self.stack), is_loading))
        elif tag == "p" and is_loading:
            if self.loading_targets:
                self.malformed = True
            else:
                self.loading_targets.append(_Target(tag, len(self.stack), is_loading))

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
                if not target.append(data):
                    self.malformed = True

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


class _PollAttempt:
    __slots__ = ("done", "error", "expired", "result")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.expired = False
        self.result: DockurProgress | None = None


class DockurProgressReader:
    """Poll dockur progress and suppress values unchanged for 60 seconds."""

    def __init__(self, vnc_port: int) -> None:
        self._vnc_port = vnc_port
        self._last_progress: DockurProgress | None = None
        self._first_seen_at: float | None = None
        self._attempt: _PollAttempt | None = None
        self._attempt_lock = threading.Lock()

    def _fetch(self) -> DockurProgress | None:
        connection: http.client.HTTPConnection | None = None
        close_failed = False
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                self._vnc_port,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
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
            if connection is not None:
                try:
                    connection.close()
                except (OSError, http.client.HTTPException):
                    close_failed = True

        if close_failed:
            return None
        return parse_dockur_progress(body)

    def _run_attempt(self, attempt: _PollAttempt) -> None:
        try:
            result = self._fetch()
        except BaseException as error:  # noqa: BLE001 -- transfer across thread boundary
            with self._attempt_lock:
                if not attempt.expired:
                    attempt.error = error
        else:
            with self._attempt_lock:
                if not attempt.expired:
                    attempt.result = result
        finally:
            attempt.done.set()

    def poll(self) -> DockurProgress | None:
        """Fetch one progress document, returning None when unavailable or stale."""
        start_attempt = False
        with self._attempt_lock:
            attempt = self._attempt
            if attempt is not None and attempt.expired:
                if not attempt.done.is_set():
                    return None
                self._attempt = None
                attempt = None
            if attempt is None:
                attempt = _PollAttempt()
                self._attempt = attempt
                start_attempt = True

        if start_attempt:
            threading.Thread(target=self._run_attempt, args=(attempt,), daemon=True).start()

        if not attempt.done.wait(_REQUEST_TIMEOUT_SECONDS):
            with self._attempt_lock:
                if not attempt.done.is_set():
                    attempt.expired = True
                    return None

        with self._attempt_lock:
            if self._attempt is attempt:
                self._attempt = None
            if attempt.expired:
                return None
            error = attempt.error
            progress = attempt.result

        if error is not None:
            raise error

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
