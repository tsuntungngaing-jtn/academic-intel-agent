"""
Map OpenAlex ``work`` JSON objects to prompt-oriented fields.

Crawl-time search policy (``recent`` vs ``related``) lives in
``crawler.client`` / ``storage.crawl_jsonl`` and reads ``ACADEMIC_ANALYZE_MODE``.
"""

from __future__ import annotations

from typing import Any, Optional

MAX_ABSTRACT_CHARS = 8000


def extract_journal_name(work: dict[str, Any]) -> Optional[str]:
    """Journal display name from OpenAlex ``primary_location.source.display_name``."""
    loc = work.get("primary_location")
    if not isinstance(loc, dict):
        return None
    src = loc.get("source")
    if not isinstance(src, dict):
        return None
    name = src.get("display_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def extract_work_doi(work: dict[str, Any]) -> Optional[str]:
    d = work.get("doi")
    if isinstance(d, str) and d.strip():
        return d.strip()
    return None


def reconstruct_abstract(inverted: Optional[dict[str, list[int]]]) -> str:
    """Rebuild plain text from OpenAlex ``abstract_inverted_index``."""
    if not inverted:
        return ""
    try:
        max_pos = max(p for positions in inverted.values() for p in positions)
    except ValueError:
        return ""
    parts: list[str] = [""] * (max_pos + 1)
    for word, positions in inverted.items():
        for p in positions:
            if 0 <= p < len(parts):
                parts[p] = word
    return " ".join(w for w in parts if w).strip()


def work_to_prompt_payload(work: dict[str, Any]) -> str:
    title = (work.get("title") or work.get("display_name") or "").strip()
    year = work.get("publication_year")
    journal = extract_journal_name(work)
    doi = extract_work_doi(work)
    inv = work.get("abstract_inverted_index")
    abstract = ""
    if isinstance(inv, dict):
        abstract = reconstruct_abstract(inv)  # type: ignore[arg-type]
    if len(abstract) > MAX_ABSTRACT_CHARS:
        abstract = abstract[: MAX_ABSTRACT_CHARS - 3] + "..."
    lines = [
        f"标题: {title}",
        f"年份: {year}" if year is not None else "年份: 未知",
        f"期刊: {journal}" if journal else "期刊: （未知）",
    ]
    if doi:
        lines.append(f"DOI: {doi}")
    lines.append(f"摘要: {abstract or '（无摘要）'}")
    return "\n".join(lines)
