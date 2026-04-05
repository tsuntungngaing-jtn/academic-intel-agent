"""
Stream OpenAlex works into a JSONL file (uses ``crawler`` only for API access).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from crawler.client import MAX_PER_PAGE, OpenAlexClient

from .crawl_state import load_state, state_path_for, write_state

logger = logging.getLogger(__name__)


def fetch_works_to_file(
    path: Path,
    *,
    filter_expr: Optional[str] = None,
    search: Optional[str] = None,
    max_records: Optional[int] = None,
    resume: bool = True,
    client: Optional[OpenAlexClient] = None,
) -> int:
    """
    Append works as JSON lines to ``path``.

    With ``resume``, continues from ``path.openalex_state.json`` when filter/search
    match (cursor-based). State is updated after each full page. If a run stops mid-page,
    re-running may duplicate up to one page of rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    state_path = state_path_for(path)
    ox = client or OpenAlexClient()

    start_cursor: Optional[str] = None
    mode = "a"
    if not resume:
        if state_path.exists():
            state_path.unlink(missing_ok=True)
        mode = "w"
    else:
        st = load_state(state_path)
        if st and st.get("filter") == filter_expr and st.get("search") == search:
            start_cursor = st.get("next_cursor")
            if isinstance(start_cursor, str) and start_cursor:
                logger.info("resuming from saved cursor for %s", path.name)
            else:
                start_cursor = None
        elif st:
            logger.info("state query mismatch; starting new crawl")

    written = 0
    with path.open(mode, encoding="utf-8") as out:
        for results, meta in ox.iter_work_pages(
            filter_expr=filter_expr,
            search=search,
            per_page=MAX_PER_PAGE,
            cursor=start_cursor,
        ):
            for work in results:
                out.write(json.dumps(work, ensure_ascii=False) + "\n")
                written += 1
                if max_records is not None and written >= max_records:
                    out.flush()
                    state_path.unlink(missing_ok=True)
                    logger.warning(
                        "max_records reached mid-page; checkpoint cleared so the next run "
                        "does not skip the rest of this page"
                    )
                    return written

            out.flush()
            next_cursor = meta.get("next_cursor")
            if isinstance(next_cursor, str) and next_cursor:
                write_state(
                    state_path,
                    {
                        "filter": filter_expr,
                        "search": search,
                        "next_cursor": next_cursor,
                    },
                )
            else:
                state_path.unlink(missing_ok=True)

    return written


def run_crawl_main() -> None:
    """CLI entry: read crawl settings from environment and write ``works_sample.jsonl``."""
    import os

    from core.config import default_data_dir
    from core.env import load_environment
    from crawler.client import build_recent_publication_filter
    from utils.stdio import configure_logging

    load_environment()
    configure_logging()

    data_dir = default_data_dir()
    out_file = data_dir / "works_sample.jsonl"

    custom_filter = os.getenv("OPENALEX_FILTER", "").strip()
    recent_days_raw = os.getenv("OPENALEX_RECENT_DAYS", "90").strip()
    recent_days = int(recent_days_raw) if recent_days_raw.isdigit() else 90
    filter_expr = custom_filter or build_recent_publication_filter(recent_days)

    search = os.getenv("OPENALEX_SEARCH", "").strip() or None
    if not search:
        logger.error(
            "未设置 OPENALEX_SEARCH。抓取必须与 search 关键词绑定；请在 .env 中设置 OPENALEX_SEARCH。"
        )
        raise SystemExit(1)

    max_n = os.getenv("OPENALEX_MAX_RECORDS")
    max_records = int(max_n) if max_n and max_n.isdigit() else 50
    resume = os.getenv("OPENALEX_RESUME", "0").strip().lower() not in ("0", "false", "no")

    logger.info(
        "crawl filter=%r search=%r (override filter with OPENALEX_FILTER if needed)",
        filter_expr,
        search,
    )

    n = fetch_works_to_file(
        out_file,
        filter_expr=filter_expr,
        search=search,
        max_records=max_records,
        resume=resume,
    )
    logger.info("wrote %s works to %s", n, out_file)
