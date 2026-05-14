# Contributing to winpodx

**English** | [한국어](docs/CONTRIBUTING.ko.md)

Thank you for your interest in contributing to winpodx! This guide will help you get started.

## Prerequisites

- Python 3.9+ (developed on 3.13; CI covers 3.9 / 3.10 / 3.11 / 3.12 / 3.13)
- FreeRDP 3+

## Build

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Test

```bash
# Run tests
pytest tests/ -v

# Lint
ruff check src/ tests/

# Format check
ruff format --check src/ tests/
```

## Workflow

1. **Fork** the repository
2. Create a **feature branch** (`git checkout -b feat/my-feature`)
3. Write your changes following **conventional commits**
4. Submit a **Pull Request**

## PR Checklist

Before submitting a PR, ensure the following:

- [ ] `pytest tests/ -v` passes
- [ ] `ruff check src/ tests/` reports zero errors
- [ ] `ruff format --check src/ tests/` passes
- [ ] Documentation is updated (if applicable)
- [ ] No hardcoded credentials or secrets

## Commit Convention

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Purpose |
|--------|---------|
| `feat` | New feature |
| `fix` | Bug fix |
| `docs` | Documentation changes |
| `refactor` | Code refactoring (no feature change) |
| `test` | Adding or updating tests |
| `chore` | Maintenance tasks (CI, deps, etc.) |

### Examples

```
feat: add Wayland display detection
fix: resolve DPI scaling on multi-monitor setups
docs: update installation instructions
refactor: simplify backend abstraction layer
test: add unit tests for UNC path conversion
chore: update ruff to 0.8.x
```

### No AI tool co-author trailers

Do **not** add `Co-authored-by:` trailers that name AI tools / coding agents. This applies to all of:

- `Co-authored-by: Cursor <cursoragent@cursor.com>`
- `Co-authored-by: Claude <noreply@anthropic.com>` (and any other Anthropic email)
- `Co-authored-by: Copilot <...>` (any GitHub Copilot variant)
- `Co-authored-by: <any other AI tool / agent identity>`

You wrote the patch — the human author of record is you. AI tooling doesn't get co-authorship credit in this repo regardless of how much it contributed. If you forgot and a trailer slipped in, we'll ask you to amend (or, for already-merged PRs, propose a coordinated history-rewrite via a follow-up PR).

Human co-authors (e.g., a colleague who pair-programmed with you on the change) are fine and welcome — those should use real human identities + emails.

## Writing release notes

Each version section in `CHANGELOG.md` (and `docs/CHANGELOG.ko.md`) starts with `### Highlights` — a one-sentence headline followed by 3–6 scannable bullets. This is what users see at the top of the GitHub release page: `release.yml` extracts the version's section verbatim, so the first thing in the section is the first thing in the release body.

The detailed `### Added` / `### Changed` / `### Fixed` bullets follow underneath. They're for archeology and exhaustive tracking, not first-read.

Skeleton:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Highlights

**One-sentence headline.** Optional 1-2 sentence elaboration if needed.

- Most important user-visible change (one line, scannable)
- Second most important change
- (3-6 bullets max; no prose blocks)

### Added
- (detailed bullets)

### Changed
- (detailed bullets)

### Fixed
- (detailed bullets)
```

When cutting a release, also push the `REL-vX.Y.Z` marker tag — this is what fires `release.yml` (which builds the `wheel` + `sdist`, extracts the CHANGELOG section, and updates the GitHub release body). Without the REL- marker, the version tag (`vX.Y.Z`) triggers only the four packaging workflows (`obs-publish.yml`, `rhel-publish.yml`, `debs-publish.yml`, `aur-publish.yml`) but no `wheel` / `sdist` and no auto-extracted release body.

```bash
git tag vX.Y.Z <commit>
git tag REL-vX.Y.Z vX.Y.Z^{}    # dereference to commit to avoid a nested-tag warning
git push origin vX.Y.Z REL-vX.Y.Z
```

### Crediting contributors in Highlights

When a Highlights bullet covers work that came from outside the
maintainer (external PR or external bug report / feature request),
credit the contributor inline. The convention:

| Source | Suffix |
|---|---|
| External PR (someone else's commits) | `(by @username, #PR)` |
| External issue / feature request (maintainer wrote the code) | `(reported by @username, #issue)` |
| Both — external report **and** external PR by the same person | `(by @username, #PR / #issue)` |

GitHub auto-renders both forms as the user's avatar + handle on the
release page, so the recognition surfaces without extra work.

Example:

```markdown
### Highlights

- Atomic Fedora flavours (Silverblue / Kinoite / Bazzite) now ship via the
  OBS repo with `rpm-ostree install --apply-live`. (by @Zeik0s, #163)
- LTSC IoT and Win10 LTSC pickable from Settings or `--win-version`.
  (reported by @gabe39, #178)
```

The "no AI tool co-author trailers" rule above is unrelated: it bans
machine-generated attribution. Human contributors are credited
liberally and explicitly.

## Security

If you discover a security vulnerability, please follow the process described in [SECURITY.md](SECURITY.md). **Do NOT open a public issue.**
