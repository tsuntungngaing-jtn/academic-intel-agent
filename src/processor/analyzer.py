"""
Backward-compatible re-exports. Prefer:

- ``engine.pipeline`` / ``engine.summary_engine`` / ``engine.deep_dive_engine``
- ``storage.jsonl`` / ``storage.markdown_reports``
- ``core.interest`` / ``core.models``
"""

from __future__ import annotations

from core.interest import resolve_research_interest
from core.models import FULL_RELEVANCE, PARTIAL_RELEVANCE
from engine.openalex_work import (
    extract_journal_name,
    extract_work_doi,
    reconstruct_abstract,
    work_to_prompt_payload,
)
from engine.pipeline import analyze_works_file, run_analyze_cli as main
from storage.jsonl import iter_jsonl
from storage.markdown_reports import write_deep_dive_tech_report, write_report_summary_md

__all__ = [
    "FULL_RELEVANCE",
    "PARTIAL_RELEVANCE",
    "analyze_works_file",
    "extract_journal_name",
    "extract_work_doi",
    "iter_jsonl",
    "main",
    "reconstruct_abstract",
    "resolve_research_interest",
    "work_to_prompt_payload",
    "write_deep_dive_tech_report",
    "write_report_summary_md",
]
