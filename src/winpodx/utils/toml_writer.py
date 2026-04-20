"""Minimal TOML writer (replaces tomli-w dependency)."""

from __future__ import annotations

from typing import Any


def dumps(data: dict[str, Any]) -> str:
    """Serialize a dict to TOML string."""
    lines: list[str] = []
    for section, values in data.items():
        if isinstance(values, dict):
            lines.append(f"[{section}]")
            for key, val in values.items():
                lines.append(f"{key} = {_format_value(val)}")
            lines.append("")
        else:
            lines.append(f"{section} = {_format_value(values)}")
    return "\n".join(lines) + "\n"


def _escape_string(val: str) -> str:
    """Escape a TOML basic string with full control-character coverage.

    TOML requires all control chars (U+0000-U+001F, U+007F) to be escaped.
    """
    out: list[str] = ['"']
    for ch in val:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _format_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, str):
        return _escape_string(val)
    elif isinstance(val, float):
        return str(val)
    elif isinstance(val, list):
        items = ", ".join(_format_value(v) for v in val)
        return f"[{items}]"
    elif val is None:
        return '""'
    raise TypeError(f"Unsupported TOML value type: {type(val).__name__}")
