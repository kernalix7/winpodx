# winpodx AppImage build

Builds a distro-agnostic AppImage of winpodx.

There are **two build paths**, and they produce **different** AppImages:

| | Bundles FreeRDP + Podman? | How |
|---|---|---|
| **CI (shipped release artifact)** | **Yes — fat** | `.github/workflows/appimage-publish.yml` |
| **`build.sh` (local dev)** | No — lean | python-appimage, host-resolved deps |

The release asset attached to a tag is the **fat** CI build. `build.sh`
is a quick local-dev convenience that produces a lean AppImage (Python
+ winpodx + Qt only); it is not what ships.

## Fat AppImage (CI release artifact)

`.github/workflows/appimage-publish.yml` runs on every `v*.*.*` tag
push, builds the fat AppImage, and uploads it as a release asset
alongside the `.deb` / `.rpm` / wheel artefacts.

**Bundled** (self-contained for immutable distros):

- Python 3.11 runtime (astral-sh python-build-standalone, pinned tag +
  SHA256-verified against its `.sha256` sidecar)
- `winpodx` wheel + `gui` (PySide6/Qt6) + `reverse-open`
  (Pillow / cairosvg / pyxdg) extras
- FreeRDP 3 client: `xfreerdp`, `wlfreerdp`, `sdl-freerdp` (from Fedora 41)
- Podman + `podman-compose` + `conmon` + `crun` + `netavark` +
  `slirp4netns` + `passt` / `pasta` (from Fedora 41)
- Transitive `.so` deps for the above (via `ldd`), minus the
  host-critical exclude list (glibc / libX11 / libGL / libwayland /
  libxkbcommon stay on the host)

**Still resolved from the host** (cannot be bundled in user space):

- KVM kernel module + `/dev/kvm` access + `kvm` group membership
- `/etc/subuid` + `/etc/subgid` for rootless Podman
- dockur/windows container image (pulled at first pod start)

`winpodx setup-host` runs a one-shot `pkexec` wizard for the kvm-group /
subuid / kvm-module bits; `winpodx setup` / `winpodx doctor` surface
anything else.

## Local lean build (`build.sh`)

```bash
./packaging/appimage/build.sh
# -> packaging/appimage/winpodx-<version>-x86_64.AppImage  (lean: host FreeRDP/Podman)
```

Prerequisites: Python 3.11+, `pip install python-appimage build`,
internet (pulls PySide6 + extras from PyPI). This path does **not**
bundle the system layer — it relies on the host's FreeRDP / Podman,
exactly like the wheel / `.deb` / `.rpm`.

## Licensing

winpodx itself is **MIT** and stays MIT — the bundled GPL-2.0
`podman-compose` is invoked only as a separate executable via
subprocess (mere aggregation under GPLv2 §2; no linking, no derivative
work), so its copyleft does not reach winpodx.

The **fat** CI AppImage redistributes third-party binaries, so their
license + NOTICE texts travel inside it:

- winpodx `LICENSE` (MIT) + `THIRD_PARTY_LICENSES.md` at
  `${APPDIR}/usr/share/doc/winpodx/`
- every bundled Fedora package's license (FreeRDP / Podman /
  podman-compose / conmon / crun / netavark / slirp4netns / passt —
  Apache-2.0 and, for podman-compose, GPL-2.0) under
  `${APPDIR}/usr/share/doc/winpodx/third-party/<pkg>/`
- a GPL-2.0 source-offer note for podman-compose in that directory
- the python-build-standalone (PSF) license
- PySide6 / Qt6 (LGPL-3.0), cairosvg (LGPL-3.0), pyxdg (LGPL-2.0),
  Pillow (HPND) carry their license in their `*.dist-info` inside
  `${APPDIR}/opt/python`. The AppImage SquashFS is `--appimage-extract`-able,
  satisfying LGPL relinking.

See the repo-root `THIRD_PARTY_LICENSES.md` for the full breakdown,
including the bundled rdprrap (MIT + vendored Apache-2.0 rdpwrap) and
rcedit (MIT) that ship in every channel via the wheel's OEM payload.
