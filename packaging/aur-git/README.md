# `winpodx-git` — AUR development package

Builds winpodx from the GitHub **main** branch (latest, unreleased code), as a
separate AUR package from the stable [`winpodx`](../aur/) (which uses tagged
release tarballs).

## How it stays current

The version is **not** hand-maintained. `pkgver()` derives it from git at build
time:

```
0.6.0.r12.gabc1234   =  <last tag> . r<commits since tag> . g<short hash>
```

So you publish this PKGBUILD to AUR **once**. Every rebuild re-runs `pkgver()`
against fresh `main`, so the version tracks the newest commit automatically.

- **Users get updates** with `yay -Syu --devel` / `paru -Syu --devel` (the
  `--devel` pass re-checks VCS packages and rebuilds when `main` moved).
  Without `--devel`, a user reinstalls (`yay -S winpodx-git`) to pull latest.
- **`winpodx-git` and `winpodx` can't coexist** (`provides`/`conflicts`).
- **Pin instead of tracking main:** add a fragment to `source`, e.g.
  `…winpodx.git#commit=<sha>` or `#tag=v0.6.0`. (If you want a fixed version,
  the stable `winpodx` package is the better choice.)

## Publishing to AUR (maintainer steps)

Requires an AUR account with your SSH public key registered
(https://aur.archlinux.org/account → My Account → SSH Public Key).

```bash
# 1. Clone the (empty) AUR repo — created on first push.
git clone ssh://aur@aur.archlinux.org/winpodx-git.git
cd winpodx-git

# 2. Copy the package files from this repo.
cp /path/to/winpodx/packaging/aur-git/PKGBUILD .
cp /path/to/winpodx/packaging/aur-git/winpodx.install .

# 3. Regenerate .SRCINFO from the PKGBUILD (AUR validates it; the one in this
#    repo is a starting point — always regenerate on an Arch box).
makepkg --printsrcinfo > .SRCINFO

# 4. (recommended) Test-build locally first.
makepkg -si

# 5. Commit + push — this publishes / updates the AUR package.
git add PKGBUILD .SRCINFO winpodx.install
git commit -m "winpodx-git: build from main"
git push
```

Re-push only when the **build recipe** changes (deps, install steps) — not per
upstream commit; the git source + `pkgver()` handle commit tracking.
