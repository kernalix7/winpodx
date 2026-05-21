# SPDX-License-Identifier: MIT
"""Tests for ``winpodx config set --auto`` (#254 phase 2)."""

from __future__ import annotations

import pytest

from winpodx.cli.config_cmd import _resolve_auto_value, _set
from winpodx.core.config import Config


class TestResolveAutoValue:
    def test_pod_timezone_uses_detect(self, monkeypatch):
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "Asia/Seoul")
        assert _resolve_auto_value("pod", "timezone") == "Asia/Seoul"

    def test_unknown_key_returns_none(self):
        # pod.language / pod.region / pod.keyboard not yet wired in P2 --
        # the helper returns None so the caller can surface a clear error.
        assert _resolve_auto_value("pod", "language") is None
        assert _resolve_auto_value("pod", "region") is None
        assert _resolve_auto_value("pod", "keyboard") is None

    def test_other_sections_return_none(self):
        assert _resolve_auto_value("rdp", "user") is None
        assert _resolve_auto_value("install", "agent_first") is None


class TestSetAuto:
    def test_auto_writes_detected_value_to_config(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("winpodx.utils.locale.detect_timezone", lambda: "Asia/Seoul")
        # Seed an existing config so Config.load() doesn't fall through to
        # winapps import etc.
        Config().save()

        _set("pod.timezone", value=None, auto=True)
        out = capsys.readouterr().out
        assert "Set pod.timezone = Asia/Seoul" in out

        reloaded = Config.load()
        assert reloaded.pod.timezone == "Asia/Seoul"

    def test_auto_rejects_positional_value(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()

        with pytest.raises(SystemExit) as exc:
            _set("pod.timezone", value="Asia/Seoul", auto=True)
        assert exc.value.code == 1
        assert "mutually exclusive" in capsys.readouterr().out

    def test_auto_on_unsupported_key_exits_with_hint(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()

        with pytest.raises(SystemExit) as exc:
            _set("pod.language", value=None, auto=True)
        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "--auto not yet supported" in out
        assert "pod.timezone" in out

    def test_missing_value_without_auto_exits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        Config().save()

        with pytest.raises(SystemExit) as exc:
            _set("pod.timezone", value=None, auto=False)
        assert exc.value.code == 1
        assert "Either pass a positional value or --auto" in capsys.readouterr().out
