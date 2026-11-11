# SPDX-License-Identifier: MIT
from __future__ import annotations

from unittest.mock import patch

from winpodx.core.config import Config
from winpodx.core.deps_quickcheck import collect_first_run_checks
from winpodx.display.x11 import X11Environment
from winpodx.utils.deps import DepCheck


def test_collect_first_run_checks_isolates_each_probe_failure() -> None:
    cfg = Config()

    with (
        patch("winpodx.core.deps_quickcheck.shutil.which", side_effect=OSError("path broken")),
        patch("winpodx.utils.deps.check_freerdp", side_effect=RuntimeError("probe failed")),
        patch(
            "winpodx.display.x11.resolve_x11_environment", side_effect=RuntimeError("x11 failed")
        ),
        patch("winpodx.core.pod.pod_status", side_effect=RuntimeError("runtime failed")),
        patch("winpodx.core.pod.check_rdp_port", side_effect=OSError("socket failed")),
        patch("winpodx.core.app.list_available_apps", side_effect=OSError("apps failed")),
    ):
        result = collect_first_run_checks(cfg)

    assert result == {
        "backend": "unknown",
        "freerdp": "unknown",
        "x11": "unknown",
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
        patch(
            "winpodx.display.x11.resolve_x11_environment",
            return_value=X11Environment({}, usable=False, problem="missing"),
        ),
        patch("winpodx.core.pod.pod_status", return_value=Status()),
        patch("winpodx.core.pod.check_rdp_port", return_value=False),
        patch("winpodx.core.app.list_available_apps", return_value=[]),
    ):
        result = collect_first_run_checks(cfg)

    assert result["pod_state"] == "custom-state"
