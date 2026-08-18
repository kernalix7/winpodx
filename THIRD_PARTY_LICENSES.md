# Third-Party Licenses

WinPodX is MIT-licensed (see [LICENSE](LICENSE)). This document lists the
third-party components redistributed inside the source tree or pulled in as
runtime/optional dependencies, together with their upstream licenses.

## Bundled binaries

### rdprrap

- Upstream: https://github.com/kernalix7/rdprrap
- Version: 0.3.0 (pinned by `config/oem/rdprrap_version.txt`, SHA256-verified)
- License: MIT
- Bundled as: `config/oem/rdprrap-0.3.0-windows-x64.zip`
- Role: enables multi-session RDP on the Windows guest during first-boot OEM
  install. Same copyright holder as WinPodX.

rdprrap's own source tree ports code from three upstream projects whose
licenses require attribution / license-text redistribution. The bundled ZIP
therefore ships:

- `LICENSE` — rdprrap's own MIT terms.
- `NOTICE` — names each upstream project and lists the rdprrap source files
  derived from it: `stascorp/rdpwrap` (Apache-2.0), `llccd/TermWrap` (MIT),
  `llccd/RDPWrapOffsetFinder` (MIT).
- `vendor/licenses/` — verbatim copies of the three upstream license texts.
- `THIRD_PARTY_LICENSES.txt` — compiled Rust-dependency attributions,
  auto-generated from the crate graph.

WinPodX redistributes the ZIP unmodified. All four attribution files are
extracted into the Windows guest at first-boot install time
(`C:\Program Files\RDP Wrapper\` and `C:\winpodx\rdprrap\`), which is where
the binaries live and is the redistribution surface that the upstream
licenses govern.

> **Historical note.** WinPodX 0.1.6 bundled rdprrap 0.1.0, which upstream
> later withdrew because the 0.1.0 / 0.1.1 ZIPs were missing `NOTICE` and
> `vendor/licenses/`. 0.1.7 onward bundles 0.1.3 and is the first
> license-compliant WinPodX release for this component. WinPodX 0.8.0 bumps the
> bundled component to rdprrap 0.3.0 (same MIT terms, same copyright holder;
> 0.3.0 derives the `termsrv.dll` patch sites dynamically).

### rcedit

- Upstream: https://github.com/electron/rcedit
- License: MIT (`Copyright (c) 2013 GitHub Inc.`)
- Bundled as: `config/oem/reverse-open/shim/bin/rcedit.exe`
- Role: patches PE metadata on the per-app reverse-open shim during OEM
  install. `LICENSE-rcedit.txt` ships beside the binary in the same
  directory.

### winpodx-reverse-open-shim

- Own code (`config/oem/reverse-open/shim/`, `Cargo.toml` declares MIT).
- License: MIT (same as WinPodX).
- Bundled as: `config/oem/reverse-open/shim/bin/winpodx-reverse-open-shim.exe`
- Role: stub Windows Explorer invokes from "Open with" to relay a
  file-open request back to the host's reverse-open listener.

The shipped `.exe` is statically linked, so the crates below are compiled into
the redistributed binary. All are permissive and compatible with WinPodX's MIT
terms. The source manifest declares the dependency ranges; `Cargo.lock` is not
tracked, so the exact resolved versions must be inventoried from the build that
produces the prebuilt binary whenever that binary is regenerated.

| Crate | License | Why it is linked in |
|-------|---------|---------------------|
| [getrandom](https://crates.io/crates/getrandom) | MIT OR Apache-2.0 | Crypto-quality randomness for the request UUID (routes to `BCryptGenRandom` on Windows). Direct dependency. |
| [cfg-if](https://crates.io/crates/cfg-if) | MIT OR Apache-2.0 | Transitive, via `getrandom`. |

`winresource` and the `toml`/`serde` crates it pulls in are **build**
dependencies only — they stamp the PE VERSIONINFO resource at compile time and
are not linked into the shipped binary.

## Runtime dependency (always required)

| Package | License | When | Notes |
|---------|---------|------|-------|
| [tomli](https://pypi.org/project/tomli/) | MIT | Python 3.9 / 3.10 only | Back-fills stdlib `tomllib` (3.11+). Pure Python. |

## Optional dependencies (only installed with matching extras)

| Package | License | Extra | Linkage |
|---------|---------|-------|---------|
| [PySide6](https://pypi.org/project/PySide6/) | LGPL-3.0-or-later (with [Qt for Python FAQ exceptions](https://www.qt.io/qt-for-python)) | `winpodx[gui]` | Dynamic — imported at runtime. Redistributed **only** inside the AppImage (see below). |
| [docker](https://pypi.org/project/docker/) (docker-py) | Apache-2.0 | `winpodx[docker]` | Dynamic — imported at runtime. |
| [Pillow](https://pypi.org/project/Pillow/) | MIT-CMU | `winpodx[reverse-open]` | Dynamic — function-local import in `reverse_open/icons.py` for raster → multi-resolution ICO. |
| [cairosvg](https://pypi.org/project/CairoSVG/) | LGPL-3.0-or-later | `winpodx[reverse-open]` | Dynamic — function-local import in `reverse_open/icons.py` for SVG → PNG. |
| [pyxdg](https://pypi.org/project/pyxdg/) | LGPL-2.0-or-later | `winpodx[reverse-open]` | Dynamic — function-local import for the freedesktop icon-theme lookup and the long-tail MIME→extension fallback. WinPodX degrades gracefully without it. Not vendored. |

Without the `reverse-open` extra the discovery layer still works; ICO
conversion falls back to a logged warning and writes no file.

LGPL compliance: the source tree, wheel, sdist, `.deb` and `.rpm` do not
statically link, vendor, or redistribute PySide6 / cairosvg / pyxdg — users
install them from PyPI or their distro. The AppImage **does** redistribute
them; the LGPL relinking right is preserved there because the SquashFS is
user-extractable (`--appimage-extract`) and the libraries stay dynamically
loaded and replaceable at the Python import level.

## Development-only dependencies (`winpodx[dev]`)

| Package | License |
|---------|---------|
| pytest | MIT |
| pytest-xdist | MIT |
| pytest-cov | MIT |
| ruff | MIT |
| pip-audit | Apache-2.0 |
| Pillow | MIT-CMU |
| hatchling (build backend) | MIT |

Dev dependencies are not shipped in the wheel / sdist / distro packages.

## Thin AppImage release artifact (DOES redistribute the components below)

The **source tree, wheel, `.deb`, and `.rpm` do not vendor** FreeRDP / Podman
/ Qt / Python — they are runtime dependencies the host provides (see the next
section). **The AppImage release artifact is the exception.** Since 0.6.0 the
shipped artifact is the *Thin* AppImage (`winpodx-x86_64.AppImage`), which
bundles only:

- **Python 3.11** (astral-sh python-build-standalone) — PSF
- **PySide6 / Qt6** — LGPL-3.0 (dynamically loaded; the AppImage SquashFS is
  user-extractable via `--appimage-extract`, satisfying LGPL relinking)
- **Pillow** (MIT-CMU), **cairosvg** (LGPL-3.0-or-later), **pyxdg** (LGPL-2.0)
- **FreeRDP** (xfreerdp / wlfreerdp / sdl-freerdp) and **libwinpr** —
  Apache-2.0, plus the shared libraries `ldd` resolves for them

The container stack is **no longer bundled**. `podman` / `docker` /
`podman-compose` / `conmon` / `crun` / `netavark` / `slirp4netns` / `passt`
are installed by the user through their distro package manager and executed as
separate processes, so none of them is redistributed by WinPodX. Their license
texts are retained under `packaging/appimage/licenses/` for provenance of the
pre-0.6.0 Fat AppImage and are not part of the current artifact.

Each bundled component's license + NOTICE text is shipped inside the AppImage
at `usr/share/doc/winpodx/third-party/`, alongside WinPodX's own `LICENSE` and
this file at `usr/share/doc/winpodx/`. The CI build step that collects these is
in `.github/workflows/appimage-publish.yml`; the PySide6 and FreeRDP license
copies are hard-fail gated there.

> **Known gap.** `packaging/appimage/bundle-system-bins.sh` walks `ldd` and
> copies every non-excluded shared library FreeRDP needs, but the workflow only
> copies the `freerdp-libs` and `libwinpr` license directories explicitly. A
> transitive `.so` pulled in that way is therefore not guaranteed to ship with
> its own license text. Tracked for a follow-up that makes the collection
> fail-closed per copied library.

## Runtime system dependencies (not vendored)

Installed by `install.sh` via the host's package manager, or by the user.
This is the default for every install path; the Thin AppImage above bundles
FreeRDP but still relies on a host-installed container runtime:

- **FreeRDP 3+** — Apache-2.0 (bundled in the AppImage only)
- **Podman** / Docker — Apache-2.0 / Apache-2.0 (never bundled)
- **Microsoft Windows** — EULA-governed; the user supplies their own license
  via the dockur/windows image, which WinPodX pulls at setup time.
- **dockur/windows container image** — MIT
  (https://github.com/dockur/windows). WinPodX orchestrates but does not
  redistribute this image.

## Reference projects (inspiration only, no code redistributed)

- **winapps** (https://github.com/winapps-org/winapps) — independent
  predecessor that also wraps FreeRDP RemoteApp. WinPodX's CLI shape and
  `.cproc` tracking concepts are compatible with winapps configuration
  conventions for migration, but WinPodX does not copy winapps source code.
- **LinOffice** — concept reference only; no source derivation.

If you find any attribution gap, please open an issue.
