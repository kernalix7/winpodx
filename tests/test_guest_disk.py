# SPDX-License-Identifier: MIT
"""Guest-disk SMB path translation (#616)."""

from __future__ import annotations

from pathlib import Path

from winpodx.core.guest_disk import (
    GUEST_SMB_PORT,
    GUEST_SMB_SHARE,
    SMB_HOST_PORT,
    guest_win_path_to_host,
    smb_uri,
)


def test_host_port_is_unprivileged() -> None:
    # Rootless podman/docker can't bind a privileged host port (#616).
    assert SMB_HOST_PORT >= 1024
    # The guest still listens on the standard SMB port.
    assert GUEST_SMB_PORT == 445


def test_smb_uri_uses_user_and_host_port() -> None:
    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "WPX-User"
    uri = smb_uri(cfg)
    assert uri == f"smb://WPX-User@127.0.0.1:{SMB_HOST_PORT}/{GUEST_SMB_SHARE}"


def test_smb_uri_with_password_is_url_encoded() -> None:
    from winpodx.core.config import Config

    cfg = Config()
    cfg.rdp.user = "WPX-User"
    cfg.rdp.password = "p@ss/wo rd"
    uri = smb_uri(cfg, with_password=True)
    # Special chars in the password are percent-encoded so the URL parses.
    assert "p%40ss%2Fwo%20rd" in uri
    assert uri.startswith("smb://WPX-User:")
    assert uri.endswith(f"@127.0.0.1:{SMB_HOST_PORT}/{GUEST_SMB_SHARE}")


def test_kio_fuse_mount_parses_dbus_path(monkeypatch) -> None:
    from winpodx.core import guest_disk
    from winpodx.core.config import Config

    fuse_path = "/run/user/1000/kio-fuse-AB/smb/WPX-User@127.0.0.1:4445/winpodx-c"

    class _Proc:
        returncode = 0
        stdout = f'method return ...\n   string "{fuse_path}"\n'
        stderr = ""

    def fake_run(argv, **kwargs):
        # The credentialed mountUrl D-Bus call is what we issue.
        assert argv[0] == "dbus-send"
        assert "org.kde.KIOFuse.VFS.mountUrl" in argv
        return _Proc()

    monkeypatch.setattr(guest_disk.subprocess, "run", fake_run)
    monkeypatch.setattr(guest_disk.Path, "is_dir", lambda self: True)
    cfg = Config()
    assert guest_disk._kio_fuse_mount(cfg) == Path(fuse_path)


def test_translate_maps_c_drive_under_mount() -> None:
    mr = Path("/mnt/guest")
    assert guest_win_path_to_host(r"C:\Users\me\Desktop\x.txt", mr) == (
        mr / "Users" / "me" / "Desktop" / "x.txt"
    )


def test_translate_normalises_forward_slashes() -> None:
    mr = Path("/mnt/guest")
    assert guest_win_path_to_host("C:/Users/me/y", mr) == mr / "Users" / "me" / "y"


def test_translate_rejects_non_c_drive() -> None:
    assert guest_win_path_to_host(r"D:\data\x", Path("/mnt/guest")) is None


def test_translate_rejects_traversal() -> None:
    assert guest_win_path_to_host(r"C:\..\..\etc\passwd", Path("/mnt/guest")) is None
    assert guest_win_path_to_host(r"C:\Users\..\..\x", Path("/mnt/guest")) is None


def test_translate_rejects_non_drive_path() -> None:
    assert guest_win_path_to_host(r"\\tsclient\home\x", Path("/mnt/guest")) is None
    assert guest_win_path_to_host("relative/path", Path("/mnt/guest")) is None


def test_translate_bare_drive_root_is_mount_root() -> None:
    mr = Path("/mnt/guest")
    assert guest_win_path_to_host("C:\\", mr) == mr
