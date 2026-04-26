r"""Execute PowerShell inside the Windows guest via FreeRDP RemoteApp.

Why this module exists
======================

``podman exec winpodx-windows ...`` runs inside the **Linux container**
that hosts QEMU, not in the **Windows VM** running inside QEMU. The
Linux container has no ``powershell.exe`` on $PATH, so any host-side
attempt to push registry / service / NIC changes via ``podman exec``
fails with rc=127. This bit v0.1.9.1, v0.1.9.2, and v0.1.9.3 — the
runtime apply functions appeared to succeed because they only logged
warnings on rc!=0 instead of raising. kernalix7 reported the silent-
fail symptom on 2026-04-26.

The reliable channel for actually-running-in-Windows commands is the
same RemoteApp launch path winpodx already uses to start Word, Excel,
etc.: launch ``powershell.exe`` as a RemoteApp via FreeRDP, have it
read its script body from the host-shared ``\\tsclient\home`` directory
(already mounted via ``+home-drive``), write its result back to that
same share, then exit so the RemoteApp window closes.

Cost
----

- ~5-10 seconds per call (RDP handshake + auth + script + disconnect).
- Brief PowerShell window flash unless the payload is wrapped in
  ``-WindowStyle Hidden`` (we do).
- Requires the configured RDP password to actually match the Windows
  guest's; if password rotation has been silently failing for months
  (which it has — same root cause: ``_change_windows_password`` also
  used podman exec), the first call here will fail with auth error and
  the user will need to manually sync.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from winpodx.core.config import Config
from winpodx.core.rdp import find_freerdp
from winpodx.utils.paths import data_dir

log = logging.getLogger(__name__)


class WindowsExecError(RuntimeError):
    """Raised when a Windows-guest PowerShell exec attempt fails to even
    return a parseable result.

    Distinct from a non-zero ``rc`` inside ``WindowsExecResult``: this
    means the channel itself broke (FreeRDP missing / auth failed /
    timeout / no result file written) rather than the script just
    exiting with a failure code.
    """


@dataclass
class WindowsExecResult:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


def run_in_windows(
    cfg: Config,
    payload: str,
    *,
    timeout: int = 60,
    description: str = "winpodx-exec",
) -> WindowsExecResult:
    r"""Run ``payload`` (PowerShell source) inside the Windows guest.

    The payload is wrapped to capture stdout / stderr / rc and write a
    JSON result file under ``data_dir() / "windows-exec" /``. The wrapper
    is written as a ``.ps1`` and FreeRDP launches PowerShell as a
    RemoteApp pointing at it via the ``\\tsclient\home`` redirection.

    ``description`` becomes the file stem so concurrent / sequential
    callers can disambiguate temp files. Avoid characters that aren't
    safe in a filename — caller is trusted, no validation here.

    Raises ``WindowsExecError`` when the channel itself fails (FreeRDP
    missing, auth fail, timeout, no result file). Returns
    ``WindowsExecResult`` with the script's own rc otherwise — caller
    inspects ``.ok`` / ``.rc`` to decide success.
    """
    found = find_freerdp()
    if found is None:
        raise WindowsExecError("FreeRDP not found on $PATH")
    binary, _flavor = found

    if not cfg.rdp.password:
        raise WindowsExecError("RDP password not set in config — cannot authenticate")

    work_dir = data_dir() / "windows-exec"
    work_dir.mkdir(parents=True, exist_ok=True)
    script_path = work_dir / f"{description}.ps1"
    result_path = work_dir / f"{description}-result.json"

    home = Path.home().resolve()
    try:
        rel_script = script_path.resolve().relative_to(home)
        rel_result = result_path.resolve().relative_to(home)
    except ValueError as e:
        raise WindowsExecError(
            f"work paths must be under $HOME for tsclient redirection: {e}"
        ) from e

    win_script_unc = "\\\\tsclient\\home\\" + str(rel_script).replace("/", "\\")
    win_result_unc = "\\\\tsclient\\home\\" + str(rel_result).replace("/", "\\")

    # The wrapper runs the user payload, captures combined output, and
    # writes a JSON {rc,stdout,stderr} blob to the result path. Single
    # quotes in the result path are escaped because we wrap it in single
    # quotes inside the PowerShell string literal.
    indented_payload = textwrap.indent(payload.rstrip(), "                ")
    safe_result_path = win_result_unc.replace("'", "''")
    wrapper = textwrap.dedent(
        f"""\
        $ErrorActionPreference = 'Continue'
        $rc = 0
        $stdout = ''
        $stderr = ''
        try {{
            $stdout = & {{
{indented_payload}
            }} 2>&1 | Out-String
            if ($null -ne $LASTEXITCODE) {{
                $rc = $LASTEXITCODE
            }}
        }} catch {{
            $rc = 1
            $stderr = $_.Exception.Message
        }}
        $payload = @{{rc=$rc; stdout=$stdout; stderr=$stderr}} | ConvertTo-Json -Compress
        # -Force lets us overwrite stale results from prior runs.
        $payload | Out-File -FilePath '{safe_result_path}' -Encoding utf8 -Force
        exit $rc
        """
    )
    script_path.write_text(wrapper, encoding="utf-8")
    result_path.unlink(missing_ok=True)

    # FreeRDP RemoteApp invocation. -WindowStyle Hidden keeps the PS
    # window from flashing; `cmd:` value is a single string passed as
    # PowerShell's command-line args.
    ps_args = f'-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "{win_script_unc}"'
    app_arg = f"/app:program:powershell.exe,name:{description},cmd:{ps_args}"

    # Split a flatpak-style binary like "flatpak run com.freerdp.FreeRDP".
    cmd_parts = shlex.split(binary) if " " in binary else [binary]
    cmd_parts += [
        f"/v:{cfg.rdp.ip}:{cfg.rdp.port}",
        f"/u:{cfg.rdp.user}",
        f"/p:{cfg.rdp.password}",
        "+home-drive",
        "/sec:tls",
        "/cert:ignore",
        app_arg,
    ]
    if cfg.rdp.domain:
        cmd_parts.append(f"/d:{cfg.rdp.domain}")

    try:
        proc = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise WindowsExecError(f"FreeRDP binary vanished: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise WindowsExecError(
            f"FreeRDP timed out after {timeout}s waiting for the script to complete"
        ) from e
    finally:
        # Script file is no longer needed — the result, if any, is in the JSON.
        script_path.unlink(missing_ok=True)

    if not result_path.exists():
        # Nothing landed — likely auth failure, FreeRDP couldn't connect,
        # or the +home-drive redirection didn't work. Surface the
        # FreeRDP stderr verbatim (truncated) so the user can debug.
        stderr_tail = (proc.stderr or "").strip()[-400:]
        raise WindowsExecError(
            f"No result file written (FreeRDP rc={proc.returncode}). stderr tail: {stderr_tail!r}"
        )

    try:
        # PowerShell's `Out-File -Encoding utf8` writes a UTF-8 BOM (the
        # 5.1 / Windows-PowerShell behavior; PS Core 7+ doesn't). Use
        # `utf-8-sig` so the BOM is consumed transparently. v0.1.9.5
        # caught this — kernalix7's apply-fixes returned "result file
        # unparseable: Unexpected UTF-8 BOM" when the wrapper actually
        # *had* succeeded.
        raw = result_path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError) as e:
        raise WindowsExecError(f"result file unparseable: {e}") from e
    finally:
        result_path.unlink(missing_ok=True)

    return WindowsExecResult(
        rc=int(data.get("rc", 0)),
        stdout=str(data.get("stdout", "")),
        stderr=str(data.get("stderr", "")),
    )


# Local fallback noqa-suppress for `shutil` import when tests stub it out.
_ = shutil
