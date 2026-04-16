"""Security-focused tests for winpodx.

Tests input validation, path traversal prevention, injection attacks,
and credential protection.
"""

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
        assert cfg.rdp.ip == "127.0.0.1"  # Defaults


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
        # Raw newlines in PASSWORD value would break YAML structure
        for line in compose.splitlines():
            if "PASSWORD" in line:
                assert "\nword" not in line
                assert "\\n" in line or "\\r" in line
                break


# --- Password filter ---


class TestPasswordFilter:
    def test_filter_masks_password_in_log(self):
        from winpodx.utils.logging import PasswordFilter

        f = PasswordFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="password=supersecret123",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert "supersecret123" not in record.getMessage()
        assert "***" in record.getMessage()

    def test_filter_passes_normal_log(self):
        from winpodx.utils.logging import PasswordFilter

        f = PasswordFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Pod started successfully",
            args=None,
            exc_info=None,
        )
        f.filter(record)
        assert record.getMessage() == "Pod started successfully"

    def test_filter_installed_on_handlers(self):
        from winpodx.utils.logging import PasswordFilter, setup_logging

        root = logging.getLogger("winpodx")
        # Clear handlers from prior tests
        root.handlers.clear()
        setup_logging(log_file=False)
        assert any(isinstance(flt, PasswordFilter) for h in root.handlers for flt in h.filters)
        root.handlers.clear()


# --- Config set integer validation ---


class TestConfigSetValidation:
    def test_int_coercion_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        cfg = Config()
        cfg.save()

        from winpodx.cli.config_cmd import _set

        with pytest.raises(SystemExit):
            _set("rdp.port", "not-a-number")
