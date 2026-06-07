# SPDX-License-Identifier: MIT
"""`winpodx disguise build-image` — host-derived patched-QEMU image (#246)."""

from __future__ import annotations

import argparse


def test_build_image_uses_host_values_and_sets_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    Config().save()  # seed (podman backend default)

    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")

    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    monkeypatch.setattr(d, "_host_dmi", lambda n: "LENOVO" if n == "sys_vendor" else "")
    monkeypatch.setattr(d, "_host_disk_model", lambda: "Samsung SSD 990")
    monkeypatch.setattr(d, "_qemu_version", lambda backend, image: "10.0.8")

    captured: dict = {}
    monkeypatch.setattr(
        d.subprocess, "call", lambda cmd: captured.setdefault("cmd", cmd) and 0 or 0
    )

    d.handle_disguise(argparse.Namespace(disguise_command="build-image"))

    joined = " ".join(captured["cmd"])
    assert "build" in captured["cmd"]
    assert "ACPI_OEM6=LENOVO" in joined  # host vendor, not a fixed ASUS
    assert "DISK_MODEL=Samsung SSD 990" in joined  # host disk, not a fixed model
    assert "QEMU_VERSION=10.0.8" in joined
    assert Config.load().pod.disguise_image == "winpodx-windows-disguise"


def test_build_image_aborts_without_recipe(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    import pytest

    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    Config().save()
    monkeypatch.setattr(d, "_recipe_dir", lambda: None)
    called = {"n": 0}
    monkeypatch.setattr(d.subprocess, "call", lambda cmd: called.__setitem__("n", 1) or 0)

    with pytest.raises(SystemExit):
        d.handle_disguise(argparse.Namespace(disguise_command="build-image"))
    assert called["n"] == 0  # never shelled out to a build
