# Third-Party Licenses

WinPodX is MIT-licensed (see [LICENSE](LICENSE)). This document lists the
third-party components redistributed inside the source tree or pulled in as
runtime/optional dependencies, together with their upstream licenses.

## Bundled binaries

### rdprrap

- Upstream: https://github.com/kernalix7/rdprrap
- Version: 0.3.0 (pinned by `config/oem/rdprrap_version.txt`, SHA256-verified)
- License: MIT (WinPodX-side); supplemental notice for upstream license texts
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

The 0.12.0 supplement, `config/oem/rdprrap-NOTICES.txt`, supplies original
copyright notices and MIT texts for 17 external crates whose generated MIT
block used placeholder holders. The ZIP and its historical notices remain
unchanged. The supplement is copied to `C:\winpodx\rdprrap\` during OEM
installation and existing-pod activation; a failed copy is reported as an error.

Exact publication commits remain unresolved for `native-windows-derive 1.0.5`
and `native-windows-gui 1.0.13`. Their MIT `LICENSE` and
`Copyright (c) 2019 Gabriel Dube` notice are byte-identical across the surviving
2019 creation and 2022 pre-publication anchors. This license-text verification
does not verify their exact published source trees. `pelite-macros 0.1.1`'s
recorded source commit `432e769bf3152f21452a4b8908639f5ed1595912` resolves and its
license is identical; the supplement records that revision. These crate-source
facts are separate from rdprrap's historical `termsrv.dll` derivations.

> **Historical note.** WinPodX 0.1.6 bundled rdprrap 0.1.0, which upstream
> later withdrew because the 0.1.0 / 0.1.1 ZIPs were missing `NOTICE` and
> `vendor/licenses/`. WinPodX 0.1.7 bundled 0.1.3 with those attribution files.
> WinPodX 0.8.0 bumps the
> bundled component to rdprrap 0.3.0 (same MIT terms, same copyright holder;
> 0.3.0 derives the `termsrv.dll` patch sites dynamically). WinPodX 0.12.0
> keeps rdprrap at 0.3.0 and adds the explicit `rdprrap-NOTICES.txt`
> supplement plus per-guest copy enforcement on three code paths.

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
terms. `bin/BUILDINFO.json` records exact inputs, toolchain, target, and two
independent clean builds with identical executable SHA256
`f79f95408006b1caa0a0c638f8a6395dcf3d9e70ba04a63a08fb11fe67e0bc22`.
The actual build lock is preserved at `bin/licenses/provenance/Cargo.lock.txt`.
The previous binary's build graph is unknown; this record describes the rebuild.

| Crate | License | Why it is linked in |
|-------|---------|---------------------|
| [getrandom 0.2.17](https://crates.io/crates/getrandom) | MIT OR Apache-2.0 (MIT selected) | Randomness for the request UUID (`BCryptGenRandom` on Windows). Direct dependency. |
| [cfg-if 1.0.4](https://crates.io/crates/cfg-if) | MIT OR Apache-2.0 (MIT selected) | Transitive, via `getrandom`. |

`winresource` and the `toml`/`serde` crates it pulls in are **build**
dependencies only — they stamp the PE VERSIONINFO resource at compile time and
are not linked into the shipped binary.

MIT crate texts and the Rust `1.99.0-nightly` standard-library copyright and
license inventory are under `bin/licenses/`, alongside `THIRD_PARTY_NOTICES.txt`.
Guest synchronization carries these files, `BUILDINFO.json`, WinPodX's `LICENSE`,
and `LICENSE-rcedit.txt`; missing required notices stop synchronization.

### Selawik

- Source: https://github.com/microsoft/Selawik (release 1.01)
- Copyright: 2015 Microsoft Corporation (www.microsoft.com); Reserved Font Name: Selawik
- License: SIL Open Font License 1.1 (OFL-1.1) — full text in
  `src/winpodx/gui/fonts/LICENSE-Selawik.txt`
- Files: `src/winpodx/gui/fonts/selawk.ttf`, `selawksb.ttf`, `selawkb.ttf`
- Why: Microsoft's open-source metric-compatible alternative to Segoe UI. The Qt GUI loads
  the unmodified fonts only when Segoe UI is not installed on the host.
- Distribution: The OFL text is packaged beside the font files in the Python package. It
  therefore travels in the wheel, sdist, distro packages, and AppImage.

### Bootstrap Icons (usb-symbol)

- Source: https://github.com/twbs/icons (`icons/usb-symbol.svg`)
- Copyright: 2019-2024 The Bootstrap Authors
- License: MIT -- full text in `src/winpodx/gui/icons/LICENSE-bootstrap-icons.txt`
- Files: `src/winpodx/gui/icons/usb.svg`
- Why: the USB trident is a standardised mark; an in-house approximation misreads at 24-28px.
  Every other icon in that directory is original WinPodX work.
- Distribution: shipped unmodified inside the Python package, so it travels in the wheel,
  sdist, distro packages, and AppImage alongside its licence text.

## Runtime dependency (always required)

| Package | License | When | Notes |
|---------|---------|------|-------|
| [tomli](https://pypi.org/project/tomli/) | MIT | Python 3.10 only | Back-fills stdlib `tomllib` (3.11+). Pure Python. WinPodX requires Python 3.10+. |

## Optional dependencies (only installed with matching extras)

| Package | License | Extra | Linkage |
|---------|---------|-------|---------|
| [PySide6](https://pypi.org/project/PySide6/) | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only (6.12.0 metadata) | `winpodx[gui]` | Dynamic — imported at runtime. AppImage redistributes the runtime; individual Qt components have their own terms. |
| [docker](https://pypi.org/project/docker/) (docker-py) | Apache-2.0 | `winpodx[docker]` | Dynamic — imported at runtime. |
| [Pillow](https://pypi.org/project/Pillow/) | MIT-CMU | `winpodx[reverse-open]` | Dynamic — function-local import in `reverse_open/icons.py` for raster → multi-resolution ICO. |
| [cairosvg](https://pypi.org/project/CairoSVG/) | LGPL-3.0-or-later | `winpodx[reverse-open]` | Dynamic — function-local import in `reverse_open/icons.py` for SVG → PNG. |
| [pyxdg](https://pypi.org/project/pyxdg/) | LGPL-2.0-or-later | `winpodx[reverse-open]` | Dynamic — function-local import for the freedesktop icon-theme lookup and the long-tail MIME→extension fallback. WinPodX degrades gracefully without it. Not vendored. |

Without the `reverse-open` extra the discovery layer still works; ICO
conversion falls back to a logged warning and writes no file.

The source tree, wheel, sdist, `.deb` and `.rpm` do not
statically link, vendor, or redistribute PySide6 / cairosvg / pyxdg — users
install them from PyPI or their distro. The AppImage redistributes them.
Extraction (`--appimage-extract`) permits replacement of dynamically loaded
libraries, but does not alone establish fulfillment of LGPL obligations:
the conveyed versions also need notices, corresponding source, and applicable
replacement/relinking information.

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

## Thin AppImage bundle

The **source tree, wheel, `.deb`, and `.rpm` do not vendor** FreeRDP / Podman
/ Qt / Python — they are runtime dependencies the host provides (see the next
section). **The AppImage release artifact is the exception.** Since 0.6.0 the
bundle uses the *Thin* AppImage model (`winpodx-x86_64.AppImage`). The 0.12.0
candidate recipe selects:

- **Python 3.11.17+20261009** (astral-sh python-build-standalone,
  pinned URL + SHA256) — PSF
- **PySide6 6.12.0** — LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only;
  dynamically loaded Qt6 libraries have component-specific terms
- **Pillow 12.3.0** (MIT-CMU), **cairosvg 2.9.1** (LGPL-3.0-or-later),
  **pyxdg 0.28** (LGPL-2.0)
- **FreeRDP 3.32.1+dfsg-0ubuntu0.24.04.1** (`xfreerdp3`, `wlfreerdp3`) and
  transitively loaded `libwinpr` and supporting `.so` files from a
  **digest-pinned Ubuntu 24.04** base — Apache-2.0 (and the per-file
  licenses the dpkg copyright files reference)

The container stack is **no longer bundled in the Thin AppImage**. `podman` / `docker` /
`podman-compose` / `conmon` / `crun` / `netavark` / `slirp4netns` / `passt`
are installed by the user through their distro package manager and executed as
separate processes, so none is bundled in this AppImage. Their license
texts are retained under `packaging/appimage/licenses/` for **provenance of the
pre-0.6.0 Fat AppImage only** and are not part of the current Thin AppImage
artifact (the binaries they cover are not bundled). The current 0.12.0 build
flow is fully described in `packaging/appimage/README.md`; see also the
`packaging/appimage/licenses/README.md` clarification that the vendored
historic folders include both Fat-only container-stack archives and license
texts used by the pre-0.12.0 Thin FreeRDP bundle.

The build recipe collects component license and NOTICE texts inside the AppImage
at `usr/share/doc/winpodx/third-party/`, alongside WinPodX's own `LICENSE` and
this file at `usr/share/doc/winpodx/`. The CI build step that collects these is
in `.github/workflows/appimage-publish.yml`; the PySide6 and FreeRDP license
copies are hard-fail gated there.

The 0.12.0 candidate pipeline collects `conveyed-files.tsv`,
`dpkg-provenance.json`, `wheels-provenance.json`, `pip-report.json`, and
`license-inventory/`, with `PROVENANCE.json` summarizing build inputs.
The collector maps copied FreeRDP files and supporting libraries to package
owners, versions, copyright files, common-license texts, and source metadata;
missing required provenance raises a build error. `pip --report` records each
download's URL and SHA256 after resolution. It does not enforce a prevalidated
`--require-hashes` lock.

Corresponding-source delivery is implemented: the pipeline SHA256-verifies and
mirrors Qt/PySide, CPython, PyPI, Ubuntu, and AppImage runtime source originals
into `winpodx-appimage-sources.tar.gz`, with a source/use index, licenses, input
locks, recipes, and replacement/relinking directions. Tag builds attach the
archive, `PROVENANCE.json`, `SOURCE-OFFER.txt`, and `SHA256SUMS` alongside the
binary. Each artifact has its own provenance and source-delivery record;
this is not blanket legal or security clearance.
The runtime's unlocked Alpine apk inputs remain a reconstruction limit, not
a libfuse source mismatch; byte-identical reconstruction of every native
component is not claimed.

The earlier transitive-library notice-coverage gap also affected the
pre-0.12.0 Thin AppImage. `freerdp-libs/` and `libwinpr/` were used by that
Thin workflow, not solely by the pre-0.6.0 Fat bundle. Their historical Fedora
texts must not be assumed to cover the new Ubuntu package closure. The
container-stack folders, by contrast, belong to the retired Fat bundle.
See `packaging/appimage/licenses/README.md` for that distinction.

## Host-side QEMU disguise recipe

The wheel includes the five source files in `packaging/qemu-disguise/`:
`Dockerfile`, `patch-strings.sh`, `README.md`, `ssdt-sensors.asl`, and `wsmt.asl`.
The local-copy installer retains that recipe and this aggregate notice file.
This is an optional host-side image-build recipe, not a Windows-guest QEMU
binary or a guest-side source offer.

## Runtime system dependencies (not vendored)

Installed by `install.sh` via the host's package manager, or by the user.
This is the default for every install path; the Thin AppImage above bundles
FreeRDP but still relies on a host-installed container runtime:

- **FreeRDP 3+** — Apache-2.0 (bundled in the AppImage only)
- **Podman** / Docker — Apache-2.0 / Apache-2.0 (never bundled)
- **Microsoft Windows** — governed by Microsoft's applicable terms; the user
  supplies the required license and entitlements. Activation alone does not
  establish all virtualization, remote-access, or multi-user rights.
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
