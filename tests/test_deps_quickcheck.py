# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import patch

from winpodx.core.config import Config
from winpodx.core.deps_quickcheck import collect_first_run_checks
from winpodx.core.pod.backend import PodState, PodStatus
from winpodx.utils.deps import DepCheck


def test_collect_first_run_checks_reports_present_dependencies_and_runtime_state() -> None:
    cfg = Config()
    cfg.pod.backend = "docker"
    cfg.rdp.ip = "127.0.0.9"
    cfg.rdp.port = 3391

    with (
        patch("winpodx.core.deps_quickcheck.shutil.which", return_value="/usr/bin/docker") as which,
        patch(
            "winpodx.utils.deps.check_freerdp",
            return_value=DepCheck("xfreerdp3", True, "/usr/bin/xfreerdp3"),
        ),
        patch(
            "winpodx.core.pod.pod_status",
            return_value=PodStatus(state=PodState.RUNNING),
        ),
        patch("winpodx.core.pod.check_rdp_port", return_value=True) as check_port,
        patch("winpodx.core.app.list_available_apps", return_value=["word", "excel"]),
    ):
        result = collect_first_run_checks(cfg)

    assert result == {
        "backend": "OK",
        "freerdp": "OK",
        "pod_state": "running",
        "rdp_port": "open at 127.0.0.9:3391",
        "apps_count": 2,
    }
    which.assert_called_once_with("docker")
    check_port.assert_called_once_with("127.0.0.9", 3391, timeout=1.0)


def test_collect_first_run_checks_reports_missing_dependencies_and_closed_port() -> None:
    cfg = Config()

    with (
        patch("winpodx.core.deps_quickcheck.shutil.which", return_value=None),
        patch(
            "winpodx.utils.deps.check_freerdp",
            return_value=DepCheck("xfreerdp", False),
        ),
        patch("winpodx.core.pod.pod_status", return_value=PodStatus(state=PodState.STOPPED)),
        patch("winpodx.core.pod.check_rdp_port", return_value=False),
        patch("winpodx.core.app.list_available_apps", return_value=[]),
    ):
        result = collect_first_run_checks(cfg)

    assert result["backend"] == "missing — install podman or change backend in Settings"
    assert result["freerdp"] == "missing — install freerdp 3+"
    assert result["pod_state"] == "stopped"
    assert result["rdp_port"] == "not reachable yet (Windows may still be booting on first install)"
    assert result["apps_count"] == 0


def test_collect_first_run_checks_isolates_each_probe_failure() -> None:
    cfg = Config()

    with (
        patch("winpodx.core.deps_quickcheck.shutil.which", side_effect=OSError("path broken")),
        patch("winpodx.utils.deps.check_freerdp", side_effect=RuntimeError("probe failed")),
        patch("winpodx.core.pod.pod_status", side_effect=RuntimeError("runtime failed")),
        patch("winpodx.core.pod.check_rdp_port", side_effect=OSError("socket failed")),
        patch("winpodx.core.app.list_available_apps", side_effect=OSError("apps failed")),
    ):
        result = collect_first_run_checks(cfg)

    assert result == {
        "backend": "unknown",
        "freerdp": "unknown",
        "pod_state": "unknown",
        "rdp_port": "unknown",
        "apps_count": "unknown",
    }


def test_collect_first_run_checks_stringifies_non_enum_pod_state() -> None:
    cfg = Config()

    class Status:
        state = "custom-state"

    with (
        patch("winpodx.core.deps_quickcheck.shutil.which", return_value="/usr/bin/podman"),
        patch(
            "winpodx.utils.deps.check_freerdp",
            return_value=DepCheck("xfreerdp3", True),
        ),
        patch("winpodx.core.pod.pod_status", return_value=Status()),
        patch("winpodx.core.pod.check_rdp_port", return_value=False),
        patch("winpodx.core.app.list_available_apps", return_value=[]),
    ):
        result = collect_first_run_checks(cfg)

    assert result["pod_state"] == "custom-state"
