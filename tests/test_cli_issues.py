"""Tests for CLI issues 7-22 (4th audit) and H2, H7, M3, M6, M7 (5th audit)."""

from __future__ import annotations

import argparse
import logging
import string
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Issue 22 — '$' removed from password alphabet
# ---------------------------------------------------------------------------


class TestPasswordAlphabet:
    def test_dollar_sign_never_generated(self):
        from winpodx.cli.setup_cmd import _generate_password

        for _ in range(500):
            pw = _generate_password()
            assert "$" not in pw, f"'$' found in generated password: {pw!r}"

    def test_password_contains_required_character_classes(self):
        from winpodx.cli.setup_cmd import _generate_password

        for _ in range(50):
            pw = _generate_password()
            assert any(c in string.ascii_uppercase for c in pw)
            assert any(c in string.ascii_lowercase for c in pw)
            assert any(c in string.digits for c in pw)
            assert any(c in "!@#%&*" for c in pw)

    def test_password_default_length(self):
        from winpodx.cli.setup_cmd import _generate_password

        assert len(_generate_password()) == 20

    def test_allowed_specials_present_in_alphabet(self):
        """Every special character in _SPECIALS must be reachable."""
        from winpodx.cli.setup_cmd import _generate_password

        seen_specials: set[str] = set()
        for _ in range(2000):
            pw = _generate_password()
            for ch in pw:
                if ch not in string.ascii_letters and ch not in string.digits:
                    seen_specials.add(ch)
        assert seen_specials == set("!@#%&*"), f"Unexpected specials: {seen_specials}"


# ---------------------------------------------------------------------------
# Issue 7 — EOFError handling in setup wizard
# ---------------------------------------------------------------------------


class TestAskHelper:
    def test_returns_input_on_normal_read(self, monkeypatch):
        from winpodx.cli.setup_cmd import _ask

        monkeypatch.setattr("builtins.input", lambda _: "hello")
        assert _ask("prompt: ") == "hello"

    def test_returns_default_on_eof(self, monkeypatch):
        from winpodx.cli.setup_cmd import _ask

        def _raise(_prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        assert _ask("prompt: ", default="mydefault") == "mydefault"

    def test_returns_empty_string_default_on_eof(self, monkeypatch):
        from winpodx.cli.setup_cmd import _ask

        def _raise(_prompt):
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        assert _ask("prompt: ") == ""

    def test_non_tty_forces_non_interactive(self, tmp_path, monkeypatch):
        """handle_setup with non-TTY stdin must not raise."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: False))

        args = argparse.Namespace(backend="manual", non_interactive=False)

        freerdp_dep = MagicMock()
        freerdp_dep.found = True
        freerdp_dep.note = ""

        with (
            patch(
                "winpodx.cli.setup_cmd.check_all",
                return_value={"freerdp": freerdp_dep},
            ),
            patch(
                "winpodx.cli.setup_cmd.import_winapps_config",
                return_value=None,
            ),
            patch("winpodx.cli.setup_cmd._generate_compose"),
            patch("winpodx.cli.setup_cmd._recreate_container"),
            patch("winpodx.cli.setup_cmd._register_all_desktop_entries"),
            patch(
                "winpodx.display.scaling.detect_scale_factor",
                return_value=100,
            ),
            patch(
                "winpodx.display.scaling.detect_raw_scale",
                return_value=1.0,
            ),
        ):
            from winpodx.cli.setup_cmd import handle_setup

            # Must not raise EOFError or any other exception
            handle_setup(args)


# ---------------------------------------------------------------------------
# Issue 8 — password rotation split-brain
# ---------------------------------------------------------------------------


class TestRotatePasswordAtomicity:
    def _make_cfg(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.cli.setup_cmd import _generate_compose
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.password = "oldpassword1"
        cfg.rdp.user = "User"
        cfg.pod.backend = "podman"
        cfg.save()
        _generate_compose(cfg)

    def test_compose_failure_does_not_corrupt_config(self, tmp_path, monkeypatch):
        """Compose failure must leave on-disk config unchanged."""
        self._make_cfg(tmp_path, monkeypatch)
        from winpodx.core.config import Config

        original_password = Config.load().rdp.password
        args = argparse.Namespace()

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch(
                "winpodx.core.provisioner._change_windows_password",
                return_value=True,
            ),
            patch(
                "winpodx.cli.setup_cmd._generate_compose_to",
                side_effect=OSError("disk full"),
            ),
        ):
            from winpodx.core.pod import PodState, PodStatus

            mock_status.return_value = PodStatus(state=PodState.RUNNING)

            from winpodx.cli.setup_cmd import handle_rotate_password

            try:
                handle_rotate_password(args)
            except (OSError, SystemExit):
                pass

        reloaded = Config.load()
        assert reloaded.rdp.password == original_password

    def test_successful_rotation_updates_both(self, tmp_path, monkeypatch):
        """On success, both config and compose must reflect the new password."""
        self._make_cfg(tmp_path, monkeypatch)
        from winpodx.core.config import Config

        old_password = Config.load().rdp.password
        args = argparse.Namespace()

        with (
            patch("winpodx.core.pod.pod_status") as mock_status,
            patch(
                "winpodx.core.provisioner._change_windows_password",
                return_value=True,
            ),
        ):
            from winpodx.core.pod import PodState, PodStatus

            mock_status.return_value = PodStatus(state=PodState.RUNNING)

            from winpodx.cli.setup_cmd import handle_rotate_password

            handle_rotate_password(args)

        reloaded = Config.load()
        assert reloaded.rdp.password != old_password

        compose_path = tmp_path / "winpodx" / "compose.yaml"
        assert compose_path.exists()
        assert reloaded.rdp.password in compose_path.read_text()


# ---------------------------------------------------------------------------
# Issue 9 — install-all icon cache refresh
# ---------------------------------------------------------------------------


class TestInstallAllIconCache:
    def test_update_icon_cache_called_after_install_all(self):
        fake_apps = [
            MagicMock(full_name="Notepad"),
            MagicMock(full_name="WordPad"),
        ]
        update_called: list[bool] = []

        with (
            patch(
                "winpodx.core.app.list_available_apps",
                return_value=fake_apps,
            ),
            patch(
                "winpodx.desktop.entry.install_desktop_entry",
                return_value=Path("/tmp/x.desktop"),
            ),
            patch(
                "winpodx.desktop.icons.update_icon_cache",
                side_effect=lambda: update_called.append(True),
            ),
        ):
            from winpodx.cli.app import _install_all

            _install_all()

        assert update_called, "update_icon_cache() was not called by _install_all()"

    def test_update_icon_cache_not_called_when_no_apps(self):
        """When there are no apps, icon cache must not be refreshed."""
        update_called: list[bool] = []

        with (
            patch("winpodx.core.app.list_available_apps", return_value=[]),
            patch(
                "winpodx.desktop.icons.update_icon_cache",
                side_effect=lambda: update_called.append(True),
            ),
        ):
            from winpodx.cli.app import _install_all

            _install_all()

        assert not update_called


# ---------------------------------------------------------------------------
# Issue 10 — _run_app catches RuntimeError from ensure_ready
# ---------------------------------------------------------------------------


class TestRunAppExceptionHandling:
    def test_runtime_error_from_ensure_ready_exits_cleanly(self, capsys):
        with (
            patch(
                "winpodx.core.provisioner.ensure_ready",
                side_effect=RuntimeError("compose failed"),
            ),
            patch("winpodx.desktop.notify.notify_error"),
            pytest.raises(SystemExit) as exc_info,
        ):
            from winpodx.cli.app import _run_app

            _run_app("notepad", None, False)

        assert exc_info.value.code == 1
        assert "compose failed" in capsys.readouterr().err

    def test_provision_error_exits_cleanly(self, capsys):
        from winpodx.core.provisioner import ProvisionError

        with (
            patch(
                "winpodx.core.provisioner.ensure_ready",
                side_effect=ProvisionError("pod broken"),
            ),
            patch("winpodx.desktop.notify.notify_error"),
            pytest.raises(SystemExit) as exc_info,
        ):
            from winpodx.cli.app import _run_app

            _run_app("notepad", None, False)

        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Issue 19 — RDP_FLAGS filtered at winapps import time
# ---------------------------------------------------------------------------


class TestWinappsRDPFlagsFilter:
    def _write_conf(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_safe_flags_preserved(self, tmp_path):
        conf = tmp_path / "winapps.conf"
        self._write_conf(conf, 'RDP_FLAGS="/scale:200 /sound:sys:alsa"\n')

        with patch("winpodx.utils.compat.find_winapps_conf", return_value=conf):
            from winpodx.utils.compat import import_winapps_config

            cfg = import_winapps_config()

        assert cfg is not None
        assert "/scale:200" in cfg.rdp.extra_flags
        assert "/sound:sys:alsa" in cfg.rdp.extra_flags

    def test_dangerous_flags_refused_entirely(self, tmp_path):
        """H7: when ANY flag is blocked, extra_flags must be empty (all-or-nothing)."""
        conf = tmp_path / "winapps.conf"
        self._write_conf(conf, 'RDP_FLAGS="/exec:whoami /shell:sh /scale:100"\n')

        with patch("winpodx.utils.compat.find_winapps_conf", return_value=conf):
            from winpodx.utils.compat import import_winapps_config

            cfg = import_winapps_config()

        assert cfg is not None
        # All-or-nothing: even the safe /scale:100 flag must NOT be written
        # because the input contained blocked flags.
        assert cfg.rdp.extra_flags == ""

    def test_all_dangerous_flags_blocked(self, tmp_path):
        conf = tmp_path / "winapps.conf"
        self._write_conf(conf, 'RDP_FLAGS="/exec:cmd /app:evil.exe /cert:ignore"\n')

        with patch("winpodx.utils.compat.find_winapps_conf", return_value=conf):
            from winpodx.utils.compat import import_winapps_config

            cfg = import_winapps_config()

        assert cfg is not None
        assert cfg.rdp.extra_flags == ""

    def test_warning_logged_when_flags_removed(self, tmp_path, caplog):
        conf = tmp_path / "winapps.conf"
        self._write_conf(conf, 'RDP_FLAGS="/exec:pwned /scale:100"\n')

        with (
            patch("winpodx.utils.compat.find_winapps_conf", return_value=conf),
            caplog.at_level(logging.WARNING, logger="winpodx.utils.compat"),
        ):
            from winpodx.utils.compat import import_winapps_config

            import_winapps_config()

        assert any(
            "blocked" in r.message.lower() or "allowlist" in r.message.lower()
            for r in caplog.records
        )

    def test_empty_rdp_flags_no_error(self, tmp_path):
        conf = tmp_path / "winapps.conf"
        self._write_conf(conf, "RDP_USER=User\n")

        with patch("winpodx.utils.compat.find_winapps_conf", return_value=conf):
            from winpodx.utils.compat import import_winapps_config

            cfg = import_winapps_config()

        assert cfg is not None
        assert cfg.rdp.extra_flags == ""


# ---------------------------------------------------------------------------
# H2 — adversarial username in COMPOSE_TEMPLATE.format()
# ---------------------------------------------------------------------------


class TestComposeTemplateFormatInjection:
    """H2: usernames/passwords containing { } must not cause IndexError or
    leak values across YAML fields via str.format() placeholder expansion."""

    def _make_cfg(self, tmp_path, monkeypatch, username: str, password: str = "S3cur3!pw"):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = username
        cfg.rdp.password = password
        cfg.pod.backend = "podman"
        return cfg

    def test_username_with_format_index_placeholder(self, tmp_path, monkeypatch):
        """{0} in username must not raise IndexError."""
        cfg = self._make_cfg(tmp_path, monkeypatch, username="{0}")
        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        compose_path = tmp_path / "winpodx" / "compose.yaml"
        assert compose_path.exists()

    def test_username_with_named_placeholder_does_not_leak_password(self, tmp_path, monkeypatch):
        """{password} in username must not expand to the real password."""
        secret = "SuperSecret!99"
        cfg = self._make_cfg(tmp_path, monkeypatch, username="{password}", password=secret)
        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        compose_path = tmp_path / "winpodx" / "compose.yaml"
        content = compose_path.read_text()
        # The USERNAME line must contain the literal braces, not the password
        lines = [ln for ln in content.splitlines() if "USERNAME:" in ln]
        assert lines, "USERNAME field missing from compose output"
        username_line = lines[0]
        assert secret not in username_line, (
            f"Password leaked into USERNAME field: {username_line!r}"
        )

    def test_username_with_arbitrary_braces_does_not_raise(self, tmp_path, monkeypatch):
        """A username like 'a{b}c' must render without KeyError."""
        cfg = self._make_cfg(tmp_path, monkeypatch, username="a{b}c")
        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        compose_path = tmp_path / "winpodx" / "compose.yaml"
        assert compose_path.exists()

    def test_password_with_braces_does_not_raise(self, tmp_path, monkeypatch):
        """A password containing braces must not raise during compose generation."""
        cfg = self._make_cfg(tmp_path, monkeypatch, username="User", password="P@ss{word}1!")
        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        compose_path = tmp_path / "winpodx" / "compose.yaml"
        assert compose_path.exists()


# ---------------------------------------------------------------------------
# M3 — container_name routed through cfg.pod.container_name
# ---------------------------------------------------------------------------


class TestContainerNameFromConfig:
    def test_compose_uses_cfg_container_name(self, tmp_path, monkeypatch):
        """compose.yaml container_name must match cfg.pod.container_name."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = "User"
        cfg.rdp.password = "Test123!pw"
        cfg.pod.backend = "podman"
        cfg.pod.container_name = "my-custom-windows"

        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        compose_path = tmp_path / "winpodx" / "compose.yaml"
        content = compose_path.read_text()
        assert "my-custom-windows" in content
        assert "winpodx-windows" not in content


# ---------------------------------------------------------------------------
# M6 — podman-specific keys absent from Docker compose output
# ---------------------------------------------------------------------------


class TestComposeBackendSpecificKeys:
    def _generate(self, tmp_path, monkeypatch, backend: str) -> str:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = "User"
        cfg.rdp.password = "Test123!pw"
        cfg.pod.backend = backend

        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        return (tmp_path / "winpodx" / "compose.yaml").read_text()

    def test_podman_backend_includes_keep_groups(self, tmp_path, monkeypatch):
        content = self._generate(tmp_path, monkeypatch, "podman")
        assert "keep-groups" in content
        assert "run.oci.keep_original_groups" in content

    def test_docker_backend_excludes_keep_groups(self, tmp_path, monkeypatch):
        content = self._generate(tmp_path, monkeypatch, "docker")
        assert "keep-groups" not in content
        assert "run.oci.keep_original_groups" not in content


# ---------------------------------------------------------------------------
# M7 — NETWORK: "slirp" removed from compose output
# ---------------------------------------------------------------------------


class TestComposeNetworkKey:
    def test_network_slirp_not_emitted(self, tmp_path, monkeypatch):
        """NETWORK: slirp must not appear in generated compose — let Podman pick."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = "User"
        cfg.rdp.password = "Test123!pw"
        cfg.pod.backend = "podman"

        from winpodx.cli.setup_cmd import _generate_compose

        _generate_compose(cfg)
        content = (tmp_path / "winpodx" / "compose.yaml").read_text()
        assert "NETWORK" not in content
        assert "slirp" not in content
