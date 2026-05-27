# SPDX-License-Identifier: MIT
"""Resolve freedesktop icon names + convert PNG / SVG to Windows ICO.

The Windows side of reverse-open registers each Linux app as a Windows
"Open with…" entry. Each entry needs an ``.ico`` file that Windows
Explorer can render at every display DPI (16/24/32/48/64/128/256 px).
This module is the Linux-side half of that pipeline: take a
freedesktop icon name (e.g. ``"kate"``), find the concrete file on
disk via the user's active icon theme inheritance chain, and rasterise
it into a multi-resolution ``.ico``.

Dependencies are intentionally soft:

- :mod:`xdg.IconTheme` (from pyxdg) drives icon-name resolution. If
  pyxdg isn't installed, we fall back to a small built-in lookup that
  walks the Hicolor theme dirs explicitly — covers the common case
  on a stock GNOME / KDE / XFCE host.
- :mod:`PIL` (Pillow) handles raster (PNG) → ICO. Always required when
  this module's writer paths run; soft-imported so ``import icons`` at
  module load doesn't crash on a host without Pillow.
- :mod:`cairosvg` is preferred for SVG → PNG; :mod:`svglib` is a
  fallback. Either / neither: we degrade gracefully and ship a
  placeholder ico with a logged warning, so the registered app still
  launches — just without a custom icon.

Output ICO files live under
``~/.local/share/winpodx/reverse-open/icons/<slug>.ico`` per the
design doc; this module is purely the converter and doesn't choose
that path. The caller (host-side sync / listener) decides where the
output lands.

See ``docs/design/REVERSE_OPEN_DESIGN.md`` §"Component contracts →
icons.py" for the full contract + licence note (no redistribution
concern — icons are generated at runtime on the user's machine from
icons they already have).
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# Multi-resolution sizes embedded in every generated .ico. Windows
# Explorer + the shell scale appropriately per DPI; bandwidth saving
# from dropping middle sizes is meaningless on a localhost share, so
# we keep all of them.
ICO_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)

# Search the Hicolor theme fallback by descending size — Windows is
# happy with whatever's available at 256 → 16, so prefer the largest
# raster on disk to keep the rasteriser from blurring upward.
_HICOLOR_SIZE_DIRS: tuple[str, ...] = (
    "256x256",
    "192x192",
    "128x128",
    "96x96",
    "64x64",
    "48x48",
    "32x32",
    "24x24",
    "22x22",
    "16x16",
    "scalable",
)

_ICON_EXTENSIONS: tuple[str, ...] = (".png", ".svg", ".xpm")


def _icon_search_dirs() -> list[Path]:
    """Return the directories where icon themes typically live.

    Mirrors the freedesktop Icon Theme spec's lookup order: user
    override under ``$XDG_DATA_HOME/icons`` first, then per-system
    ``$XDG_DATA_DIRS/icons``, then ``/usr/share/pixmaps`` as the
    eternal fallback. Non-existent directories are kept in the list
    so callers can see what was checked.
    """
    home = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    system = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    out: list[Path] = [Path(home) / "icons"]
    for d in system.split(":"):
        d = d.strip()
        if not d:
            continue
        out.append(Path(d) / "icons")
    out.append(Path("/usr/share/pixmaps"))
    return out


def _fallback_resolve(icon_name: str) -> Path | None:
    """Resolve an icon name without pyxdg, via Hicolor + pixmaps.

    Walks ``<icondir>/hicolor/<sizedir>/apps/<icon_name>.<ext>`` for the
    standard sizes; on miss, sweeps the top level of
    ``/usr/share/pixmaps`` (where many distros drop apps that don't
    ship a themed icon). Returns the first hit or ``None``.
    """
    if not icon_name or "/" in icon_name or icon_name.startswith("."):
        return None
    for base in _icon_search_dirs():
        # Hicolor first — themed locations.
        hicolor = base / "hicolor"
        if hicolor.is_dir():
            for size_dir in _HICOLOR_SIZE_DIRS:
                for ext in _ICON_EXTENSIONS:
                    candidate = hicolor / size_dir / "apps" / f"{icon_name}{ext}"
                    if candidate.is_file():
                        return candidate
        # /usr/share/pixmaps and similar flat fallbacks.
        for ext in _ICON_EXTENSIONS:
            candidate = base / f"{icon_name}{ext}"
            if candidate.is_file():
                return candidate
    return None


def resolve_icon(icon_name: str) -> Path | None:
    """Resolve a freedesktop icon name to a concrete file on disk.

    Prefers :func:`xdg.IconTheme.getIconPath` (which implements the
    full theme inheritance chain — user theme → Hicolor → fallback).
    Falls back to a built-in Hicolor + pixmaps walker when pyxdg
    isn't installed, so the discovery → register pipeline stays
    functional on a minimal host.

    Args:
      icon_name: the value of ``Icon=`` from a ``.desktop`` entry, OR
        an absolute path. Absolute paths are accepted as-is when the
        file exists.

    Returns:
      The resolved :class:`Path`, or ``None`` if nothing matched.
    """
    if not icon_name:
        return None

    # Absolute path — accept verbatim if the file exists. Some
    # .desktop entries (notably AppImage launchers) put the full
    # extracted-icon path here.
    if os.path.isabs(icon_name):
        p = Path(icon_name)
        return p if p.is_file() else None

    try:
        from xdg.IconTheme import getIconPath  # type: ignore[import-not-found]
    except ImportError:
        return _fallback_resolve(icon_name)

    for size in (256, 128, 64, 48, 32):
        try:
            path = getIconPath(icon_name, size=size)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("xdg.IconTheme.getIconPath crashed on %r: %s", icon_name, exc)
            return _fallback_resolve(icon_name)
        if path and os.path.isfile(path):
            return Path(path)

    return _fallback_resolve(icon_name)


def _svg_to_png_bytes(src: Path, size: int) -> bytes | None:
    """Rasterise an SVG to PNG bytes at the requested square size.

    Tries :mod:`cairosvg` first (fast, accurate). Falls back to
    :mod:`svglib` + :mod:`reportlab` if cairosvg isn't available.
    Returns ``None`` if neither backend is installed or both fail;
    caller's responsibility to pick a placeholder.
    """
    try:
        import cairosvg  # type: ignore[import-not-found]
    except ImportError:
        cairosvg = None  # type: ignore[assignment]

    if cairosvg is not None:
        try:
            return cairosvg.svg2png(url=str(src), output_width=size, output_height=size)
        except Exception as exc:  # pragma: no cover - depends on broken SVG
            logger.debug("cairosvg failed on %s @ %d: %s", src, size, exc)

    try:
        from reportlab.graphics import renderPM  # type: ignore[import-not-found]
        from svglib.svglib import svg2rlg  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        drawing = svg2rlg(str(src))
        if drawing is None:
            return None
        # svglib's drawing keeps the source viewBox; scale to size.
        scale = size / max(drawing.width, drawing.height, 1)
        drawing.width *= scale
        drawing.height *= scale
        drawing.scale(scale, scale)
        buf = io.BytesIO()
        renderPM.drawToFile(drawing, buf, fmt="PNG")
        return buf.getvalue()
    except Exception as exc:  # pragma: no cover
        logger.debug("svglib failed on %s @ %d: %s", src, size, exc)
        return None


def _placeholder_image(size: int) -> "object":
    """Generate a generic placeholder image at the requested size.

    Used when the source icon can't be loaded or rasterised. Returns
    a Pillow image (the type annotation is loose so the module's
    top-level import stays soft).
    """
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    # Filled rounded square, dark grey. Recognisable as "an icon is
    # missing" without looking broken.
    draw.rectangle(
        [(2, 2), (size - 3, size - 3)],
        fill=(64, 64, 64, 255),
        outline=(160, 160, 160, 255),
        width=max(1, size // 32),
    )
    return img


def _decode_xpm_rgba(src: Path) -> "object | None":
    """Decode an XPM file to an RGBA Pillow image in pure Python, or None.

    Pillow's bundled XPM plugin only supports one char per pixel (``cpp == 1``,
    ≤256 colours) and raises on anything richer. Real-world icons routinely
    ship as XPM with ``cpp >= 2`` and >256 colours -- e.g. veracrypt's only
    icon is ``/usr/share/pixmaps/veracrypt.xpm`` at 1770 colours, cpp 2, which
    made Pillow raise ``KeyError`` and the app fall back to a blank placeholder
    in the Windows "Open with" menu.

    XPM is a plain-text C array, so we parse it ourselves: pull the quoted
    string literals, read the ``W H NCOLORS CPP`` header, build the
    colour-key → RGBA table (``c`` colour value; ``None`` → transparent;
    ``#RGB`` / ``#RRGGBB`` / ``#RRRRGGGGBBBB`` hex or an X11 colour name via
    Pillow's ``ImageColor``), then map every ``cpp``-char pixel key to a
    colour. No external dependency, so it works identically everywhere
    including the AppImage. Returns None on any malformed input so the caller
    can still write a placeholder.
    """
    import re

    from PIL import Image, ImageColor

    try:
        text = src.read_text(encoding="latin-1")
    except OSError as exc:
        logger.debug("cannot read XPM %s: %s", src, exc)
        return None

    # Quoted string literals, in order: [values, <NCOLORS colours>, <H rows>].
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
    if len(literals) < 1:
        return None
    try:
        w, h, ncolors, cpp = (int(x) for x in literals[0].split()[:4])
    except ValueError:
        logger.debug("XPM %s: unparseable values header %r", src, literals[0])
        return None
    if w <= 0 or h <= 0 or cpp <= 0 or ncolors <= 0:
        return None
    if len(literals) < 1 + ncolors + h:
        logger.debug("XPM %s: truncated (need %d literals)", src, 1 + ncolors + h)
        return None

    def _parse_color(value: str) -> tuple[int, int, int, int]:
        v = value.strip()
        if v.lower() == "none":
            return (0, 0, 0, 0)
        if v.startswith("#") and len(v) == 13:  # #RRRRGGGGBBBB -> high byte each
            return (int(v[1:3], 16), int(v[5:7], 16), int(v[9:11], 16), 255)
        try:
            r, g, b = ImageColor.getrgb(v)[:3]
            return (r, g, b, 255)
        except (ValueError, KeyError):
            return (0, 0, 0, 0)  # unknown colour name -> transparent

    palette: dict[str, tuple[int, int, int, int]] = {}
    for line in literals[1 : 1 + ncolors]:
        key = line[:cpp]
        rest = line[cpp:]
        # "<key> <type1> <val1> <type2> <val2> ...", types: c m g g4 s.
        # Prefer the 'c' (colour) entry; the value can itself be multi-word
        # only for symbolic names, which we don't need. Grab the token after
        # the first standalone 'c'.
        toks = rest.split()
        color = None
        i = 0
        while i < len(toks) - 1:
            if toks[i] == "c":
                color = toks[i + 1]
                break
            i += 1
        palette[key] = _parse_color(color) if color is not None else (0, 0, 0, 0)

    img = Image.new("RGBA", (w, h))
    px = img.load()
    transparent = (0, 0, 0, 0)
    for y, row in enumerate(literals[1 + ncolors : 1 + ncolors + h]):
        for x in range(w):
            key = row[x * cpp : x * cpp + cpp]
            px[x, y] = palette.get(key, transparent)
    return img


def _open_raster_rgba(src: Path) -> "object | None":
    """Open *src* as an RGBA Pillow image.

    Pillow first; on failure (notably XPM with ``cpp >= 2``, which Pillow's
    decoder can't handle) fall back to the pure-Python XPM decoder. Returns
    None only when nothing can read it (the caller then writes a placeholder).
    The annotation is loose to keep the module-level Pillow import soft.
    """
    from PIL import Image

    try:
        return Image.open(src).convert("RGBA")
    except Exception as exc:  # noqa: BLE001 -- Pillow raises a variety of types
        if src.suffix.lower() == ".xpm":
            logger.debug("Pillow can't open XPM %s (%s); using pure-Python decoder", src, exc)
            img = _decode_xpm_rgba(src)
            if img is not None:
                return img
        else:
            logger.debug("Pillow can't open %s: %s", src, exc)
        return None


def convert_to_ico(src: Path, dst: Path) -> bool:
    """Convert a PNG / SVG / XPM icon to a multi-resolution Windows ICO.

    Args:
      src: source raster or vector icon. ``Path`` is required; pass an
        empty / non-existent path and the function returns ``False``
        without writing.
      dst: target ``.ico`` path. Parent dirs are created. An existing
        file is overwritten atomically (write to ``<dst>.tmp`` then
        rename).

    Returns:
      ``True`` on success, ``False`` if the source couldn't be loaded
      and a placeholder was written instead. Either way, ``dst`` is
      a valid ICO file the caller can hand to Windows.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed; cannot write %s", dst)
        return False

    images: list = []
    used_placeholder = False

    if not src or not src.is_file():
        used_placeholder = True
    elif src.suffix.lower() == ".svg":
        for size in ICO_SIZES:
            png_bytes = _svg_to_png_bytes(src, size)
            if png_bytes is None:
                used_placeholder = True
                break
            images.append(Image.open(io.BytesIO(png_bytes)).convert("RGBA"))
    else:
        # Raster — open once, then upscale to the largest target size
        # before resampling down to every other size. Pillow's ICO
        # encoder silently SKIPS any requested size larger than the
        # source (``if size[0] > width: continue`` in IcoImagePlugin),
        # so a 16×16 source PNG would produce a single-frame 16×16
        # .ico even though we asked for 7 sizes. Win11's OpenWith
        # chooser then renders that tiny frame fuzzy or falls back
        # to the generic .exe icon entirely. Upscaling first guarantees
        # every requested size lands in the output.
        base = _open_raster_rgba(src)
        if base is None:
            logger.warning("could not load icon %s; using placeholder", src)
            used_placeholder = True
        else:
            max_size = max(ICO_SIZES)
            if base.size != (max_size, max_size):
                base = base.resize((max_size, max_size), Image.Resampling.LANCZOS)
            for size in ICO_SIZES:
                if size == max_size:
                    images.append(base)
                else:
                    images.append(base.resize((size, size), Image.Resampling.LANCZOS))

    if used_placeholder or not images:
        images = [_placeholder_image(size) for size in ICO_SIZES]

    # Order frames largest-first. Pillow's ICO encoder uses the BASE
    # image's dimensions as the cap for which ``sizes=`` entries it
    # actually writes — anything larger than the base is dropped.
    # Saving the 256×256 frame as the base guarantees all 7 sizes
    # land in the output.
    by_size_desc = sorted(images, key=lambda im: im.size[0], reverse=True)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    try:
        by_size_desc[0].save(
            tmp,
            format="ICO",
            sizes=[(s, s) for s in ICO_SIZES],
            append_images=by_size_desc[1:],
        )
        os.replace(tmp, dst)
    finally:
        # If save raised, make sure we don't leave a half-written .tmp.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    return not used_placeholder
