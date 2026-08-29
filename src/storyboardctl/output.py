from __future__ import annotations

import json
from typing import Any


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def table_text(value: Any) -> str:
    if isinstance(value, dict):
        rows = [{"field": key, "value": item} for key, item in value.items()]
    elif isinstance(value, list):
        rows = [item for item in value if isinstance(item, dict)]
        if len(rows) != len(value):
            return "\n".join(str(item) for item in value)
    else:
        return str(value)
    if not rows:
        return "(empty)"
    headers: list[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)
    cells = [[_cell(row.get(header)) for header in headers] for row in rows]
    widths = [min(60, max(len(header), *(len(row[index]) for row in cells))) for index, header in enumerate(headers)]
    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    divider = "  ".join("-" * width for width in widths)
    body = ["  ".join(cell[: widths[index]].ljust(widths[index]) for index, cell in enumerate(row)) for row in cells]
    return "\n".join([header_line, divider, *body])


def _cell(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if value is None:
        return ""
    return str(value)
