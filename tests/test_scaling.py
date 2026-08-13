# SPDX-License-Identifier: MIT
"""Tests for DPI scaling detection."""

from winpodx.display.scaling import detect_scale_factor


def test_scale_factor_returns_valid():
    result = detect_scale_factor()
    assert result in (100, 140, 180)


def test_env_scale_gdk(monkeypatch):
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "2")
    assert _env_scale() == 2.0


def test_env_scale_qt(monkeypatch):
    from winpodx.display.scaling import _env_scale

    monkeypatch.delenv("GDK_SCALE", raising=False)
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.5")
    assert _env_scale() == 1.5


def test_env_scale_fallback(monkeypatch):
    from winpodx.display.scaling import _env_scale

    for var in ("GDK_SCALE", "QT_SCALE_FACTOR", "ELM_SCALE"):
        monkeypatch.delenv(var, raising=False)
    assert _env_scale() == 1.0


def test_env_scale_zero_guard(monkeypatch):
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "0")
    assert _env_scale() == 1.0


def test_env_scale_negative_guard(monkeypatch):
    from winpodx.display.scaling import _env_scale

    monkeypatch.setenv("GDK_SCALE", "-1")
    assert _env_scale() == 1.0


def test_xrdb_zero_dpi_guard(monkeypatch):
    import subprocess

    from winpodx.display.scaling import _xrdb_scale

    def mock_run(*args, **kwargs):
        result = subprocess.CompletedProcess(args[0], 0)
        result.stdout = "Xft.dpi:\t0\n"
        return result

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert _xrdb_scale() == 1.0


def test_xrdb_valid_dpi(monkeypatch):
    import subprocess

    from winpodx.display.scaling import _xrdb_scale

    def mock_run(*args, **kwargs):
        result = subprocess.CompletedProcess(args[0], 0)
        result.stdout = "Xft.dpi:\t192\n"
        return result

    monkeypatch.setattr(subprocess, "run", mock_run)
    assert _xrdb_scale() == 2.0


# Audit Issue 15: Wayland multi-monitor scale picks MAX, not focused


def test_wayland_sway_returns_max_scale(monkeypatch):
    # sway with 1x external + 2x internal must return 2.0.
    import json
    import subprocess

    from winpodx.display import scaling as scaling_mod

    monkeypatch.setattr(scaling_mod, "_qt_max_device_pixel_ratio", lambda: None)

    outputs = [
        {
            "name": "HDMI-A-1",
            "active": True,
            "focused": True,
            "scale": 1.0,
        },
        {
            "name": "eDP-1",
            "active": True,
            "focused": False,
            "scale": 2.0,
        },
    ]

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "swaymsg":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(outputs))
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(scaling_mod.subprocess, "run", fake_run)
    assert scaling_mod._wayland_compositor_scale() == 2.0


def test_wayland_hyprland_returns_max_scale(monkeypatch):
    # hyprland: max across monitors, not focused.
    import json
    import subprocess

    from winpodx.display import scaling as scaling_mod

    monkeypatch.setattr(scaling_mod, "_qt_max_device_pixel_ratio", lambda: None)

    def fake_run(cmd, **_kwargs):
        if cmd[0] == "swaymsg":
            raise FileNotFoundError(cmd[0])
        if cmd[0] == "hyprctl":
            monitors = [
                {"name": "HDMI-A-1", "focused": True, "scale": 1.0},
                {"name": "eDP-1", "focused": False, "scale": 1.5},
            ]
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(monitors))
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(scaling_mod.subprocess, "run", fake_run)
    assert scaling_mod._wayland_compositor_scale() == 1.5


def test_wayland_prefers_qt_when_available(monkeypatch):
    # Qt DPR wins over swaymsg/hyprctl parsing.
    from winpodx.display import scaling as scaling_mod

    monkeypatch.setattr(scaling_mod, "_qt_max_device_pixel_ratio", lambda: 1.25)

    def boom(_cmd, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("subprocess.run must not be called when Qt answers")

    monkeypatch.setattr(scaling_mod.subprocess, "run", boom)
    assert scaling_mod._wayland_compositor_scale() == 1.25


def test_wayland_fallback_when_everything_missing(monkeypatch):
    # No Qt, no swaymsg, no hyprctl -> 1.0.
    from winpodx.display import scaling as scaling_mod

    monkeypatch.setattr(scaling_mod, "_qt_max_device_pixel_ratio", lambda: None)

    def fake_run(cmd, **_kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(scaling_mod.subprocess, "run", fake_run)
    assert scaling_mod._wayland_compositor_scale() == 1.0


def test_scale_factor_thresholds(monkeypatch):
    from winpodx.display import scaling

    monkeypatch.setattr(scaling, "detect_raw_scale", lambda: 1.3)
    assert scaling.detect_scale_factor() == 140
    monkeypatch.setattr(scaling, "detect_raw_scale", lambda: 1.7)
    assert scaling.detect_scale_factor() == 180
    monkeypatch.setattr(scaling, "detect_raw_scale", lambda: 1.29)
    assert scaling.detect_scale_factor() == 100


def test_detect_raw_scale_routes_each_desktop(monkeypatch):
    from winpodx.display import scaling

    probes = {
        "gnome": ("_gnome_scale", 1.25),
        "kde": ("_kde_scale", 1.5),
        "sway": ("_wayland_compositor_scale", 1.75),
        "hyprland": ("_wayland_compositor_scale", 2.0),
        "cinnamon": ("_cinnamon_scale", 1.2),
    }
    for desktop, (probe, expected) in probes.items():
        monkeypatch.setattr(scaling, "desktop_environment", lambda value=desktop: value)
        monkeypatch.setattr(scaling, probe, lambda value=expected: value)
        assert scaling.detect_raw_scale() == expected


def test_detect_raw_scale_unknown_prefers_environment(monkeypatch):
    from winpodx.display import scaling

    monkeypatch.setattr(scaling, "desktop_environment", lambda: "xfce")
    monkeypatch.setattr(scaling, "_env_scale", lambda: 1.6)
    monkeypatch.setattr(
        scaling,
        "_xrdb_scale",
        lambda: (_ for _ in ()).throw(AssertionError("xrdb must be the last fallback")),
    )
    assert scaling.detect_raw_scale() == 1.6


def test_gnome_combines_integer_and_text_scale(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    responses = iter(("uint32 2\n", "1.25\n"))

    def fake_run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=next(responses))

    monkeypatch.setattr(scaling.subprocess, "run", fake_run)
    assert scaling._gnome_scale() == 2.5


def test_gnome_invalid_values_fall_back(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    def fake_run(argv, **_kwargs):
        if argv[-1] == "scaling-factor":
            return subprocess.CompletedProcess(argv, 0, stdout="invalid")
        raise subprocess.TimeoutExpired(argv, 5)

    monkeypatch.setattr(scaling.subprocess, "run", fake_run)
    assert scaling._gnome_scale() == 1.0


def test_kde_tries_plasma_six_then_five(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    calls = []

    def fake_run(argv, **_kwargs):
        calls.append(argv[0])
        if argv[0] == "kreadconfig6":
            raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(argv, 0, stdout="1.75\n")

    monkeypatch.setattr(scaling.subprocess, "run", fake_run)
    assert scaling._kde_scale() == 1.75
    assert calls == ["kreadconfig6", "kreadconfig5"]


def test_kde_uses_qt_environment_after_empty_commands(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    monkeypatch.setenv("QT_SCALE_FACTOR", "1.4")
    monkeypatch.setattr(
        scaling.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout=""),
    )
    assert scaling._kde_scale() == 1.4


def test_cinnamon_parses_uint32_and_rejects_zero(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    monkeypatch.setattr(
        scaling.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout="uint32 2\n"),
    )
    assert scaling._cinnamon_scale() == 2.0
    monkeypatch.setattr(
        scaling.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout="uint32 0\n"),
    )
    assert scaling._cinnamon_scale() == 1.0


def test_env_scale_skips_invalid_value_for_next_source(monkeypatch):
    from winpodx.display import scaling

    monkeypatch.setenv("GDK_SCALE", "not-a-number")
    monkeypatch.setenv("QT_SCALE_FACTOR", "1.25")
    assert scaling._env_scale() == 1.25


def test_xrdb_missing_or_invalid_dpi_falls_back(monkeypatch):
    import subprocess

    from winpodx.display import scaling

    monkeypatch.setattr(
        scaling.subprocess,
        "run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, stdout="Xft.dpi: invalid\n"),
    )
    assert scaling._xrdb_scale() == 1.0
