"""Security-focused tests for winpodx."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from winpodx.core.app import _SAFE_NAME_RE, load_app
from winpodx.core.config import Config, PodConfig, RDPConfig
from winpodx.core.rdp import _filter_extra_flags, linux_to_unc

# --- App name injection ---


class TestAppNameValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "../etc/passwd",
            "../../root",
            "app;rm -rf /",
            "app && echo pwned",
            "app|cat /etc/shadow",
            "app\x00null",
            "",
            "app name with spaces",
            "app/subdir",
        ],
    )
    def test_rejects_malicious_names(self, name: str):
        assert not _SAFE_NAME_RE.match(name)

    @pytest.mark.parametrize(
        "name",
        [
            "notepad",
            "word-o365",
            "my_app_2",
            "Excel",
            "vscode",
        ],
    )
    def test_accepts_valid_names(self, name: str):
        assert _SAFE_NAME_RE.match(name)


# --- UNC path injection ---


class TestUNCPathValidation:
    def test_rejects_invalid_windows_chars(self):
        with pytest.raises(ValueError, match="invalid for Windows"):
            linux_to_unc('/home/user/file"name.txt')

    def test_rejects_pipe_char(self):
        with pytest.raises(ValueError, match="invalid for Windows"):
            linux_to_unc("/home/user/file|name.txt")

    def test_rejects_wildcard(self):
        with pytest.raises(ValueError, match="invalid for Windows"):
            linux_to_unc("/home/user/*.txt")

    def test_accepts_normal_path(self):
        result = linux_to_unc(str(Path.home() / "Documents" / "test.docx"))
        assert result.startswith("\\\\tsclient\\home\\")
        assert "test.docx" in result


# --- Extra flags whitelist ---


class TestExtraFlagsWhitelist:
    def test_allows_safe_flags(self):
        result = _filter_extra_flags("/scale:200 /sound:sys:alsa +fonts /dynamic-resolution")
        assert "/scale:200" in result
        assert "/sound:sys:alsa" in result
        assert "+fonts" in result
        assert "/dynamic-resolution" in result

    def test_blocks_dangerous_flags(self):
        result = _filter_extra_flags("/app:cmd.exe /cmd:bash /exec:whoami /shell:sh")
        assert len(result) == 0

    def test_blocks_cert_override(self):
        result = _filter_extra_flags("/cert:ignore /cert:tofu")
        assert len(result) == 0

    def test_blocks_auth_manipulation(self):
        result = _filter_extra_flags("/u:hacker /p:password /v:evil.com")
        assert len(result) == 0

    def test_empty_flags(self):
        assert _filter_extra_flags("") == []


# --- Device-redirection flag hardening (H1) ---


class TestDeviceRedirectionFlags:
    """Per-flag argument-shape validation for /drive, /serial, /parallel, /smartcard, /usb."""

    def test_drive_rejects_host_path_payload(self):
        result = _filter_extra_flags("/drive:etc,/etc")
        assert result == []

    def test_drive_rejects_windows_path_payload(self):
        result = _filter_extra_flags("/drive:share,C:\\Windows")
        assert result == []

    def test_drive_rejects_arbitrary_name_without_allowlist(self):
        result = _filter_extra_flags("/drive:downloads,/home/user/Downloads")
        assert result == []

    def test_drive_rejects_unknown_bare_name(self):
        result = _filter_extra_flags("/drive:root")
        assert result == []

    def test_drive_accepts_known_share_home(self):
        assert _filter_extra_flags("/drive:home") == ["/drive:home"]

    def test_drive_accepts_known_share_media(self):
        assert _filter_extra_flags("/drive:media") == ["/drive:media"]

    def test_drive_rejects_traversal_in_name(self):
        result = _filter_extra_flags("/drive:../../etc")
        assert result == []

    def test_serial_rejects_device_node(self):
        result = _filter_extra_flags("/serial:/dev/ttyUSB0")
        assert result == []

    def test_serial_rejects_named_payload(self):
        result = _filter_extra_flags("/serial:COM1,/dev/ttyS0")
        assert result == []

    def test_parallel_rejects_device_node(self):
        result = _filter_extra_flags("/parallel:lp0,/dev/lp0")
        assert result == []

    def test_smartcard_bare_accepted(self):
        assert _filter_extra_flags("/smartcard") == ["/smartcard"]

    def test_smartcard_payload_rejected(self):
        result = _filter_extra_flags("/smartcard:MyCard,/dev/card")
        assert result == []

    def test_usb_auto_accepted(self):
        assert _filter_extra_flags("/usb:auto") == ["/usb:auto"]

    def test_usb_specific_id_rejected(self):
        result = _filter_extra_flags("/usb:id,dev=1234:5678")
        assert result == []

    def test_usb_device_path_rejected(self):
        result = _filter_extra_flags("/usb:/dev/bus/usb/001/002")
        assert result == []

    def test_multi_flag_injection_drops_only_bad(self):
        result = _filter_extra_flags("/scale:150 /drive:etc,/etc /serial:/dev/tty")
        assert result == ["/scale:150"]

    def test_multi_flag_injection_logs_drops(self, caplog):
        import logging as _logging

        with caplog.at_level(_logging.WARNING, logger="winpodx.core.rdp"):
            _filter_extra_flags("/drive:etc,/etc /serial:/dev/tty")
        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "/drive:etc,/etc" in messages
        assert "/serial:/dev/tty" in messages

    def test_scale_rejects_non_numeric(self):
        result = _filter_extra_flags("/scale:abc")
        assert result == []

    def test_scale_rejects_injection_suffix(self):
        result = _filter_extra_flags("/scale:100;rm")
        assert result == []

    def test_network_rejects_unknown_profile(self):
        assert _filter_extra_flags("/network:ethernet") == []

    def test_network_accepts_documented_profile(self):
        assert _filter_extra_flags("/network:lan") == ["/network:lan"]

    def test_log_level_rejects_scope_wildcard(self):
        assert _filter_extra_flags("/log-level:TRACE:com.foo") == []

    def test_log_level_accepts_plain_level(self):
        assert _filter_extra_flags("/log-level:INFO") == ["/log-level:INFO"]

    def test_valid_flag_bundle_passes_unchanged(self):
        result = _filter_extra_flags("/scale:200 /sound:sys:alsa")
        assert result == ["/scale:200", "/sound:sys:alsa"]


# --- Config validation ---


class TestConfigSecurity:
    def test_invalid_backend_reset(self):
        pod = PodConfig(backend="'; DROP TABLE users; --")
        assert pod.backend == "podman"

    def test_extreme_port_clamped(self):
        rdp = RDPConfig(port=99999)
        assert rdp.port == 65535
        rdp = RDPConfig(port=-1)
        assert rdp.port == 1

    def test_config_file_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.rdp.password = "supersecret123"
        cfg.save()

        config_path = tmp_path / "winpodx" / "winpodx.toml"
        mode = config_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_corrupted_config_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        config_dir = tmp_path / "winpodx"
        config_dir.mkdir()
        (config_dir / "winpodx.toml").write_text("{{invalid toml}}")

        cfg = Config.load()
        assert cfg.rdp.ip == "127.0.0.1"


# --- App TOML injection ---


class TestAppTomlInjection:
    def test_rejects_app_with_no_name(self, tmp_path):
        toml = tmp_path / "app.toml"
        toml.write_text('executable = "notepad.exe"\n')
        assert load_app(tmp_path) is None

    def test_rejects_app_with_traversal_name(self, tmp_path):
        toml = tmp_path / "app.toml"
        toml.write_text('name = "../evil"\nexecutable = "notepad.exe"\n')
        assert load_app(tmp_path) is None

    def test_rejects_app_with_shell_name(self, tmp_path):
        toml = tmp_path / "app.toml"
        toml.write_text('name = "app;rm -rf /"\nexecutable = "notepad.exe"\n')
        assert load_app(tmp_path) is None

    def test_accepts_valid_app(self, tmp_path):
        toml = tmp_path / "app.toml"
        toml.write_text('name = "notepad"\nexecutable = "notepad.exe"\n')
        app = load_app(tmp_path)
        assert app is not None
        assert app.name == "notepad"

    def test_rejects_oversized_name(self, tmp_path):
        toml = tmp_path / "app.toml"
        long_name = "a" * 256
        toml.write_text(f'name = "{long_name}"\nexecutable = "notepad.exe"\n')
        assert load_app(tmp_path) is None

    def test_accepts_max_length_name(self, tmp_path):
        toml = tmp_path / "app.toml"
        name_255 = "a" * 255
        toml.write_text(f'name = "{name_255}"\nexecutable = "notepad.exe"\n')
        app = load_app(tmp_path)
        assert app is not None


# --- TOML writer escaping ---


class TestTomlWriterEscaping:
    def test_escapes_control_characters(self):
        from winpodx.utils.toml_writer import dumps

        data = {"section": {"key": "line1\nline2\ttab"}}
        result = dumps(data)
        assert "\\n" in result
        assert "\\t" in result
        assert "\n" not in result.split("=")[1].split('"')[1]

    def test_escapes_backslash_and_quotes(self):
        from winpodx.utils.toml_writer import dumps

        data = {"section": {"key": 'back\\slash "quoted"'}}
        result = dumps(data)
        assert '\\\\"' in result or '\\"' in result

    def test_escapes_all_control_chars(self):
        from winpodx.utils.toml_writer import dumps

        # Null byte, bell, vertical tab, DEL: all must be escaped
        data = {"section": {"key": "a\x00b\x07c\x0bd\x7fe"}}
        result = dumps(data)
        for bad in ("\x00", "\x07", "\x0b", "\x7f"):
            assert bad not in result
        assert "\\u0000" in result
        assert "\\u007F" in result


class TestYamlEscape:
    def test_yaml_escape_newlines(self, tmp_path, monkeypatch):
        from winpodx.cli.setup_cmd import _generate_compose
        from winpodx.core.config import Config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.rdp.user = "User"
        cfg.rdp.password = "pass\nword\rtest"

        _generate_compose(cfg)

        compose = (tmp_path / "winpodx" / "compose.yaml").read_text()
        for line in compose.splitlines():
            if "PASSWORD" in line:
                assert "\nword" not in line
                assert "\\n" in line or "\\r" in line
                break


# --- Password filter ---


class TestPasswordFilter:
    def test_filter_installed_on_handlers(self):
        from winpodx.utils.logging import PasswordFilter, setup_logging

        root = logging.getLogger("winpodx")
        root.handlers.clear()
        setup_logging(log_file=False)
        assert any(isinstance(flt, PasswordFilter) for h in root.handlers for flt in h.filters)
        root.handlers.clear()


# --- Config set integer validation ---


class TestPowerShellEscape:
    def test_username_single_quote_escaped(self):
        from unittest.mock import patch

        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = "admin'; whoami; '"
        cfg.pod.backend = "podman"

        with patch("winpodx.core.provisioner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            from winpodx.core.provisioner import _change_windows_password

            _change_windows_password(cfg, "newpass123")
            ps_cmd = mock_run.call_args[0][0][7]
            assert "admin''; whoami; ''" in ps_cmd

    def test_normal_username_unchanged(self):
        from unittest.mock import patch

        from winpodx.core.config import Config

        cfg = Config()
        cfg.rdp.user = "User"
        cfg.pod.backend = "podman"

        with patch("winpodx.core.provisioner.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            from winpodx.core.provisioner import _change_windows_password

            _change_windows_password(cfg, "testpass")
            ps_cmd = mock_run.call_args[0][0][7]
            assert "net user 'User' 'testpass'" == ps_cmd


class TestPasswordTimestamp:
    def test_naive_timestamp_no_crash(self, tmp_path, monkeypatch):
        from winpodx.core.config import Config

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.rdp.password = "testpass"
        cfg.rdp.password_max_age = 7
        cfg.rdp.password_updated = "2020-01-01T00:00:00"  # naive, no timezone
        cfg.pod.backend = "podman"

        from winpodx.core.provisioner import _auto_rotate_password

        result = _auto_rotate_password(cfg)
        assert result is not None


class TestConfigSetValidation:
    def test_int_coercion_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.save()

        from winpodx.cli.config_cmd import _set

        with pytest.raises(SystemExit):
            _set("rdp.port", "not-a-number")
