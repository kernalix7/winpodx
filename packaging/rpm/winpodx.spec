%global pypi_name winpodx

Name:           %{pypi_name}
# OBS's _service chain runs `set_version` on every build and rewrites this
# Version: line from the @PARENT_TAG@ tarball filename (e.g. winpodx-0.5.9
# → "0.5.9"). The literal here is a cosmetic placeholder for local builds;
# bumping it per release is NOT required and has no effect on OBS output.
# scripts/ci/verify_versions.py guards against drift between this literal and
# pyproject.toml so a local-build version doesn't masquerade as a stale one.
Version:        0.7.4
Release:        0
Summary:        Windows app integration for Linux desktop
# MIT covers winpodx + bundled rdprrap (same MIT terms).
# Apache-2.0 covers stascorp/rdpwrap, ported into rdprrap and
# redistributed inside config/oem/rdprrap-*-windows-x64.zip. See
# debian/copyright + THIRD_PARTY_LICENSES.md for the full breakdown.
# Combined SPDX expression follows Fedora packaging guidelines.
License:        MIT AND Apache-2.0
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
BuildRequires:  python3 >= 3.9
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-setuptools
BuildRequires:  python3-hatchling
BuildRequires:  python3-installer
BuildRequires:  pyproject-rpm-macros
# Fedora 42: pluggy has two providers (pluggy / pluggy1.3). Pin the base one.
BuildRequires:  python3-pluggy
Requires:       python3 >= 3.9
Recommends:     python3-PySide6
# tomllib is stdlib on Python 3.11+; RHEL 9's default python3 is 3.9, so pull
# in python3-tomli as the TOML reader fallback. EPEL ships python3-tomli for
# el9. Fedora's default python3 is already >= 3.11, so this is harmless there
# (the Python dist-info declares the marker python_version < '3.11').
%if 0%{?rhel} && 0%{?rhel} <= 9
Requires:       python3-tomli
%endif
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

%postun
# #255 PR 4: post-remove cleanup. rpm passes $1 = number of remaining
# installs (0 = uninstall, >=1 = upgrade). Delegate to the shared
# helper which iterates /home/* users and pkill's tray/GUI/listener.
# rpm has no purge concept; tell the user how to do the full wipe.
if [ -x %{_datadir}/winpodx/packaging/postrm-common.sh ]; then
    %{_datadir}/winpodx/packaging/postrm-common.sh "$1" || true
fi
if [ "$1" -eq 0 ]; then
    cat <<'EOF'

[WinPodX] Package removed. User-side state (containers, configs,
reverse-open daemon, autostart) was NOT touched. To wipe everything:
  winpodx uninstall --purge --yes

EOF
fi
exit 0

%files
%license LICENSE
%license THIRD_PARTY_LICENSES.md
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
* Mon Apr 20 2026 Kim DaeHyun <kernalix7@kodenet.io> - 0.1.0-0
- See https://github.com/kernalix7/winpodx/releases for per-version release notes.
