# winpodx AppImage build

Builds a distro-agnostic AppImage of winpodx. Pairs with the
[python-appimage](https://github.com/niess/python-appimage) tool.

## Scope (this recipe)

**Bundled**:

- Python 3.11 runtime (from python-appimage's manylinux base)
- `winpodx` wheel (current branch, pinned at build time by `build.sh`)
- `PySide6` + `Qt6` runtime (`gui` extra)
- `Pillow` + `cairosvg` + `pyxdg` (`reverse-open` extra)

**Not bundled** -- still resolved from the host:

- FreeRDP 3+ (`xfreerdp3` / `xfreerdp`)
- Podman / Docker (rootless OK)
- `podman-compose`
- KVM kernel module + `/dev/kvm` access + `kvm` group membership
- libvirt (optional backend)
- dockur/windows container image (pulled at first pod start)

winpodx detects missing system components through its existing
`winpodx setup` / `winpodx doctor` flows and surfaces per-distro
install hints. The AppImage is "just another install method" --
first-run setup is handled by `winpodx`'s own first-run prompt
(`src/winpodx/cli/first_run.py`).

Bundling the system layer (xfreerdp3 + podman + ...) into a "fat"
AppImage is a follow-up tracked separately.

## Local build

```bash
./packaging/appimage/build.sh
# -> packaging/appimage/winpodx-<version>-x86_64.AppImage
```

Prerequisites:

- Python 3.11 or newer
- `pip install python-appimage build` (the script does this for you)
- Internet access (pulls PySide6 + extras from PyPI)

## CI

`.github/workflows/appimage-publish.yml` runs on every `v*.*.*` tag
push, builds the AppImage, and uploads it as a release asset alongside
the `.deb` / `.rpm` / wheel artefacts.

## Licensing

- winpodx itself: MIT
- Bundled rdprrap (inside the wheel's OEM payload): MIT + Apache-2.0
  (`THIRD_PARTY_LICENSES.md` for the full breakdown)
- Bundled PySide6 / Qt6: LGPL v3 -- AppImage's SquashFS payload is
  extractable, satisfying LGPL relinking
- Python runtime: PSF
- Other Python deps (Pillow HPND, cairosvg LGPL v3, pyxdg LGPL v2):
  all permissive or LGPL with the same SquashFS-extractability argument

The AppImage carries `LICENSE` (MIT) and `THIRD_PARTY_LICENSES.md`
under `${APPDIR}/usr/share/doc/winpodx/`.
