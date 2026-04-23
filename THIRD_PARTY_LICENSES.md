# Third-Party Licenses

winpodx is MIT-licensed (see [LICENSE](LICENSE)). This document lists the
third-party components redistributed inside the source tree or pulled in as
runtime/optional dependencies, together with their upstream licenses.

## Bundled binaries

### rdprrap

- Upstream: https://github.com/kernalix7/rdprrap
- Version: 0.1.3 (pinned by `config/oem/rdprrap_version.txt`, SHA256-verified)
- License: MIT
- Bundled as: `config/oem/rdprrap-0.1.3-windows-x64.zip`
- Role: enables multi-session RDP on the Windows guest during first-boot OEM
  install. Same copyright holder as winpodx.

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

winpodx redistributes the ZIP unmodified. All four attribution files are
extracted into the Windows guest at first-boot install time
(`C:\Program Files\RDP Wrapper\` and `C:\winpodx\rdprrap\`), which is where
the binaries live and is the redistribution surface that the upstream
licenses govern.

> **Historical note.** winpodx 0.1.6 bundled rdprrap 0.1.0, which upstream
> later withdrew because the 0.1.0 / 0.1.1 ZIPs were missing `NOTICE` and
> `vendor/licenses/`. 0.1.7 onward bundles 0.1.3 and is the first
> license-compliant winpodx release for this component.

## Runtime dependency (always required)

| Package | License | When | Notes |
|---------|---------|------|-------|
| [tomli](https://pypi.org/project/tomli/) | MIT | Python 3.9 / 3.10 only | Back-fills stdlib `tomllib` (3.11+). Pure Python. |

## Optional dependencies (only installed with matching extras)

| Package | License | Extra | Linkage |
|---------|---------|-------|---------|
| [PySide6](https://pypi.org/project/PySide6/) | LGPL-3.0-or-later (with [Qt for Python FAQ exceptions](https://www.qt.io/qt-for-python)) | `winpodx[gui]` | Dynamic — imported at runtime. Not redistributed by winpodx. |
| [libvirt-python](https://pypi.org/project/libvirt-python/) | LGPL-2.1-or-later | `winpodx[libvirt]` | Dynamic — imported at runtime. Not redistributed by winpodx. |
| [docker](https://pypi.org/project/docker/) (docker-py) | Apache-2.0 | `winpodx[docker]` | Dynamic — imported at runtime. |

LGPL compliance: winpodx does not statically link, vendor, or redistribute
PySide6 or libvirt-python binaries. Users install them from PyPI (or their
distro) at their own discretion; the LGPL reverse-engineering / replacement
rights are preserved because the libraries remain swappable at the Python
import level.

## Development-only dependencies (`winpodx[dev]`)

| Package | License |
|---------|---------|
| pytest | MIT |
| ruff | MIT |
| pip-audit | Apache-2.0 |
| hatchling (build backend) | MIT |

Dev dependencies are not shipped in the wheel / sdist / distro packages.

## Runtime system dependencies (not vendored)

Installed by `install.sh` via the host's package manager, or by the user:

- **FreeRDP 3+** — Apache-2.0
- **Podman** / Docker / libvirt — Apache-2.0 / Apache-2.0 / LGPL-2.1-or-later
- **Microsoft Windows** — EULA-governed; the user supplies their own license
  via the dockur/windows image, which winpodx pulls at setup time.
- **dockur/windows container image** — MIT
  (https://github.com/dockur/windows). winpodx orchestrates but does not
  redistribute this image.

## Reference projects (inspiration only, no code redistributed)

- **winapps** (https://github.com/winapps-org/winapps) — independent
  predecessor that also wraps FreeRDP RemoteApp. winpodx's CLI shape and
  `.cproc` tracking concepts are compatible with winapps configuration
  conventions for migration, but winpodx does not copy winapps source code.
- **LinOffice** — concept reference only; no source derivation.

If you find any attribution gap, please open an issue.
