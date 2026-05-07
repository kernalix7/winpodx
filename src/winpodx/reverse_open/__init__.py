"""Reverse file-association layer (#48).

Mirror of winpodx's primary direction (Windows app → Linux ``.desktop``
entries) but in the opposite direction: Linux apps appear in the
Windows context menu's "Open with…" list, so the user can right-
click a file inside a Windows file manager (Directory Opus, Total
Commander, Explorer) and pick the matching Linux handler — Kate for
``.xml``, GIMP for ``.psd``, VS Code for source files, etc.

Full design: ``docs/design/REVERSE_OPEN_DESIGN.md``.

Phase 1 (this module set) — foundations only:

- :mod:`paths` — UNC ↔ POSIX translation with TOCTOU-safe spawn
- :mod:`config` — :class:`ReverseOpenConfig` schema (feature flag off
  by default, dangerous-app denylist baked in)
- :mod:`mime` — MIME type → Windows extension mapping (curated table
  + xdg.Mime fallback for the long tail)
- :mod:`seen_uuids` — persistent ring buffer for replay defence

Phase 2 will add :mod:`discovery` (Linux ``.desktop`` scan) and
:mod:`icons` (PNG/SVG → ICO conversion). Phase 3 adds :mod:`listener`
(inotify daemon) and :mod:`lifecycle` (fork/PID/kill plumbing).
Phase 4 wires the CLI (``winpodx host-open``) and GUI Settings card
and flips ``cfg.reverse_open.enabled`` default to True for v0.5.0.

Until phase 4 lands, all modules in this package are inert — nothing
imports them from the host launch path. Tests cover them in
isolation.
"""

from __future__ import annotations

__all__: list[str] = []
