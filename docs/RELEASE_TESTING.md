<!-- SPDX-License-Identifier: MIT -->
# Release Testing Checklist

**English** | [한국어](RELEASE_TESTING.ko.md)

A repeatable pass so a release doesn't ship a regression that the automated
tests can't see. Most winpodx breakage is **guest-side** — the Windows VM,
FreeRDP/RAIL, the OEM scripts, the install flow — which `pytest` on a Linux CI
runner cannot exercise. This checklist makes the manual, real-Windows part
explicit instead of relying on memory.

> **The load-bearing rule:** any change that touches the guest (`config/oem/`,
> `scripts/windows/`, the reverse-open shim, `compose` ports/QEMU args, the
> agent, install.bat, discovery, RAIL launch) **must be smoke-tested against a
> real Windows guest before merge** — not just `pytest`. Several releases broke
> because a guest-side change passed CI and was merged without a real-Windows
> smoke (media_monitor #613/#638, the 4445/`USER_PORTS` port bugs #616).

## When to run

- **Before tagging any release** — run the relevant sections of the feature
  checklist + at least one full fresh-install smoke.
- **After any guest-side change, before merge** — the guest-side smoke for the
  surface you touched (this is the gate that catches what CI can't).
- **After an `install.sh` / `compose` / OEM change** — the install + update
  smoke on a clean machine *and* an upgrade-over-existing machine.

## 1. Automated gates (CI — must be green)

These run on every PR; they are the floor, not the whole story.

- [ ] `lint` — `ruff check src/ tests/` + `ruff format --check src/ tests/`
- [ ] `test (3.9 … 3.14)` — `pytest tests/ -v` on every supported Python
- [ ] `audit` — `pip-audit`
- [ ] `discover-apps-ps` — PowerShell discovery script syntax
- [ ] `verify_versions` — `pyproject.toml` ↔ `packaging/rpm/winpodx.spec` ↔ installed metadata agree

Local pre-push mirror of CI lint (whole tree, not per-file):
`ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -q`

## 2. Guest-side smoke (real Windows — CI cannot do this)

Run on a real install. `winpodx doctor` after each step is a quick health gate.

### Install / update
- [ ] **Fresh install** completes: `curl … install.sh | bash -s -- --main` → reaches
      `Provisioning complete` (no `[3/4]`/`[4/4]` hang, no `Invalid port`, agent up).
- [ ] **Update over existing** (`--main` again): regenerates compose, recreates +
      starts a stopped pod, runs apply-fixes (`guest_share: ok`, etc.) — no `Skipping`.
- [ ] **`--ref <branch>`** installs the branch's **latest** commit (verify
      `git -C ~/.local/bin/winpodx-app log -1` advanced — don't assume a re-run updated).
- [ ] `apply_fixes: N/N fixes OK` (currently 7) + `discovery: N apps` + `reverse_open: ok`.

### Apps / RDP / RAIL
- [ ] `winpodx app run desktop` — full desktop renders.
- [ ] `winpodx app run <app>` — RAIL window appears (own window, taskbar entry), not the
      logon/lock screen, no `Invalid appWindow` corruption.
- [ ] `winpodx app refresh` — completes without `/exec timed out` (slow/cold guest too).
- [ ] Multiple app windows / multi-session (rdprrap) work.

### Reverse-open (#616) — KDE host
- [ ] Host file under `\\tsclient\home` → *Open with* a Linux app → opens.
- [ ] **Guest-local file** (Windows Desktop `C:\Users\…`) → *Open with* a Linux app →
      opens on the host; edits save back. (Requires kio-fuse; `winpodx doctor` `guest_mount`.)

### Dashboard / GUI / tray
- [ ] Dashboard Pod / CPU / **RAM** / **Disk** gauges all show numbers (not `n/a`).
- [ ] Settings → **UI Language** switches the interface; **Idle Action** (Pause/Stop) present.
- [ ] Tray icon appears; submenus (sessions / USB) open on KDE Plasma.

### Power / idle / devices / disguise / debloat
- [ ] Idle **Pause** (default) suspends + auto-resumes on launch.
- [ ] Idle **Stop** (`pod.idle_action=stop`) stops the pod (frees RAM); next launch cold-boots.
- [ ] `winpodx device` USB attach/detach (live hot-plug).
- [ ] Disguise (`pod.disguise_level balanced|max`) boots + RDP renders (no #557 black screen).
- [ ] `winpodx debloat` + undo run without breaking activation/updates.
- [ ] `winpodx rotate-password` keeps host config ↔ guest account in sync.

## 3. Platform / channel matrix

Spot-check across the surfaces that diverge; full coverage isn't required every
release, but rotate so each is hit periodically.

| Axis | Cover |
|------|-------|
| Install channel | pip/curl · AppImage · AUR · RPM (Fedora/openSUSE/AlmaLinux) · `.deb` (Debian/Ubuntu) |
| Desktop | KDE Plasma · GNOME (note: reverse-open guest-disk is KDE/kio-fuse only) |
| Display | Wayland (XWayland RAIL) · X11 |
| Backend | Podman (default) · Docker |

## 4. Release sign-off

- [ ] Version bumped in `pyproject.toml` + `packaging/rpm/winpodx.spec` + `debian/changelog`
      (`python scripts/ci/verify_versions.py` → consistent).
- [ ] `CHANGELOG.md` **and** `docs/CHANGELOG.ko.md`: `[X.Y.Z] - <date>` + a
      **### Contributors** section thanking every external reporter/contributor
      (`gh issue view <N>` for the author; exclude the maintainer).
- [ ] `README.md` + `docs/README.ko.md` "active development" line + summary updated.
- [ ] CI green on the release commit.
- [ ] Push **both** tags: `vX.Y.Z` (publish workflows: OBS / RHEL / deb / AUR / AppImage)
      **and** `REL-vX.Y.Z` (Release workflow → GitHub release body from the CHANGELOG section).
- [ ] GitHub release published with the **Contributors** section + all assets
      (wheel, sdist, AppImage, RPMs, debs).
- [ ] Comment "shipped in vX.Y.Z" on the fixed issues; close the ones with no
      outstanding reporter question.
