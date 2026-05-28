# SPDX-License-Identifier: MIT
"""Tests for the AppImage clean-host-env helper (#357, #363; 0.6.0 item A).

The pre-Thin (fat) AppImage prepended ``${APPDIR}/usr/bin`` to PATH and
``${APPDIR}/usr/lib`` to LD_LIBRARY_PATH, which shadowed (#357) or poisoned
(#363) a host that already had a working podman. 0.6.0 item A drops the
bundled podman stack entirely, so nothing in ``${APPDIR}/usr/bin`` shadows
the host container runtime anymore -- ``host_path()`` /
``resolve_backend_bin()`` were removed.

What survives in Thin: the bundled FreeRDP / Python / Qt still need
``${APPDIR}/usr/lib`` on the AppImage's ``LD_LIBRARY_PATH`` (the entrypoint
prepends it), and when the host container runtime spawns host helpers
(``systemd-run`` / ``netavark`` / ``aardvark-dns``) they must NOT inherit
that and load the bundled ``libcrypto`` / ``libssl`` -- the #363 root cause.
:func:`host_env` returns an ``os.environ`` copy with ``${APPDIR}`` entries
stripped, so callers pass it as ``env=`` to ``subprocess`` and the spawned
host runtime + its host helpers load HOST libs. Outside an AppImage every
function here is a strict no-op.
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
    """The #363 root-cause fix that survives Thin: bundled FreeRDP / Python
    / Qt still need ``${APPDIR}/usr/lib`` on the AppImage's own
    LD_LIBRARY_PATH, but the host container runtime + the host helpers it
    spawns (``systemd-run`` / ``netavark`` / ``aardvark-dns``) must NOT
    inherit it and must load HOST libcrypto / libssl."""
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


# --- removed surface: resolve_backend_bin / host_path are gone -----------


def test_resolve_backend_bin_is_gone():
    """Thin AppImage dropped the host-first bundled-shadow dance because
    nothing in ${APPDIR}/usr/bin shadows host container binaries anymore.
    Standard PATH resolution finds the host podman / docker directly."""
    assert not hasattr(_hostenv, "resolve_backend_bin")


def test_host_path_is_gone():
    """``host_path()`` only existed to support ``resolve_backend_bin``."""
    assert not hasattr(_hostenv, "host_path")


# --- call-site wiring: env reaches the subprocess ------------------------


def test_podman_container_state_passes_host_env_inside_appimage():
    """The podman ``ps`` probe (and by extension is_running / is_paused)
    must run under the clean host env inside an AppImage so the host runtime
    + the host helpers it spawns load HOST libs (the #363 mitigation that
    survives Thin)."""
    from winpodx.backend.podman import PodmanBackend
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.container_name = "winpodx-windows"
    backend = PodmanBackend(cfg)

    sentinel_env = {"PATH": "/usr/bin"}
    with patch("winpodx.backend.podman.host_env", return_value=sentinel_env):
        with patch("winpodx.backend.podman.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "running\n"
            mock_run.return_value.stderr = ""
            backend.is_running()

    _, kwargs = mock_run.call_args
    assert kwargs["env"] is sentinel_env
    cmd = mock_run.call_args[0][0]
    # Thin: bare "podman" argv0 -- no resolve_backend_bin host-first dance.
    assert cmd[0] == "podman"


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


def test_docker_container_state_uses_bare_docker_argv0():
    """Same Thin invariant on the docker backend: bare ``docker`` argv0,
    no host-first resolve."""
    from winpodx.backend.docker import DockerBackend
    from winpodx.core.config import Config

    cfg = Config()
    cfg.pod.container_name = "winpodx-windows"
    backend = DockerBackend(cfg)

    with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
        with patch("winpodx.backend.docker.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "running\n"
            mock_run.return_value.stderr = ""
            backend.is_running()

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "docker"
