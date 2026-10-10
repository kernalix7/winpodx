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
alongside the `.deb` / `.rpm` / wheel artefacts. A `workflow_dispatch`
build runs the same pipeline but never attaches a release — it only
emits build artifacts.

**Bundled:**

- Python 3.11.17+20261009 runtime (astral-sh python-build-standalone,
  pinned URL + SHA256, license inventory copied by relative path)
- `winpodx` wheel + `gui` (PySide6/Qt6) + `reverse-open`
  (Pillow / cairosvg / pyxdg) extras — the runtime pip closure is
  version-locked and hash-recorded via pip's `--report`
- FreeRDP 3 client 3.32.1+dfsg-0ubuntu0.24.04.1 (`xfreerdp3`,
  `wlfreerdp3`) from a digest-pinned Ubuntu 24.04 image — leaf binaries,
  they don't spawn host helpers
- Transitive `.so` deps for the above (via `ldd`), minus the
  host-critical exclude list (glibc / libX11 / libGL / libwayland /
  libxkbcommon stay on the host)

**NOT bundled** (Thin acknowledges these have to come from the host):

- Container runtime: `podman` / `podman-compose` / `conmon` / `crun` /
  `netavark` / `aardvark-dns` / `pasta` / `passt` / `slirp4netns`
- KVM kernel module + `/dev/kvm` access + `kvm` group membership
- `/etc/subuid` + `/etc/subgid` for rootless Podman
- dockur/windows container image (pulled at first pod start)

## Provenance: fail-closed collection + pinned inputs

The build records these input and delivery scopes
(`packaging/appimage/provenance_pins.py` + `provenance_collect.py`):

- **Ubuntu base image** pinned by digest
  (`sha256:f610ab94…`); the five FreeRDP dpkg packages pinned to exact
  versions — `apt` fails if the pinned version is unavailable.
- **appimagetool 1.9.1** pinned URL + SHA256; the **Type 2 runtime**
  pinned (continuous release @ commit `8f39b89e…`, SHA256-verified) and
  passed via `--runtime-file` so appimagetool never downloads a floating
  runtime.
- **Portable Python** pinned URL + SHA256; the python-build-standalone
  license inventory (LICENSE + one `LICENSE.<dep>.txt` per linked
  library) is copied by relative path, never collapsed into one file.
- **Runtime wheels** version-constrained (`PySide6==6.12.0`, `Pillow==12.3.0`,
  `CairoSVG==2.9.1`, `pyxdg==0.28` + transitives); pip's JSON report
  records every resolved wheel URL + sha256. This report is a post-resolution
  record, not a pre-install `--require-hashes` lock. Qt split-wheel transitives
  are recorded too; modules removed by slimming are not conveyed.
- **Every conveyed FreeRDP file** is recorded in a sha256 manifest
  (`bundle-system-bins.sh`), then mapped by the collector to its dpkg
  owner, exact version, copyright + referenced common-license texts
  (copied into the AppDir), and the signed-archive source URLs from
  Ubuntu's `Sources` index. Unknown owner / license / text / source
  **blocks the build** — nothing is inferred.
- **ELF checks**: every conveyed ELF must stay under the `GLIBC_2.39`
  symbol ceiling, and the actual `xfreerdp3 --version` output is
  recorded (no RDP connection is made during the build).

Emitted per build (workflow artifacts, always; and for tag builds also
attached to the GitHub Release next to the AppImage):

- `PROVENANCE.json` — machine-readable record of everything above
- `SOURCE-OFFER.txt` — the corresponding-source delivery index: source
  archive URLs + checksums (Ubuntu orig + Debian patch tarballs,
  Qt everywhere source, pyside-setup source, CPython, PyPI sdists,
  appimagetool + Type 2 runtime sources) plus LGPL replacement /
  relinking directions. Also shipped inside the AppImage at
  `usr/share/doc/winpodx/SOURCE-OFFER.txt`.
- Input locks: `conveyed-files.tsv`, `dpkg-provenance.json`,
  `wheels-provenance.json`, `pip-report.json`, `license-inventory/`
- `winpodx-appimage-sources.tar.gz` — publicly attached beside the binary:
  SHA-verified Qt/PySide/CPython/PyPI sources, Ubuntu orig + Debian patches
  and descriptors, static runtime sources and patches, portable Python full
  distribution (including relinkable objects), recipes, original notices,
  `source-mirror.json`, and input locks. Ubuntu hashes are rechecked against
  the recorded signed-index checksums before delivery.
- `SHA256SUMS` — hashes the actual AppImage, source archive and both manifests.
  Sources receive equivalent network access on the release page; an expiring
  Actions artifact or an upstream URL alone is not the delivery mechanism.

The appended Type 2 runtime's MIT text and static libfuse 3.15.0,
squashfuse 0.5.2, musl, zstd, zlib and mimalloc original notices are preserved
under `third-party/type2-runtime/`. Runtime scripts and `mount.c.diff` are
included in the source bundle. Alpine 3.21 recipes and sources reconstruct
the dependency environment at the release date. Upstream's runtime build
did not publish an exact apk/image lock: those reconstructed versions are
not a proof of the exact static dependency closure or a reproducible runtime.

Portable Python native notice coverage uses the matching SHA-pinned full
distribution's `PYTHON.json`, with interpreter/libpython byte comparisons
against `install_only`, rather than `ldd` or dist-info alone. Missing full
distribution notices are taken from its pinned recipe or verified native
source. The full distribution and native source archives accompany the binary.
This scope does not certify every wheel-vendored native byte.

## Local lean build (`build.sh`)

```bash
./packaging/appimage/build.sh
# -> packaging/appimage/winpodx-<version>-x86_64.AppImage  (lean: host FreeRDP too)
```

Prerequisites: Python 3.11+, `pip install python-appimage build`,
internet (pulls PySide6 + extras from PyPI). This path does **not**
bundle FreeRDP either — it relies on the host's FreeRDP / podman,
exactly like the wheel / `.deb` / `.rpm`. It also does not run the
provenance pipeline (that is CI-only; the local build is a dev loop).

## Licensing

WinPodX itself is **MIT** and stays MIT. The Thin AppImage redistributes
third-party binaries, so their license + NOTICE texts travel inside it —
collected fail-closed by the provenance pipeline above:

- WinPodX `LICENSE` (MIT) + `THIRD_PARTY_LICENSES.md` at
  `${APPDIR}/usr/share/doc/winpodx/`
- bundled Ubuntu FreeRDP packages' `copyright` files + referenced
  `/usr/share/common-licenses/*` texts under
  `${APPDIR}/usr/share/doc/winpodx/third-party/<pkg>/`
- the portable Python's CPython license (at its in-tree relative path)
  + the python-build-standalone license inventory under
  `third-party/python-build-standalone/`
- PySide6 / Qt6 (LGPL-3.0 OR GPL-2.0 OR GPL-3.0), cairosvg (LGPL-3.0),
  pyxdg (LGPL-2.1), Pillow (MIT-CMU), etc.: license texts in their
  `*.dist-info` inside `${APPDIR}/opt/python`; the PySide6 family's
  LGPL/GPL texts are vendored in-repo
  (`packaging/appimage/licenses/pyside6/`) because the wheels ship none,
  and copied in under `third-party/pyside6/`.

LGPL source delivery: extractability of the SquashFS is **not** claimed
to satisfy the LGPL by itself. The corresponding-source delivery index
(`SOURCE-OFFER.txt`, published next to the AppImage and inside it)
lists source archives + checksums and replacement / relinking steps;
the source bytes, notices and build inputs are attached publicly as
`winpodx-appimage-sources.tar.gz` on the same release page. No unsupported
three-year written offer or complete legal/security clearance is asserted.

The pre-Thin podman-stack license dirs (`podman/`, `podman-compose/`,
`conmon/`, `crun/`, `netavark/`, `passt/`, `slirp4netns/`) stay vendored
in-repo at `packaging/appimage/licenses/` for provenance + to make a
future re-bundling cheap, but they no longer ship inside the AppImage
because the binaries they cover are no longer bundled. (Their
`licenses/README.md` still documents the retired Fedora-based flow; the
live flow is the Ubuntu pipeline described above.)

See the repo-root `THIRD_PARTY_LICENSES.md` for the full breakdown,
including the bundled rdprrap (MIT + vendored Apache-2.0 rdpwrap) and
rcedit (MIT) that ship in every channel via the wheel's OEM payload.
