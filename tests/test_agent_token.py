"""Tests for the host-side agent bearer token helpers."""

from __future__ import annotations

import os

import pytest

from winpodx.utils.agent_token import (
    ensure_agent_token,
    stage_token_to_oem,
    token_path,
)


class TestEnsureAgentToken:
    def test_creates_file_with_mode_0600(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        token = ensure_agent_token()
        path = token_path()

        assert path.exists()
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600
        assert token

    def test_token_is_64_hex_chars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        token = ensure_agent_token()

        assert len(token) == 64
        int(token, 16)  # raises ValueError if not hex

    def test_idempotent_returns_same_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        first = ensure_agent_token()
        second = ensure_agent_token()

        assert first == second

    def test_reapplies_0600_when_existing_file_has_wrong_mode(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        ensure_agent_token()
        path = token_path()
        os.chmod(path, 0o644)
        assert (path.stat().st_mode & 0o777) == 0o644

        ensure_agent_token()

        assert (path.stat().st_mode & 0o777) == 0o600

    def test_empty_existing_file_is_regenerated(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        path = token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

        token = ensure_agent_token()

        assert token
        assert len(token) == 64
        assert path.read_text(encoding="ascii").strip() == token

    def test_token_path_under_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        path = token_path()

        assert path == tmp_path / "winpodx" / "agent_token.txt"


class TestStageTokenToOem:
    def test_writes_oem_copy_with_mode_0600(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        oem_dir = tmp_path / "oem"
        oem_dir.mkdir()

        dest = stage_token_to_oem(oem_dir)

        assert dest == oem_dir / "agent_token.txt"
        assert dest.exists()
        assert (dest.stat().st_mode & 0o777) == 0o600

    def test_oem_copy_matches_host_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        oem_dir = tmp_path / "oem"
        oem_dir.mkdir()

        host_token = ensure_agent_token()
        dest = stage_token_to_oem(oem_dir)

        assert dest.read_text(encoding="ascii").strip() == host_token


class TestSetupStagesOemToken:
    """End-to-end: handle_setup wires _ensure_oem_token_staged() in."""

    def test_existing_config_skip_path_stages_token(self, tmp_path, monkeypatch):
        import argparse

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import handle_setup
        from winpodx.core.config import Config

        # Create an existing config so handle_setup takes the skip path.
        cfg = Config()
        cfg.save()

        oem_dir = tmp_path / "oem"
        oem_dir.mkdir()
        monkeypatch.setattr("winpodx.cli.setup_cmd._find_oem_dir", lambda: str(oem_dir))

        # check_all is called before the skip check, so stub it to a minimal
        # mapping that satisfies the freerdp gate.
        from winpodx.utils.deps import DepCheck

        monkeypatch.setattr(
            "winpodx.cli.setup_cmd.check_all",
            lambda: {"freerdp": DepCheck(name="freerdp", found=True, note="stub")},
        )

        args = argparse.Namespace(backend=None, non_interactive=True)
        handle_setup(args)

        staged = oem_dir / "agent_token.txt"
        assert staged.exists()
        assert (staged.stat().st_mode & 0o777) == 0o600
        assert staged.read_text(encoding="ascii").strip()


@pytest.fixture(autouse=True)
def _isolate_token_dir(tmp_path, monkeypatch):
    """Always isolate XDG_CONFIG_HOME so tests don't touch the real ~/.config."""
    if "XDG_CONFIG_HOME" not in os.environ:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "_default_isolation"))
