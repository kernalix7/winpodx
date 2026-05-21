# SPDX-License-Identifier: MIT
"""Tests for the consolidated ``winpodx uninstall`` (#255 PR 3)."""

from __future__ import annotations

import argparse

import pytest

from winpodx.cli.uninstall import _build_preview, handle_uninstall
from winpodx.utils.install_source import InstallSource


def _args(*, purge=False, yes=True, no_package_prompt=True):
    return argparse.Namespace(
        purge=purge,
        yes=yes,
        no_package_prompt=no_package_prompt,
    )


class TestBuildPreview:
    def test_no_purge_lists_keep_lines(self):
        src = InstallSource(kind="unknown", label="unknown")
        text = _build_preview(purge=False, install_source=src)
        assert "KEEP" in text
        assert "stop (keep disk for reinstall)" in text
        assert "Podman volume -- KEEP" in text

    def test_purge_lists_destructive_lines(self):
        src = InstallSource(kind="unknown", label="unknown")
        text = _build_preview(purge=True, install_source=src)
        assert "stop AND remove" in text
        assert "Podman volume winpodx-data -- remove" in text
        assert "wipe" in text
        assert "winpodx.toml" in text

    def test_apt_install_lists_package_section(self):
        src = InstallSource(
            kind="apt",
            label="installed via apt (winpodx)",
            package_name="winpodx",
            removal_command="sudo apt remove winpodx",
        )
        text = _build_preview(purge=False, install_source=src)
        assert "[system package]" in text
        assert "winpodx -- will prompt for sudo" in text

    def test_curl_install_lists_install_dir(self):
        src = InstallSource(
            kind="curl",
            label="curl install",
            removal_command="curl ... uninstall.sh | bash",
        )
        text = _build_preview(purge=False, install_source=src)
        assert "[install dir]" in text
        assert "winpodx-app" in text

    def test_source_install_no_package_section(self):
        src = InstallSource(
            kind="source",
            label="pip install",
            removal_command="pip uninstall winpodx",
        )
        text = _build_preview(purge=False, install_source=src)
        assert "[system package]" not in text


class TestHandleUninstall:
    def test_aborted_when_user_declines(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("builtins.input", lambda _p: "n")

        with pytest.raises(SystemExit) as exc:
            handle_uninstall(_args(yes=False))
        assert exc.value.code == 2
        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_yes_skips_confirm(self, tmp_path, monkeypatch, capsys):
        """--yes -> no prompt, runs cleanup, prints completion line."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        def _no_input(_p):
            raise AssertionError("input() should not be called with --yes")

        monkeypatch.setattr("builtins.input", _no_input)
        # Stub out the destructive helpers so the test doesn't try to
        # pkill or talk to podman.
        monkeypatch.setattr("winpodx.cli.uninstall._kill_winpodx_processes", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_reverse_open_listener", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_container", lambda *, remove: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._remove_podman_volume", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._wipe_storage_path", lambda: 0)

        handle_uninstall(_args(yes=True, no_package_prompt=True))
        captured = capsys.readouterr()
        assert "Uninstall complete" in captured.out

    def test_no_package_prompt_skips_sudo_offer(self, tmp_path, monkeypatch, capsys):
        """--no-package-prompt -> never asks about sudo, even if package detected."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("winpodx.cli.uninstall._kill_winpodx_processes", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_reverse_open_listener", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_container", lambda *, remove: 0)
        monkeypatch.setattr(
            "winpodx.utils.install_source.detect",
            lambda: InstallSource(
                kind="apt",
                label="apt (winpodx)",
                package_name="winpodx",
                removal_command="sudo apt remove winpodx",
            ),
        )
        called = []
        monkeypatch.setattr(
            "winpodx.cli.uninstall._offer_package_removal",
            lambda src: called.append(src),
        )

        handle_uninstall(_args(yes=True, no_package_prompt=True))
        captured = capsys.readouterr()
        assert called == [], "_offer_package_removal should not fire"
        assert "Uninstall complete" in captured.out

    def test_package_prompt_fires_when_apt_detected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("winpodx.cli.uninstall._kill_winpodx_processes", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_reverse_open_listener", lambda: 0)
        monkeypatch.setattr("winpodx.cli.uninstall._stop_container", lambda *, remove: 0)
        monkeypatch.setattr(
            "winpodx.utils.install_source.detect",
            lambda: InstallSource(
                kind="apt",
                label="apt (winpodx)",
                package_name="winpodx",
                removal_command="sudo apt remove winpodx",
            ),
        )
        called = []
        monkeypatch.setattr(
            "winpodx.cli.uninstall._offer_package_removal",
            lambda src: called.append(src),
        )

        handle_uninstall(_args(yes=True, no_package_prompt=False))
        assert len(called) == 1
        assert called[0].kind == "apt"
