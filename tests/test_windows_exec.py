"""Tests for winpodx.core.windows_exec — FreeRDP RemoteApp PowerShell channel."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from winpodx.core.config import Config
from winpodx.core.windows_exec import WindowsExecError, WindowsExecResult, run_in_windows


def _cfg(password: str = "secret") -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    cfg.rdp.ip = "127.0.0.1"
    cfg.rdp.port = 3390
    cfg.rdp.user = "User"
    cfg.rdp.password = password
    return cfg


def _patch_data_dir(monkeypatch, tmp_path):
    """Force windows_exec to use tmp_path so the test can predict the result file."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    fake_data = fake_home / ".local" / "share" / "winpodx"
    fake_data.mkdir(parents=True)
    monkeypatch.setattr("winpodx.core.windows_exec.data_dir", lambda: fake_data)
    monkeypatch.setattr("winpodx.core.windows_exec.Path.home", staticmethod(lambda: fake_home))
    return fake_home, fake_data


def test_run_in_windows_raises_when_freerdp_missing(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr("winpodx.core.windows_exec.find_freerdp", lambda: None)
    with pytest.raises(WindowsExecError, match="FreeRDP not found"):
        run_in_windows(_cfg(), "Write-Output 'hello'")


def test_run_in_windows_raises_when_password_missing(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )
    cfg = _cfg(password="")
    with pytest.raises(WindowsExecError, match="password not set"):
        run_in_windows(cfg, "Write-Output 'hello'")


def test_run_in_windows_raises_on_freerdp_timeout(monkeypatch, tmp_path):
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", boom)
    with pytest.raises(WindowsExecError, match="timed out"):
        run_in_windows(_cfg(), "Write-Output 'hello'", timeout=1)


def test_run_in_windows_raises_when_no_result_file(monkeypatch, tmp_path):
    """FreeRDP exits but the wrapper PS never wrote a result — likely auth/connect failure."""
    _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    def fake_run(cmd, **kw):
        # FreeRDP "succeeded" (rc=0) but produced no result file.
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = "auth failure: NTLM"
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    with pytest.raises(WindowsExecError, match="No result file"):
        run_in_windows(_cfg(), "Write-Output 'hello'")


def test_run_in_windows_returns_result_when_wrapper_writes_json(monkeypatch, tmp_path):
    """Happy path — simulate the wrapper writing the JSON result file."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = work_dir / "winpodx-exec-result.json"

    def fake_run(cmd, **kw):
        # Simulate the in-guest wrapper writing the result file before exiting.
        result_path.write_text(
            json.dumps({"rc": 0, "stdout": "registry updated", "stderr": ""}),
            encoding="utf-8",
        )
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    result = run_in_windows(_cfg(), "Write-Output 'registry updated'")
    assert isinstance(result, WindowsExecResult)
    assert result.rc == 0
    assert result.ok is True
    assert "registry updated" in result.stdout
    # Result file cleaned up afterwards.
    assert not result_path.exists()


def test_run_in_windows_propagates_nonzero_rc_from_wrapper(monkeypatch, tmp_path):
    """If the user payload returned non-zero, surface it in WindowsExecResult.rc."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = work_dir / "winpodx-exec-result.json"

    def fake_run(cmd, **kw):
        result_path.write_text(
            json.dumps({"rc": 7, "stdout": "", "stderr": "boom"}), encoding="utf-8"
        )
        m = MagicMock()
        m.returncode = 7
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    result = run_in_windows(_cfg(), "exit 7")
    assert result.rc == 7
    assert result.ok is False
    assert result.stderr == "boom"


def test_run_in_windows_command_uses_freerdp_app_remoteapp(monkeypatch, tmp_path):
    """The cmd passed to subprocess.run must look like a RemoteApp /app:program: launch."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    captured: list[list[str]] = []
    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        # Write a successful result so the function returns cleanly.
        (work_dir / "apply-X-result.json").write_text(
            json.dumps({"rc": 0, "stdout": "", "stderr": ""}), encoding="utf-8"
        )
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    run_in_windows(_cfg(), "Write-Output 'hi'", description="apply-X")

    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0] == "/usr/bin/xfreerdp"
    # Standard winpodx RDP flags must be there.
    joined = " ".join(cmd)
    assert "/v:127.0.0.1:3390" in joined
    assert "/u:User" in joined
    assert "/p:secret" in joined
    assert "+home-drive" in joined
    assert "/sec:tls" in joined
    assert "/cert:ignore" in joined
    # The /app:program: payload should target powershell.exe with -File pointing
    # at a tsclient UNC path.
    app_arg = next((a for a in cmd if a.startswith("/app:program:")), "")
    assert "powershell.exe" in app_arg
    assert "tsclient" in app_arg
    assert "-WindowStyle Hidden" in app_arg
    assert "-NoProfile" in app_arg
    assert "-File" in app_arg


def test_run_in_windows_supports_flatpak_freerdp_with_spaces(monkeypatch, tmp_path):
    """flatpak-style binary 'flatpak run com.freerdp.FreeRDP' must split correctly."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp",
        lambda: ("flatpak run com.freerdp.FreeRDP", "flatpak"),
    )

    captured: list[list[str]] = []
    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kw):
        captured.append(list(cmd))
        (work_dir / "winpodx-exec-result.json").write_text(
            json.dumps({"rc": 0, "stdout": "", "stderr": ""}), encoding="utf-8"
        )
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    run_in_windows(_cfg(), "Write-Output 'flatpak'")
    assert captured[0][:3] == ["flatpak", "run", "com.freerdp.FreeRDP"]


def test_run_in_windows_handles_unparseable_result_file(monkeypatch, tmp_path):
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = work_dir / "winpodx-exec-result.json"

    def fake_run(cmd, **kw):
        result_path.write_text("not valid json", encoding="utf-8")
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    with pytest.raises(WindowsExecError, match="unparseable"):
        run_in_windows(_cfg(), "Write-Output 'hi'")


# --- v0.2.0: streaming progress callback -----------------------------------


def test_run_in_windows_streams_progress_lines(monkeypatch, tmp_path):
    """Streaming path: payload writes lines to the progress file; host tails them."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)
    progress_path = work_dir / "stream-test-progress.log"
    result_path = work_dir / "stream-test-result.json"

    received: list[str] = []

    # Simulate FreeRDP running for ~1s, writing progress lines mid-run, then
    # completing normally with a result file.
    def fake_popen(cmd, **kw):
        # Pre-populate result so the post-loop drain succeeds.
        progress_path.write_text("Stage A\nStage B\n", encoding="utf-8")

        m = MagicMock()
        m.stdout = MagicMock()
        m.stderr = MagicMock()
        # poll() returns None twice (still running), then 0 (done).
        m._poll_count = 0

        def _poll():
            m._poll_count += 1
            if m._poll_count <= 2:
                # While "running", write more progress between polls.
                if m._poll_count == 2:
                    progress_path.write_text("Stage A\nStage B\nStage C\n", encoding="utf-8")
                return None
            return 0

        m.poll.side_effect = _poll
        m.returncode = 0
        m.kill.return_value = None
        m.wait.return_value = 0
        m.communicate.return_value = ("", "")
        # Write the JSON result so the post-popen parse succeeds.
        result_path.write_text(
            json.dumps({"rc": 0, "stdout": "done", "stderr": ""}), encoding="utf-8"
        )
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.Popen", fake_popen)

    # Speed up the polling loop.
    monkeypatch.setattr("winpodx.core.windows_exec.time.sleep", lambda _x: None)

    result = run_in_windows(
        _cfg(),
        "Write-WinpodxProgress 'Stage A'",
        description="stream-test",
        progress_callback=received.append,
    )
    assert result.rc == 0
    assert "Stage A" in received
    assert "Stage B" in received
    assert "Stage C" in received


def test_run_in_windows_progress_file_created_only_when_callback_set(monkeypatch, tmp_path):
    """No progress_callback -> no progress file pre-created (we still cleanup it
    in finally, but the simple path doesn't try to read it)."""
    _, fake_data = _patch_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "winpodx.core.windows_exec.find_freerdp", lambda: ("/usr/bin/xfreerdp", "xfreerdp")
    )

    work_dir = fake_data / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)

    def fake_run(cmd, **kw):
        (work_dir / "winpodx-exec-result.json").write_text(
            json.dumps({"rc": 0, "stdout": "", "stderr": ""}), encoding="utf-8"
        )
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    monkeypatch.setattr("winpodx.core.windows_exec.subprocess.run", fake_run)
    run_in_windows(_cfg(), "Write-Output 'hi'")
    # Without callback, the synchronous subprocess.run path is taken; no
    # progress file should remain (cleanup runs unconditionally).
    progress_files = list(work_dir.glob("*-progress.log"))
    assert progress_files == []
