%global pypi_name winpodx

Name:           %{pypi_name}
Version:        0.1.2
Release:        0
Summary:        Windows app integration for Linux desktop
License:        MIT
URL:            https://github.com/Kernalix7/winpodx
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
BuildRequires:  python3 >= 3.11
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  python3-setuptools
BuildRequires:  python3-hatchling
BuildRequires:  python3-installer
BuildRequires:  pyproject-rpm-macros
# Fedora 42: pluggy has two providers (pluggy / pluggy1.3). Pin the base one.
BuildRequires:  python3-pluggy
Requires:       python3 >= 3.11
Recommends:     python3-PySide6
%endif

Requires:       freerdp >= 3.0
Recommends:     podman

%description
Native integration layer that runs Windows applications from a Podman, Docker,
or libvirt backend and exposes them on the Linux desktop with desktop entries,
MIME handlers, icons, and a Qt tray.

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install

%files
%license LICENSE
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
- See https://github.com/Kernalix7/winpodx/releases for per-version release notes.
