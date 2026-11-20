# SPDX-License-Identifier: MIT
"""#902: re-launching the full desktop must activate the live FreeRDP window.

These tests pin the consumer-visible behaviour: a tracked full-desktop session
is focused by exact PID ownership, never by a guessed title/class, and a failed
activation leaves the session marker intact (no silent duplicate spawn) while
surfacing a warning and a desktop notification.
"""

from __future__ import annotations

import fcntl
import logging
import os
import subprocess
from pathlib import Path

import pytest

from winpodx.core import rdp as rdp_mod
from winpodx.core.config import Config
from winpodx.core.rdp import RDPSession
from winpodx.display.x11 import X11Environment

WMCTRL = "/usr/bin/wmctrl"
XDOTOOL = "/usr/bin/xdotool"
TOOL_ENV = {"DISPLAY": ":7", "XAUTHORITY": "/tmp/winpodx-test-xauth"}


class _Result:
    """Stand-in for ``subprocess.CompletedProcess`` from a tool invocation."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _Tools:
    """Deterministic wmctrl/xdotool double that records every invocation."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.wmctrl_list = _Result()
        self.wmctrl_activate = _Result()
        self.xdotool_search = _Result()
        self.xdotool_activate = _Result()
        self.raise_wmctrl_list: Exception | None = None
        self.raise_xdotool_search: Exception | None = None
        self.raise_xdotool_activate: Exception | None = None

    def run(self, cmd, **kwargs):
        argv = [str(part) for part in cmd]
        self.calls.append((argv, dict(kwargs)))
        base = os.path.basename(argv[0])
        if base == "wmctrl" and argv[1:] == ["-lp"]:
            if self.raise_wmctrl_list is not None:
                raise self.raise_wmctrl_list
            return self.wmctrl_list
        if base == "wmctrl" and argv[1:3] == ["-i", "-a"]:
            return self.wmctrl_activate
        if base == "xdotool" and argv[1:4] == ["search", "--onlyvisible", "--pid"]:
            if self.raise_xdotool_search is not None:
                raise self.raise_xdotool_search
            return self.xdotool_search
        if base == "xdotool" and argv[1:2] == ["windowactivate"]:
            if self.raise_xdotool_activate is not None:
                raise self.raise_xdotool_activate
            return self.xdotool_activate
        raise AssertionError(f"unexpected tool invocation: {argv}")

    def commands(self) -> list[list[str]]:
        return [argv for argv, _kwargs in self.calls]


def _write_pid(pid_dir: Path, app_name: str, contents: str) -> Path:
    pid_file = pid_dir / f"{app_name}.cproc"
    pid_file.write_text(contents)
    return pid_file


def _capture_notifications(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    import winpodx.desktop.notify as notify_mod

    sent: list[tuple[str, str]] = []

    def fake(title: str, body: str, *_args, **_kwargs) -> None:
        sent.append((title, body))

    monkeypatch.setattr(notify_mod, "send_notification", fake)
    return sent


def _patch_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tools: _Tools,
    *,
    accepted_pids: set[int] | frozenset[int],
    env: dict[str, str] | None = None,
    wmctrl: str | None = WMCTRL,
    xdotool: str | None = None,
) -> tuple[X11Environment, list[tuple[str, str]]]:
    """Isolate runtime dir, X11 resolution, PID validation and tool probes."""

    def runtime_dir() -> Path:
        return tmp_path

    monkeypatch.setattr(rdp_mod, "runtime_dir", runtime_dir)
    monkeypatch.setattr("winpodx.core.process.runtime_dir", runtime_dir)

    resolved = X11Environment(env=dict(TOOL_ENV if env is None else env))

    def resolve_env(*_args, **_kwargs) -> X11Environment:
        return resolved

    monkeypatch.setattr(rdp_mod, "resolve_x11_environment", resolve_env)
    monkeypatch.setattr("winpodx.display.x11.resolve_x11_environment", resolve_env)

    monkeypatch.setattr(rdp_mod.subprocess, "run", tools.run)
    binaries = {"wmctrl": wmctrl, "xdotool": xdotool, "notify-send": None}

    def which(name: str) -> str | None:
        return binaries.get(name)

    monkeypatch.setattr(rdp_mod.shutil, "which", which)

    accepted = set(accepted_pids)

    def is_freerdp_pid(pid: int) -> bool:
        return pid in accepted

    monkeypatch.setattr("winpodx.core.process.is_freerdp_pid", is_freerdp_pid)

    return resolved, _capture_notifications(monkeypatch)


def _refuse_spawn(*_args, **_kwargs):
    raise AssertionError("the existing desktop must not spawn another FreeRDP client")


# --- _focus_existing_desktop: exact-PID window ownership -------------------


def test_focus_activates_first_window_owned_by_exact_pid(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result(
        "0x00200003  0 22220 host xfreerdp\n"
        "0x00400007  0 2222  host xfreerdp\n"
        "0x00600009  0 3333  host desktop 2222 - xfreerdp\n"
        "0x0080000b  0 2222  host second xfreerdp window\n"
    )
    _, notifications = _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=XDOTOOL
    )
    marker = _write_pid(tmp_path, "desktop", "2222")
    unrelated_marker = _write_pid(tmp_path, "notepad", "9999")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [WMCTRL, "-i", "-a", "0x00400007"],
    ]
    assert marker.read_text() == "2222"
    assert unrelated_marker.read_text() == "9999"
    assert notifications == []


def test_focus_skips_malformed_window_ids_until_owned_hex_window(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result(
        "0xnope  0 2222 host xfreerdp\n12345   0 2222 host xfreerdp\n0x00AF  0 2222 host xfreerdp\n"
    )
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222})
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [WMCTRL, "-i", "-a", "0x00AF"],
    ]


# --- _focus_existing_desktop: tool fallback and failure paths --------------


def test_focus_falls_back_to_xdotool_when_wmctrl_missing(monkeypatch, tmp_path):
    tools = _Tools()
    tools.xdotool_search = _Result("abc\n-3\n0\n111\n2222\n")
    _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, wmctrl=None, xdotool=XDOTOOL
    )
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [XDOTOOL, "search", "--onlyvisible", "--pid", "2222"],
        [XDOTOOL, "windowactivate", "111"],
    ]
    assert marker.read_text() == "2222"


def test_focus_falls_back_to_xdotool_when_wmctrl_listing_fails(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result("0x0031  0 2222 host xfreerdp\n", returncode=1)
    tools.xdotool_search = _Result("777\n")
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=XDOTOOL)
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [XDOTOOL, "search", "--onlyvisible", "--pid", "2222"],
        [XDOTOOL, "windowactivate", "777"],
    ]


def test_focus_falls_back_to_xdotool_when_wmctrl_listing_errors(monkeypatch, tmp_path):
    tools = _Tools()
    tools.raise_wmctrl_list = OSError("wmctrl could not run")
    tools.xdotool_search = _Result("777\n")
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=XDOTOOL)
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [XDOTOOL, "search", "--onlyvisible", "--pid", "2222"],
        [XDOTOOL, "windowactivate", "777"],
    ]


def test_focus_falls_back_to_xdotool_when_wmctrl_owns_no_window(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result("0x0041  0 9999 host xfreerdp\n")
    tools.xdotool_search = _Result("777\n")
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=XDOTOOL)
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    commands = tools.commands()
    assert commands[0] == [WMCTRL, "-lp"]
    assert [XDOTOOL, "windowactivate", "777"] in commands
    assert not any(argv[1:3] == ["-i", "-a"] for argv in commands)


def test_focus_falls_back_to_xdotool_when_wmctrl_activation_fails(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result("0x0031  0 2222 host xfreerdp\n")
    tools.wmctrl_activate = _Result(returncode=1)
    tools.xdotool_search = _Result("888\n")
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=XDOTOOL)
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is True

    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [WMCTRL, "-i", "-a", "0x0031"],
        [XDOTOOL, "search", "--onlyvisible", "--pid", "2222"],
        [XDOTOOL, "windowactivate", "888"],
    ]


def test_focus_returns_false_when_wmctrl_activation_fails_without_xdotool(monkeypatch, tmp_path):
    tools = _Tools()
    tools.wmctrl_list = _Result("0x0031  0 2222 host xfreerdp\n")
    tools.wmctrl_activate = _Result(returncode=1)
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222}, xdotool=None)
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert marker.read_text() == "2222"


def test_focus_returns_false_without_activation_tools(monkeypatch, tmp_path):
    tools = _Tools()
    _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, wmctrl=None, xdotool=None
    )
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert tools.calls == []
    assert marker.read_text() == "2222"


@pytest.mark.parametrize("env", [{"DISPLAY": ""}, {"XAUTHORITY": "/tmp/xauth-only"}])
def test_focus_requires_display_before_probing_tools(monkeypatch, tmp_path, env):
    tools = _Tools()
    _patch_dependencies(
        monkeypatch,
        tmp_path,
        tools,
        accepted_pids={2222},
        env=env,
        wmctrl=WMCTRL,
        xdotool=XDOTOOL,
    )
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert tools.calls == []
    assert marker.read_text() == "2222"


@pytest.mark.parametrize("contents", ["not-a-pid", "-7", "0"])
def test_focus_rejects_malformed_or_nonpositive_pid_without_losing_marker(
    monkeypatch, tmp_path, contents
):
    tools = _Tools()
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={2222})
    marker = _write_pid(tmp_path, "desktop", contents)

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert tools.calls == []
    assert marker.read_text() == contents


def test_focus_rejects_dead_freerdp_pid_without_losing_marker(monkeypatch, tmp_path):
    tools = _Tools()
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids=set())
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert tools.calls == []
    assert marker.read_text() == "2222"


def test_focus_survives_xdotool_search_error(monkeypatch, tmp_path):
    tools = _Tools()
    tools.raise_xdotool_search = subprocess.SubprocessError("xdotool crashed")
    _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, wmctrl=None, xdotool=XDOTOOL
    )
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert tools.commands() == [
        [XDOTOOL, "search", "--onlyvisible", "--pid", "2222"],
    ]
    assert marker.read_text() == "2222"


@pytest.mark.parametrize("stdout", ["", "notanid\n-4\n0\n"])
def test_focus_ignores_malformed_xdotool_window_ids(monkeypatch, tmp_path, stdout):
    tools = _Tools()
    tools.xdotool_search = _Result(stdout)
    _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, wmctrl=None, xdotool=XDOTOOL
    )
    _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    commands = tools.commands()
    assert commands == [[XDOTOOL, "search", "--onlyvisible", "--pid", "2222"]]
    assert not any(argv[1:2] == ["windowactivate"] for argv in commands)


def test_focus_reports_failure_when_xdotool_activation_errors(monkeypatch, tmp_path):
    tools = _Tools()
    tools.xdotool_search = _Result("4242\n")
    tools.raise_xdotool_activate = OSError("window vanished")
    _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={2222}, wmctrl=None, xdotool=XDOTOOL
    )
    marker = _write_pid(tmp_path, "desktop", "2222")

    assert rdp_mod._focus_existing_desktop(RDPSession(app_name="desktop")) is False

    assert marker.read_text() == "2222"


# --- launch_app integration: real session discovery + marker -------------


@pytest.fixture()
def live_pid():
    """Use the live pytest worker PID; only the FreeRDP identity check is stubbed."""
    return os.getpid()


def test_relaunching_desktop_activates_tracked_window(monkeypatch, tmp_path, live_pid):
    tools = _Tools()
    tools.wmctrl_list = _Result(f"0x00300005  0 {live_pid}  host xfreerdp\n")
    _, notifications = _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={live_pid})
    marker = _write_pid(tmp_path, "desktop", str(live_pid))
    monkeypatch.setattr(rdp_mod, "build_rdp_command", _refuse_spawn)
    monkeypatch.setattr(rdp_mod, "_spawn_detached", _refuse_spawn)

    cfg = Config()
    cfg.pod.backend = "manual"
    session = rdp_mod.launch_app(cfg)

    assert session.app_name == "desktop"
    assert session.process is None
    assert tools.commands() == [
        [WMCTRL, "-lp"],
        [WMCTRL, "-i", "-a", "0x00300005"],
    ]
    assert marker.read_text() == str(live_pid)
    assert notifications == []


def test_failed_desktop_activation_keeps_session_and_notifies(
    monkeypatch, tmp_path, live_pid, caplog
):
    tools = _Tools()
    tools.wmctrl_list = _Result(f"0x00300005  0 {live_pid}  host xfreerdp\n")
    tools.wmctrl_activate = _Result(returncode=1)
    _, notifications = _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={live_pid}, xdotool=None
    )
    marker = _write_pid(tmp_path, "desktop", str(live_pid))
    monkeypatch.setattr(rdp_mod, "build_rdp_command", _refuse_spawn)
    monkeypatch.setattr(rdp_mod, "_spawn_detached", _refuse_spawn)

    cfg = Config()
    cfg.pod.backend = "manual"
    with caplog.at_level(logging.WARNING, logger="winpodx.core.rdp"):
        session = rdp_mod.launch_app(cfg)

    assert session.app_name == "desktop"
    assert session.process is None
    assert marker.read_text() == str(live_pid)
    assert len(notifications) == 1
    assert (
        sum(
            record.name == rdp_mod.__name__ and record.levelno == logging.WARNING
            for record in caplog.records
        )
        == 1
    )


def test_lock_contention_reuse_focuses_desktop(monkeypatch, tmp_path, live_pid):
    tools = _Tools()
    tools.wmctrl_list = _Result(f"0x00300005  0 {live_pid}  host xfreerdp\n")
    _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={live_pid})
    marker = _write_pid(tmp_path, "desktop", str(live_pid))

    held = os.open(marker, os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    real_find = rdp_mod._find_existing_session
    lookups = {"count": 0}

    def racing_find(app_name: str):
        lookups["count"] += 1
        if lookups["count"] == 1:
            # The concurrent launch materialised while our own check ran, but
            # before _spawn_detached tried to take the lock.
            return None
        return real_find(app_name)

    monkeypatch.setattr(rdp_mod, "_find_existing_session", racing_find)
    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rdp_mod, "build_rdp_command", lambda *_a, **_k: (["xfreerdp3", "/v:127.0.0.1"], "")
    )
    monkeypatch.setattr(rdp_mod.subprocess, "Popen", _refuse_spawn)

    cfg = Config()
    cfg.pod.backend = "manual"
    try:
        session = rdp_mod.launch_app(cfg)
    finally:
        os.close(held)

    assert session.app_name == "desktop"
    assert session.process is None
    assert [WMCTRL, "-i", "-a", "0x00300005"] in tools.commands()
    assert marker.read_text() == str(live_pid)


def test_lock_contention_activation_failure_keeps_marker_and_notifies(
    monkeypatch, tmp_path, live_pid, caplog
):
    tools = _Tools()
    tools.wmctrl_list = _Result(f"0x00300005  0 {live_pid}  host xfreerdp\n")
    tools.wmctrl_activate = _Result(returncode=1)
    _, notifications = _patch_dependencies(
        monkeypatch, tmp_path, tools, accepted_pids={live_pid}, xdotool=None
    )
    marker = _write_pid(tmp_path, "desktop", str(live_pid))

    held = os.open(marker, os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    real_find = rdp_mod._find_existing_session
    lookups = {"count": 0}

    def racing_find(app_name: str):
        lookups["count"] += 1
        if lookups["count"] == 1:
            return None
        return real_find(app_name)

    monkeypatch.setattr(rdp_mod, "_find_existing_session", racing_find)
    monkeypatch.setattr(rdp_mod, "find_freerdp", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rdp_mod, "build_rdp_command", lambda *_a, **_k: (["xfreerdp3", "/v:127.0.0.1"], "")
    )
    monkeypatch.setattr(rdp_mod.subprocess, "Popen", _refuse_spawn)

    cfg = Config()
    cfg.pod.backend = "manual"
    try:
        with caplog.at_level(logging.WARNING, logger="winpodx.core.rdp"):
            session = rdp_mod.launch_app(cfg)
    finally:
        os.close(held)

    assert session.app_name == "desktop"
    assert session.process is None
    assert marker.read_text() == str(live_pid)
    assert len(notifications) == 1
    assert (
        sum(
            record.name == rdp_mod.__name__ and record.levelno == logging.WARNING
            for record in caplog.records
        )
        == 1
    )


def test_remoteapp_reuse_does_not_request_desktop_focus(monkeypatch, tmp_path, live_pid):
    # Only a full-desktop reuse is focused; RemoteApp relaunches keep their
    # existing no-op reuse behaviour.
    tools = _Tools()
    _, notifications = _patch_dependencies(monkeypatch, tmp_path, tools, accepted_pids={live_pid})
    marker = _write_pid(tmp_path, "notepad", str(live_pid))
    monkeypatch.setattr(rdp_mod, "build_rdp_command", _refuse_spawn)
    monkeypatch.setattr(rdp_mod, "_spawn_detached", _refuse_spawn)

    cfg = Config()
    cfg.pod.backend = "manual"
    session = rdp_mod.launch_app(cfg, app_executable="notepad.exe")

    assert session.app_name == "notepad"
    assert tools.calls == []
    assert notifications == []
    assert marker.read_text() == str(live_pid)
