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
