# Rotation Memory-Exposure Review — Agent-first vs FreeRDP

**Status**: complete, sign-off
**Date**: 2026-05-07
**Reviewer**: security-reviewer (rotation-agent-first team)
**Scope**: `_change_windows_password` migration from FreeRDP-only (Rule #6)
to agent-first with FreeRDP fallback. Verifies the team's "neither is
strictly worse" claim used to retire Rule #6 in `docs/TRANSPORT_ABC.md`.
**Sources**:
- `src/winpodx/core/rotation/__init__.py` (`_change_windows_password`, line 72)
- `src/winpodx/core/windows_exec.py` (`run_in_windows`, line 120; `run_via_transport`, line 77)
- `src/winpodx/core/transport/agent.py` (`AgentTransport.exec`, line 84)
- `src/winpodx/core/agent.py` (`AgentClient.exec`, line 195)
- `config/oem/agent/agent.ps1` (`Invoke-ExecScript`, line 146)

---

## TL;DR

The team's claim that **"agent path is not strictly worse than FreeRDP
path for new-password exposure"** is correct and approved.

- Both paths terminate at the same dominant exposure: `net.exe <user>
  <newpw>` argv inside the guest, visible to Task Manager, WMI
  `Win32_Process.CommandLine`, ETW process-create events
  (`Microsoft-Windows-Kernel-Process` GUID
  `{22FB2CD6-0E7B-422B-A0C7-2FAD1FD0E716}`), Sysmon Event ID 1, and
  Defender ASR / AMSI telemetry.
- The on-disk `.ps1` artifact moves from the **host** filesystem
  (FreeRDP path, `~/.local/share/winpodx/windows-exec/`) to the
  **guest** filesystem (agent path, `C:\OEM\agent-runs\<guid>.ps1`).
  Both are deleted in a `finally` immediately after the call. Lifetime
  is comparable (~1-2s). The host-side artifact under a single-user
  `$HOME` and the guest-side artifact under `C:\OEM` (default perms:
  Administrators / SYSTEM full, authenticated Users read+execute) have
  similar realistic blast radius — neither materially weaker than the
  other for an attacker who already has code execution at that level.
- Agent path adds **one** in-memory copy not present in the FreeRDP
  path: the HTTP request body inside the guest agent's PowerShell
  process (`$body`, `$parsed.script`, decoded `$bytes`), held until GC.
  This is the new surface introduced by the migration.
- FreeRDP path contains **one** offsetting exposure not present in the
  agent path: `xfreerdp /p:<password>` on host argv — but that is the
  *current* RDP password, not the new password being set, so it does
  not change the new-password surface. (It does mean FreeRDP path
  exposes the OLD password on host argv, which is a separate
  long-standing concern unrelated to this migration.)

The "agent process memory" objection in the original Rule #6 rationale
overstated the marginal risk: the dominant exposure (`net.exe` argv)
is identical between the two paths, and an attacker capable of reading
agent process memory inside the guest is already inside the trust
boundary that owns the local SAM database.

**Recommendation**: sign off the migration. Optional hardenings below
are cheap and worth landing, but **none are blocking**.

---

## Path-by-path enumeration

### FreeRDP path

Code: `_change_windows_password` → `run_in_windows`
(`src/winpodx/core/windows_exec.py:120`).

| # | Artifact | Where | Form | Persistence | Lifetime |
|---|---|---|---|---|---|
| 1 | Python local `pw` | host process heap | string `<newpw>` | in-memory | until function returns + GC |
| 2 | `payload` string | host process heap | `& net user '<u>' '<newpw>' | Out-Null\n…` | in-memory | until function returns + GC |
| 3 | `wrapper` string | host process heap | full `.ps1` source incl. payload | in-memory | until function returns + GC |
| 4 | `script_path` on host disk | `~/.local/share/winpodx/windows-exec/rotate-password.ps1` | full `.ps1` source | **on-disk** | written line 213, **deleted in `finally` line 313**. Lives ~5-15s (RDP handshake + script + disconnect). Default umask → typically 0644 on single-user `$HOME` |
| 5 | xfreerdp argv | host argv | `/p:<RDP_old_pw>` plus `/v: /u: …` | in-memory + ps argv | until xfreerdp exits. **Does not contain the new password.** |
| 6 | RDP wire | TCP to guest | TLS-wrapped (`/sec:tls`, `/cert:ignore`) | in-transit | duration of session |
| 7 | `\\tsclient\home\…\rotate-password.ps1` (guest view of #4) | RDP drive redirect | full `.ps1` source | virtual; backed by host file | identical to #4 |
| 8 | guest PS argv | guest process argv | `powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File "\\tsclient\home\…\rotate-password.ps1"` | in-memory + ps argv | until PS exits. **Does not contain the new password.** |
| 9 | guest PS heap | guest process heap | `$payload`-equivalent string from script source | in-memory | until PS exits |
| 10 | **`net.exe` argv** | guest process argv | `net user <user> <newpw>` | in-memory + ps argv | until `net.exe` exits (sub-second). Visible via Task Manager → Details → Command line column, WMI `Win32_Process.CommandLine`, ETW Kernel-Process / Sysmon EID 1, Defender process telemetry. **Dominant exposure.** |
| 11 | `result.json` on host | `~/.local/share/winpodx/windows-exec/rotate-password-result.json` | `{rc, stdout: "password set", stderr: ""}` | on-disk | written by guest, read+deleted by host (line 337). Does **not** contain the password. |
| 12 | RDP video framebuffer | RDP wire / FreeRDP buffers | rendered console of PS — but `-WindowStyle Hidden` + RemoteApp means no console paint, plus payload uses `Out-Null` | likely-empty | RDP session lifetime |

### Agent path

Code: `_change_windows_password` → `run_via_transport` →
`AgentTransport.exec` → `AgentClient.exec` → guest `Invoke-ExecScript`
(`config/oem/agent/agent.ps1:146`).

| # | Artifact | Where | Form | Persistence | Lifetime |
|---|---|---|---|---|---|
| 1 | Python local `pw` | host process heap | string `<newpw>` | in-memory | until function returns + GC |
| 2 | `payload` string | host process heap | `& net user '<u>' '<newpw>' | Out-Null\n…` | in-memory | until function returns + GC |
| 3 | `encoded` (base64) | host process heap | b64 of script bytes | in-memory | until function returns + GC |
| 4 | `body` (JSON) | host process heap | `{"script":"<b64>","timeout_sec":45}` bytes | in-memory | until function returns + GC |
| 5 | HTTP wire | `127.0.0.1:8765` → QEMU slirp → guest `+:8765` | plaintext HTTP body containing #4 | in-transit | request duration. Host loopback + container-to-VM slirp; never on a physical wire. The compose mapping is `127.0.0.1:8765:8765/tcp` — loopback-only on the host |
| 6 | guest agent `$body` | guest agent PS heap | full request body string | in-memory | until next GC after `Read-Body` returns. Held simultaneously with #7-#9 |
| 7 | guest agent `$parsed.script` | guest agent PS heap | b64 of script | in-memory | until next GC |
| 8 | guest agent `$bytes` (decoded) | guest agent PS heap | raw script bytes incl. password | in-memory | written to `$tempFile` then conceptually superseded; PS GC keeps until next collection. **This is the marginal new exposure vs. FreeRDP path.** |
| 9 | guest agent `$hash` / log entry | `C:\OEM\agent.log` | SHA256 hex of script bytes (NOT script content) | on-disk | persistent. **Does not contain the password.** Hash is one-way; not reversible without dictionary attack on the (small) password keyspace. Generated passwords (`generate_password`) are high-entropy → not a practical recovery vector |
| 10 | `$tempFile` on guest disk | `C:\OEM\agent-runs\<guid>.ps1` | full script bytes | **on-disk** | written line 157, **deleted in `finally` line 218**. Default `C:\OEM` ACL: BUILTIN\Administrators / NT AUTHORITY\SYSTEM full, authenticated Users read+execute |
| 11 | guest child PS argv | guest process argv | `powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<tempFile>"` | in-memory + ps argv | until PS exits. **Does not contain the new password.** |
| 12 | guest child PS heap | guest process heap | full script source from `-File` read | in-memory | until PS exits |
| 13 | **`net.exe` argv** | guest process argv | `net user <user> <newpw>` | in-memory + ps argv | until `net.exe` exits. **Identical to FreeRDP row #10.** |
| 14 | guest `$result.stdout/stderr` | guest agent PS heap then HTTP response | drained from child PS via `ReadToEndAsync` | in-memory | until response written. Does **not** contain the password (payload uses `Out-Null`) |
| 15 | host `payload` JSON response | host process heap | `{rc, stdout, stderr}` | in-memory | until function returns + GC. Does **not** contain the password |

---

## Side-by-side comparison

| Dimension | FreeRDP | Agent | Worse? |
|---|---|---|---|
| Host disk artifact (script .ps1) | yes — `$HOME/.local/share/winpodx/windows-exec/` ~5-15s | **no** | agent better |
| Guest disk artifact (script .ps1) | no | yes — `C:\OEM\agent-runs\<guid>.ps1` ~1-3s | freerdp better |
| Wire (script body) | RDP-TLS (`/sec:tls /cert:ignore`) | plaintext HTTP on loopback + slirp | tie (both effectively local) |
| Host argv exposure of NEW password | no | no | tie |
| Host argv exposure of OLD/RDP password | yes (`/p:`) | no | agent better (out-of-scope of new-pw question, but a free win) |
| Guest argv exposure of NEW password (PS launch) | no | no | tie |
| **Guest argv exposure of NEW password (`net.exe`)** | **yes — Task Manager / WMI / ETW / Defender** | **yes — Task Manager / WMI / ETW / Defender** | **tie. Dominant exposure.** |
| Guest agent process memory (script body) | n/a | yes — `$body`, `$parsed.script`, `$bytes` until GC | freerdp better (marginal) |
| Persisted log of password content | no | no (only SHA256 hash in agent.log) | tie |
| Persisted result file containing password | no | no | tie |
| RDP framebuffer / console paint | mitigated by `-WindowStyle Hidden` + RemoteApp + `Out-Null` | n/a (no console) | agent better |
| Lifetime — script body on disk | ~5-15s host | ~1-3s guest | agent shorter |
| Lifetime — script body in memory after dispatch | host PS exits → freed | guest agent PS keeps `$body`/`$bytes` until GC (long-lived process) | freerdp shorter |

### Where they differ, in plain English

- **Agent gives up**: a marginally longer-lived in-memory copy of the
  script (and therefore the new password) inside the guest agent's
  long-running PowerShell process, until GC reclaims `$body`,
  `$parsed.script`, and `$bytes`.
- **Agent gains**: zero host-disk script artifact, zero RDP/xfreerdp
  invocation (so no host argv exposure of the OLD RDP password
  either), no PowerShell window flash, ~5x faster.

Both paths have **identical** `net.exe` argv exposure — the dominant
attack surface for "anyone watching processes inside the guest" — so
neither is meaningfully worse for the realistic threat model.

---

## Threat model check

The attacker classes that matter for new-password disclosure:

1. **Other unprivileged user on the host** — neither path lets them
   read the password. FreeRDP `.ps1` is in the user's own `$HOME`
   under default 0644 (no other user has it; this is single-user
   linux). Agent path has no host-disk artifact.
2. **Other unprivileged user inside the guest Windows VM** — both
   paths expose `net.exe` argv, readable via `Win32_Process.CommandLine`.
   Default Windows: any authenticated user can read any process's
   command line via WMI. Both paths fall the same way.
3. **Defender / EDR / Sysmon telemetry** — both paths trigger Sysmon
   EID 1 with the `net user <user> <pw>` command line. Both fall the
   same way.
4. **Memory-read inside guest agent process (e.g., crash dump leaked)**
   — agent path adds this surface; FreeRDP doesn't have it. But: an
   attacker able to read a privileged guest process's memory has
   already won (same boundary owns SAM via `lsass.exe`).
5. **Wire sniffing** — neither path crosses a physical wire. Loopback
   + slirp + RDP/TLS are all local to the host. Tie.
6. **Bad actor with disk forensics on host** — FreeRDP path leaves a
   delete-then-overwrite-eligible inode on the host briefly; agent
   path leaves nothing. Agent better.
7. **Bad actor with disk forensics on guest** — agent leaves a
   delete-then-overwrite-eligible inode on guest briefly; FreeRDP
   leaves nothing. FreeRDP better.

Symmetric overall.

---

## Verdict

**Sign-off granted.** The "neither is strictly worse" claim is
accurate. Rule #6 should be retired (task #2). The migration unblocks
the cachyos xfreerdp3 drive-redirect timeout bug without introducing
a meaningfully larger attack surface for the new password.

The original Rule #6 rationale — "expose the new password to the
agent process and to anyone who could read the agent's process
memory" — was technically true but rendered moot by `net.exe` being
the unavoidable terminus for both paths. Anyone capable of reading
the agent's process memory is already inside the guest's high-
privilege boundary, where reading `net.exe`'s argv (or, for that
matter, dumping `lsass.exe`) is strictly easier than diffing a
PowerShell heap looking for ungarbage-collected strings.

---

## Bonus hardening (optional, non-blocking)

These are cheap, principled, and don't break the agent's existing
simplicity. None are required for sign-off, but they tighten the
agent path beyond parity with the FreeRDP path.

### H1 — replace argv `net.exe` with `NetUserSetInfo` (BIGGEST WIN)

**Where**: the rotation payload itself, in
`_change_windows_password`. Both transport paths inherit this.

**Change**: instead of `& net user '<u>' '<pw>' | Out-Null`, call the
Win32 `NetUserSetInfo` API directly with `USER_INFO_1003` and a
`SecureString`-derived buffer. PowerShell can do this via
`[DirectoryServices.AccountManagement.UserPrincipal]::FindByIdentity(...).SetPassword($pw)`
or Add-Type'd P/Invoke to `netapi32!NetUserSetInfo`.

**Why this is the biggest win**: the new password no longer appears
in any process's argv at all. Eliminates Sysmon EID 1, WMI
`CommandLine`, Defender process-telemetry, Task Manager Details
column exposure in one shot. **This is the single change with the
highest leverage for both transports**, and is independent of the
agent-first migration.

**Cost**: ~10 lines of PowerShell. The host doesn't need to change.

### H2 — pass payload via stdin instead of `-File <temp.ps1>`

**Where**: `Invoke-ExecScript` in `agent.ps1` (line 146).

**Change**: spawn `powershell.exe -NoProfile -ExecutionPolicy Bypass
-Command -` with `RedirectStandardInput=$true` and write the script
bytes to `$proc.StandardInput`. Skip the temp file entirely.

**Why**: removes the on-disk artifact (#10 in the agent table). Even
though it's deleted in `finally`, "no file written" beats "file
written then unlinked" for forensic recovery.

**Cost**: stdin redirect adds ~5 lines; needs care that the existing
async stdout/stderr drain still works (it does — they're independent
streams). Tested pattern. Worth it.

### H3 — zero `$body` / `$bytes` after dispatch

**Where**: `Invoke-ExecScript` in `agent.ps1`, end of the function.

**Change**: after `WriteAllBytes` (or stdin write if H2 lands), do
`[Array]::Clear($bytes, 0, $bytes.Length)` and explicitly null out
`$body`, `$parsed.script`, `$scriptB64`, `$bytes`. Optionally call
`[GC]::Collect()` to make the freed strings less likely to linger
across the next request handler.

**Why**: shrinks the in-memory window where the password lives in the
agent's PS heap from "until next GC" to "until the next allocator
event". `[Array]::Clear` on the byte[] is the only one of these that
reliably zeroes — strings in .NET are interned/immutable and can't be
overwritten, but nulling local refs lets them be collected sooner.

**Cost**: ~4 lines. Doesn't fully close the gap (PS strings are
immutable so the b64 and decoded-string copies still exist until GC),
but it cuts the dominant lifetime.

### H4 — explicit SHA256 hash blocklist for the rotation payload

**Where**: agent.log already records `hash=<sha256>` for every /exec.

**Change**: nothing in code. Operationally, the rotation payload's
hash is constant per-`(user, newpw)` pair; if a future audit wants to
prove "this payload was the rotation call, not something else", the
hash is the link. Document this in the rotation module so an incident
response can correlate without exposing the password.

**Cost**: a docstring line. Free.

### Recommended package

If you ship one thing, ship **H1** (`NetUserSetInfo`). It eliminates
the dominant exposure for **both** transports and is independent of
the migration. **H2 + H3** are cheap follow-ups specific to the agent
path. H4 is a docs note.

None of these block the agent-first migration. Land that on its own,
then file H1 as a separate hardening PR.

---

## Sign-off

- [x] FreeRDP path enumerated
- [x] Agent path enumerated
- [x] Side-by-side comparison
- [x] Threat-model check
- [x] Verdict: agent path is not strictly worse — sign-off granted
- [x] Hardening recommendations provided

Reviewer: security-reviewer
Date: 2026-05-07
