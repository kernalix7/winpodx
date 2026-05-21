# SPDX-License-Identifier: MIT
"""Tests for #254 timezone wiring in compose._prepare_oem_dir."""

from __future__ import annotations

from pathlib import Path

from winpodx.core.config import Config
from winpodx.core.pod.compose import _prepare_oem_dir


def _make_cfg(monkeypatch, tmp_path, *, timezone: str) -> Config:
    """Build a Config with XDG_CONFIG_HOME pointed at tmp_path and a
    specific cfg.pod.timezone value."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = Config()
    cfg.pod.timezone = timezone
    return cfg


class TestPrepareOemDirTimezone:
    def test_explicit_iana_is_translated_and_written(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert tz_file.exists(), "timezone.txt should be dropped into user OEM dir"
        assert tz_file.read_text().strip() == "Korea Standard Time"

    def test_explicit_windows_id_passes_through(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Russia Time Zone 11")
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert tz_file.exists()
        assert tz_file.read_text().strip() == "Russia Time Zone 11"

    def test_explicit_utc_is_written(self, tmp_path, monkeypatch):
        """Explicit ``UTC`` in TOML -> write the file. Distinguishes
        deliberate UTC from detection-failure UTC (which doesn't write)."""
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="UTC")
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert tz_file.exists()
        assert tz_file.read_text().strip() == "UTC"

    def test_empty_timezone_with_failed_detection_skips_file(self, tmp_path, monkeypatch):
        """When cfg.pod.timezone is empty AND host detection falls back
        to UTC, we don't write the file -- install.bat skips the tzutil
        call and the guest keeps its current TZ rather than being forced
        onto UTC."""
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "UTC")
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="")
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert not tz_file.exists(), "no file should be written on detection-failure UTC"

    def test_empty_timezone_with_real_host_value_is_written(self, tmp_path, monkeypatch):
        """When cfg.pod.timezone is empty but host detection returns a
        real zone, we write that zone's Windows ID."""
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "Asia/Tokyo")
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="")
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert tz_file.exists()
        assert tz_file.read_text().strip() == "Tokyo Standard Time"

    def test_changing_timezone_overwrites_file(self, tmp_path, monkeypatch):
        """Subsequent compose generations with a different timezone
        must overwrite the previous file, not leave it stale."""
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        _prepare_oem_dir(cfg)

        cfg.pod.timezone = "Europe/Paris"
        result = Path(_prepare_oem_dir(cfg))

        tz_file = result / "timezone.txt"
        assert tz_file.read_text().strip() == "Romance Standard Time"

    def test_changing_to_detection_failure_removes_stale_file(self, tmp_path, monkeypatch):
        """If a previous run wrote the file and a subsequent run resolves
        to detection-failure-UTC, the stale file must be removed so
        install.bat doesn't apply an outdated TZ."""
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        result_first = Path(_prepare_oem_dir(cfg))
        assert (result_first / "timezone.txt").exists()

        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "UTC")
        cfg.pod.timezone = ""
        result_second = Path(_prepare_oem_dir(cfg))

        assert not (result_second / "timezone.txt").exists(), (
            "stale tz file should be removed on detection-failure run"
        )

    def test_no_cfg_skips_timezone_write(self, tmp_path, monkeypatch):
        """Back-compat path: _prepare_oem_dir(cfg=None) is callable from
        the legacy _find_oem_dir alias and must not touch timezone.txt."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        result = Path(_prepare_oem_dir(cfg=None))

        tz_file = result / "timezone.txt"
        assert not tz_file.exists(), "no cfg -> no timezone.txt write"
