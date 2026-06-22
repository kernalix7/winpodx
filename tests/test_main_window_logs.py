# SPDX-License-Identifier: MIT
"""Tests for the Logs-tab diagnostics buttons (LogsMixin).

Regression coverage for the bug where the Terminal-tab quick buttons
("Status" / "Pod logs" / "Inspect") hardcoded ``podman`` and so fired
``podman ...`` even when the user had selected the Docker backend.

The command construction lives in the pure ``_diagnostic_commands`` /
``_backend_cli`` helpers so it can be exercised headlessly — only the
module import needs Qt (the LogsMixin module imports PySide6 widgets at
top level), hence the importorskip.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from winpodx.core.config import Config  # noqa: E402
from winpodx.gui._main_window_logs import LogsMixin  # noqa: E402


class Harness(LogsMixin):
    """Bare host exposing only what the diagnostics helpers read."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg


def _cfg(backend: str, container: str = "winpodx-windows") -> Config:
    cfg = Config()
    cfg.pod.backend = backend
    cfg.pod.container_name = container
    return cfg


# ----- _backend_cli ------------------------------------------------------


def test_backend_cli_podman():
    assert Harness(_cfg("podman"))._backend_cli() == "podman"


def test_backend_cli_docker():
    assert Harness(_cfg("docker"))._backend_cli() == "docker"


def test_backend_cli_manual_falls_back_to_podman():
    # manual / raw-RDP has no container CLI; fall back to podman (inert).
    assert Harness(_cfg("manual"))._backend_cli() == "podman"


def test_backend_cli_unexpected_falls_back_to_podman():
    assert Harness(_cfg("libvirt"))._backend_cli() == "podman"


# ----- _diagnostic_commands honours the backend --------------------------


def _container_cmds(harness: Harness) -> list[list[str]]:
    """The list-shaped (shelled-out) commands among the quick buttons."""
    return [cmd for _label, cmd in harness._diagnostic_commands() if isinstance(cmd, list)]


def test_diagnostic_commands_use_docker_when_docker_backend():
    cmds = _container_cmds(Harness(_cfg("docker")))
    assert cmds, "expected at least one container command"
    # Every shelled-out container command must target docker, never podman.
    assert all(cmd[0] == "docker" for cmd in cmds)
    assert not any(cmd[0] == "podman" for cmd in cmds)


def test_diagnostic_commands_use_podman_when_podman_backend():
    cmds = _container_cmds(Harness(_cfg("podman")))
    assert cmds
    assert all(cmd[0] == "podman" for cmd in cmds)


def test_diagnostic_commands_cover_status_logs_inspect():
    """The three container probes are present and shaped as expected."""
    harness = Harness(_cfg("docker", container="my-win"))
    by_label = {label: cmd for label, cmd in harness._diagnostic_commands()}
    assert by_label["Status"] == ["docker", "ps", "-a", "--filter", "name=my-win"]
    assert by_label["Pod logs"] == ["docker", "logs", "--tail", "100", "my-win"]
    assert by_label["Inspect"] == ["docker", "inspect", "my-win"]
    # Non-command entries are preserved for the caller's signal wiring.
    assert by_label["App log"] == "tail_app_log"
    assert by_label["RDP Test"] is None
    assert by_label["Clear"] is None


def test_diagnostic_commands_track_renamed_container():
    cmds = _container_cmds(Harness(_cfg("podman", container="renamed-pod")))
    # The name appears either as a bare arg (logs / inspect) or inside the
    # ps --filter value (name=renamed-pod), so match against the joined line.
    assert all("renamed-pod" in " ".join(cmd) for cmd in cmds)
