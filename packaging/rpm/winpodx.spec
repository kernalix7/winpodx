%global pypi_name winpodx

Name:           %{pypi_name}
# OBS's _service chain runs `set_version` on every build and rewrites this
# Version: line from the @PARENT_TAG@ tarball filename (e.g. winpodx-0.5.9
# → "0.5.9"). The literal here is a cosmetic placeholder for local builds;
# bumping it per release is NOT required and has no effect on OBS output.
# scripts/ci/verify_versions.py guards against drift between this literal and
# pyproject.toml so a local-build version doesn't masquerade as a stale one.
Version:        0.11.0
Release:        0
Summary:        Windows app integration for Linux desktop
# MIT covers winpodx + bundled rdprrap (same MIT terms).
# Apache-2.0 covers stascorp/rdpwrap, ported into rdprrap and
# redistributed inside config/oem/rdprrap-*-windows-x64.zip. See
# debian/copyright + THIRD_PARTY_LICENSES.md for the full breakdown.
# OFL-1.1 covers the bundled unmodified Microsoft Selawik UI fallback font.
# Combined SPDX expression follows Fedora packaging guidelines.
License:        MIT AND Apache-2.0 AND OFL-1.1
URL:            https://github.com/kernalix7/winpodx
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

%if 0%{?suse_version}
# Leap 16 (suse_version 1600) drops python311; use python313.
# Tumbleweed (>= 1600) also has python313. Leap 15.x (1550..1560) keeps python311.
%if 0%{?suse_version} >= 1600
%global pythons python313
%define py_flavor python313
%define py_sitelib %{python313_sitelib}
%else
%global pythons python311
%define py_flavor python311
%define py_sitelib %{python311_sitelib}
%endif
BuildRequires:  %{py_flavor}
BuildRequires:  %{py_flavor}-pip
BuildRequires:  %{py_flavor}-wheel
BuildRequires:  %{py_flavor}-setuptools
BuildRequires:  %{py_flavor}-hatchling
BuildRequires:  python-rpm-macros
Requires:       %{py_flavor} >= 3.11
Recommends:     %{py_flavor}-pyside6
%endif

%if 0%{?fedora} || 0%{?rhel}
# RHEL 9's default python3 stack is 3.9, which winpodx no longer supports
# (minimum is 3.10). el9 ships python3.11 (+ python3.11-* subpackages) in
# AppStream, so build against and require that there by setting
# %python3_pkgversion to 3.11 (the Fedora/RHEL convention: python-rpm-macros
# then rewrites python%{python3_pkgversion}-* and %python3_sitelib to the
# python3.11 stack). Fedora and el10 keep the macro at its default of 3, so
# python%{python3_pkgversion} == python3 there (already >= 3.11) and the
# %pyproject_* macros honour it automatically.
%if 0%{?rhel} && 0%{?rhel} <= 9
%global python3_pkgversion 3.11
%endif
BuildRequires:  python%{python3_pkgversion} >= 3.10
BuildRequires:  python%{python3_pkgversion}-pip
BuildRequires:  python%{python3_pkgversion}-wheel
BuildRequires:  python%{python3_pkgversion}-setuptools
BuildRequires:  python%{python3_pkgversion}-hatchling
BuildRequires:  python%{python3_pkgversion}-installer
BuildRequires:  pyproject-rpm-macros
# Fedora 42: pluggy has two providers (pluggy / pluggy1.3). Pin the base one.
BuildRequires:  python%{python3_pkgversion}-pluggy
Requires:       python%{python3_pkgversion} >= 3.10
Recommends:     python%{python3_pkgversion}-PySide6
# No python3-tomli fallback needed: every target now has tomllib in stdlib —
# el9 via python3.11, el10 via python3.12+, Fedora via its default python3.
%endif

Requires:       freerdp >= 3.0
Recommends:     podman

%description
Native integration layer that runs Windows applications from a Podman or Docker
backend and exposes them on the Linux desktop with desktop entries,
MIME handlers, icons, and a Qt tray.

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
install -Dm755 packaging/scripts/postrm-common.sh \
    %{buildroot}%{_datadir}/winpodx/packaging/postrm-common.sh
install -Dm755 uninstall.sh \
    %{buildroot}%{_datadir}/winpodx/uninstall.sh
install -Dm644 src/winpodx/gui/fonts/LICENSE-Selawik.txt \
    %{buildroot}%{_datadir}/licenses/winpodx/LICENSE-Selawik.txt

%post
# #255 PR 4: post-install banner pointing users at 'winpodx setup'.
# The package install only drops the binary + desktop entry; the
# Windows VM provisioning is deferred to the first-run prompt (CLI
# or GUI). Banner stays terse -- full guidance in docs/INSTALL.md.
if [ "$1" -eq 1 ]; then
    cat <<'EOF'

[WinPodX] Package installed. Next step:
  - Open WinPodX from your application menu (GUI) OR
  - Run 'winpodx' in a terminal
First-run prompt will offer auto setup / customize wizard / skip.

For the curl-like all-in-one experience, run:
  winpodx setup

Full docs: https://github.com/kernalix7/winpodx/blob/main/docs/INSTALL.md

EOF
fi
exit 0

%preun
# #255 packaging audit fix: rpm passes $1 = number of remaining
# installs (0 = erase, >=1 = upgrade). %preun runs BEFORE rpm removes
# the package's files -- this is the only scriptlet stage where
# %{_datadir}/winpodx/packaging/postrm-common.sh is guaranteed to
# still be on disk. Calling it from %postun (as this used to) is dead
# code on erase, since rpm has already deleted it by the time %postun
# runs. Only run on a real erase ($1 == 0); an upgrade ($1 >= 1) must
# never touch the container/VM/config, so skip entirely.
if [ "$1" -eq 0 ]; then
    if [ -x %{_datadir}/winpodx/packaging/postrm-common.sh ]; then
        %{_datadir}/winpodx/packaging/postrm-common.sh remove || true
    fi
fi
exit 0

%postun
# #255 packaging audit fix: runs AFTER rpm has already removed the
# package's files, so it can no longer call the (now-deleted) helper
# script -- self-contained banner only. rpm has no purge concept:
# point the user at the canonical curl uninstaller for a full wipe.
if [ "$1" -eq 0 ]; then
    cat <<'EOF'

[WinPodX] Package removed. User-side state (containers, configs,
reverse-open daemon, autostart) was NOT touched. To wipe everything:
  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge

EOF
fi
exit 0

%files
%license LICENSE
%license THIRD_PARTY_LICENSES.md
%license %{_datadir}/licenses/winpodx/LICENSE-Selawik.txt
%doc README.md CHANGELOG.md
%{_bindir}/winpodx
# Use a glob for dist-info so a pyproject.toml version that has drifted past
# the latest git tag (@PARENT_TAG@) does not break the build. set_version
# updates Version: from the tarball filename, but the wheel metadata uses
# pyproject.toml's version, and the two can disagree between tag bumps.
%if 0%{?suse_version}
%{py_sitelib}/winpodx/
%{py_sitelib}/winpodx-*.dist-info/
%endif
%if 0%{?fedora} || 0%{?rhel}
%{python3_sitelib}/winpodx/
%{python3_sitelib}/winpodx-*.dist-info/
%endif
%{_datadir}/winpodx/

%changelog
* Mon Sep 07 2026 Kim DaeHyun <kernalix7@kodenet.io> - 0.11.0-0
- Windows 11-style GUI redesign, bundled Selawik font, dockur HTTP progress,
  tray launcher, PCI names + VFIO group nodes. See the GitHub release notes.
* Mon Apr 20 2026 Kim DaeHyun <kernalix7@kodenet.io> - 0.1.0-0
- See https://github.com/kernalix7/winpodx/releases for per-version release notes.
