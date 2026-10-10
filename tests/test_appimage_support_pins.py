# SPDX-License-Identifier: MIT
"""Native support-package pins stay installed and recorded alongside FreeRDP."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packaging/appimage"))

import provenance_pins as pins  # noqa: E402


def test_support_pin_is_recorded_when_build_inputs_are_serialized() -> None:
    # Given: the binary version whose exact OpenSSL source is available.
    expected = [{"package": "libssl3t64", "version": "3.0.13-0ubuntu3.16"}]
    # When: build inputs are serialized for provenance.
    inputs = pins.as_dict()
    # Then: support pins are explicit, rather than inherited from the base image.
    assert inputs.get("support_dpkg") == expected


def test_support_pin_is_installed_when_native_workflow_runs() -> None:
    # Given: the native stage's actual apt install command.
    workflow = (REPO_ROOT / ".github/workflows/appimage-publish.yml").read_text("utf-8")
    native = workflow.split("- name: Bundle FreeRDP via pinned Ubuntu 24.04", 1)[1]
    install = native.split("apt-get install -y --no-install-recommends", 1)[1].split(
        "python3 binutils", 1
    )[0]
    # When: exact package/version arguments are extracted from that command.
    installed = dict(re.findall(r"([\w-]+)=([^\s\\]+)", install))
    # Then: the available support pin is installed, in lockstep with recorded inputs.
    assert installed.get("libssl3t64") == "3.0.13-0ubuntu3.16"
    assert {pin.package: pin.version for pin in pins.SUPPORT_DPKG_PINS} == {
        "libssl3t64": installed["libssl3t64"]
    }


def test_freerdp_pins_are_preserved_when_support_packages_are_separate() -> None:
    # Given: the original five FreeRDP package identities.
    expected = {
        "freerdp3-x11",
        "freerdp3-wayland",
        "libfreerdp3-3",
        "libfreerdp-client3-3",
        "libwinpr3-3",
    }
    # When: the FreeRDP pin set is read.
    actual = {pin.package: pin.version for pin in pins.FREERDP_DPKG_PINS}
    # Then: no support package changes its semantics or FreeRDP 3.32.1 version.
    assert actual == {package: "3.32.1+dfsg-0ubuntu0.24.04.1" for package in expected}
