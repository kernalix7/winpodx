"""UNC -> POSIX path translation for incoming reverse-open requests.

Mirror of ``core.rdp.linux_to_unc``. The Windows guest's shim writes
each request with the path as the guest sees it -- typically a UNC
path through the FreeRDP drive redirect (e.g.
``\\\\tsclient\\home\\kernalix7\\Documents\\foo.xml``). Before we hand
the path to the spawned Linux app, we have to:

1. Map it back to the Linux-side ``/home/kernalix7/Documents/foo.xml``.
2. Verify it's actually under a directory we know we shared with the
   guest.
3. Defend against the TOCTOU window between resolution and spawn --
   a malicious guest can swap a symlink between the two.

The atomic context manager :func:`safe_open_unc` is the *only* correct
entry point for callers that will hand the resulting path to a child
process: it opens the FD before validation, then validates against
the kernel's authoritative ``/proc/self/fd/N`` readlink. Any later
on-disk symlink swap can no longer redirect what the spawn targets,
because the FD pins the inode in the listener process.

The display-only helper :func:`translate_unc_to_posix` remains for
log/error messages and CLI status lines. It is **not** TOCTOU-safe;
the window between its return and the caller's open-or-spawn is
unguarded, so the result must never be passed to ``subprocess.Popen``
or any other open()-by-name primitive. Use :func:`safe_open_unc`
instead.

Security: this module is the trust boundary between guest-supplied
input and host-side execution. Refuses paths that:

- Aren't under one of the share roots active in the current
  ``RDPConfig.drives`` (caller passes the live mapping).
- Resolve via ``..`` or symlink to outside that root.
- Land under ``/proc``, ``/sys``, or ``/dev`` after resolution.
- Are empty, contain NUL, or aren't strings.
- Carry a non-ASCII share name (Windows shares are ASCII; non-ASCII
  is either a misconfiguration or an obfuscation attempt).

If ``share_roots`` is empty, every input is refused -- no shares
configured means no legal target exists.

Limitations:

- **Hard links**: ``Path.resolve()`` doesn't see them. A guest-side
  hard-link of a privileged file into ``~`` would only succeed if
  the user already has read access to the file, so the blast radius
  is bounded -- but audit logs will be confusing. Mitigation belongs
  in the file-format apps (the ones that actually parse the data),
  not at this layer.

The functions are total: every input either returns / yields a valid
result under a share root or raises ``ReversePathError`` with a
reason. Callers should ALWAYS treat a raise as a security event and
log at WARNING -- a guest shim trying to write a path outside the
share is either misconfigured or actively malicious.
"""

from __future__ import annotations

import logging
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Iterator

__all__ = [
    "ReversePathError",
    "SafeFile",
    "is_relative_to",
    "safe_open_unc",
    "translate_unc_to_posix",
]

log = logging.getLogger(__name__)


class ReversePathError(ValueError):
    """Raised when a guest-supplied UNC path can't be safely translated.

    Distinct from generic ``ValueError`` so the listener can log these
    at WARNING level (potential security event) rather than as routine
    parse failures.
    """


def is_relative_to(path: PurePath, root: PurePath) -> bool:
    """Return whether ``path`` is contained under ``root`` (strict semantics).

    ``Path.is_relative_to`` was added in 3.9 but the implementation
    semantics shifted in 3.12 to use ``relative_to(walk_up=True)``-
    style traversal, which silently changes behaviour for inputs that
    contain a partial overlap. The reverse-open security validation
    depends on the *strict* "subtree of root" semantics, not the new
    traversal-aware ones -- so we implement the check ourselves on top
    of ``relative_to`` which has stable strict semantics across
    3.9-3.13+.

    Both arguments must already be canonicalised by the caller
    (``Path.resolve(strict=False)`` or equivalent). Symlink and ``..``
    handling are NOT performed here -- that's the caller's job, and
    doing it again here would mask a missing resolve in the caller.
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


# Doubled-backslash forms of FreeRDP's drive-redirect path prefixes.
# These are what Windows hands to the shim verbatim; the shim
# forwards them in JSON without normalisation. See
# core/rdp.py:linux_to_unc for the producer side.
#
# We match the share name (``home``, ``media``, or any custom
# ``/drive:NAME,/path`` the user added in cfg.rdp.extra_flags) case-
# insensitively because Windows is case-insensitive and the shim
# faithfully forwards whatever case the OS supplied.
_UNC_PREFIX = "\\\\tsclient\\"

# Post-resolve denylist -- paths under these roots are always
# rejected even if a share root happens to overlap them via a
# misconfigured drive redirect. Defence-in-depth: a user with
# /drive:proc,/proc passed via cfg.rdp.extra_flags (currently
# blocked by the allowlist, but belts AND braces) would still get
# their /proc requests refused here.
_DENYLIST_ROOTS = (
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
)


def _unc_to_candidate(
    unc_path: str,
    share_roots: dict[str, Path],
) -> tuple[Path, Path]:
    """Parse a UNC path into an unresolved candidate + matched share root.

    Performs prefix-strip, share-root mapping, and character-level
    validation only. Does **not** call ``resolve()``, does **not**
    apply the denylist, and does **not** check ``is_relative_to`` --
    those steps are the caller's responsibility and must run *after*
    the FD is acquired in :func:`safe_open_unc`, so that the kernel's
    authoritative readlink-of-FD result is what we validate, not the
    pre-open guess.

    Returns ``(candidate_unresolved, matched_share_root)``.

    Raises :class:`ReversePathError` for:

    - empty ``share_roots``
    - non-string / empty / NUL-containing input
    - input not starting with ``\\\\tsclient\\``
    - non-ASCII share name
    - share name not in ``share_roots``
    - bare share root (no path component after the share name)
    """
    if not share_roots:
        raise ReversePathError("no share roots configured; refusing all paths")

    if not isinstance(unc_path, str):
        raise ReversePathError(f"unc_path must be str, got {type(unc_path).__name__}")
    if not unc_path:
        raise ReversePathError("empty path")
    if "\0" in unc_path:
        raise ReversePathError("embedded NUL byte")

    if not unc_path.lower().startswith(_UNC_PREFIX.lower()):
        raise ReversePathError(f"path is not under \\tsclient\\<share>: {unc_path!r}")

    # Strip the \\tsclient\ prefix (case preserved past this point --
    # only the prefix match was case-insensitive). The remainder
    # starts with the share name followed by \, then the rest.
    after_prefix = unc_path[len(_UNC_PREFIX) :]
    sep_index = after_prefix.find("\\")
    if sep_index == -1:
        # \\tsclient\home with no trailing path -- bare share root.
        raise ReversePathError(f"path resolves to bare share root: {unc_path!r}")

    share_name = after_prefix[:sep_index]
    rest = after_prefix[sep_index + 1 :]
    if not rest:
        raise ReversePathError(f"path resolves to bare share root: {unc_path!r}")

    if not share_name.isascii():
        raise ReversePathError(f"non-ASCII share name {share_name!r} in {unc_path!r}")

    # Case-insensitive match against the live share table.
    share_root: Path | None = None
    for name, root in share_roots.items():
        if name.lower() == share_name.lower():
            share_root = root
            break
    if share_root is None:
        raise ReversePathError(
            f"unknown share name {share_name!r} in {unc_path!r}; "
            f"known shares: {sorted(share_roots.keys())}"
        )

    # Convert Windows separators to POSIX. The shim already rejected
    # input with embedded NUL; the only other characters that POSIX
    # can't represent are NUL itself (already covered) and the path
    # separator, which we're remapping right here. Forward slashes
    # in the UNC component (theoretically possible if the guest hand-
    # crafts the request) survive verbatim; the kernel will canonicalise
    # at open() time.
    posix_relative = rest.replace("\\", "/")
    candidate = share_root / posix_relative

    return candidate, share_root


def translate_unc_to_posix(
    unc_path: str,
    share_roots: dict[str, Path],
) -> Path:
    """Translate a guest-supplied UNC path to a host-side ``Path`` (display-only).

    .. warning::

       Do **NOT** pass the result of this function to ``subprocess.Popen``,
       ``os.open``, ``open()``, or any other primitive that opens a path by
       name. The path is **not** TOCTOU-safe -- between the return of this
       function and the caller's open, a guest can swap a symlink anywhere
       along the path and redirect the operation to a different inode.
       Use :func:`safe_open_unc` instead, which acquires the FD before
       validation and validates against ``/proc/self/fd/N``.

       Acceptable callers: error-message formatting, CLI status lines,
       structured-log fields, anything that only displays the string.

    ``share_roots`` maps share name (e.g. ``'home'``, ``'media'``, or
    any user-configured ``/drive:NAME,/path``) -> host root ``Path``.
    The caller derives this from the live ``RDPConfig.drives`` /
    ``cfg.rdp.extra_flags`` parse so a user with a custom mount
    (``/drive:work,/mnt/work``) gets ``\\\\tsclient\\work\\report.pdf``
    resolved to ``/mnt/work/report.pdf`` automatically.

    Walks ``unc_path``'s leading share component case-insensitively
    (Windows is case-insensitive). Re-resolves both candidate and
    matching root via ``Path.resolve(strict=False)``, then enforces
    :func:`is_relative_to` to catch ``..`` traversal and symlink
    escape. Finally checks the resolved path against the post-resolve
    denylist (``/proc``, ``/sys``, ``/dev``).

    Raises :class:`ReversePathError` for:

    - empty ``share_roots``
    - non-string / empty / NUL-containing input
    - input not starting with ``\\\\tsclient\\``
    - non-ASCII share name
    - share name not in ``share_roots``
    - bare share root (no path component after the share name)
    - resolved path outside the share root
    - resolved path under ``/proc``, ``/sys``, or ``/dev``
    """
    candidate, share_root = _unc_to_candidate(unc_path, share_roots)

    # Resolve to canonicalise .. and symlinks. strict=False so we
    # don't require the file to exist -- the listener checks
    # existence separately and surfaces a friendlier error than
    # FileNotFoundError before we attempt to spawn.
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ReversePathError(f"path resolution failed for {unc_path!r}: {e}") from e

    # Re-resolve the share root the same way for an apples-to-apples
    # comparison (e.g. if $HOME is a symlink to /home/x, both sides
    # should canonicalise to /home/x).
    try:
        resolved_root = share_root.resolve(strict=False)
    except (OSError, RuntimeError) as e:
        raise ReversePathError(f"share root resolution failed: {e}") from e

    if not is_relative_to(resolved, resolved_root):
        raise ReversePathError(
            f"path escapes share root after canonicalisation: "
            f"{unc_path!r} -> {resolved} (root: {resolved_root})"
        )

    # Post-resolve denylist. A symlink in $HOME pointing into /proc
    # canonicalises away from $HOME first (which would already be
    # caught by is_relative_to above), but if the share root itself
    # ever overlaps /proc somehow this is the final guard.
    for deny_root in _DENYLIST_ROOTS:
        if is_relative_to(resolved, deny_root):
            raise ReversePathError(
                f"path resolves under system denylist root {deny_root}: {unc_path!r} -> {resolved}"
            )

    return resolved


# ---- TOCTOU-safe spawn helper -----------------------------------


@dataclass
class SafeFile:
    """Holder for an FD plus the kernel's canonical real path.

    Two paths are available on the holder; pick the right one for
    your call site:

    ``real_path``
        The kernel's canonical, post-resolve, real path to the inode
        the FD points at (the result of
        ``os.readlink('/proc/self/fd/N')``). This is what to hand to
        the spawned app's argv — opaque to the child, no FD inheritance
        required, works for D-Bus-handoff apps like Firefox /
        LibreOffice / Chromium that route the open through a singleton
        process (the receiver wouldn't have the listener's FD).

    ``proc_path``
        The literal ``/proc/self/fd/N`` form. Kept for callers that
        truly need the FD-pinned referent (e.g. tools that want to
        defend against between-validate-and-spawn TOCTOU swaps). Using
        it requires the child to inherit ``fd`` via Popen
        ``pass_fds=(self.fd,)``. **The listener does NOT use this**
        because the threat model is user-acting-on-own-files and the
        single-instance-app handoff failure is the dominant concern.

    Use the :func:`safe_open_unc` context-manager wrapper rather
    than constructing this directly -- it handles validation, FD
    acquisition, and cleanup on exception in a single atomic block.
    """

    fd: int
    real_path: Path
    proc_path: Path

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError as e:
            log.debug("SafeFile.close: %s", e)

    def popen_kwargs(self) -> dict[str, tuple[int, ...]]:
        """Return kwargs to merge into ``subprocess.Popen(...)``.

        Empty dict by default — call sites pass ``real_path`` as a
        regular argv slot, so the child opens by name (no inherited
        FD needed). If a caller switches back to ``proc_path``, this
        helper would need to return ``{"pass_fds": (self.fd,)}`` so
        the child can resolve ``/proc/self/fd/N`` in its own FD
        table; the listener doesn't take that path because Firefox /
        LibreOffice handoff to a singleton process makes inherited
        FDs unusable in the receiver.
        """
        return {}


# os.O_PATH was added in Linux's standard headers ages ago, but
# Python only exposes it on Linux in 3.10+; tolerate older Pythons
# where the constant lives under fcntl or as a literal.
_O_PATH = getattr(os, "O_PATH", 0o010000000)  # Linux O_PATH = 0o10000000


@contextmanager
def safe_open_unc(
    unc_path: str,
    share_roots: dict[str, Path],
) -> Iterator[SafeFile]:
    """Atomically translate, open, and validate a guest-supplied UNC path.

    This is the **only** correct entry point for callers that will pass
    the resulting path to a child process. It collapses the previously-
    separate translate-then-open flow into a single critical section so
    the kernel's authoritative view of the inode is what's validated.

    Sequence:

    1. Parse the UNC path into an unresolved candidate via
       :func:`_unc_to_candidate` (prefix-strip, share-root mapping,
       character validation -- no resolve, no denylist yet).
    2. Open the candidate immediately with ``O_PATH | O_NOFOLLOW``.
       From this point on the kernel pins the inode the FD points at,
       even if the on-disk path is swapped to a different inode.
    3. Read the kernel's authoritative resolved path via
       ``os.readlink('/proc/self/fd/N')`` -- this is the canonical
       path to the inode the FD references, regardless of any later
       symlink swap.
    4. Validate that readlink result against the matched share root
       (also resolved via ``Path.resolve(strict=False)``) using
       :func:`is_relative_to`.
    5. Apply the post-resolve denylist (``/proc``, ``/sys``, ``/dev``)
       to the readlink result.
    6. On any validation failure, close the FD and raise
       :class:`ReversePathError`.
    7. On success, yield a :class:`SafeFile` whose ``proc_path``
       (``/proc/self/fd/N``) is safe to pass to ``subprocess.Popen``
       (with ``pass_fds=(self.fd,)``).
    8. Close the FD on context exit (success or exception).

    Raises:

    - :class:`ReversePathError` for any guest-input or post-validation
      failure (parse, missing share, escape, denylist).
    - :class:`OSError` if the host can't open the candidate at all
      (file gone, permission denied, EACCES, ELOOP from the kernel's
      ``O_NOFOLLOW`` catching a leaf symlink, etc.). The listener
      treats this as INFO-level "target file not openable" rather than
      a security event.
    """
    candidate, share_root = _unc_to_candidate(unc_path, share_roots)

    # Acquire the FD FIRST. Once this returns, the kernel pins the
    # inode and any subsequent symlink swap on disk can no longer
    # redirect what we (and our future child) operate on.
    #
    # O_NOFOLLOW catches the leaf-symlink case -- if `candidate`
    # itself is a symlink at this exact instant, the kernel raises
    # ELOOP rather than following it. Non-leaf component swaps are
    # caught by the readlink validation below: the kernel's
    # /proc/self/fd/N readlink returns the *real* path to the inode,
    # and if that path isn't under the share root we reject.
    fd = os.open(str(candidate), _O_PATH | os.O_NOFOLLOW)

    try:
        # With ``O_PATH | O_NOFOLLOW``, the kernel opens the symlink
        # *itself* if the leaf is a symlink (rather than raising ELOOP
        # the way plain ``O_NOFOLLOW`` would). That's a problem for
        # us: ``readlink('/proc/self/fd/N')`` then returns the
        # symlink's own path -- which is inside the share root by
        # construction -- and the ``is_relative_to`` check would
        # incorrectly pass even though the symlink target points
        # outside.
        #
        # Fstat the FD and refuse if the leaf is a symlink. Regular
        # files, directories, and other inodes are fine; symlinks at
        # this layer always indicate either an attack or a guest
        # producing a path the listener shouldn't follow.
        try:
            st = os.fstat(fd)
        except OSError as e:
            raise ReversePathError(f"could not fstat fd {fd} for {unc_path!r}: {e}") from e
        if stat.S_ISLNK(st.st_mode):
            raise ReversePathError(
                f"refusing symlink leaf for {unc_path!r}: "
                f"O_PATH|O_NOFOLLOW pinned a symlink, not a real inode"
            )

        # Read the canonical path the kernel sees for this FD. This
        # is the authoritative answer -- not affected by any later
        # on-disk rename or symlink swap.
        try:
            true_path_str = os.readlink(f"/proc/self/fd/{fd}")
        except OSError as e:
            raise ReversePathError(
                f"could not read /proc/self/fd/{fd} for {unc_path!r}: {e}"
            ) from e
        true_path = Path(true_path_str)

        # Resolve the share root for an apples-to-apples comparison.
        try:
            resolved_root = share_root.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise ReversePathError(f"share root resolution failed: {e}") from e

        if not is_relative_to(true_path, resolved_root):
            raise ReversePathError(
                f"path escapes share root after canonicalisation: "
                f"{unc_path!r} -> {true_path} (root: {resolved_root})"
            )

        # Post-resolve denylist applied to the kernel's canonical
        # path -- defence-in-depth against share roots that overlap
        # /proc, /sys, or /dev.
        for deny_root in _DENYLIST_ROOTS:
            if is_relative_to(true_path, deny_root):
                raise ReversePathError(
                    f"path resolves under system denylist root {deny_root}: "
                    f"{unc_path!r} -> {true_path}"
                )

        proc_path = Path(f"/proc/self/fd/{fd}")
        safe = SafeFile(fd=fd, real_path=true_path, proc_path=proc_path)
        try:
            yield safe
        finally:
            safe.close()
    except BaseException:
        # Validation failed (or yield body raised before SafeFile.close
        # ran) -- ensure the FD never leaks.
        try:
            os.close(fd)
        except OSError:
            pass
        raise
