# Vendored third-party license texts

The container-stack directories (`podman/`, `podman-compose/`, `conmon/`,
`crun/`, `netavark/`, `passt/`, `slirp4netns/`) document the pre-0.6.0
Fat AppImage. The Thin AppImage uses the host container runtime.

`freerdp-libs/` and `libwinpr/` were also used by the pre-0.12.0 Thin
AppImage workflow. They are historical Fedora package texts, not an inventory
of the new Ubuntu FreeRDP closure. `pyside6/` supplies runtime license texts
for the current recipe.

**The candidate 0.12.0 pipeline is described in
`packaging/appimage/README.md`.** Its recipe builds
from a digest-pinned Ubuntu 24.04 image, bundles FreeRDP 3.32.1,
PySide6 6.12.0, Pillow 12.3.0, CairoSVG 2.9.1, pyxdg 0.28, and Python
3.11.17 (astral-sh python-build-standalone); the host container
runtime (Podman / Docker / podman-compose / conmon / crun /
netavark / aardvark-dns / pasta / passt / slirp4netns /
fuse-overlayfs) is NOT bundled; the **container stack license
directories below are not a license-text source for the current
artifact**.

## Why these folders still exist

The historical Fat AppImage pipeline (pre-0.6.0, retired) was based
on a `fedora:41` Docker image. That image installs with
`tsflags=nodocs`, which strips `/usr/share/licenses`, and every
attempt to defeat that on the GitHub runner (editing `dnf.conf`,
`--setopt=tsflags=`, `--setopt=keepcache=1` + `rpm2cpio`, a second
`dnf download`) silently produced nothing — even though each works in
a local `fedora:41` of the identical image digest. Vendoring the
texts removed that CI dependency; the workflow checked for required directories.

## Historical Fedora package texts (Fat and pre-0.12.0 Thin workflows)

Harvested verbatim from the Fedora 41 rpm payloads:

| dir | package | license |
|---|---|---|
| `podman/` | podman | Apache-2.0 (+ `modules.txt`) |
| `freerdp-libs/` | freerdp-libs | Apache-2.0 / HPND / LGPL-2.1 / OFL-1.1 |
| `libwinpr/` | libwinpr | (FreeRDP stack) |
| `podman-compose/` | podman-compose | **GPL-2.0-only** (from the wheel dist-info) |
| `conmon/` | conmon | Apache-2.0 |
| `crun/` | crun | `COPYING` |
| `netavark/` | netavark | + `LICENSE.dependencies`, `cargo-vendor.txt` |
| `passt/` | passt | BSD-3-Clause + GPL-2.0-or-later |
| `slirp4netns/` | slirp4netns | `COPYING` |

`podman`, `freerdp-libs`, `libwinpr`, `podman-compose` were
**required** for the Fat AppImage build; the rest were best-effort.
The Thin workflow retained the FreeRDP texts while dropping container binaries.
The 0.12.0 recipe collects FreeRDP and supporting-library texts from Ubuntu.

## Refreshing the historical folders (when bumping the bundled Fedora package versions)

The `docker run --rm fedora:41` snippets below refresh the historical
**provenance** archives only. The current 0.12.0 build pipeline does
not run them; the recipes are preserved here so a contributor can
recreate the legacy provenance if needed.

```sh
docker run --rm fedora:41 bash -c '
  dnf install -y -q --setopt=install_weak_deps=False cpio
  h=$(mktemp -d); cd "$h"
  dnf download --setopt=install_weak_deps=False \
    podman freerdp-libs libwinpr conmon crun netavark passt slirp4netns
  for r in *.x86_64.rpm *.noarch.rpm; do rpm2cpio "$r" | cpio -idmu --quiet; done
  cd ./usr/share/licenses && tar -cf - .
' > /tmp/licenses.tar
tar -xf /tmp/licenses.tar -C packaging/appimage/licenses

# podman-compose GPL-2.0 (lives in the wheel dist-info, not /usr/share/licenses):
docker run --rm fedora:41 bash -c '
  dnf install -y -q podman-compose
  cat /usr/lib/python3*/site-packages/podman_compose-*.dist-info/LICENSE
' > packaging/appimage/licenses/podman-compose/LICENSE
```

## Candidate 0.12.0 license collection

The 0.12.0 recipe collects notices at `usr/share/doc/winpodx/third-party/`
and records package ownership, versions, and referenced license texts.
Required missing provenance raises a build error.

- PySide6 6.12.0 metadata declares `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`;
  Qt libraries have component-specific terms. Runtime license texts are vendored
  in-repo at `packaging/appimage/licenses/pyside6/` because the
  PySide6 wheels ship no license text; copied into the AppImage at
  build time.
- The portable Python's CPython license (at its in-tree relative
  path) + the python-build-standalone license inventory under
  `third-party/python-build-standalone/` inside the AppImage.
- WinPodX `LICENSE` (MIT) + this `THIRD_PARTY_LICENSES.md` at
  `${APPDIR}/usr/share/doc/winpodx/`.
- The Ubuntu FreeRDP packages' `copyright` files + referenced
  `/usr/share/common-licenses/*` texts under
  `${APPDIR}/usr/share/doc/winpodx/third-party/<pkg>/`.
- Pillow / CairoSVG / pyxdg: license texts in their `*.dist-info`
  inside `${APPDIR}/opt/python`.

FreeRDP file ownership and source metadata are recorded in
`dpkg-provenance.json` and `conveyed-files.tsv`. Wheel URLs and downloaded
archive hashes come from `pip --report`; this is hash recording, not a
prevalidated hash lock. The implemented source pipeline SHA256-verifies and
mirrors Qt/PySide, CPython, PyPI, Ubuntu, and AppImage runtime originals into
`winpodx-appimage-sources.tar.gz`, with a source/use index, licenses, input locks,
and recipes. Tag builds attach the archive, `PROVENANCE.json`, `SOURCE-OFFER.txt`,
and `SHA256SUMS` alongside the binary. Each artifact has its own provenance and
source-delivery record; this is not blanket legal or security clearance. Unlocked Alpine apk inputs limit runtime
reconstruction, not libfuse source matching; byte-identical reconstruction of
every native component and a complete security audit are not attested.
Extraction alone does not establish LGPL compliance; applicable
notices, corresponding source, and replacement/relinking information are needed.
