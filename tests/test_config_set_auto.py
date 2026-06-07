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


class TestApplyComposeChange:
    """`config set` of a compose-affecting key auto-applies it (#246)."""

    def _seed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.pod.backend = "podman"
        cfg.save()

    def _stub_pod(self, monkeypatch, *, running: bool):
        import winpodx.core.compose as _compose
        import winpodx.core.pod as _pod

        monkeypatch.setattr(_compose, "generate_compose", lambda c: None)
        state = _pod.PodState.RUNNING if running else _pod.PodState.STOPPED
        monkeypatch.setattr(_pod, "pod_status", lambda c: type("S", (), {"state": state})())
        calls = {"stop": 0, "start": 0}
        monkeypatch.setattr(
            _pod, "stop_pod", lambda c: calls.__setitem__("stop", calls["stop"] + 1)
        )
        monkeypatch.setattr(
            _pod, "start_pod", lambda c: calls.__setitem__("start", calls["start"] + 1)
        )
        return calls

    def test_disguise_level_recreates_when_running(self, tmp_path, monkeypatch, capsys):
        # balanced -> off keeps virtio (device-safe) → plain recreate applies it.
        self._seed(tmp_path, monkeypatch)
        calls = self._stub_pod(monkeypatch, running=True)
        _set("pod.disguise_level", value="off", auto=False)
        out = capsys.readouterr().out
        assert "Set pod.disguise_level = off" in out
        assert calls == {"stop": 1, "start": 1}  # recreated now
        assert Config.load().pod.disguise_level == "off"

    def test_disguise_level_defers_when_stopped(self, tmp_path, monkeypatch, capsys):
        self._seed(tmp_path, monkeypatch)
        calls = self._stub_pod(monkeypatch, running=False)
        _set("pod.disguise_level", value="off", auto=False)
        out = capsys.readouterr().out
        assert "applies on the next" in out
        assert calls == {"stop": 0, "start": 0}  # not running → no recreate
        assert Config.load().pod.disguise_level == "off"

    def test_disguise_level_max_warns_and_skips_recreate(self, tmp_path, monkeypatch, capsys):
        # balanced -> max swaps virtio for emulated devices: a plain recreate
        # would brick the install, so config set must NOT auto-apply — it warns
        # and points at --wipe-storage instead.
        self._seed(tmp_path, monkeypatch)
        calls = self._stub_pod(monkeypatch, running=True)
        _set("pod.disguise_level", value="max", auto=False)
        out = capsys.readouterr().out
        assert "Set pod.disguise_level = max" in out
        assert "--wipe-storage" in out
        assert calls == {"stop": 0, "start": 0}  # NOT recreated (would brick)
        assert Config.load().pod.disguise_level == "max"  # value still persisted

    def test_non_compose_key_does_not_recreate(self, tmp_path, monkeypatch, capsys):
        self._seed(tmp_path, monkeypatch)
        calls = self._stub_pod(monkeypatch, running=True)
        _set("pod.auto_start", value="true", auto=False)
        assert calls == {"stop": 0, "start": 0}  # not a compose key
