# SPDX-License-Identifier: MIT
"""Configured display recovery must not redirect or leak a caller's session."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from winpodx.display import x11


@pytest.fixture
def manager(monkeypatch):
    """A same-user D-Bus environment, distinct from the launching process."""
    state = {
        "values": ["DISPLAY=:21", "XAUTHORITY=/session/auth"],
        "probe": subprocess.CompletedProcess([], 0, "", ""),
    }
    monkeypatch.setattr(x11.shutil, "which", lambda *_args, **_kwargs: "/fake/busctl")

    def run(argv, **_kwargs):
        if argv[0] == sys.executable:
            return state["probe"]
        error = state.get("error")
        if error:
            raise error
        body = state.get("body", json.dumps({"type": "as", "data": state["values"]}))
        return subprocess.CompletedProcess(argv, state.get("returncode", 0), body, "")

    monkeypatch.setattr(x11.subprocess, "run", run)
    return state


def test_explicit_display_does_not_acquire_other_sessions_authority(manager):
    original = {"DISPLAY": ":7", "WAYLAND_DISPLAY": "wayland-test", "PATH": "/caller/bin"}

    result = x11.resolve_x11_environment(environ=original)

    assert result.env == original
    assert "XAUTHORITY" not in result.env
    assert not result.recovered


def test_recovery_whitelists_manager_values_without_mutating_the_caller(manager):
    manager["values"] += ["PATH=/other/bin", "AGENT_TOKEN=private", "LD_LIBRARY_PATH=/other/lib"]
    original = {"WAYLAND_DISPLAY": "wayland-test", "PATH": "/caller/bin"}
    process_before = dict(os.environ)

    result = x11.resolve_x11_environment(environ=original)

    assert result.env == {
        "WAYLAND_DISPLAY": "wayland-test",
        "PATH": "/caller/bin",
        "DISPLAY": ":21",
        "XAUTHORITY": "/session/auth",
    }
    assert original == {"WAYLAND_DISPLAY": "wayland-test", "PATH": "/caller/bin"}
    assert dict(os.environ) == process_before
    assert result.recovered


def test_explicit_authority_wins_when_only_display_needs_recovery(manager):
    result = x11.resolve_x11_environment(
        environ={"XDG_SESSION_TYPE": "wayland", "XAUTHORITY": "/caller/auth"}
    )

    assert result.env["DISPLAY"] == ":21"
    assert result.env["XAUTHORITY"] == "/caller/auth"


def test_registered_values_are_not_shell_expanded_or_split(manager):
    authority = '/session/$HOME/auth file="quoted"'
    manager["values"] = ["DISPLAY=unix/:21.0", f"XAUTHORITY={authority}"]

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"})

    assert result.env["DISPLAY"] == "unix/:21.0"
    assert result.env["XAUTHORITY"] == authority


def test_headless_context_does_not_open_the_users_desktop(manager):
    result = x11.resolve_x11_environment(environ={"XDG_SESSION_TYPE": "tty"}, probe=True)

    assert "DISPLAY" not in result.env
    assert result.usable is False
    assert result.problem == "missing"


def test_recovery_does_not_cache_an_initially_missing_bridge(manager):
    environment = {"WAYLAND_DISPLAY": "wayland-test", "DISPLAY": ""}
    manager["values"] = []
    before = x11.resolve_x11_environment(environ=environment)
    manager["values"] = ["DISPLAY=:22"]
    after = x11.resolve_x11_environment(environ=environment)

    assert before.problem == "missing"
    assert before.usable is False
    assert after.recovered
    assert after.env["DISPLAY"] == ":22"


def test_user_manager_network_display_is_not_automatically_selected(manager):
    manager["values"] = ["DISPLAY=other-host:0", "XAUTHORITY=/session/auth"]

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"})

    assert result.problem == "missing"
    assert "DISPLAY" not in result.env
    assert "XAUTHORITY" not in result.env


def test_unavailable_session_manager_keeps_missing_display_diagnostic(manager):
    manager["error"] = subprocess.TimeoutExpired("busctl", 1)

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"})

    assert result.usable is False
    assert result.problem == "missing"
    assert "DISPLAY" not in result.env


def test_session_manager_error_does_not_consume_its_output(manager):
    manager["returncode"] = 1

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"})

    assert result.usable is False
    assert "DISPLAY" not in result.env


def test_invalid_session_response_cannot_supply_a_display(manager):
    manager["body"] = json.dumps({"type": "s", "data": "DISPLAY=:21"})

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"})

    assert result.problem == "missing"
    assert "DISPLAY" not in result.env


def test_authentication_failure_is_not_reported_as_missing_xwayland(manager):
    manager["probe"] = subprocess.CompletedProcess([], 2, "", "Invalid MIT-MAGIC-COOKIE-1 key")

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"}, probe=True)

    assert result.usable is False
    assert result.problem == "authentication"
    assert result.env["DISPLAY"] == ":21"


def test_stale_display_is_not_reported_as_verified_access(manager):
    manager["probe"] = subprocess.CompletedProcess([], 2, "", "")

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"}, probe=True)

    assert result.usable is False
    assert result.problem == "unreachable"


def test_host_without_xlib_does_not_claim_authentication_is_verified(manager):
    manager["probe"] = subprocess.CompletedProcess([], 3, "", "")

    result = x11.resolve_x11_environment(environ={"WAYLAND_DISPLAY": "wayland-test"}, probe=True)

    assert result.usable is None
    assert result.problem == "unverified"
    assert result.env["DISPLAY"] == ":21"


def test_remote_explicit_display_is_preserved_but_not_network_probed(manager):
    # An attempted native probe would produce a verified result in this fixture.
    result = x11.resolve_x11_environment(environ={"DISPLAY": "localhost:10.0"}, probe=True)

    assert result.env["DISPLAY"] == "localhost:10.0"
    assert result.usable is None
    assert result.problem == "unverified"
