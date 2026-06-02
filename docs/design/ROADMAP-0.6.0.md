# WinPodX 0.6.0 — Consolidation & UX Release

## Goal

Single-pass cleanup of accumulated structural debt: unify duplicated implementations, sharpen the command surface, and rethink the AppImage so it stops being fragile. After 0.6.0, the shells (`install.sh`, `uninstall.sh`, `postinst`, `postrm`) are thin wrappers and the Python CLI is the single source of truth.

This roadmap is the cut-list. Items move from "in scope" to "done" only when the corresponding PR has merged to `main`. Deferred items are recorded so we don't lose them; "won't do" items are decided no.

## In scope (must land in 0.6.0)

### Provisioning + AppImage

- **A. Thin AppImage.** Drop bundled podman / podman-compose / conmon / crun / netavark / pasta. Keep FreeRDP + Python + Qt + WinPodX. Require host podman/docker/libvirt (same model as `install.sh`). Closes #357 and #363 by *removing the root cause* (bundled podman shadowing or poisoning the host stack) instead of patching around it. AppImage shrinks ~150 MB → ~50 MB. `_hostenv` helper collapses to an `LD_LIBRARY_PATH` strip (no host-first `PATH` games needed once nothing is shadowing). **Effort M / Risk M** (recipe rebuild + reporter smoke).
- **B. P1 — `winpodx provision` single source of truth.** New `core/provisioner.finish_provisioning(cfg, *, wait_timeout, require_agent, with_reverse_open)` consolidates the `wait-ready → apply-fixes → discovery → reverse-open` chain currently duplicated in `install.sh`, `setup_cmd._run_full_provision`, `migrate`, and `pending.resume`. `install.sh` collapses ~140 lines → ~5. `setup --create-only` is dropped. **Effort L / Risk M** (load-bearing; needs real-Windows smoke). **Absorbs I.**
- **I. Qt GUI bring-up → `winpodx provision`.** `gui/_main_window_bringup.py` is the 5th copy of the chain. Folded into B. **Effort S (inside B).**

### Hardcoding / single source

- **C. P2 — port 8765 constant.** `core/agent.AGENT_PORT` referenced by all host-side callers (compose generation, checks, guest_sync, `install.sh` health-poll). The guest-side `agent.ps1` keeps its own literal (can't import Python); the pairing is documented. 8 literals → 1 host SoT. **Effort S / Risk Low.**
- **D. P3 — dependency / health single detector.** Extend `utils/deps.py:check_all` (+ kvm, + podman-major gate). `deps_quickcheck`, `doctor`, `setup_cmd` all consume it. Drop the re-hardcoded freerdp lists. `install.sh` keeps a minimal pre-venv bash probe (genuinely shell-unique). 5 places → 1 Python SoT + 1 minimal shell probe. **Effort M / Risk Low-M.**
- **E. P4 — backend selection helper.** `core/backend/select.py:choose_backend(prefer, present)` implementing `podman → docker → libvirt` + the podman major-version gate. `install.sh` and `setup_cmd` both consume it. 3 places → 1. **Effort S-M / Risk Low.**
- **F. P5 — version + edition single source.** `__init__.__version__` reads from package metadata. Windows-edition help/locale strings generate from `_KNOWN_WIN_VERSIONS`. Fix stale `packaging/rpm/winpodx.spec:8` literal. **Effort S / Risk Low.**

### Command surface

- **G. Command taxonomy reorg.**
  - Diagnostics: `info` + `check` + `doctor` → single `winpodx doctor` (`--json` machine-readable, `--quick` fast subset, `--fix` see K).
  - `pod` keeps lifecycle only: `start | stop | restart | recreate | status | wait-ready`.
  - New `winpodx guest`: `apply-fixes`, `sync`, `sync-password`, `multi-session`, `recover-oem`.
  - New `winpodx install`: `status`, `resume`, `grow-disk`, `disk-usage`.
  - Deprecation aliases: old `pod apply-fixes` etc. keep working through 0.6.x with a deprecation note; removed in 0.7.0.
  - `install.sh` / `uninstall.sh` / `postinst` / `postrm` call only public `winpodx <command>` (no internal Python imports).
  - **Effort M-L / Risk Low** with aliases.
- **K. `winpodx doctor --fix` auto-remediation.** ✅ Implemented (PR `feat/doctor-fix`); guest-touching fixers smoke-gated before merge. Common recoverable failures: dead agent → keepalive kick, stale lock files → purge, missing desktop entries → re-register, oem-version drift → trigger guest-sync. Each fix idempotent. **Effort S-M / Risk Low.**

### Docs + ops

- **H. Documentation refresh (mandatory for G).** README, FEATURES, COMPARISON, ARCHITECTURE, LIFECYCLE, USAGE + their `docs/*.ko.md` mirrors. New-command table + old→new alias map. Install / upgrade flows reflect Thin AppImage + `winpodx provision`. **Effort M / Risk Low.**
- **J. Config schema versioning.** `cfg.schema_version: int = 1` field + load-time migration hook. 0.6.0 doesn't change config structure, but the marker lets 0.7.0+ migrate cleanly. **Effort S / Risk Low.**
- **L. Logging hygiene pass.** Consistent levels across modules (some INFO too noisy, some WARNING missing). One-pass review. **Effort S / Risk Low.**
- **M. `install.sh`: drop discovery 6× retry.** Agent keepalive (#359) proven; 6× is overkill. 1-2× max. Faster first run. **Effort S / Risk Low.**
- **N. Packaging single-source enforcement.** `flake.nix` already reads `pyproject` (good); fix `winpodx.spec:8` stale literal + a lint that catches future drift. Subset of F. **Effort S / Risk Low.**

## Execution order

1. **C** — port constant (warm-up, fastest, low risk).
2. **J** — `schema_version` (cheap future-proofing before big changes).
3. **F + N** — version / edition / packaging single-source (quick).
4. **D** — dep check (prerequisite for cleaner E and `install.sh`).
5. **E** — backend select (prerequisite for cleaner A and `install.sh`).
6. **A** — Thin AppImage (root-cause kill for #357 / #363).
7. **B + I** — provision unify + GUI bring-up (biggest piece; real-Windows smoke gate).
8. **G** — command taxonomy (depends on B; deprecation aliases for back-compat).
9. **K** — `doctor --fix` (pairs with G's doctor).
10. **L** — logging hygiene (pass once everything else has landed).
11. **M** — `install.sh` retry trim (final tightening).
12. **H** — docs refresh (last; reflects final shapes; ko mirrors after EN approved).
13. Cut `v0.6.0` + `REL-v0.6.0`.

Real-Windows smoke gate applies to: **A** (rebuilt AppImage), **B / I** (provision chain), **G** (command renames touching guest paths), **K** (--fix actions). Other items don't need it.

## Deferred to 0.7.0+ (tracked, not started)

- **Agent authentication.** The listener is localhost-only and only reachable from the host via dockur's port map → low risk today. Future: HMAC or shared-token on `/exec` to harden against host-local malice.
- **Real-Windows smoke CI lane.** A cloud Windows VM in CI to gate guest-side install changes. Expensive (~$50/mo + setup); manual user smoke continues for now.
- **libvirt backend feature-completeness.** podman is primary; libvirt has rougher edges (storage growth, multi-session). Separate effort.
- **Manual mode (no backend) testing.** Niche path; document constraints rather than build a test surface.
- **Performance pass.** Discovery cache, FreeRDP launch latency, full-rescan vs. incremental. Current is adequate.
- **Reverse-open edge cases.** Special chars in paths, multi-monitor positioning, MIME priority conflicts. File as encountered.
- **i18n drift detection.** A lint that catches keys present in only some `locale/*.json`. Small but needs CI infra.
- **Content-addressed `oem_bundle`.** Currently a counter (#359 bumped to 26). A hash would be drift-proof. Backwards-compat work to migrate existing stamps.
- **OBS publish race.** Timing race on the publish workflow (intermittent re-runs needed). Workflow refactor.
- **Lock-file lifecycle.** Stale lock cleanup in `~/.local/share/winpodx/run/` is hit-or-miss. Owned-pid check + boot-time sweep.
- **PowerShell window flash regression test.** A test that asserts no host→guest op ends up on `run_in_windows` (the FreeRDP path that flashes) outside of explicitly-flash-okay callers. Locks in the #355 win.

## Won't do

- **External telemetry.** Decided. Local `FreeRDP-fallback` log markers (already shipped) substitute for the only signal we needed.
- **New opt-in flags for behaviour.** Decided. Features ship default-on; behaviour-changing flags are debt.
- **Bundled-podman "actually works".** Option A acknowledges the fat AppImage's "self-contained" promise was always limited by the host-systemd dependency. Thin makes that limit explicit instead of pretending.
