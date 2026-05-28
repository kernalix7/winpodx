# SPDX-License-Identifier: MIT
"""Tests for the AppImage host-first container-backend helper (#357, #363).

The fat AppImage prepends ``${APPDIR}/usr/bin`` to PATH and
``${APPDIR}/usr/lib`` to LD_LIBRARY_PATH, which shadows (#357) or poisons
(#363) a host that already has a working podman. ``backend/_hostenv.py``
re-resolves the container backend host-first and runs it under a clean host
env. Outside an AppImage every function must be a strict no-op.
"""

import os
from unittest.mock import patch

from winpodx.backend import _hostenv

# --- in_appimage ---------------------------------------------------------


def test_in_appimage_false_when_appdir_unset():
    with patch.dict(os.environ, {}, clear=True):
        assert _hostenv.in_appimage() is False


def test_in_appimage_false_when_appdir_empty():
    with patch.dict(os.environ, {"APPDIR": ""}, clear=True):
        assert _hostenv.in_appimage() is False


def test_in_appimage_true_when_appdir_set():
    with patch.dict(os.environ, {"APPDIR": "/tmp/x.AppDir"}, clear=True):
        assert _hostenv.in_appimage() is True


# --- host_env: no-op guarantee outside AppImage --------------------------


def test_host_env_none_when_appdir_unset():
    """The safety guarantee for the ~99% non-AppImage installs: host_env()
    returns None so callers pass ``env=None`` and subprocess inherits the
    current environment unchanged."""
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LD_LIBRARY_PATH": "/host/lib"}
    with patch.dict(os.environ, env, clear=True):
        assert _hostenv.host_env() is None


def test_host_path_unchanged_when_appdir_unset():
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
    with patch.dict(os.environ, env, clear=True):
        assert _hostenv.host_path() == "/usr/local/bin:/usr/bin:/bin"


# --- host_env: strip APPDIR inside an AppImage ---------------------------


def test_host_env_strips_appdir_bin_from_path():
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/local/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/opt/app.AppDir/usr/lib:/host/lib",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    assert "/opt/app.AppDir" not in result["PATH"]


def test_host_env_strips_appdir_lib_from_ld_library_path():
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/opt/app.AppDir/usr/lib:/host/lib:/other/lib",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["LD_LIBRARY_PATH"] == "/host/lib:/other/lib"
    assert "/opt/app.AppDir" not in result["LD_LIBRARY_PATH"]


def test_host_env_drops_ld_library_path_when_it_empties():
    """An empty LD_LIBRARY_PATH is NOT equivalent to unset -- the dynamic
    linker treats "" as the current directory. When stripping APPDIR empties
    it, the key must be dropped so the host linker uses its default search
    path (the #363 fix: load HOST libcrypto, not the bundled OPENSSL_3.4.0)."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/opt/app.AppDir/usr/lib",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert "LD_LIBRARY_PATH" not in result


def test_host_env_strips_any_appdir_rooted_entry():
    """Strip *any* entry under ${APPDIR}, not just the two exact dirs the
    entrypoint prepends -- so a future entrypoint adding e.g.
    ${APPDIR}/usr/lib64 is still neutralised."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/opt/app.AppDir/usr/sbin:/usr/bin",
        "LD_LIBRARY_PATH": "/opt/app.AppDir/usr/lib:/opt/app.AppDir/usr/lib64:/host/lib",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["PATH"] == "/usr/bin"
    assert result["LD_LIBRARY_PATH"] == "/host/lib"


def test_host_env_preserves_other_variables():
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
        "HOME": "/home/u",
        "XDG_RUNTIME_DIR": "/run/user/1000",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["HOME"] == "/home/u"
    assert result["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert result["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"


def test_host_env_handles_appdir_with_trailing_slash():
    env = {
        "APPDIR": "/opt/app.AppDir/",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/opt/app.AppDir/usr/lib:/host/lib",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["PATH"] == "/usr/bin"
    assert result["LD_LIBRARY_PATH"] == "/host/lib"


def test_host_env_leaves_path_alone_when_no_appdir_entries():
    """An AppImage whose entrypoint did not prepend (lean build) -- nothing
    to strip, but still not None inside the AppImage."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/usr/local/bin:/usr/bin",
    }
    with patch.dict(os.environ, env, clear=True):
        result = _hostenv.host_env()
    assert result is not None
    assert result["PATH"] == "/usr/local/bin:/usr/bin"
    assert "LD_LIBRARY_PATH" not in result


# --- resolve_backend_bin -------------------------------------------------


def test_resolve_backend_bin_noop_outside_appimage():
    """Strict no-op outside an AppImage: returns the bare name, the caller's
    normal PATH resolution applies exactly as before."""
    with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
        assert _hostenv.resolve_backend_bin("podman") == "podman"
        assert _hostenv.resolve_backend_bin("podman-compose") == "podman-compose"


def test_resolve_backend_bin_prefers_host_over_bundled():
    """Host-first: when the host PATH has podman, return the host copy even
    though a bundled copy exists under ${APPDIR}/usr/bin (the #357 fix)."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
    }

    def fake_which(name, path=None):
        # Host PATH (APPDIR stripped) is "/usr/bin"; host podman lives there.
        if name == "podman" and path == "/usr/bin":
            return "/usr/bin/podman"
        return None

    with patch.dict(os.environ, env, clear=True):
        with patch("winpodx.backend._hostenv.shutil.which", side_effect=fake_which):
            resolved = _hostenv.resolve_backend_bin("podman")
    assert resolved == "/usr/bin/podman"


def test_resolve_backend_bin_falls_back_to_bundled_when_host_lacks_it():
    """When the host genuinely lacks podman, fall back to the bundled copy
    (the original self-contained AppImage best-effort path)."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
    }
    bundled = "/opt/app.AppDir/usr/bin/podman"

    # Reset the one-time-warning guard so this test sees a deterministic
    # state regardless of run order.
    _hostenv._BUNDLED_FALLBACK_WARNED.discard("podman")

    with patch.dict(os.environ, env, clear=True):
        with patch("winpodx.backend._hostenv.shutil.which", return_value=None):
            with patch("winpodx.backend._hostenv.os.path.isfile", return_value=True):
                with patch("winpodx.backend._hostenv.os.access", return_value=True):
                    resolved = _hostenv.resolve_backend_bin("podman")
    assert resolved == bundled


def test_resolve_backend_bin_bare_name_when_nowhere():
    """Neither host nor bundled has it -- return the bare name so the caller
    raises a clean FileNotFoundError with a recognisable argv[0]."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
    }
    with patch.dict(os.environ, env, clear=True):
        with patch("winpodx.backend._hostenv.shutil.which", return_value=None):
            with patch("winpodx.backend._hostenv.os.path.isfile", return_value=False):
                resolved = _hostenv.resolve_backend_bin("podman")
    assert resolved == "podman"


def test_resolve_backend_bin_bundled_fallback_warns_once():
    """The bundled-fallback WARNING fires at most once per binary per
    process so a host without podman doesn't spam stderr on every probe."""
    env = {
        "APPDIR": "/opt/app.AppDir",
        "PATH": "/opt/app.AppDir/usr/bin:/usr/bin",
    }
    _hostenv._BUNDLED_FALLBACK_WARNED.discard("podman")

    with patch.dict(os.environ, env, clear=True):
        with patch("winpodx.backend._hostenv.shutil.which", return_value=None):
            with patch("winpodx.backend._hostenv.os.path.isfile", return_value=True):
                with patch("winpodx.backend._hostenv.os.access", return_value=True):
                    with patch("winpodx.backend._hostenv.log.warning") as mock_warn:
                        _hostenv.resolve_backend_bin("podman")
                        _hostenv.resolve_backend_bin("podman")
                        _hostenv.resolve_backend_bin("podman")
    assert mock_warn.call_count == 1


# --- call-site wiring: env reaches the subprocess ------------------------


def test_podman_container_state_passes_host_env_inside_appimage():
    """The podman ``ps`` probe (and by extension is_running / is_paused)
    must run under the clean host env inside an AppImage."""
    from winpodx.backend.podman import PodmanBackend
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.container_name = "winpodx-windows"
    backend = PodmanBackend(cfg)

    sentinel_env = {"PATH": "/usr/bin"}
    with patch("winpodx.backend.podman.host_env", return_value=sentinel_env):
        with patch("winpodx.backend.podman.resolve_backend_bin", return_value="/usr/bin/podman"):
            with patch("winpodx.backend.podman.subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "running\n"
                mock_run.return_value.stderr = ""
                backend.is_running()

    _, kwargs = mock_run.call_args
    assert kwargs["env"] is sentinel_env
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "/usr/bin/podman"


def test_podman_container_state_passes_none_env_outside_appimage():
    """Outside an AppImage the same probe must pass ``env=None`` (inherit)
    and the bare ``podman`` binary -- byte-for-byte unchanged behaviour."""
    from winpodx.backend.podman import PodmanBackend
    from winpodx.core.config import Config

    cfg = Config()
    backend = PodmanBackend(cfg)

    with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
        with patch("winpodx.backend.podman.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "running\n"
            mock_run.return_value.stderr = ""
            backend.is_running()

    _, kwargs = mock_run.call_args
    assert kwargs["env"] is None
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "podman"
