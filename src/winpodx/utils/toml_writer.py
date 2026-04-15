"""Minimal TOML writer — replaces tomli-w dependency.

Only supports the subset of TOML used by winpodx config:
flat tables with string, int, bool values.
"""

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


def _format_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, str):
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        return f'"{escaped}"'
    elif isinstance(val, float):
        return str(val)
    elif isinstance(val, list):
        items = ", ".join(_format_value(v) for v in val)
        return f"[{items}]"
    elif val is None:
        return '""'
    raise TypeError(f"Unsupported TOML value type: {type(val).__name__}")
