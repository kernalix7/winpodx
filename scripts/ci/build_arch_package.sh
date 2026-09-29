#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ] || [[ ! "$1" =~ ^v[0-9]+(\.[0-9]+){2,}((rc|alpha|beta)[0-9]+)?$ ]]; then
    printf 'Expected version tag (e.g. v0.11.0 or v0.11.0rc1) and output directory\n' >&2
    exit 2
fi

tag="$1"
version="${tag#v}"
mkdir -p "$2"
output_dir="$(realpath "$2")"
stage="$(mktemp -d "${output_dir}/.arch-build.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT

archive="${stage}/winpodx-${version}.tar.gz"
curl -fsSL --retry 5 --retry-delay 3 \
    -o "$archive" "https://github.com/kernalix7/winpodx/archive/${tag}.tar.gz"
if [ ! -s "$archive" ]; then
    printf 'Source archive for %s is empty\n' "$tag" >&2
    exit 1
fi
sha="$(sha256sum "$archive")"
sha="${sha%% *}"

tar -xOf "$archive" "winpodx-${version}/packaging/aur/PKGBUILD" \
    | sed -e "s|^pkgver=__PKGVER__$|pkgver=${version}|" \
    -e "s|^sha256sums=('__SHA256__')$|sha256sums=('${sha}')|" \
    -e "s|^arch=('any')$|arch=('x86_64')|" > "$stage/PKGBUILD"
tar -xOf "$archive" "winpodx-${version}/packaging/aur/winpodx.install" \
    > "$stage/winpodx.install"

(cd "$stage" && makepkg --noconfirm --cleanbuild)

shopt -s nullglob
packages=("$stage"/*.pkg.tar.zst)
filename="winpodx-${version}-1-x86_64.pkg.tar.zst"
if [ "${#packages[@]}" -ne 1 ] || [ "${packages[0]##*/}" != "$filename" ]; then
    printf 'Expected exactly one %s package, found %s\n' "$filename" "${#packages[@]}" >&2
    exit 1
fi

cp "${packages[0]}" "$output_dir/$filename"
(cd "$output_dir" && sha256sum "$filename" > "${filename}.sha256" \
    && sha256sum -c "${filename}.sha256")
