"""Tests for configuration management."""

from winpodx.core.config import Config, PodConfig, RDPConfig
from winpodx.utils.compat import parse_winapps_conf


def test_config_defaults():
    cfg = Config()
    assert cfg.rdp.ip == "127.0.0.1"
    assert cfg.rdp.port == 3390
    assert cfg.rdp.scale == 100
    assert cfg.pod.backend == "podman"
    assert cfg.pod.auto_start is True


def test_rdp_config_port_clamping():
    rdp = RDPConfig(port=0)
    assert rdp.port == 1
    rdp = RDPConfig(port=99999)
    assert rdp.port == 65535


def test_rdp_config_scale_clamping():
    rdp = RDPConfig(scale=50)
    assert rdp.scale == 100
    rdp = RDPConfig(scale=9999)
    assert rdp.scale == 500


def test_pod_config_backend_validation():
    pod = PodConfig(backend="invalid")
    assert pod.backend == "podman"
    pod = PodConfig(backend="docker")
    assert pod.backend == "docker"


def test_pod_config_resource_clamping():
    pod = PodConfig(cpu_cores=-1, ram_gb=0)
    assert pod.cpu_cores == 1
    assert pod.ram_gb == 1
    pod = PodConfig(cpu_cores=999, ram_gb=9999)
    assert pod.cpu_cores == 128
    assert pod.ram_gb == 512


def test_pod_config_idle_timeout_clamping():
    pod = PodConfig(idle_timeout=-100)
    assert pod.idle_timeout == 0


def test_pod_config_win_version_known_values():
    # Win10+ family — round-trip untouched without triggering the warning.
    for v in ("11", "10", "ltsc11", "ltsc10", "iot11", "tiny11", "tiny10", "2022", "2016"):
        assert PodConfig(win_version=v).win_version == v


def test_pod_config_win_version_pre_win10_warns_but_passes_through(caplog):
    # Pre-Win10 editions are off the known list but still accepted with a
    # warning so users on bleeding-edge dockur builds aren't blocked.
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="winpodx.core.config"):
        pod = PodConfig(win_version="xp")
    assert pod.win_version == "xp"
    assert any("not in winpodx's known list" in r.message for r in caplog.records)


def test_pod_config_win_version_normalises_case_and_whitespace():
    assert PodConfig(win_version="  LTSC11  ").win_version == "ltsc11"


def test_pod_config_win_version_empty_falls_back_to_default():
    assert PodConfig(win_version="").win_version == "11"
    assert PodConfig(win_version="   ").win_version == "11"


def test_pod_config_win_version_unknown_passes_through_with_warning(caplog):
    # Bleeding-edge dockur values aren't blocked — just warned.
    import logging as _logging

    with caplog.at_level(_logging.WARNING, logger="winpodx.core.config"):
        pod = PodConfig(win_version="future-edition")
    assert pod.win_version == "future-edition"
    assert any("not in winpodx's known list" in r.message for r in caplog.records)


def test_config_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    cfg.rdp.user = "testuser"
    cfg.rdp.ip = "192.168.1.100"
    cfg.pod.backend = "libvirt"
    cfg.save()

    loaded = Config.load()
    assert loaded.rdp.user == "testuser"
    assert loaded.rdp.ip == "192.168.1.100"
    assert loaded.pod.backend == "libvirt"


def test_config_load_revalidates_loaded_values(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[rdp]\n"
        "port = 99999\n"
        "scale = 50\n"
        "dpi = 9999\n"
        "password_max_age = -1\n"
        "\n[pod]\n"
        'backend = "bad"\n'
        "cpu_cores = -10\n"
        "ram_gb = 9999\n"
        'container_name = "../bad"\n',
        encoding="utf-8",
    )

    loaded = Config.load()

    assert loaded.rdp.port == 65535
    assert loaded.rdp.scale == 100
    assert loaded.rdp.dpi == 500
    assert loaded.rdp.password_max_age == 0
    assert loaded.pod.backend == "podman"
    assert loaded.pod.cpu_cores == 1
    assert loaded.pod.ram_gb == 512
    assert loaded.pod.container_name == "winpodx-windows"


def test_logging_config_defaults_to_info():
    from winpodx.core.config import LoggingConfig

    cfg = LoggingConfig()
    assert cfg.level == "INFO"
    assert cfg.numeric_level() == 20  # logging.INFO == 20


def test_logging_config_normalises_case_and_whitespace():
    from winpodx.core.config import LoggingConfig

    assert LoggingConfig(level="  debug  ").level == "DEBUG"
    assert LoggingConfig(level="Warning").level == "WARNING"


def test_logging_config_unknown_falls_back_to_info():
    from winpodx.core.config import LoggingConfig

    assert LoggingConfig(level="VERBOSE").level == "INFO"
    assert LoggingConfig(level="").level == "INFO"
    assert LoggingConfig(level=None).level == "INFO"


def test_logging_config_numeric_level():
    import logging as _logging

    from winpodx.core.config import LoggingConfig

    assert LoggingConfig(level="DEBUG").numeric_level() == _logging.DEBUG
    assert LoggingConfig(level="ERROR").numeric_level() == _logging.ERROR
    assert LoggingConfig(level="CRITICAL").numeric_level() == _logging.CRITICAL


def test_logging_config_round_trip(tmp_path, monkeypatch):
    """``cfg.logging.level`` survives save / load via the new ``[logging]``
    TOML section."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    from winpodx.core.config import Config

    cfg = Config()
    cfg.logging.level = "DEBUG"
    cfg.save()

    loaded = Config.load()
    assert loaded.logging.level == "DEBUG"


def test_apply_bool_coercion_from_string():
    from winpodx.core.config import _apply

    pod = PodConfig()
    _apply(pod, {"auto_start": "false"})
    assert pod.auto_start is False

    _apply(pod, {"auto_start": "true"})
    assert pod.auto_start is True

    _apply(pod, {"auto_start": "0"})
    assert pod.auto_start is False

    _apply(pod, {"auto_start": "yes"})
    assert pod.auto_start is True

    _apply(pod, {"auto_start": "no"})
    assert pod.auto_start is False


def test_pod_config_boot_timeout_defaults_and_clamping():
    assert PodConfig().boot_timeout == 300
    assert PodConfig(boot_timeout=10).boot_timeout == 30
    assert PodConfig(boot_timeout=99999).boot_timeout == 3600
    assert PodConfig(boot_timeout=600).boot_timeout == 600


def test_pod_config_container_name_default_and_persist(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    assert cfg.pod.container_name == "winpodx-windows"

    cfg.pod.container_name = "custom-win-pod"
    cfg.save()

    loaded = Config.load()
    assert loaded.pod.container_name == "custom-win-pod"


def test_pod_config_container_name_empty_fallback():
    pod = PodConfig(container_name="")
    assert pod.container_name == "winpodx-windows"


def test_config_save_calls_fsync(tmp_path, monkeypatch):
    # save() must fsync the tmp file before rename.
    import os

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def tracking_fsync(fd: int) -> None:
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", tracking_fsync)

    cfg = Config()
    cfg.rdp.user = "syncuser"
    cfg.save()

    assert len(fsynced_fds) >= 1
    path = Config.path()
    assert path.exists()
    assert path.stat().st_size > 0


def test_parse_winapps_conf(tmp_path):
    conf = tmp_path / "winapps.conf"
    conf.write_text(
        'RDP_USER="myuser"\n'
        'RDP_PASS="mypass"\n'
        'RDP_IP="10.0.0.5"\n'
        'WAFLAVOR="libvirt"\n'
        'RDP_SCALE="140"\n'
    )

    vals = parse_winapps_conf(conf)
    assert vals["RDP_USER"] == "myuser"
    assert vals["RDP_PASS"] == "mypass"
    assert vals["RDP_IP"] == "10.0.0.5"
    assert vals["WAFLAVOR"] == "libvirt"
    assert vals["RDP_SCALE"] == "140"


# --- v0.1.8: pod.max_sessions + memory budget helpers ---


def test_pod_config_max_sessions_default():
    # v0.2.1: bumped from 10 to 25.
    assert PodConfig().max_sessions == 25


def test_pod_config_max_sessions_clamping():
    assert PodConfig(max_sessions=0).max_sessions == 1
    assert PodConfig(max_sessions=-5).max_sessions == 1
    assert PodConfig(max_sessions=200).max_sessions == 50


def test_pod_config_max_sessions_roundtrip(tmp_path, monkeypatch):
    from winpodx.core.config import Config
    from winpodx.utils import paths as pmod

    monkeypatch.setattr(pmod, "config_dir", lambda: tmp_path)
    cfg = Config()
    cfg.pod.max_sessions = 25
    cfg.save()
    loaded = Config.load()
    assert loaded.pod.max_sessions == 25


def test_estimate_session_memory_shape():
    from winpodx.core.config import estimate_session_memory

    assert estimate_session_memory(1) == 2.1
    assert estimate_session_memory(10) == 3.0
    assert estimate_session_memory(30) == 5.0
    assert estimate_session_memory(50) == 7.0


def test_check_session_budget_silent_on_default():
    """Default config (25 sessions, 6 GB) must NOT produce a warning.

    v0.2.1: defaults bumped 10/4 -> 25/6 so a real-world setup with
    Office + Teams + Edge + a couple side apps fits without warning.
    """
    from winpodx.core.config import Config, check_session_budget

    cfg = Config()
    assert cfg.pod.max_sessions == 25
    assert cfg.pod.ram_gb == 6
    assert check_session_budget(cfg) is None


def test_check_session_budget_silent_when_ram_sufficient():
    from winpodx.core.config import Config, check_session_budget

    cfg = Config()
    cfg.pod.max_sessions = 30
    cfg.pod.ram_gb = 8
    assert check_session_budget(cfg) is None


def test_check_session_budget_warns_when_over_subscribed():
    from winpodx.core.config import Config, check_session_budget

    cfg = Config()
    cfg.pod.max_sessions = 30
    cfg.pod.ram_gb = 4
    msg = check_session_budget(cfg)
    assert msg is not None
    assert "30" in msg
    assert "ram_gb" in msg
    assert "4" in msg


def test_check_session_budget_recommends_sufficient_ram():
    """The recommended ram_gb must actually be large enough to silence the warning."""
    from winpodx.core.config import Config, check_session_budget

    cfg = Config()
    cfg.pod.max_sessions = 50
    cfg.pod.ram_gb = 4
    msg = check_session_budget(cfg)
    assert msg is not None
    # Extract recommended value from the message and confirm it clears.
    import re

    match = re.search(r"raising pod\.ram_gb to at least (\d+)", msg)
    assert match, f"no recommendation in: {msg}"
    rec = int(match.group(1))
    cfg.pod.ram_gb = rec
    assert check_session_budget(cfg) is None, f"recommended ram_gb={rec} should silence the warning"


# --- storage_path validation (Security review hardening) ---


class TestStoragePathValidation:
    """`PodConfig.__post_init__` rejects unsafe storage_path values.

    The denylist + allowlist exists so a hand-edited TOML can't get
    `chattr +C /etc` or `rsync -aS ... /` past validation.
    """

    def _cfg(self, raw):
        from winpodx.core.config import PodConfig

        cfg = PodConfig()
        cfg.storage_path = raw
        cfg.__post_init__()
        return cfg.storage_path

    def test_empty_string_passes_through(self):
        assert self._cfg("") == ""

    def test_whitespace_becomes_empty(self):
        assert self._cfg("   ") == ""

    def test_non_string_becomes_empty(self):
        assert self._cfg(123) == ""
        assert self._cfg(None) == ""

    def test_relative_path_rejected(self):
        assert self._cfg("./storage") == ""
        assert self._cfg("storage") == ""
        assert self._cfg("../foo") == ""

    def test_system_root_rejected(self):
        assert self._cfg("/") == ""
        assert self._cfg("/etc") == ""
        assert self._cfg("/etc/shadow") == ""
        assert self._cfg("/usr/local/bin") == ""
        assert self._cfg("/var/lib/anything-not-winpodx") == ""
        assert self._cfg("/proc/1") == ""
        assert self._cfg("/sys") == ""
        assert self._cfg("/dev/null") == ""

    def test_yaml_breaking_chars_rejected(self):
        for bad in (
            "/home/u/path\nfoo",
            "/home/u/path\rfoo",
            '/home/u/x"foo',
            "/home/u/x'foo",
            "/home/u/${HOME}",
            "/home/u/`whoami`",
            "/home/u/{a}",
        ):
            assert self._cfg(bad) == "", f"{bad!r} should have been rejected"

    def test_user_home_subdirectory_accepted(self, tmp_path, monkeypatch):
        # Path.home() reads $HOME; redirect via monkeypatch so the
        # accepted-prefix check matches our tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        accepted = str(tmp_path / "winpodx-storage")
        assert self._cfg(accepted) == accepted

    def test_tilde_expansion_subdirectory_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        # Original (unexpanded) string should round-trip; resolution is
        # only used for the safety check.
        assert self._cfg("~/.local/share/winpodx/storage") == "~/.local/share/winpodx/storage"

    def test_var_lib_winpodx_accepted(self):
        assert self._cfg("/var/lib/winpodx/storage") == "/var/lib/winpodx/storage"

    def test_tmp_subpath_accepted_for_tests(self):
        # Used by pytest fixtures (tmp_path under /tmp/pytest-of-*).
        assert self._cfg("/tmp/winpodx-test/storage") == "/tmp/winpodx-test/storage"
