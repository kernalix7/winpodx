# Provision-unify fidelity audit (0.6.0 item B follow-up)

The first cut of item B (PR #375) consolidated the four post-create
provisioning paths (`install.sh`, `setup_cmd._run_full_provision`,
`migrate`, `pending.resume`) into `core/provisioner.finish_provisioning`.
It homogenised them by conforming everything to the silent
`wait_for_windows_responsive` + a single `require_agent` gate — and in doing
so it **dropped behaviours that each path had accumulated for specific
issues**. This document records the audit (behaviour → source → issue) and
the faithful restoration.

Merging spread-out logic is not "pick one and unify": you analyse each path's
current behaviour, trace it to the issue / PR that added it, and decide
per-behaviour what to keep vs. discard. The first cut skipped that step. This
is the redo.

## Regressions found (each traced to its issue)

| # | Lost behaviour | Original source | Issue | Symptom |
|---|---|---|---|---|
| 1 | **Dynamic wait** — live self-erasing progress line + wget-ETA deadline auto-extension | `cli/pod._wait_ready` (`pod wait-ready --logs`) | **#126** (xiyeming, 86-min ISO download) | Fresh install showed one `[wait_ready] up to 3600s` line then silence for the whole Windows boot — looked frozen; slow links could time out |
| 2 | **`WINPODX_REQUIRE_AGENT=1` propagation to discovery/apply** | pre-B `install.sh` `export WINPODX_REQUIRE_AGENT=1` | **#271 / agent-first** | `finish_provisioning(require_agent=True)` gated only the one-shot Stage-2 settle re-probe; discovery still fell back to FreeRDP (3× `FreeRDP-fallback` in a real smoke) → FreeRDP can kick install.bat's autologon session during first boot |
| 3 | **Upgrade → `winpodx migrate`** (guest_sync + image-pin + release notes) | pre-B `install.sh` `"$SYMLINK" migrate --non-interactive` on existing-config | guest-sync-on-upgrade feature | New `install.sh` ran `provision` for BOTH fresh + upgrade; `provision` doesn't guest_sync, so upgraded guests kept STALE `agent.ps1` / OEM scripts |
| 4 | **`pending.resume` "migrate" step → guest_sync** | pre-B `pending.resume` ran `winpodx migrate` | same | B mapped the "migrate" pending step to `finish_provisioning`'s apply-fixes (no guest_sync), so a deferred upgrade resumed via pending also kept stale guest scripts |

Behaviours B preserved correctly (verified, not regressed): Ctrl+C / SIGTERM
(rc 130/143) clean bail, `no such container` → mark-pending, `tee` of the
provision output, the per-step `.pending_setup` markers.

## Faithful restoration

- **`finish_provisioning` gains `wait_fn`** (injection, keeps `core` cli-free).
  CLI `provision`, interactive `setup`, and `migrate` inject the rich
  `cli/pod._wait_ready` (the #126 dynamic deadline + live line). `None`
  (pending.resume, GUI) keeps the silent wait. [#1]
- **`finish_provisioning` exports `WINPODX_REQUIRE_AGENT=1`** for the duration
  of the apply + discovery stages when `require_agent=True`, restoring it
  after. `_run_discovery_with_retry` takes `require_agent` and, on a
  persistent `agent_unavailable`, raises `ProvisionAgentUnavailable` so the
  caller defers (exit 5 → pending) rather than recording a generic failure.
  `_cmd_provision` already maps `ProvisionAgentUnavailable` → exit 5. [#2]
- **`install.sh` branches fresh vs upgrade** on the pre-setup
  `IS_FRESH_INSTALL` snapshot: fresh → `winpodx provision --require-agent`;
  upgrade → `winpodx migrate --non-interactive`. [#3]
- **`migrate` runs reverse-open** (`with_reverse_open=cfg.reverse_open.enabled`)
  since the upgrade path is now `migrate`-only and no longer has install.sh's
  separate host-open step. [#3]
- **`pending.resume` "migrate" step calls `maybe_autosync`** (idempotent
  guest_sync) so a deferred upgrade resumed later still refreshes guest
  scripts. [#4]

## Tests

- `test_finish_provisioning.py`: `wait_fn` override used / silent fallback /
  timeout-skips-downstream; `require_agent` exports + restores
  `WINPODX_REQUIRE_AGENT`; `require_agent=False` leaves env untouched;
  discovery `agent_unavailable` → `ProvisionAgentUnavailable`;
  `_run_discovery_with_retry` escalation vs. re-raise.
- `test_install_sh_provision.py`: fresh → `provision --require-agent`,
  upgrade → `migrate`, the `IS_FRESH_INSTALL` branch, old inline chain steps
  still absent.
- `test_pending.py`: "migrate" step runs `maybe_autosync`; left pending when
  guest_sync fails.

Real-Windows smoke is still the gate (fresh install: live progress + no
FreeRDP-fallback during discovery; upgrade: guest scripts refreshed).
