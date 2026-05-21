# SPDX-License-Identifier: MIT
"""Tests for utils.locale: host TZ detection + IANA->Windows translation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from winpodx.utils.locale import (
    _tz_from_etc_timezone,
    _tz_from_localtime_symlink,
    _tz_from_timedatectl,
    detect_timezone,
    iana_to_windows,
    resolve_timezone_for_oem,
)


class TestDetectTimezone:
    def test_falls_back_to_utc_when_all_helpers_return_none(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale._tz_from_timedatectl", lambda: None)
        monkeypatch.setattr("winpodx.utils.locale._tz_from_localtime_symlink", lambda: None)
        monkeypatch.setattr("winpodx.utils.locale._tz_from_etc_timezone", lambda: None)
        assert detect_timezone() == "UTC"

    def test_timedatectl_wins_over_other_helpers(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale._tz_from_timedatectl", lambda: "Asia/Seoul")
        monkeypatch.setattr(
            "winpodx.utils.locale._tz_from_localtime_symlink", lambda: "America/New_York"
        )
        monkeypatch.setattr("winpodx.utils.locale._tz_from_etc_timezone", lambda: "Europe/Paris")
        assert detect_timezone() == "Asia/Seoul"

    def test_localtime_symlink_used_when_timedatectl_unavailable(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale._tz_from_timedatectl", lambda: None)
        monkeypatch.setattr(
            "winpodx.utils.locale._tz_from_localtime_symlink", lambda: "Europe/Berlin"
        )
        monkeypatch.setattr("winpodx.utils.locale._tz_from_etc_timezone", lambda: "Europe/Paris")
        assert detect_timezone() == "Europe/Berlin"

    def test_etc_timezone_used_when_both_others_unavailable(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale._tz_from_timedatectl", lambda: None)
        monkeypatch.setattr("winpodx.utils.locale._tz_from_localtime_symlink", lambda: None)
        monkeypatch.setattr(
            "winpodx.utils.locale._tz_from_etc_timezone", lambda: "Australia/Sydney"
        )
        assert detect_timezone() == "Australia/Sydney"

    def test_helper_exception_does_not_break_chain(self, monkeypatch):
        """Defensive: a helper raising must not abort the detection chain."""

        def boom():
            raise RuntimeError("nope")

        monkeypatch.setattr("winpodx.utils.locale._tz_from_timedatectl", boom)
        monkeypatch.setattr("winpodx.utils.locale._tz_from_localtime_symlink", lambda: "Asia/Tokyo")
        monkeypatch.setattr("winpodx.utils.locale._tz_from_etc_timezone", lambda: None)
        assert detect_timezone() == "Asia/Tokyo"


class TestTimedatectlHelper:
    def test_returns_value_on_success(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Asia/Seoul\n"
            assert _tz_from_timedatectl() == "Asia/Seoul"

    def test_returns_none_when_command_missing(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert _tz_from_timedatectl() is None

    def test_returns_none_when_command_fails(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            assert _tz_from_timedatectl() is None

    def test_returns_none_on_empty_output(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "\n"
            assert _tz_from_timedatectl() is None


class TestLocaltimeSymlinkHelper:
    def test_parses_zoneinfo_suffix(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.utils.locale.os.readlink",
            lambda p: "/usr/share/zoneinfo/Asia/Seoul",
        )
        assert _tz_from_localtime_symlink() == "Asia/Seoul"

    def test_parses_relative_zoneinfo_suffix(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.utils.locale.os.readlink",
            lambda p: "../usr/share/zoneinfo/Europe/London",
        )
        assert _tz_from_localtime_symlink() == "Europe/London"

    def test_returns_none_when_target_lacks_zoneinfo(self, monkeypatch):
        monkeypatch.setattr(
            "winpodx.utils.locale.os.readlink",
            lambda p: "/some/other/path",
        )
        assert _tz_from_localtime_symlink() is None

    def test_returns_none_when_localtime_not_a_symlink(self, monkeypatch):
        def boom(p):
            raise OSError("not a symlink")

        monkeypatch.setattr("winpodx.utils.locale.os.readlink", boom)
        assert _tz_from_localtime_symlink() is None


class TestEtcTimezoneHelper:
    def test_returns_first_nonblank_line(self, tmp_path, monkeypatch):
        f = tmp_path / "timezone"
        f.write_text("# comment\n\nAmerica/Chicago\nignored\n")
        monkeypatch.setattr("winpodx.utils.locale.Path", lambda p: f if p == "/etc/timezone" else p)
        assert _tz_from_etc_timezone() == "America/Chicago"

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        missing = tmp_path / "absent"
        monkeypatch.setattr(
            "winpodx.utils.locale.Path", lambda p: missing if p == "/etc/timezone" else p
        )
        assert _tz_from_etc_timezone() is None


class TestIanaToWindows:
    @pytest.mark.parametrize(
        "iana,expected",
        [
            ("Asia/Seoul", "Korea Standard Time"),
            ("America/New_York", "Eastern Standard Time"),
            ("Europe/Berlin", "W. Europe Standard Time"),
            ("Australia/Sydney", "AUS Eastern Standard Time"),
            ("Etc/UTC", "UTC"),
            ("UTC", "UTC"),
        ],
    )
    def test_known_mappings(self, iana, expected):
        assert iana_to_windows(iana) == expected

    def test_unknown_iana_falls_back_to_utc(self):
        assert iana_to_windows("Made/Up/Zone") == "UTC"

    def test_empty_input_falls_back_to_utc(self):
        assert iana_to_windows("") == "UTC"


class TestResolveTimezoneForOem:
    def test_empty_triggers_host_detection(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "Asia/Tokyo")
        assert resolve_timezone_for_oem("") == "Tokyo Standard Time"

    def test_iana_name_gets_translated(self):
        assert resolve_timezone_for_oem("Asia/Seoul") == "Korea Standard Time"

    def test_windows_id_passes_through_verbatim(self):
        # Niche territory variant not in the CLDR 001 wildcard table --
        # user knows the Windows-side string and we shouldn't second-guess.
        assert resolve_timezone_for_oem("Russia Time Zone 11") == "Russia Time Zone 11"

    def test_utc_case_insensitive(self):
        assert resolve_timezone_for_oem("UTC") == "UTC"
        assert resolve_timezone_for_oem("utc") == "UTC"

    def test_whitespace_stripped(self):
        assert resolve_timezone_for_oem("  Asia/Seoul  ") == "Korea Standard Time"

    def test_unknown_iana_falls_back_to_utc(self):
        assert resolve_timezone_for_oem("Made/Up/Zone") == "UTC"
