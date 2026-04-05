"""Plain-text and Markdown table helpers."""

from __future__ import annotations

from typing import Any


def md_table_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|")
    s = " ".join(line.strip() for line in s.split("\n") if line.strip())
    return s.strip()
