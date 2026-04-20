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
