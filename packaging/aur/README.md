# AUR (Arch User Repository)

This directory holds the `PKGBUILD` template and the one-time setup notes
for publishing `winpodx` to AUR. The actual publish is automated by
[`.github/workflows/aur-publish.yml`](../../.github/workflows/aur-publish.yml),
which fires on `push: tags: v*.*.*` (mirroring `obs-publish.yml`,
`debs-publish.yml`, and `rhel-publish.yml`).

The workflow is **secret-gated**: if `AUR_SSH_PRIVATE_KEY` is not set, the
publish step short-circuits with an `::notice::` log line instead of failing.
Tag pushes before the one-time setup below is complete will therefore *not*
red-X the release.

## One-time setup

All of these steps are done once, by the repo owner.

### 1. Create an AUR account

AUR uses its own account system, not GitHub SSO.

- Sign up at https://aur.archlinux.org/register/.
- Verify your email.

### 2. Add an SSH key to the AUR profile

Generate a dedicated key (do not reuse your personal SSH key):

```bash
ssh-keygen -t ed25519 -C "aur-winpodx@kodenet.io" -f ~/.ssh/aur_winpodx
```

- Copy the contents of `~/.ssh/aur_winpodx.pub` into the "SSH Public Key"
  field of your AUR account profile (https://aur.archlinux.org/account/).

### 3. Reserve the package name (first push)

AUR packages are just git repos. The first push *creates* the package:

```bash
# Use the private key from step 2
export GIT_SSH_COMMAND="ssh -i ~/.ssh/aur_winpodx"

# Clone the (empty) placeholder
git clone ssh://aur@aur.archlinux.org/winpodx.git /tmp/aur-winpodx
cd /tmp/aur-winpodx

# Drop in a minimal PKGBUILD + .SRCINFO just to reserve the name.
# The CI workflow will overwrite both on the next tag push.
cp /path/to/winpodx/packaging/aur/PKGBUILD ./PKGBUILD
# Replace placeholders with the current version so makepkg can run:
sed -i "s|__PKGVER__|0.1.7|; s|__SHA256__|SKIP|" PKGBUILD
makepkg --printsrcinfo > .SRCINFO

git add PKGBUILD .SRCINFO
git commit -m "Initial upload: winpodx 0.1.7"
git push
```

### 4. Register the SSH private key as a GitHub Actions secret

```bash
# From the repo root
gh secret set AUR_SSH_PRIVATE_KEY < ~/.ssh/aur_winpodx
```

Or via the web UI: Settings → Secrets and variables → Actions →
New repository secret → name `AUR_SSH_PRIVATE_KEY`, value = full contents
of the private key file (including the `-----BEGIN OPENSSH PRIVATE KEY-----`
header and trailing newline).

### 5. Done

Every subsequent tag push (`git tag vX.Y.Z && git push origin vX.Y.Z`)
will:

1. Compute the `sha256sum` of
   `https://github.com/Kernalix7/winpodx/archive/vX.Y.Z.tar.gz`.
2. Stamp `pkgver=X.Y.Z` and the sha into `packaging/aur/PKGBUILD`.
3. Push the rendered `PKGBUILD` + regenerated `.SRCINFO` to
   `ssh://aur@aur.archlinux.org/winpodx.git`.

Users then install via:

```bash
yay -S winpodx        # or:
paru -S winpodx
```

## Notes

- Arch ships `python` rolling (currently 3.13+), so the stdlib `tomllib`
  path is always taken. The marker-gated `tomli` fallback declared in
  `pyproject.toml` is a no-op on Arch.
- The PKGBUILD builds from the GitHub release tarball, not from the wheel
  uploaded to the GitHub Release. This keeps Arch users building from the
  same source OBS and the debs-publish/rhel-publish workflows build from.
- No `winpodx-bin` companion package: winpodx is pure-Python `noarch`/`any`,
  so building from source takes ~1 second on any Arch install — a -bin
  package would add maintenance burden without any install-speed payoff.
