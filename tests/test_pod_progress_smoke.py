from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import winpodx.core.dockur_progress as dockur_progress
from winpodx.core.config import DOCKUR_IMAGE_PIN
from winpodx.core.dockur_progress import DockurProgress, DockurProgressReader


class _FixtureResponse:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.body = body

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return default

    def read(self, amount: int | None = None) -> bytes:
        return self.body if amount is None else self.body[:amount]


class _FixtureConnection:
    def __init__(self, response: _FixtureResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, endpoint: str) -> None:
        self.requests.append((method, endpoint))

    def getresponse(self) -> _FixtureResponse:
        return self.response

    def close(self) -> None:
        return None


@pytest.mark.parametrize("sample_index", [0, 1], ids=["loading", "complete"])
def test_pinned_dockur_msg_fixture_smoke(
    monkeypatch: pytest.MonkeyPatch,
    sample_index: int,
) -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "dockur"
    record = json.loads((fixture_dir / "record.json").read_text())
    sample = record["samples"][sample_index]
    body = (fixture_dir / sample["file"]).read_bytes()
    connection = _FixtureConnection(_FixtureResponse(body))
    monkeypatch.setattr(
        dockur_progress.http.client, "HTTPConnection", lambda *args, **kwargs: connection
    )

    result = DockurProgressReader(vnc_port=8006).poll()

    assert DOCKUR_IMAGE_PIN == record["DOCKUR_IMAGE_PIN"]
    assert record["dockur_tag"] == "v6.05"
    assert record["base_image"] == "qemux/qemu:7.48"
    assert record["endpoint"] == "/msg.html"
    assert hashlib.sha256(body).hexdigest() == sample["sha256"]
    assert result == DockurProgress(
        text=sample["expected_text"],
        is_loading=sample["is_loading"],
    )
    assert connection.requests == [("GET", "/msg.html")]
