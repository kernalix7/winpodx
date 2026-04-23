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

## Security

If you discover a security vulnerability, please follow the process described in [SECURITY.md](SECURITY.md). **Do NOT open a public issue.**
