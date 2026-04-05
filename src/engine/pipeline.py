"""Orchestrate works JSONL → AI analysis → report JSONL and Markdown."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from core.config import default_data_dir
from core.interest import resolve_research_interest
from core.models import DEFAULT_DEEPSEEK_API_BASE, DEFAULT_DEEPSEEK_MODEL
from core.env import load_environment
from engine.openalex_work import extract_journal_name, extract_work_doi, work_to_prompt_payload
from engine.summary_engine import summarize_work_for_interest
from storage.jsonl import iter_jsonl, next_index_no_for_report
from storage.markdown_reports import write_deep_dive_tech_report, write_report_summary_md
from utils.stdio import configure_logging, ensure_utf8_stdio

logger = logging.getLogger(__name__)


def analyze_works_file(
    *,
    research_interest: str,
    works_path: Path,
    out_path: Path,
    api_key: str,
    api_base: str = DEFAULT_DEEPSEEK_API_BASE,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    delay_sec: float = 0.0,
    append_out: bool = False,
) -> tuple[int, int]:
    """
    Process each work; append one JSON line per input work to ``out_path``.
    Each record includes ``index_no`` (1-based, stable line order in this file).
    Returns (success_count, failure_count).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0

    index_no = next_index_no_for_report(out_path, append_out=append_out)
    out_mode = "a" if append_out else "w"
    with out_path.open(out_mode, encoding="utf-8") as out:
        for work in iter_jsonl(works_path):
            wid = work.get("id") or work.get("doi") or "unknown"
            journal_name = extract_journal_name(work)
            doi = extract_work_doi(work)
            record: dict[str, Any] = {
                "index_no": index_no,
                "work_id": wid,
                "title": work.get("title") or work.get("display_name"),
                "publication_year": work.get("publication_year"),
                "journal_name": journal_name,
                "doi": doi,
            }
            try:
                block = work_to_prompt_payload(work)
                ai = summarize_work_for_interest(
                    api_key=api_key,
                    api_base=api_base,
                    model=model,
                    research_interest=research_interest,
                    paper_block=block,
                )
                ai_out = dict(ai)
                ai_out.setdefault("extracted_figures", [])
                record["ai"] = ai_out
                record["ok"] = True
                ok += 1
            except Exception as e:
                logger.error("分析失败 work=%s: %s", wid, e)
                record["ok"] = False
                record["error"] = str(e)
                fail += 1
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            index_no += 1
            if delay_sec > 0:
                time.sleep(delay_sec)
    return ok, fail


def run_analyze_cli(argv: Optional[list[str]] = None) -> int:
    load_environment()
    ensure_utf8_stdio()
    configure_logging()
    parser = argparse.ArgumentParser(description="Analyze works_sample.jsonl with DeepSeek.")
    parser.add_argument(
        "--interest",
        type=str,
        default=None,
        help="研究需求（否则读 RESEARCH_INTEREST / USER_RESEARCH_NEED 或交互输入）",
    )
    parser.add_argument(
        "--works",
        type=Path,
        default=None,
        help="输入 JSONL，默认 data/works_sample.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="输出 JSONL，默认 data/final_report.jsonl",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=os.getenv("DEEPSEEK_API_BASE", DEFAULT_DEEPSEEK_API_BASE),
        help="如 https://api.deepseek.com/v1",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=float(os.getenv("ANALYZER_DELAY_SEC", "0") or 0),
        help="每条请求之间的休眠秒数，可选",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="追加写入输出文件（默认每次运行覆盖 final_report.jsonl）",
    )
    args = parser.parse_args(argv)

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        logger.error("请设置环境变量 DEEPSEEK_API_KEY")
        return 1

    data_dir = default_data_dir()
    works_path = args.works or data_dir / "works_sample.jsonl"
    out_path = args.out or data_dir / "final_report.jsonl"

    if not works_path.is_file():
        logger.error("找不到输入文件: %s", works_path)
        return 1

    try:
        interest = resolve_research_interest(args.interest)
    except SystemExit as e:
        logger.error("%s", e)
        return 1

    ok, fail = analyze_works_file(
        research_interest=interest,
        works_path=works_path,
        out_path=out_path,
        api_key=api_key,
        api_base=args.api_base,
        model=args.model,
        delay_sec=args.delay,
        append_out=args.append,
    )
    logger.info("完成：成功 %s，失败 %s，结果写入 %s", ok, fail, out_path)

    summary_md = out_path.parent / "report_summary.md"
    n_high = write_report_summary_md(
        out_path,
        summary_md,
        min_score=60,
        research_interest=interest,
    )
    logger.info("已生成 Markdown 汇总：%s（%s 条 match_score>60）", summary_md, n_high)

    deep_md = data_dir / "deep_dive_tech_report.md"
    dd_ok, dd_fail = write_deep_dive_tech_report(
        report_jsonl=out_path,
        works_path=works_path,
        md_path=deep_md,
        api_key=api_key,
        api_base=args.api_base,
        model=args.model,
        research_interest=interest,
        delay_sec=args.delay,
        elite_min_score=95,
    )
    logger.info("Deep Dive 完成：成功 %s，失败 %s，报告 %s", dd_ok, dd_fail, deep_md)
    return 0
