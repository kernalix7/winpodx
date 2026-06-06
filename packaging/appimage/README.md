# WinPodX AppImage build

Builds a distro-agnostic **Thin** AppImage of WinPodX (0.6.0 item A).

There are **two build paths**, both producing a Thin AppImage:

| | What is bundled | How |
|---|---|---|
| **CI (shipped release artifact)** | Python + winpodx + Qt + **FreeRDP 3** | `.github/workflows/appimage-publish.yml` |
| **`build.sh` (local dev)** | Python + winpodx + Qt | python-appimage, host-resolved FreeRDP |

The release asset attached to a tag is the CI build (the only difference vs
`build.sh` is the bundled FreeRDP overlay). Neither build bundles the
container runtime.

## Prerequisites the user provides on the host

Same model as `install.sh`:

- **`podman` (recommended)** or `docker` — installed via the
  host distro package manager. Rootless podman fundamentally needs host
  systemd / subuid integration that an AppImage can't carry, so WinPodX
  cannot ship one that works.
- KVM kernel module + `/dev/kvm` access + `kvm` group membership.
- `/etc/subuid` + `/etc/subgid` for rootless Podman.

`winpodx setup-host` runs a one-shot `pkexec` wizard for the kvm-group /
subuid / kvm-module bits; `winpodx setup` / `winpodx doctor` surface
anything else.

The dockur/windows container image (~500MB–1GB) is pulled at first pod
start via the host podman/docker.

## Why Thin (was Fat before 0.6.0)

The pre-0.6.0 Fat AppImage bundled the entire podman stack
(podman / podman-compose / conmon / crun / netavark / aardvark-dns / pasta /
slirp4netns) into `${APPDIR}/usr/bin` and prepended that directory to PATH
+ `${APPDIR}/usr/lib` to LD_LIBRARY_PATH. That broke every host that
already had a working podman:

- **#357 (Ubuntu 26.04)** — bundled `podman-compose` resolved first,
  probed for a podman it couldn't drive standalone, died with
  `it seems that you do not have podman installed`.
- **#363 (Fedora Bluefin)** — host `systemd-run` rootless aardvark-dns
  spawn loaded the bundled `libcrypto.so.3` from the inherited
  `LD_LIBRARY_PATH`, died with `OPENSSL_3.4.0 not found`.

PR #365 patched around it with a host-first `_hostenv` helper. 0.6.0
item A removes the root cause: drop the entire container stack, require
host podman/docker (same model as `install.sh`) and stops fighting
the host. That alone only reached ~274 MB (from ~296 MB fat), so a
companion Qt6 slim (`slim-pyside6.sh`) strips the unused Qt6 modules
PySide6 bundles — winpodx links only QtCore/QtGui/QtWidgets/QtSvg/
QtDBus — bringing the AppImage to ~110 MB. `_hostenv`
collapses to an `LD_LIBRARY_PATH` strip (still needed: bundled FreeRDP /
Python / Qt keep the AppImage's `LD_LIBRARY_PATH`, and host helpers
spawned by the host runtime must not inherit bundled libcrypto / libssl).

## CI release artifact (Thin)

`.github/workflows/appimage-publish.yml` runs on every `v*.*.*` tag
push, builds the Thin AppImage, and uploads it as a release asset
alongside the `.deb` / `.rpm` / wheel artefacts.

**Bundled:**

- Python 3.11 runtime (astral-sh python-build-standalone, pinned tag +
  SHA256-verified against its `.sha256` sidecar)
- `winpodx` wheel + `gui` (PySide6/Qt6) + `reverse-open`
  (Pillow / cairosvg / pyxdg) extras
- FreeRDP 3 client: `xfreerdp3`, `wlfreerdp3`, `sdl-freerdp3` (from
  Fedora 41) — leaf binary, doesn't spawn host helpers
- Transitive `.so` deps for the above (via `ldd`), minus the
  host-critical exclude list (glibc / libX11 / libGL / libwayland /
  libxkbcommon stay on the host)

**NOT bundled** (Thin acknowledges these have to come from the host):

- Container runtime: `podman` / `podman-compose` / `conmon` / `crun` /
  `netavark` / `aardvark-dns` / `pasta` / `passt` / `slirp4netns`
- KVM kernel module + `/dev/kvm` access + `kvm` group membership
- `/etc/subuid` + `/etc/subgid` for rootless Podman
- dockur/windows container image (pulled at first pod start)

## Local lean build (`build.sh`)

```bash
./packaging/appimage/build.sh
# -> packaging/appimage/winpodx-<version>-x86_64.AppImage  (lean: host FreeRDP too)
```

Prerequisites: Python 3.11+, `pip install python-appimage build`,
internet (pulls PySide6 + extras from PyPI). This path does **not**
bundle FreeRDP either — it relies on the host's FreeRDP / podman,
exactly like the wheel / `.deb` / `.rpm`.

## Licensing

WinPodX itself is **MIT** and stays MIT. The Thin AppImage redistributes
only the FreeRDP 3 client stack from Fedora 41, so its license + NOTICE
texts travel inside it:

- WinPodX `LICENSE` (MIT) + `THIRD_PARTY_LICENSES.md` at
  `${APPDIR}/usr/share/doc/winpodx/`
- bundled FreeRDP package licenses (Apache-2.0: `freerdp-libs`,
  `libwinpr`) under `${APPDIR}/usr/share/doc/winpodx/third-party/<pkg>/`
- the python-build-standalone (PSF) license
- PySide6 / Qt6 (LGPL-3.0), cairosvg (LGPL-3.0), pyxdg (LGPL-2.0),
  Pillow (HPND) carry their license in their `*.dist-info` inside
  `${APPDIR}/opt/python`. The AppImage SquashFS is `--appimage-extract`-able,
  satisfying LGPL relinking.

The pre-Thin podman-stack license dirs (`podman/`, `podman-compose/`,
`conmon/`, `crun/`, `netavark/`, `passt/`, `slirp4netns/`) stay vendored
in-repo at `packaging/appimage/licenses/` for provenance + to make a
future re-bundling cheap, but they no longer ship inside the AppImage
because the binaries they cover are no longer bundled.

See the repo-root `THIRD_PARTY_LICENSES.md` for the full breakdown,
including the bundled rdprrap (MIT + vendored Apache-2.0 rdpwrap) and
rcedit (MIT) that ship in every channel via the wheel's OEM payload.
