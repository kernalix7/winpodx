# SPDX-License-Identifier: MIT
"""Tests for ``_parse_wget_eta_secs`` -- dockur download ETA parser used
by ``winpodx pod wait-ready`` to extend its deadline on slow links
(#126: 86min ISO download exceeded the 60min static timeout).
"""

from __future__ import annotations

from winpodx.cli.pod import _WGET_DOTS_RE, _parse_wget_eta_secs


def test_dots_line_matches_v601_download() -> None:
    # dockur v6.01 prints dots-only ISO-download lines (no %/speed/ETA).
    assert _WGET_DOTS_RE.match(" ........ ........ ........ ........")
    assert _WGET_DOTS_RE.match("........")


def test_dots_re_rejects_wget_percent_and_text_lines() -> None:
    # Must not swallow v6.00 wget progress (carries %) or real dockur
    # milestones -- those keep their existing handling.
    done = "8257536K ........ .......                   100% 34.0M=4m27s"
    assert _WGET_DOTS_RE.match("6488064K ........ ........ 78% 4.55M 21m22s") is None
    assert _WGET_DOTS_RE.match(done) is None
    assert _WGET_DOTS_RE.match("❯ Extracting Windows 11 image...") is None
    assert _WGET_DOTS_RE.match("") is None


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
