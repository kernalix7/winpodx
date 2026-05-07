"""Reverse-open feature configuration schema.

Persisted under ``[reverse_open]`` in ``winpodx.toml`` alongside the
existing ``[rdp]`` and ``[pod]`` sections. The defaults are tuned so
that simply pulling this module into the load path is *invisible* to
existing users — :attr:`ReverseOpenConfig.enabled` is ``False`` until
the user explicitly opts in via ``winpodx host-open enable`` (or the
GUI Settings card in phase 4). See ``docs/design/REVERSE_OPEN_DESIGN.md``
section "Component contracts → config.py" for the full contract.

The dataclass deliberately avoids raising in :meth:`__post_init__`:
this layer sits between an untrusted-by-default file (the user's TOML
on disk, hand-editable) and a feature that ends up forking
subprocesses on the host. A malformed slug, a wrongly-typed list, or
a corrupt ISO-8601 string must coerce to a safe default, not crash
``Config.load``. Any guarantees the rest of the codebase relies on
(slug regex, list-of-string typing, ISO-8601 round-trippability) are
enforced *here*, before the values reach
:mod:`winpodx.reverse_open.listener` or the CLI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

# Slug grammar shared with `discovery.slug_for_desktop` — desktop file
# basename, dots replaced with dashes, lowercased. The grammar is
# strict on purpose: every slug we accept here is later substituted
# verbatim into a Windows registry key name (``winpodx-<slug>.exe``)
# and into a JSON request from the guest. Anything outside this set
# would either break the registry write or open a smuggling avenue
# for malformed input from a hand-edited TOML.
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass
class ReverseOpenConfig:
    """Persisted state for the reverse file-association feature.

    Fields:

    - ``enabled``: master toggle. Defaults to ``False`` so a winpodx
      upgrade adds this section silently — no behaviour change for
      existing installs. Flipped via ``winpodx host-open enable``.
    - ``allowlist``: explicit slugs the user has whitelisted. Empty
      list means "all discovered apps" (subject to the denylist).
    - ``denylist``: explicit slugs to suppress. Combined with the
      ``DANGEROUS_DEFAULTS`` set when ``deny_dangerous=True``.
    - ``last_synced_at``: ISO-8601 timestamp of the last successful
      host-to-guest sync. Used for the staleness warning ("last
      synced 4 h ago") and to drive auto-refresh on pod start when
      it's older than 24 h.
    - ``deny_dangerous``: when True (default), the
      :attr:`DANGEROUS_DEFAULTS` slugs are folded into the effective
      denylist on each load. Users who want one of those apps
      surfaced anyway can either flip ``deny_dangerous=False`` or
      leave the default and explicitly remove the slug from
      ``denylist`` after loading (the explicit-remove path is wiped
      on next ``__post_init__``, so the proper escape hatch is the
      master toggle — documented in the design doc).
    """

    enabled: bool = False
    allowlist: list[str] = field(default_factory=list)
    denylist: list[str] = field(default_factory=list)
    last_synced_at: str = ""
    deny_dangerous: bool = True

    # Apps that are unsafe to expose by default because they execute
    # arbitrary code on file-open (editors with shell integration,
    # extension auto-load) or because the file argument is interpreted
    # as a working directory rather than data (terminal emulators,
    # which ``Terminal=true`` apps already imply). The set is folded
    # into ``denylist`` whenever ``deny_dangerous`` is true. Threat
    # model rationale: see "Compromised guest — bounded blast radius"
    # in the design doc.
    DANGEROUS_DEFAULTS: ClassVar[frozenset[str]] = frozenset(
        {
            "code",
            "vscodium",
            "atom",
            "gnome-terminal",
            "konsole",
            "xfce4-terminal",
            "alacritty",
            "kitty",
            "wezterm",
            "foot",
            "tilix",
        }
    )

    def __post_init__(self) -> None:
        """Coerce all fields to safe values; never raise.

        Any type or content violation degrades to a default that the
        rest of the package can rely on without re-validating:

        - non-list ``allowlist`` / ``denylist`` → empty list
        - any element not matching the slug grammar → dropped
        - non-ISO-8601 ``last_synced_at`` → empty string

        After coercion, when ``deny_dangerous`` is true, we union the
        :attr:`DANGEROUS_DEFAULTS` into ``denylist``. The fold is
        idempotent — running ``__post_init__`` repeatedly leaves
        the same effective set.
        """
        # Type-coerce list fields. A hand-edited TOML could put a
        # string or table in here; we just bin those.
        if not isinstance(self.allowlist, list):
            self.allowlist = []
        if not isinstance(self.denylist, list):
            self.denylist = []

        # Drop any element that isn't a slug-shaped string. We don't
        # try to lower-case or rewrite — every slug producer in the
        # codebase already emits the canonical form, so a non-matching
        # entry here is corruption, not a stylistic difference.
        self.allowlist = [s for s in self.allowlist if isinstance(s, str) and _SLUG_RE.fullmatch(s)]
        self.denylist = [s for s in self.denylist if isinstance(s, str) and _SLUG_RE.fullmatch(s)]

        # Validate ISO-8601. ``fromisoformat`` learned to handle the
        # trailing ``Z`` suffix only in 3.11; we still support 3.9 so
        # we normalise ``Z`` → ``+00:00`` before parsing.
        if not isinstance(self.last_synced_at, str):
            self.last_synced_at = ""
        elif self.last_synced_at:
            try:
                datetime.fromisoformat(self.last_synced_at.replace("Z", "+00:00"))
            except ValueError:
                self.last_synced_at = ""

        # Coerce booleans defensively — the TOML loader passes them
        # through as native bools, but `_apply` may pass through a
        # truthy/falsy other-type if the user hand-edited.
        self.enabled = bool(self.enabled)
        self.deny_dangerous = bool(self.deny_dangerous)

        # Fold the dangerous defaults in last so a user-supplied
        # denylist is preserved verbatim and only augmented.
        if self.deny_dangerous:
            for slug in self.DANGEROUS_DEFAULTS:
                if slug not in self.denylist:
                    self.denylist.append(slug)
