"""Read/write JSON Lines with tolerant parsing."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("skip line %s: invalid JSON (%s)", lineno, e)
                continue
            if isinstance(obj, dict):
                yield obj
            else:
                logger.warning("skip line %s: not an object", lineno)


def next_index_no_for_report(out_path: Path, *, append_out: bool) -> int:
    """Next ``index_no`` when writing ``final_report.jsonl`` (1-based)."""
    if not append_out or not out_path.is_file():
        return 1
    m = 0
    n_lines = 0
    for rec in iter_jsonl(out_path):
        n_lines += 1
        try:
            n = int(rec.get("index_no", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n > m:
            m = n
    if m > 0:
        return m + 1
    return n_lines + 1


def write_jsonl_line(fp, obj: dict[str, Any]) -> None:
    fp.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_jsonl_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
        encoding="utf-8",
    )


def merge_ai_extracted_figures(report_jsonl: Path, work_id: str, paths: list[str]) -> bool:
    """
    Append unique ``paths`` to ``ai.extracted_figures`` for the row matching ``work_id``.
    Returns whether the file was updated.
    """
    if not paths:
        return False
    rows = [dict(r) for r in iter_jsonl(report_jsonl)]
    changed = False
    for r in rows:
        if r.get("work_id") != work_id:
            continue
        ai = r.get("ai")
        if not isinstance(ai, dict):
            ai = {}
            r["ai"] = ai
        old = ai.get("extracted_figures")
        if not isinstance(old, list):
            old = []
        seen = set(old)
        merged = list(old)
        for p in paths:
            if p not in seen:
                merged.append(p)
                seen.add(p)
        ai["extracted_figures"] = merged
        changed = True
        break
    if changed:
        write_jsonl_records(report_jsonl, rows)
    return changed
