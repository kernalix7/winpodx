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


def test_disguise_unknown_command_exits(capsys):
    from winpodx.cli import disguise as d

    with pytest.raises(SystemExit) as exc:
        d.handle_disguise(argparse.Namespace(disguise_command="unknown"))
    assert exc.value.code == 1
    assert "Usage: winpodx disguise build-image" in capsys.readouterr().out


def test_recipe_dir_finds_bundled_recipe(monkeypatch, tmp_path):
    from winpodx.cli import disguise as d
    from winpodx.utils import paths

    recipe = tmp_path / "packaging" / "qemu-disguise"
    recipe.mkdir(parents=True)
    (recipe / "Dockerfile").write_text("FROM scratch", encoding="utf-8")
    monkeypatch.setattr(paths, "bundle_dir", lambda: tmp_path)

    assert d._recipe_dir() == recipe


def test_host_dmi_returns_value_and_handles_oserror(monkeypatch):
    from winpodx.cli import disguise as d

    monkeypatch.setattr(d.Path, "read_text", lambda self, **kwargs: " ACME \n")
    assert d._host_dmi("sys_vendor") == "ACME"

    def fail(self, **kwargs):
        raise OSError("denied")

    monkeypatch.setattr(d.Path, "read_text", fail)
    assert d._host_dmi("sys_vendor") == ""


def test_host_disk_model_skips_virtual_unreadable_and_qemu(monkeypatch):
    from winpodx.cli import disguise as d

    paths = [
        "/sys/block/loop0/device/model",
        "/sys/block/sda/device/model",
        "/sys/block/sdb/device/model",
        "/sys/block/nvme0n1/device/model",
    ]
    monkeypatch.setattr(d.glob, "glob", lambda pattern: paths)

    def read_model(path, **kwargs):
        if str(path).startswith("/sys/block/sda"):
            raise OSError("gone")
        if str(path).startswith("/sys/block/sdb"):
            return "QEMU HARDDISK\n"
        return "ACME NVME\n"

    monkeypatch.setattr(d.Path, "read_text", read_model)
    assert d._host_disk_model() == "ACME NVME"


def test_qemu_version_uses_exact_runtime_argv(monkeypatch):
    from types import SimpleNamespace

    from winpodx.cli import disguise as d

    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(stdout="QEMU emulator version 9.2.1\n")

    monkeypatch.setattr(d.subprocess, "run", run)

    assert d._qemu_version("docker", "dockur/windows:6.02") == "9.2.1"
    assert captured == {
        "cmd": [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "qemu-system-x86_64",
            "dockur/windows:6.02",
            "--version",
        ],
        "kwargs": {"capture_output": True, "text": True, "timeout": 120},
    }


def test_qemu_version_returns_empty_on_failure_or_unmatched_output(monkeypatch):
    from types import SimpleNamespace

    from winpodx.cli import disguise as d

    monkeypatch.setattr(
        d.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="unknown version")
    )
    assert d._qemu_version("podman", "image") == ""

    def fail(*args, **kwargs):
        raise d.subprocess.TimeoutExpired(args[0], 120)

    monkeypatch.setattr(d.subprocess, "run", fail)
    assert d._qemu_version("podman", "image") == ""


def test_disguise_image_present_uses_fallback_backend_and_exact_argv(monkeypatch):
    from types import SimpleNamespace

    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "manual"
    captured = {}

    def run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(d.subprocess, "run", run)
    assert d.disguise_image_present(cfg) is True
    assert captured == {
        "cmd": ["podman", "image", "inspect", "winpodx-windows-disguise"],
        "kwargs": {"capture_output": True, "text": True, "timeout": 30},
    }

    monkeypatch.setattr(
        d.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("podman")),
    )
    assert d.disguise_image_present(cfg) is False


def test_build_disguise_image_constructs_exact_minimal_argv(monkeypatch, tmp_path):
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.backend = "manual"
    cfg.pod.image = "example/windows:test"
    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    dockerfile = recipe / "Dockerfile"
    dockerfile.write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    monkeypatch.setattr(d, "_qemu_version", lambda backend, image: "")
    monkeypatch.setattr(d, "_host_dmi", lambda name: "")
    monkeypatch.setattr(d, "_host_disk_model", lambda: "")
    captured = {}

    def popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc(cmd, lines=[])

    monkeypatch.setattr(d.subprocess, "Popen", popen)

    assert d.build_disguise_image(cfg) is True
    assert captured == {
        "cmd": [
            "podman",
            "build",
            "-t",
            "winpodx-windows-disguise",
            "--build-arg",
            "DOCKUR_IMAGE=example/windows:test",
            "--build-arg",
            "QEMU_VERSION=10.0.8",
            "-f",
            str(dockerfile),
            str(recipe),
        ],
        "kwargs": {
            "stdout": d.subprocess.PIPE,
            "stderr": d.subprocess.STDOUT,
            "text": True,
            "bufsize": 1,
        },
    }
    assert cfg.pod.disguise_image == "winpodx-windows-disguise"


def test_build_disguise_image_reports_missing_recipe_and_callback_failure(monkeypatch):
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    monkeypatch.setattr(d, "_recipe_dir", lambda: None)
    lines = []
    assert d.build_disguise_image(cfg, on_line=lines.append) is False
    assert lines == [
        "disguise build: recipe not found (packaging/qemu-disguise); need a source checkout"
    ]

    def broken_callback(line):
        raise RuntimeError(line)

    assert d.build_disguise_image(cfg, on_line=broken_callback) is False


def test_build_disguise_image_handles_popen_failure(monkeypatch, tmp_path):
    from winpodx.cli import disguise as d
    from winpodx.core.config import Config

    cfg = Config()
    recipe = tmp_path / "qemu-disguise"
    recipe.mkdir()
    (recipe / "Dockerfile").write_text("x", encoding="utf-8")
    monkeypatch.setattr(d, "_recipe_dir", lambda: recipe)
    _seed_host_values(monkeypatch, d)
    monkeypatch.setattr(
        d.subprocess,
        "Popen",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("podman")),
    )
    lines = []

    assert d.build_disguise_image(cfg, on_line=lines.append) is False
    assert lines[-1] == "disguise build: failed to start (podman)"
    assert cfg.pod.disguise_image == ""
