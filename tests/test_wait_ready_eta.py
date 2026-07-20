# SPDX-License-Identifier: MIT
"""Tests for ``_parse_wget_eta_secs`` -- dockur download ETA parser used
by ``winpodx pod wait-ready`` to extend its deadline on slow links
(#126: 86min ISO download exceeded the 60min static timeout).
"""

from __future__ import annotations

from winpodx.cli.pod import _parse_wget_eta_secs


def test_eta_parser_minutes_seconds_form() -> None:
    """``78% 4.55M 21m22s`` -- typical mid-download progress line."""
    line = " 6488064K ........ ........ ........ ........ 78% 4.55M 21m22s"
    assert _parse_wget_eta_secs(line) == 21 * 60 + 22


def test_eta_parser_hours_minutes_form() -> None:
    """``3%  389K 7h21m`` -- very slow connection."""
    line = "  327680K ........ ........ ........ ........  4%  289K 7h21m"
    assert _parse_wget_eta_secs(line) == 7 * 3600 + 21 * 60


def test_eta_parser_seconds_only() -> None:
    """``99% 33.3M 10s`` -- near-complete download."""
    line = " 8224768K ........ ........ ........ ........ 99% 33.3M 10s"
    assert _parse_wget_eta_secs(line) == 10


def test_eta_parser_rejects_equals_elapsed_form() -> None:
    """``100% 4.16M=86m43s`` is wget's "total elapsed" form, NOT a
    remaining-time estimate. Must not be parsed as ETA -- otherwise
    we'd try to extend the deadline at 100% complete.
    """
    line = " 8257536K ........ ....... 100% 4.16M=86m43s"
    assert _parse_wget_eta_secs(line) is None


def test_eta_parser_rejects_equals_elapsed_form_mid_download() -> None:
    """``3%  484K=8m22s`` -- wget prints `=elapsed` form when it hasn't
    seen enough samples for an ETA yet. Skipping these is correct;
    we'd rather not extend on garbage data than extend wrongly.
    """
    line = "  294912K ....                                 3%  484K=8m22s"
    assert _parse_wget_eta_secs(line) is None


def test_eta_parser_rejects_non_progress_lines() -> None:
    """Random container log lines must not match."""
    assert _parse_wget_eta_secs("BdsDxe: starting Boot0004") is None
    assert _parse_wget_eta_secs("[container] Sysprep specialize") is None
    assert _parse_wget_eta_secs("") is None
    assert _parse_wget_eta_secs("  0K ........") is None


def test_eta_parser_rejects_zero_eta() -> None:
    """Defensive: any all-zero parse (shouldn't happen with the regex
    but cheap to guard against) returns None so we don't extend the
    deadline by 0+buffer when something weird matches."""
    # Synthetic line that matches structurally but parses to 0.
    assert _parse_wget_eta_secs("99% 1M 0s") is None


def test_eta_parser_handles_decimal_speed() -> None:
    """Speed field can be ``4.55M`` or ``713`` (no unit) -- both shapes
    must parse cleanly."""
    assert _parse_wget_eta_secs("50% 1.5M 30s") == 30
    assert _parse_wget_eta_secs("50% 713 30s") == 30


def test_oem_reboot_upgrade_path_exits_fast_when_agent_transitioning(monkeypatch) -> None:
    """C: on an upgrade the OEM marker never reappears; if the agent is still
    transitioning (exec keeps raising) phase 4 must bail at the appear-grace
    window, NOT block for the whole (possibly download-inflated) timeout."""
    from winpodx.cli import pod
    from winpodx.core.config import Config
    from winpodx.core.transport.base import TransportError

    # Controllable clock: only sleep advances time, so we can assert the
    # function returns well before the 600s timeout deadline.
    clock = {"t": 1000.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("time.sleep", lambda s: clock.__setitem__("t", clock["t"] + (s or 5)))

    class _Transitioning:
        def __init__(self, cfg):  # noqa: ANN001
            pass

        def exec(self, *a, **k):  # noqa: ANN002, ANN003
            raise TransportError("agent transitioning (reboot)")

    monkeypatch.setattr("winpodx.core.transport.agent.AgentTransport", _Transitioning)

    cfg = Config()
    cfg.pod.backend = "podman"
    assert pod._wait_for_oem_reboot(cfg, timeout=600) is True
    # Exited via the 30s appear-grace, not the 600s timeout.
    assert clock["t"] < 1000.0 + 600


def test_format_wget_progress_eta_form() -> None:
    from winpodx.cli.pod import _format_wget_progress

    r = _format_wget_progress("6488064K ........ ........ 78% 4.55M 21m22s")
    assert r is not None
    pct, text = r
    assert pct == 78
    assert "78%" in text and "ETA 21m22s" in text and "4.55 MB/s" in text


def test_format_wget_progress_done_form() -> None:
    from winpodx.cli.pod import _format_wget_progress

    r = _format_wget_progress("8257536K ........ 100% 34.0M=4m27s")
    assert r is not None
    pct, text = r
    assert pct == 100
    assert "done in 4m27s" in text


def test_format_wget_progress_bare_speed_and_zero() -> None:
    from winpodx.cli.pod import _format_wget_progress

    assert _format_wget_progress("50% 713 30s")[0] == 50
    assert _format_wget_progress("   0K 0% 5.57M 24m4s")[0] == 0


def test_format_wget_progress_rejects_non_progress() -> None:
    from winpodx.cli.pod import _format_wget_progress

    assert _format_wget_progress("BdsDxe: starting Boot0004") is None
    assert _format_wget_progress("Sysprep specialize") is None
    assert _format_wget_progress("") is None


# dockur v6.02 (#735) writes the ISO-download percentage as ONE line that
# grows byte-by-byte with no trailing newline until the download finishes
# (qemus/qemu src/progress.sh printPercentProgress: "10%" -> "10% -> 20%" ->
# ...), unlike v6.01's discrete wget lines above. Plain `for line in stream`
# buffers those bytes invisibly for the whole download, so the drain now
# reads raw chunks via `_LineSplitter` / `_iter_container_lines` instead.


def test_line_splitter_yields_complete_lines_across_chunk_boundaries() -> None:
    """Ordinary complete lines split across arbitrary chunk boundaries must
    still reassemble intact, in order, exactly once -- the incremental
    reader must not change behavior for lines that already had a newline."""
    from winpodx.cli.pod import _LineSplitter

    splitter = _LineSplitter()
    assert splitter.feed(b"hel") == []
    assert splitter.feed(b"lo wor") == []
    assert splitter.feed(b"ld\nsecond line\n") == ["hello world", "second line"]


def test_line_splitter_partial_tail_visible_but_not_emitted() -> None:
    """A no-newline-yet tail (dockur's still-growing download percentage)
    must not be emitted as a line -- it has to survive to be completed
    later -- but it IS visible via ``partial`` so the drain can scrape a
    live percentage out of it via ``_DOWNLOAD_PCT_RE``."""
    from winpodx.cli.pod import _DOWNLOAD_PCT_RE, _LineSplitter

    splitter = _LineSplitter()
    lines = splitter.feed("Downloading Windows ISO\n10% → 20%".encode())
    assert lines == ["Downloading Windows ISO"]
    assert splitter.partial == "10% → 20%"
    matches = _DOWNLOAD_PCT_RE.findall(splitter.partial)
    assert min(int(matches[-1]), 100) == 20


def test_line_splitter_completes_pending_line_exactly_once() -> None:
    """Once the newline finally arrives, the whole accumulated tail is
    emitted as ONE line -- not re-emitted on any later feed()."""
    from winpodx.cli.pod import _LineSplitter

    splitter = _LineSplitter()
    splitter.feed("10% → 20%".encode())
    completed = splitter.feed(" → 30%\nExtracting Windows image\n".encode())
    assert completed == ["10% → 20% → 30%", "Extracting Windows image"]
    assert splitter.feed(b"more\n") == ["more"]


def test_line_splitter_flush_returns_trailing_remainder_once() -> None:
    """At EOF (container process exited mid-line), the leftover tail must
    still surface as one final line -- mirroring how iterating a file
    object yields a last newline-less line before returning "" -- and only
    once."""
    from winpodx.cli.pod import _LineSplitter

    splitter = _LineSplitter()
    splitter.feed(b"unterminated tail")
    assert splitter.flush() == "unterminated tail"
    assert splitter.flush() is None


def test_download_pct_regex_last_match_wins_and_clamps_at_100() -> None:
    """``_DOWNLOAD_PCT_RE`` must pick the LAST "NN%" in dockur's chained
    growing line, and the caller clamps it to 100 -- dockur can't actually
    emit over 100%, but the parser must never store an out-of-range value."""
    from winpodx.cli.pod import _DOWNLOAD_PCT_RE

    matches = _DOWNLOAD_PCT_RE.findall("10% → 20% → 150%")
    assert matches[-1] == "150"
    assert min(int(matches[-1]), 100) == 100


def test_iter_container_lines_tracks_pct_and_yields_lines_end_to_end() -> None:
    """Full lifecycle through ``_iter_container_lines``: a milestone line,
    then a growing no-newline percentage tail (scraped into
    ``dl_state["pct"]``, clamped, last-match-wins), then the newline that
    finally completes it alongside the next milestone, then EOF. The
    percentage must never be lost, never double-counted, and the
    previously-partial text must reappear verbatim once completed."""
    import threading

    from winpodx.cli.pod import _iter_container_lines

    class _FakeStream:
        """Hands out one scripted chunk per ``read()`` call, then EOF."""

        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = list(chunks)

        def read(self, _size: int) -> bytes:
            return self._chunks.pop(0) if self._chunks else b""

    stream = _FakeStream(
        [
            b"Downloading Windows ISO\n",
            "10% → 20% → 150%".encode(),  # still growing: no "\n" yet
            b"\nExtracting Windows image\n",
            b"",
        ]
    )
    dl_state: dict[str, float | int | None] = {"start": 0.0, "pct": None}
    lines = list(_iter_container_lines(stream, dl_state, threading.Event()))
    assert lines == [
        "Downloading Windows ISO",
        "10% → 20% → 150%",
        "Extracting Windows image",
    ]
    assert dl_state["pct"] == 100


def test_size_chain_scraped_when_no_percent() -> None:
    """dockur v6.02 size-mode chain (server sent no total): the partial tail
    has GiB/MiB tokens but no percents -- latest size lands on dl_state."""
    import threading

    from winpodx.cli.pod import _iter_container_lines

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

    dl_state = {"start": 1.0, "pct": None, "size": None}
    stream = _FakeStream(
        [
            "❯ Downloading Windows 11...\n512MiB → 1GiB".encode(),
            " → 1.5GiB → 2GiB".encode(),
        ]
    )
    lines = list(_iter_container_lines(stream, dl_state, threading.Event()))
    assert dl_state["pct"] is None
    assert dl_state["size"] == "2GiB"
    # The completed milestone line still came through; the tail flushed at EOF.
    assert lines[0].startswith("❯ Downloading Windows 11")


def test_percent_wins_over_size_tokens() -> None:
    import threading

    from winpodx.cli.pod import _iter_container_lines

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

    dl_state = {"start": 1.0, "pct": None, "size": None}
    stream = _FakeStream([b"header\n10% \xe2\x86\x92 20%"])
    list(_iter_container_lines(stream, dl_state, threading.Event()))
    assert dl_state["pct"] == 20
    assert dl_state["size"] is None


def test_complete_line_tokens_scraped_during_download() -> None:
    """podman logs -f only ever delivers COMPLETE lines (partial writes are
    withheld until their newline arrives), so the scraper must also read
    tokens off completed lines -- this is the only path that can work on the
    podman backend once upstream newline-flushes its progress milestones."""
    import threading

    from winpodx.cli.pod import _iter_container_lines

    class _FakeStream:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

    dl_state = {"start": 1.0, "pct": None, "size": None}
    stream = _FakeStream([b"45%\n", b"3.5GiB \xe2\x86\x92 4GiB\n"])
    lines = list(_iter_container_lines(stream, dl_state, threading.Event()))
    assert lines == ["45%", "3.5GiB → 4GiB"]
    # Percent seen on a complete line sticks; the later size-only line does
    # not clear it (percent wins).
    assert dl_state["pct"] == 45
