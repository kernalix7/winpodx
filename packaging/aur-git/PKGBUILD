# Maintainer: Kim DaeHyun <kernalix7@kodenet.io>
#
# VCS package: builds winpodx from the GitHub main branch. Unlike the stable
# `winpodx` package (release tarballs), the version is derived from git at
# build time by pkgver() -- never hand-edit it. Publish this PKGBUILD to the
# separate AUR repo `winpodx-git`; users get the newest main on each rebuild
# (`yay -Syu --devel`).

pkgname=winpodx-git
pkgver=0.6.0.r0.g0000000
pkgrel=1
pkgdesc="Windows app integration for Linux desktop (Podman/FreeRDP RemoteApp) -- built from the main branch"
arch=('any')
url="https://github.com/kernalix7/winpodx"
license=('MIT')
# Arch's `python` is rolling and already >= 3.13, so tomllib is stdlib and the
# tomli fallback is a marker-gated no-op (see pyproject.toml).
depends=(
  'python'
  'freerdp'
)
optdepends=(
  'podman: default container backend'
  'docker: alternative container backend'
  'pyside6: Qt6 GUI and system tray'
)
makedepends=(
  'git'
  'python-build'
  'python-installer'
  'python-hatchling'
  'python-wheel'
)
provides=('winpodx')
conflicts=('winpodx')
# Default ref is the repo's default branch (main). Pin to a commit/tag with a
# fragment, e.g. ...winpodx.git#commit=<sha> or #tag=v0.6.0, if you ever want
# this package frozen instead of tracking main.
source=("$pkgname::git+https://github.com/kernalix7/winpodx.git")
sha256sums=('SKIP')
install=winpodx.install

pkgver() {
  cd "$srcdir/$pkgname"
  # <last tag without the v> . r<commits since tag> . g<short hash>
  # e.g. 0.6.0.r12.gabc1234 -- monotonic and unique per commit. Falls back to a
  # tagless r<count>.g<hash> form if the clone has no tags.
  git describe --long --tags --abbrev=7 2>/dev/null \
    | sed 's/^v//;s/\([^-]*-g\)/r\1/;s/-/./g' \
    || printf '0.r%s.g%s' "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}

build() {
  cd "$srcdir/$pkgname"
  python -m build --wheel --no-isolation
}

package() {
  cd "$srcdir/$pkgname"
  python -m installer --destdir="$pkgdir" dist/*.whl
  # License dir uses $pkgname (namcap); app-facing paths use the real app id.
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
  install -Dm644 THIRD_PARTY_LICENSES.md \
    "$pkgdir/usr/share/licenses/$pkgname/THIRD_PARTY_LICENSES.md"
  install -Dm644 README.md "$pkgdir/usr/share/doc/winpodx/README.md"
  install -Dm644 CHANGELOG.md "$pkgdir/usr/share/doc/winpodx/CHANGELOG.md"
  install -Dm644 data/winpodx.desktop \
    "$pkgdir/usr/share/applications/winpodx.desktop"
  install -Dm644 data/winpodx-icon.svg \
    "$pkgdir/usr/share/icons/hicolor/256x256/apps/winpodx.svg"
  install -Dm644 data/winpodx.toml.example \
    "$pkgdir/usr/share/winpodx/winpodx.toml.example"
  install -Dm755 packaging/scripts/postrm-common.sh \
    "$pkgdir/usr/share/winpodx/packaging/postrm-common.sh"
  install -Dm755 uninstall.sh \
    "$pkgdir/usr/share/winpodx/uninstall.sh"
}
