"""Tests for ``cfg.install`` (agent-first install flow).

See ``docs/design/AGENT_FIRST_INSTALL_DESIGN.md`` §"Config schema
additions" for the schema this guards.
"""

from winpodx.core.config import Config, InstallConfig

# --- defaults -----------------------------------------------------------------


def test_install_config_defaults():
    cfg = InstallConfig()
    assert cfg.agent_first is True
    assert cfg.wait_ready_stage2_secs == 900
    assert cfg.wait_ready_stage3_secs == 1800
    assert cfg.auto_resume is True
    assert cfg.watchdog_max_respawns == 3
    assert cfg.watchdog_probe_debounce_count == 2
    assert cfg.watchdog_probe_debounce_secs == [2, 5]


def test_config_includes_install_section():
    cfg = Config()
    assert isinstance(cfg.install, InstallConfig)
    assert cfg.install.agent_first is True


# --- bool coercion ------------------------------------------------------------


def test_install_config_bool_fields_coerced_from_truthy_int():
    cfg = InstallConfig(agent_first=1, auto_resume=0)
    assert cfg.agent_first is True
    assert cfg.auto_resume is False


def test_install_config_bool_fields_coerced_from_arbitrary_string():
    # `bool("maybe")` is True; the point is we don't crash on weird values.
    cfg = InstallConfig(agent_first="maybe")
    assert cfg.agent_first is True


# --- int clamping -------------------------------------------------------------


def test_install_config_wait_ready_clamping():
    cfg = InstallConfig(wait_ready_stage2_secs=-5)
    assert cfg.wait_ready_stage2_secs == 60

    cfg = InstallConfig(wait_ready_stage2_secs=0)
    assert cfg.wait_ready_stage2_secs == 60

    cfg = InstallConfig(wait_ready_stage2_secs=99999)
    assert cfg.wait_ready_stage2_secs == 14400


def test_install_config_wait_ready_stage3_clamping():
    cfg = InstallConfig(wait_ready_stage3_secs=-1)
    assert cfg.wait_ready_stage3_secs == 60
    cfg = InstallConfig(wait_ready_stage3_secs=99999)
    assert cfg.wait_ready_stage3_secs == 14400


def test_install_config_watchdog_int_clamping():
    cfg = InstallConfig(watchdog_max_respawns=-1, watchdog_probe_debounce_count=0)
    assert cfg.watchdog_max_respawns == 0
    assert cfg.watchdog_probe_debounce_count == 1


# --- watchdog_probe_debounce_secs list coercion -------------------------------


def test_install_config_debounce_secs_string_falls_back_to_default():
    cfg = InstallConfig(watchdog_probe_debounce_secs="not-a-list")  # type: ignore[arg-type]
    assert cfg.watchdog_probe_debounce_secs == [2, 5]


def test_install_config_debounce_secs_empty_falls_back_to_default():
    cfg = InstallConfig(watchdog_probe_debounce_secs=[])
    assert cfg.watchdog_probe_debounce_secs == [2, 5]


def test_install_config_debounce_secs_negative_falls_back_to_default():
    cfg = InstallConfig(watchdog_probe_debounce_secs=[2, -3])
    assert cfg.watchdog_probe_debounce_secs == [2, 5]


def test_install_config_debounce_secs_garbage_element_falls_back_to_default():
    cfg = InstallConfig(watchdog_probe_debounce_secs=[2, "five"])  # type: ignore[list-item]
    assert cfg.watchdog_probe_debounce_secs == [2, 5]


def test_install_config_debounce_secs_custom_value_preserved():
    cfg = InstallConfig(watchdog_probe_debounce_secs=[1, 3, 7])
    assert cfg.watchdog_probe_debounce_secs == [1, 3, 7]


def test_install_config_debounce_secs_default_is_independent_per_instance():
    a = InstallConfig()
    b = InstallConfig()
    a.watchdog_probe_debounce_secs.append(99)
    assert b.watchdog_probe_debounce_secs == [2, 5]


# --- TOML round-trip ----------------------------------------------------------


def test_config_load_with_no_install_section_uses_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[rdp]\nuser = "alice"\n', encoding="utf-8")

    loaded = Config.load()
    assert loaded.rdp.user == "alice"
    assert loaded.install.agent_first is True
    assert loaded.install.wait_ready_stage2_secs == 900
    assert loaded.install.watchdog_probe_debounce_secs == [2, 5]


def test_config_save_load_install_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    cfg = Config()
    cfg.install.agent_first = True
    cfg.install.wait_ready_stage2_secs = 1200
    cfg.install.wait_ready_stage3_secs = 2400
    cfg.install.auto_resume = False
    cfg.install.watchdog_max_respawns = 5
    cfg.install.watchdog_probe_debounce_count = 3
    cfg.install.watchdog_probe_debounce_secs = [1, 2, 4]
    cfg.save()

    loaded = Config.load()
    assert loaded.install.agent_first is True
    assert loaded.install.wait_ready_stage2_secs == 1200
    assert loaded.install.wait_ready_stage3_secs == 2400
    assert loaded.install.auto_resume is False
    assert loaded.install.watchdog_max_respawns == 5
    assert loaded.install.watchdog_probe_debounce_count == 3
    assert loaded.install.watchdog_probe_debounce_secs == [1, 2, 4]


def test_config_load_revalidates_install_values(tmp_path, monkeypatch):
    """A hand-edited TOML with bad values must coerce, not crash."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[install]\n"
        'agent_first = "maybe"\n'
        "wait_ready_stage2_secs = -5\n"
        "wait_ready_stage3_secs = 99999\n"
        "watchdog_max_respawns = -10\n"
        "watchdog_probe_debounce_count = 0\n"
        "watchdog_probe_debounce_secs = [-1, 2]\n",
        encoding="utf-8",
    )

    loaded = Config.load()
    # `bool("maybe")` is True — what matters is we don't raise.
    assert isinstance(loaded.install.agent_first, bool)
    assert loaded.install.wait_ready_stage2_secs == 60
    assert loaded.install.wait_ready_stage3_secs == 14400
    assert loaded.install.watchdog_max_respawns == 0
    assert loaded.install.watchdog_probe_debounce_count == 1
    assert loaded.install.watchdog_probe_debounce_secs == [2, 5]


def test_config_load_install_secs_string_in_toml_falls_back(tmp_path, monkeypatch):
    """``watchdog_probe_debounce_secs = "oops"`` must not crash."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = Config.path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '[install]\nwatchdog_probe_debounce_secs = "oops"\n',
        encoding="utf-8",
    )

    loaded = Config.load()
    assert loaded.install.watchdog_probe_debounce_secs == [2, 5]
