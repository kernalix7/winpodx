# Vendored third-party license texts (fat AppImage)

The fat CI AppImage (`.github/workflows/appimage-publish.yml`)
redistributes FreeRDP + Podman + podman-compose and their helpers, so
each component's license / NOTICE text must travel inside the image.

These texts are **vendored here** rather than read off the built
container's filesystem. The `fedora:41` image installs with
`tsflags=nodocs`, which strips `/usr/share/licenses`, and every attempt
to defeat that on the GitHub runner (editing `dnf.conf`,
`--setopt=tsflags=`, `--setopt=keepcache=1` + `rpm2cpio`, a second
`dnf download`) silently produced nothing — even though each works in a
local `fedora:41` of the identical image digest. Vendoring the texts
removes that CI dependency entirely and makes the redistribution
obligation impossible to regress silently (the workflow fail-closes if
a required dir is absent).

## Provenance

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

`podman`, `freerdp-libs`, `libwinpr`, `podman-compose` are **required**
(the build fails closed without them); the rest are best-effort.

## Refreshing (when bumping the bundled Fedora package versions)

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
