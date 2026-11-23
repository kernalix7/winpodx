# SPDX-License-Identifier: MIT
"""Execute autologon policy with only registry/LSA primitives replaced."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts/windows/sync_autologon_secret.ps1"
_PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(_PWSH is None, reason="PowerShell is not installed")


@pytest.mark.parametrize(
    ("overrides", "expected_status", "events", "plaintext"),
    [
        ({}, 10, ["store", "remove"], False),
        ({"Enabled": "0"}, 0, [], True),
        ({"UserName": "another-user"}, 0, [], True),
        ({"UserName": "another-user", "PlaintextKind": "Binary"}, 0, [], True),
        ({"Domain": "CORPORATE"}, 0, [], True),
        ({"Domain": "CORPORATE", "requested_domain": "CORPORATE"}, 0, [], True),
        ({"Domain": "__MACHINE__", "requested_domain": "."}, 10, ["store", "remove"], False),
        ({"Enabled": 1, "EnabledKind": "String"}, 0, [], True),
        ({"store_status": 11}, 11, ["store"], True),
        ({"store_status": 12}, 12, ["store"], True),
        ({"remove_fails": True}, 12, ["store", "remove"], True),
    ],
)
def test_autologon_preserves_policy_and_plaintext_until_secret_success(
    tmp_path, overrides, expected_status, events, plaintext
):
    settings = {
        "Enabled": "1",
        "EnabledKind": "String",
        "UserName": "fixture",
        "UserKind": "String",
        "Domain": ".",
        "DomainKind": "String",
        "HasPlaintext": True,
        "PlaintextKind": "String",
        "AutoLogonCount": 2,
        "ForceAutoLogon": "1",
        "requested_domain": "",
        "store_status": 10,
        "remove_fails": False,
    }
    settings.update(overrides)
    harness = tmp_path / "autologon-policy.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{str(_SCRIPT).replace(chr(39), chr(39) * 2)}'\n"
        "$global:settings = @'\n" + json.dumps(settings) + "\n'@ | ConvertFrom-Json\n"
        "$global:events = [Collections.Generic.List[string]]::new()\n"
        "if ($global:settings.Domain -eq '__MACHINE__') {"
        " $global:settings.Domain = [Environment]::MachineName }\n"
        "function Get-WinpodxAutologonSettings { return $global:settings }\n"
        "function Set-WinpodxAutologonSecret([string]$Password) {"
        " $global:events.Add('store'); return $global:settings.store_status }\n"
        "function Remove-WinpodxAutologonPlaintext {"
        " $global:events.Add('remove');"
        " if ($global:settings.remove_fails) { throw 'registry write rejected' };"
        " $global:settings.HasPlaintext = $false }\n"
        "$status = Invoke-WinpodxAutologonSync -UserName 'fixture'"
        " -Domain $global:settings.requested_domain -Password 'synthetic-candidate'\n"
        "@{status=$status;events=@($global:events.ToArray());settings=$global:settings}"
        " | ConvertTo-Json -Depth 4 -Compress\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [_PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["status"] == f"WINPODX_AUTOLOGON_STATUS={expected_status}"
    assert observed["events"] == events
    assert observed["settings"]["HasPlaintext"] is plaintext
    assert observed["settings"]["AutoLogonCount"] == 2
    assert observed["settings"]["ForceAutoLogon"] == "1"


def test_freerdp_sync_uses_verified_password_and_preserves_quoted_unicode(tmp_path, monkeypatch):
    from winpodx.core.config import Config
    from winpodx.core.rotation import _sync_windows_autologon
    from winpodx.core.transport.base import ExecResult, TransportUnavailable

    cfg = Config()
    cfg.rdp.user = "User’'한"
    cfg.rdp.password = "old-fixture"
    candidate = "new’'🔑; $(throw 'must not execute')"

    def unavailable(*_args, **_kwargs):
        raise TransportUnavailable("fixture: agent unavailable")

    def run_in_windows(auth_cfg, payload, **_kwargs):
        assert auth_cfg.rdp.password == candidate
        assert cfg.rdp.password == "old-fixture"
        definitions, invocation = payload.rsplit("\nInvoke-WinpodxAutologonSync ", 1)
        harness = tmp_path / "transport-payload.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\n"
            + definitions
            + "\nfunction Invoke-WinpodxAutologonSync {"
            " param($UserName, $Domain, $Password);"
            " @{user=$UserName; domain=$Domain; password=$Password} | ConvertTo-Json -Compress }\n"
            + "Invoke-WinpodxAutologonSync "
            + invocation,
            encoding="ascii",
        )
        result = subprocess.run(
            [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert json.loads(result.stdout) == {
            "user": cfg.rdp.user,
            "domain": "",
            "password": candidate,
        }
        return ExecResult(rc=0, stdout="WINPODX_AUTOLOGON_STATUS=10\r\n", stderr="")

    monkeypatch.setattr("winpodx.core.transport.dispatch", unavailable)
    monkeypatch.setattr("winpodx.core.windows_exec.run_in_windows", run_in_windows)
    assert _sync_windows_autologon(cfg, candidate)
