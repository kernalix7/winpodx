# SPDX-License-Identifier: MIT
"""Tests for #254 timezone wiring via the dockur TZ env var."""

from __future__ import annotations

from winpodx.core.config import Config
from winpodx.core.pod.compose import _build_compose_content, _resolve_timezone_for_compose


def _make_cfg(monkeypatch, tmp_path, *, timezone: str) -> Config:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    cfg = Config()
    cfg.pod.timezone = timezone
    return cfg


class TestResolveTimezoneForCompose:
    def test_explicit_iana_passes_through(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        assert _resolve_timezone_for_compose(cfg) == "Asia/Seoul"

    def test_explicit_windows_id_passes_through(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Korea Standard Time")
        assert _resolve_timezone_for_compose(cfg) == "Korea Standard Time"

    def test_empty_triggers_host_detect(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "winpodx.utils.locale.detect_timezone",
            lambda: "Europe/Berlin",
        )
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="")
        assert _resolve_timezone_for_compose(cfg) == "Europe/Berlin"

    def test_whitespace_is_stripped(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="  Asia/Tokyo  ")
        assert _resolve_timezone_for_compose(cfg) == "Asia/Tokyo"


class TestComposeContainsTzEnv:
    """The generated compose YAML must carry a ``TZ:`` line whose value
    matches whatever cfg.pod.timezone resolves to. dockur reads TZ at
    Sysprep time and writes the <TimeZone> element into unattend.xml."""

    def test_explicit_iana_lands_in_tz_env(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        content = _build_compose_content(cfg)
        assert 'TZ: "Asia/Seoul"' in content

    def test_host_detect_lands_in_tz_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "winpodx.utils.locale.detect_timezone",
            lambda: "America/Los_Angeles",
        )
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="")
        content = _build_compose_content(cfg)
        assert 'TZ: "America/Los_Angeles"' in content

    def test_utc_detect_fallback_still_lands(self, tmp_path, monkeypatch):
        """Even when host detect returns the UTC fallback, the TZ env
        still gets that value. This differs from the pre-refactor
        OEM-file approach, where we skipped writing the file on a
        detection-failure UTC to avoid forcing the guest onto UTC.
        Now we just hand whatever we resolve to dockur and let dockur
        decide; dockur's own fallback is also UTC, so the net effect
        is identical."""
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "UTC")
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="")
        content = _build_compose_content(cfg)
        assert 'TZ: "UTC"' in content


class TestNoStaleTimezoneFile:
    """The pre-refactor design dropped a ``timezone.txt`` into the OEM
    dir for install.bat to consume. That code path is gone -- compose
    generation must not create the file, and the OEM dir must not
    contain one after a fresh compose run."""

    def test_compose_does_not_write_timezone_txt(self, tmp_path, monkeypatch):
        cfg = _make_cfg(monkeypatch, tmp_path, timezone="Asia/Seoul")
        _build_compose_content(cfg)

        # User OEM dir may not even exist (case-1 fast path uses bundle
        # dir directly); when it does exist, timezone.txt must not.
        user_oem = tmp_path / "config" / "winpodx" / "oem"
        if user_oem.is_dir():
            assert not (user_oem / "timezone.txt").exists()
