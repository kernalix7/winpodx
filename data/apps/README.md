# winpodx bundled app definitions

Each subdirectory is one Windows application exposed to the Linux desktop.
A minimum definition is a single `app.toml`; optional `icon.svg` / `icon.png`
alongside it will be installed by the desktop-integration pipeline.

## `app.toml` schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Short name. Must match `^[a-zA-Z0-9_-]+$`. Used as the `.desktop` filename suffix (`winpodx-<name>.desktop`). |
| `full_name` | string | yes | Human-readable name shown in the application menu. |
| `executable` | string | yes | Windows-side absolute path, `%ENVVAR%` expansion allowed (expanded by the Windows RemoteApp runtime, not winpodx). |
| `categories` | array<string> | no | Freedesktop menu categories. |
| `mime_types` | array<string> | no | MIME types this app should handle. See the overlap policy below. |
| `mime_priority` | integer | no | **Reserved.** Lower number = higher priority when two apps declare the same MIME type. Defaults to `100`. See "MIME overlap policy". |

## MIME overlap policy

Multiple bundled apps can legitimately open the same MIME type:

| MIME type | Claimed by |
|-----------|-----------|
| `text/plain` | `notepad`, `vscode` |
| `application/json` | `vscode` (single claimant today) |
| `image/bmp` | `mspaint` (single claimant today) |
| `text/csv` | `excel-o365` |

`xdg-mime default` performs a last-write-wins assignment: the association
from the app registered most recently clobbers earlier ones. Because
`desktop.entry.register_all()` iterates `list_available_apps()` in directory
order (alphabetical), the *last* app in alphabetical order wins by default.
For `text/plain` this means `vscode` wins over `notepad`, which matches
developer-oriented defaults.

### Overriding priority

Until `src/winpodx/desktop/mime.py` supports ordering, users who prefer a
different association can override at runtime:

```bash
# Make notepad the default text/plain handler
xdg-mime default winpodx-notepad.desktop text/plain

# Verify
xdg-mime query default text/plain
```

### Roadmap

When `mime_priority` is implemented in `desktop/mime.py` (`TODO(L1)`), the
registration loop will sort by `(priority, name)` ascending and only call
`xdg-mime default` for the lowest-priority claimant per MIME type. Higher
numbers will still be registered as *handlers* but not as *defaults*.

Until then, setting `mime_priority` in `app.toml` is a no-op but safe; the
TOML loader ignores unknown fields.

## Directory layout

```
data/apps/<name>/
├── app.toml      # required
├── icon.svg      # optional, preferred over PNG
└── icon.png      # optional fallback
```

## Adding a new app

1. Create `data/apps/<name>/app.toml` matching the schema above.
2. Optionally drop in `icon.svg` or `icon.png` (square, 256px+ recommended).
3. Run `pytest tests/ -v`; `test_app.py` and `test_desktop.py` validate every
   bundled definition.
4. `ruff format` is not applied to TOML; indentation is 4 spaces for lists.
