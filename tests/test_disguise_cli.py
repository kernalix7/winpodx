# SPDX-License-Identifier: MIT
"""`winpodx disguise build-image` + the reusable build helpers (#246).

The build now streams via ``subprocess.Popen`` (so the GUI can surface
progress + cancel a long compile), and the GUI auto-builds the image when
the user switches to hardened mode.
"""

from __future__ import annotations

import argparse

import pytest


class _FakeProc:
    """Minimal Popen stand-in: records the cmd, streams a couple of lines."""

    def __init__(self, cmd, rc: int = 0, lines: list[str] | None = None) -> None:
        self.cmd = cmd
        self._rc = rc
        self.stdout = iter(lines if lines is not None else ["Step 1/5\n", "Step 5/5\n"])
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self) -> int:
        return self._rc


def _seed_host_values(monkeypatch, d) -> None:
    """Synthetic host values (NOT any real machine's) -- proves the command
    passes whatever the host reports without baking a real vendor into git."""
    monkeypatch.setattr(d, "_host_dmi", lambda n: "ACME" if n == "sys_vendor" else "")
    monkeypatch.setattr(d, "_host_disk_model", lambda: "ACME SSD 1TB")
    monkeypatch.setattr(d, "_qemu_version", lambda backend, image: "10.0.8")


def test_build_image_uses_host_values_and_sets_config(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    Config().save()  # seed (podman backend default)

    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    _seed_host_values(monkeypatch, d)

    captured: dict = {}

    def _fake_popen(cmd, **_kw):
        captured["cmd"] = cmd
        return _FakeProc(cmd)

    monkeypatch.setattr(d.subprocess, "Popen", _fake_popen)

    d.handle_disguise(argparse.Namespace(disguise_command="build-image"))

    joined = " ".join(captured["cmd"])
    assert "build" in captured["cmd"]
    assert "ACPI_OEM6=ACME" in joined  # host vendor, not a fixed brand
    assert "DISK_MODEL=ACME SSD 1TB" in joined  # host disk, not a fixed model
    assert "QEMU_VERSION=10.0.8" in joined
    assert Config.load().pod.disguise_image == "winpodx-windows-disguise"


def test_build_image_aborts_without_recipe(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    Config().save()
    monkeypatch.setattr(d, "_recipe_dir", lambda: None)
    started = {"n": 0}
    monkeypatch.setattr(
        d.subprocess, "Popen", lambda *a, **k: started.__setitem__("n", 1) or _FakeProc([])
    )

    with pytest.raises(SystemExit):
        d.handle_disguise(argparse.Namespace(disguise_command="build-image"))
    assert started["n"] == 0  # never shelled out to a build


def test_build_disguise_image_streams_and_returns_true(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    _seed_host_values(monkeypatch, d)
    monkeypatch.setattr(
        d.subprocess, "Popen", lambda cmd, **_k: _FakeProc(cmd, lines=["a\n", "b\n"])
    )

    seen: list[str] = []
    ok = d.build_disguise_image(cfg, on_line=seen.append)

    assert ok is True
    assert cfg.pod.disguise_image == "winpodx-windows-disguise"
    assert "a" in seen and "b" in seen  # streamed each build line


def test_build_disguise_image_returns_false_on_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    _seed_host_values(monkeypatch, d)
    monkeypatch.setattr(d.subprocess, "Popen", lambda cmd, **_k: _FakeProc(cmd, rc=1))

    ok = d.build_disguise_image(cfg)

    assert ok is False
    assert cfg.pod.disguise_image == ""  # not set on failure


def test_build_disguise_image_cancel_terminates(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    _seed_host_values(monkeypatch, d)
    proc = _FakeProc([], lines=["x\n", "y\n", "z\n"])
    monkeypatch.setattr(d.subprocess, "Popen", lambda *a, **k: proc)

    ok = d.build_disguise_image(cfg, should_cancel=lambda: True)

    assert ok is False
    assert proc.terminated is True
    assert cfg.pod.disguise_image == ""


def test_disguise_image_present(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()

    class _R:
        def __init__(self, rc):
            self.returncode = rc

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _R(0))
    assert d.disguise_image_present(cfg) is True

    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: _R(1))
    assert d.disguise_image_present(cfg) is False
