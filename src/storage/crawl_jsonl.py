"""
Stream OpenAlex works into a JSONL file (uses ``crawler`` only for API access).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from crawler.client import MAX_PER_PAGE, OpenAlexClient, read_academic_analyze_mode

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
    extra_params: Optional[dict[str, Any]] = None,
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
    extra_for_state: dict[str, Any] = dict(extra_params) if extra_params else {}

    start_cursor: Optional[str] = None
    mode = "a"
    if not resume:
        if state_path.exists():
            state_path.unlink(missing_ok=True)
        mode = "w"
    else:
        st = load_state(state_path)
        st_extra = st.get("extra_params") if isinstance(st.get("extra_params"), dict) else {}
        if (
            st
            and st.get("filter") == filter_expr
            and st.get("search") == search
            and st_extra == extra_for_state
        ):
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
            extra_params=extra_params,
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
                        "extra_params": extra_for_state,
                        "next_cursor": next_cursor,
                    },
                )
            else:
                state_path.unlink(missing_ok=True)

    return written


def run_crawl_main() -> int:
    """CLI entry: read crawl settings from environment and write ``works_sample.jsonl``.

    Returns the number of works written this run.
    """
    import os

    from core.config import default_data_dir
    from core.env import load_environment
    from crawler.client import build_recent_frontier_filter
    from utils.stdio import configure_logging

    load_environment()
    configure_logging()

    data_dir = default_data_dir()
    out_file = data_dir / "works_sample.jsonl"

    analyze_mode = read_academic_analyze_mode()
    custom_filter = os.getenv("OPENALEX_FILTER", "").strip()
    sort_override = os.getenv("OPENALEX_SORT", "").strip()
    extra_params: dict[str, Any] = {}

    if custom_filter:
        filter_expr = custom_filter
        if sort_override:
            extra_params["sort"] = sort_override
        elif analyze_mode == "recent":
            extra_params["sort"] = "publication_date:desc"
    elif analyze_mode == "related":
        filter_expr = None
        if sort_override:
            extra_params["sort"] = sort_override
    else:
        filter_expr = build_recent_frontier_filter()
        extra_params["sort"] = "publication_date:desc"
        if sort_override:
            extra_params["sort"] = sort_override

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
        "crawl ACADEMIC_ANALYZE_MODE=%r filter=%r sort=%r search=%r",
        analyze_mode,
        filter_expr,
        extra_params.get("sort"),
        search,
    )

    n = fetch_works_to_file(
        out_file,
        filter_expr=filter_expr,
        search=search,
        max_records=max_records,
        resume=resume,
        extra_params=extra_params if extra_params else None,
    )
    logger.info("wrote %s works to %s", n, out_file)
    return n
