# SPDX-License-Identifier: MIT
"""Regression tests: the reverse-open listener must survive hostile input bytes.

The guest is untrusted (``src/winpodx/reverse_open/listener.py`` module
docstring). Two classes of malformed request file currently abort
``process_pending`` mid-scan instead of being rejected and dropped:

* **Invalid UTF-8** — ``_handle_request`` calls
  ``path.read_text(encoding="utf-8")`` and only catches ``OSError``; a file
  whose bytes aren't valid UTF-8 raises ``UnicodeDecodeError`` straight out of
  the scan (RED: ``UnicodeDecodeError``).
* **JSON-escaped lone surrogate** — ``json.loads`` happily produces a ``str``
  holding an unpaired surrogate (``"\\ud800"``); ``_validate_schema`` then does
  ``len(path.encode("utf-8"))`` which raises ``UnicodeEncodeError``
  (RED: ``UnicodeEncodeError``).

Both cases are followed, in the same directory, by a *valid* ``origin="launch"``
request whose filename sorts later. A single ``process_pending`` scan must
reject+delete the invalid file and still accept+spawn the valid one. On the
current code the invalid file raises before the valid file is ever reached, so
these tests are RED by construction. The spawn is stubbed; no real process is
forked, no guest, private file, or network is touched.

The optional third case pins the stat/read size race: a request whose reported
``st_size`` is under the cap but whose actual bytes are over it must still be
refused — the cap is enforced on bytes actually read, not on a stat that the
writer can race.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from winpodx.reverse_open.apps_db import AppsDatabase
from winpodx.reverse_open.listener import Listener, ListenerConfig
from winpodx.reverse_open.seen_uuids import SeenUUIDs

# Deterministic names: the invalid file sorts strictly before the valid one, so
# ``process_pending`` (``os.scandir`` sorted by name) visits it first.
_INVALID_NAME = "00000000-0000-0000-0000-000000000001.json"
_VALID_NAME = "ffffffff-ffff-ffff-ffff-ffffffffffff.json"

_LAUNCH: dict[str, object] = {
    "version": 2,
    "app": "true",
    "origin": "launch",
    "path": "",
    "ts": "2026-05-11T00:00:00Z",
    "pod_id": None,
}


class _SpawnRecorder:
    """Callable ``spawn`` stub that records the argv of each accepted spawn."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], popen_kwargs: dict[str, object]) -> object:
        self.calls.append(argv)
        return object()


def _make_apps_db(tmp_path: Path) -> AppsDatabase:
    manifest = tmp_path / "apps.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-05-11T00:00:00Z",
                "host": {},
                "apps": [
                    {
                        "slug": "true",
                        "name": "true",
                        "comment": "",
                        "exec_argv": ["/bin/true", "%f"],
                        "icon_name": "",
                        "mime_types": ["text/plain"],
                        "desktop_file": "/x.desktop",
                        "is_default_for": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return AppsDatabase.load(manifest)


def _build(
    tmp_path: Path, *, max_request_bytes: int = 64 * 1024
) -> tuple[Listener, Path, _SpawnRecorder]:
    incoming = tmp_path / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)
    cfg = ListenerConfig(
        incoming_dir=incoming,
        share_roots={},
        max_request_bytes=max_request_bytes,
    )
    recorder = _SpawnRecorder()
    listener = Listener(
        cfg,
        _make_apps_db(tmp_path),
        SeenUUIDs(tmp_path / "seen"),
        spawn=recorder,
    )
    return listener, incoming, recorder


def _assert_rejected_then_valid(
    listener: Listener,
    incoming: Path,
    recorder: _SpawnRecorder,
) -> None:
    """The invalid file is gone, the valid launch was accepted and spawned."""
    assert not (incoming / _INVALID_NAME).exists()
    assert not (incoming / _VALID_NAME).exists()
    # Exactly one spawn, and it is the launch app with its %f placeholder
    # stripped (no stray path argument).
    assert recorder.calls == [["/bin/true"]]
    assert listener.stats_snapshot().accepted == 1


def test_invalid_utf8_request_does_not_abort_the_scan(tmp_path: Path) -> None:
    listener, incoming, recorder = _build(tmp_path)
    (incoming / _INVALID_NAME).write_bytes(b"\xff\xfe\xfd\x80\x81")
    (incoming / _VALID_NAME).write_text(json.dumps(_LAUNCH), encoding="utf-8")

    listener.process_pending()

    _assert_rejected_then_valid(listener, incoming, recorder)


def test_escaped_lone_surrogate_path_does_not_abort_the_scan(tmp_path: Path) -> None:
    listener, incoming, recorder = _build(tmp_path)
    # ``\ud800`` is written as an ASCII JSON escape, so the file decodes fine;
    # the surrogate only materialises after ``json.loads``.
    malformed = (
        '{"version": 2, "app": "true", "origin": "host", '
        '"path": "\\ud800", "ts": "2026-05-11T00:00:00Z", "pod_id": null}'
    )
    (incoming / _INVALID_NAME).write_text(malformed, encoding="utf-8")
    (incoming / _VALID_NAME).write_text(json.dumps(_LAUNCH), encoding="utf-8")

    listener.process_pending()

    _assert_rejected_then_valid(listener, incoming, recorder)


def test_oversize_bytes_with_lying_stat_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limit = 128
    listener, incoming, recorder = _build(tmp_path, max_request_bytes=limit)
    race = incoming / _VALID_NAME
    payload = dict(_LAUNCH)
    payload["pad"] = "x" * 4096
    body = json.dumps(payload)
    race.write_text(body, encoding="utf-8")
    assert len(body.encode("utf-8")) > limit

    real_stat = Path.stat

    def lying_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        st = real_stat(self, follow_symlinks=follow_symlinks)
        if self == race:
            # st_size (index 6) reports one byte while the file is far larger.
            return os.stat_result(tuple(st)[:6] + (1,) + tuple(st)[7:])
        return st

    monkeypatch.setattr(Path, "stat", lying_stat)

    listener.process_pending()

    assert not race.exists()
    assert recorder.calls == []
    assert listener.stats_snapshot().rejected_oversize == 1
