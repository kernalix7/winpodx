# Guest sync — apply host-side updates to a running guest without reinstall

## Problem

Upgrading winpodx on the host updates the host binary, but the **guest-side**
artifacts that were staged at first install go stale until the user wipes and
reinstalls Windows:

- `C:\OEM\agent.ps1` — the in-guest agent (bind/retry logic, endpoints).
- `install.bat`-derived runtime state — urlacl reservation (#269), RDP /
  multi-session registry, Defender exclusions, TermService recovery, NIC tweaks.
- Guest binaries — rdprrap zip, `shim.exe` (reverse-open), `rcedit.exe`.

`winpodx pod apply-fixes` already re-applies *some* idempotent registry fixes,
but it does **not** refresh `agent.ps1`, the urlacl reservation, or the guest
binaries — and it is manual. Users on an upgraded host silently run an old
guest until they reinstall.

## Key enabler

`/oem` is a **live bind mount** of the host's `config/oem`
(`{oem_dir}:/oem:Z` in compose.py). So after a host upgrade the container's
`/oem` already contains the new agent.ps1 / rdprrap / scripts — no image
rebuild. Delivery into the guest is the same channel `winpodx pod recover-oem`
already uses (tar `/oem` → container HTTP :8766 → guest pulls via the QEMU NAT
gateway `10.0.2.2`), except for sync the **agent is alive**, so it runs over
`/exec` automatically instead of noVNC paste.

## Version stamp

The guest records what provisioned it at
`C:\winpodx\install-state\guest_version.json`:

```json
{ "winpodx": "0.5.8", "oem_bundle": "25" }
```

- Host current = `winpodx.__version__` + `core.info._bundled_oem_version()`.
- `read_guest_version()` reads the file via `/exec`; missing/old/unparseable →
  treated as "needs sync".
- Written **only after** a fully successful sync (or by the installer on a
  fresh provision).

`guest_sync_needed(cfg)` returns True when the guest stamp differs from the
host pair (or is absent).

## Sync flow (`core/guest_sync.py :: sync_guest`)

All steps idempotent; ordered so a partial failure is safe to re-run.

1. **Deliver `/oem` to the guest.** Tar `/oem` in the container into a
   dedicated serve dir, start the :8766 HTTP server (reuse the recover-oem
   container ops), then over `/exec`:
   `Invoke-WebRequest http://10.0.2.2:8766/oem.tar.gz` → `tar -xzf` into
   `C:\OEM`. This refreshes `agent.ps1`, rdprrap, `shim.exe`, `rcedit.exe`,
   and the helper scripts in one shot. **`install.bat` is NOT run** — it
   contains one-shot first-boot logic (autologon, account setup) that must not
   re-run on a live install.
2. **urlacl reservation** (#269) — port install.bat's netsh block to `/exec`:
   delete the overlapping 8765 reservations, then
   `netsh http add urlacl url=http://+:8765/ sddl=D:(A;;GX;;;WD)`.
3. **Idempotent registry/runtime fixes** — call the existing
   `provisioner.apply_windows_runtime_fixes(cfg)` (max_sessions, rdp_timeouts,
   oem_runtime_fixes, vbs_launchers, multi_session). This also re-activates
   rdprrap against the refreshed binaries.
4. **Restart the agent** — the agent is what `/exec` runs through, so it can't
   kill itself synchronously. Register a **one-shot scheduled task** that fires
   in ~5 s to stop the current agent process and relaunch `C:\OEM\agent.ps1`
   (the HKCU\Run command). The `/exec` call returns before the task fires; the
   new agent binds 8765 with the now-correct urlacl.
5. **Write the version stamp** — only if steps 1–3 succeeded (step 4 is
   fire-and-forget; readiness is reconfirmed by the caller).

`sync_guest` returns a per-step result map (like `apply_windows_runtime_fixes`)
so CLI/GUI can render rows.

## Triggers

- **Auto** (default on): after the pod is responsive (`provisioner.ensure_ready`
  / `pod wait-ready` tail), if `guest_sync_needed` → run `sync_guest`. Cheap
  no-op when versions match. Gated to podman/docker.
- **Manual**: `winpodx pod sync-guest [--force]` and a GUI **Tools → Sync
  Guest** action. `--force` re-syncs even when the stamp matches.

Config: `pod.guest_autosync` (bool, default True). `false` = only manual.

## Risks / mitigations

| Risk | Mitigation |
|---|---|
| Agent restart drops the `/exec` we're inside | one-shot scheduled task fires *after* the call returns; never `Stop-Process` the agent synchronously |
| rdprrap re-activation disconnects a live session | auto-sync is post-readiness at pod start (no user session yet); manual sync warns |
| Partial sync leaves mixed state | every step idempotent + re-runnable; stamp written only on success so the next start retries |
| `/oem` tar exposes `/storage` | serve a dedicated dir with only `oem.tar.gz` (same as recover-oem) |
| Download integrity | size + extract check; agent_token already shared, no new trust boundary |

## Real-Windows smoke checklist (gate before merge/release)

Guest-side `/exec` work — pwsh-on-Linux Pester is **not** sufficient.

1. Upgrade host winpodx (bump `oem_bundle`), start an existing pod.
   → auto-sync runs; `guest_version.json` updates to the new pair.
2. `C:\OEM\agent.ps1` matches the new host copy (hash/length).
3. Agent rebinds 8765 after the restart task (no manual step); `winpodx check`
   shows agent reachable.
4. `netsh http show urlacl url=http://+:8765/` shows the WD SID reservation.
5. rdprrap still active; multi-session still works.
6. `winpodx pod sync-guest --force` on an up-to-date guest is a clean no-op
   (no session disruption, exit 0).
7. Re-run sync after killing it mid-flight → converges (idempotency).
