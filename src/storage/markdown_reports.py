"""Generate Markdown reports from analyzed JSONL."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.models import FULL_RELEVANCE, PARTIAL_RELEVANCE
from engine.deep_dive_engine import deep_dive_paper, try_extract_figures_for_elite_paper
from engine.openalex_work import extract_work_doi, work_to_prompt_payload
from storage.jsonl import iter_jsonl, merge_ai_extracted_figures
from utils.text import md_table_cell

logger = logging.getLogger(__name__)


def _append_figure_markdown(lines: list[str], stored_paths: list[str]) -> None:
    """Paths are repo-relative (e.g. ``data/figures/...``); report lives under ``data/``."""
    if not stored_paths:
        return
    lines.append("### 附图（启发式提取）")
    lines.append("")
    for fp in stored_paths:
        rel = fp
        if fp.startswith("data/figures/"):
            rel = fp[len("data/"):]
        lines.append(f"![]({rel})")
        lines.append("")


def _high_score_records(report_jsonl: Path, *, min_score: int = 60) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in iter_jsonl(report_jsonl):
        if not rec.get("ok"):
            continue
        ai = rec.get("ai")
        if not isinstance(ai, dict):
            continue
        try:
            sc = int(ai.get("match_score", 0))
        except (TypeError, ValueError):
            continue
        if sc > min_score:
            rows.append(rec)
    rows.sort(
        key=lambda r: (
            -int((r.get("ai") or {}).get("match_score") or 0),
            str(r.get("title") or ""),
        )
    )
    return rows


def write_report_summary_md(
    report_jsonl: Path,
    md_path: Path,
    *,
    min_score: int = 60,
    research_interest: Optional[str] = None,
) -> int:
    """
    Read ``report_jsonl`` and write Markdown tables for entries with ``match_score > min_score``.
    Returns number of rows included.
    """
    rows = _high_score_records(report_jsonl, min_score=min_score)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines: list[str] = [
        f"# 高分论文汇总（匹配分 > {min_score}）",
        "",
        f"- 生成时间：{now}",
        f"- 数据文件：`{report_jsonl.name}`",
    ]
    if research_interest:
        lines.append(f"- 研究兴趣：{md_table_cell(research_interest)}")
    lines.extend(["", f"共 **{len(rows)}** 篇。", ""])

    if not rows:
        lines.append("*暂无匹配分高于阈值的记录。*")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        ai = r.get("ai") if isinstance(r.get("ai"), dict) else {}
        rel = ai.get("relevance_level")
        if rel == FULL_RELEVANCE:
            buckets[FULL_RELEVANCE].append(r)
        else:
            buckets[PARTIAL_RELEVANCE].append(r)

    section_order = [FULL_RELEVANCE, PARTIAL_RELEVANCE]
    header = (
        "| 编号 | 匹配分 | 相关度 | 标题 | 期刊 | 年份 | DOI | 三句摘要 |\n"
        "| ---: | ---: | --- | --- | --- | --- | --- | --- |"
    )

    for label in section_order:
        part = buckets.get(label) or []
        if not part:
            continue
        lines.append(f"## {label}")
        lines.append("")
        lines.append(header)
        for r in part:
            ai = r.get("ai") if isinstance(r.get("ai"), dict) else {}
            score = ai.get("match_score", "")
            summ = ai.get("summary_zh", "")
            idx = r.get("index_no")
            try:
                idx_cell = str(int(idx)) if idx is not None else ""
            except (TypeError, ValueError):
                idx_cell = md_table_cell(idx)
            lines.append(
                "| "
                + " | ".join(
                    [
                        md_table_cell(idx_cell),
                        md_table_cell(score),
                        md_table_cell(ai.get("relevance_level", label)),
                        md_table_cell(r.get("title")),
                        md_table_cell(r.get("journal_name")),
                        md_table_cell(r.get("publication_year")),
                        md_table_cell(r.get("doi")),
                        md_table_cell(summ),
                    ]
                )
                + " |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(rows)


def _elite_report_records(report_jsonl: Path, *, min_score: int = 95) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in iter_jsonl(report_jsonl):
        if not rec.get("ok"):
            continue
        ai = rec.get("ai")
        if not isinstance(ai, dict):
            continue
        try:
            sc = int(ai.get("match_score", 0))
        except (TypeError, ValueError):
            continue
        if sc >= min_score:
            rows.append(rec)
    rows.sort(
        key=lambda r: (
            -int((r.get("ai") or {}).get("match_score") or 0),
            str(r.get("title") or ""),
        )
    )
    return rows


def _build_works_lookup(works_path: Path) -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    if not works_path.is_file():
        return idx
    for w in iter_jsonl(works_path):
        wid = w.get("id")
        if isinstance(wid, str) and wid.strip():
            idx[wid.strip()] = w
        d = extract_work_doi(w)
        if d:
            idx[f"doi:{d.lower()}"] = w
    return idx


def _lookup_raw_work(idx: dict[str, dict[str, Any]], rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    wid = rec.get("work_id")
    if isinstance(wid, str) and wid.strip() in idx:
        return idx[wid.strip()]
    doi = rec.get("doi")
    if isinstance(doi, str) and doi.strip():
        k = f"doi:{doi.strip().lower()}"
        if k in idx:
            return idx[k]
    return None


def write_deep_dive_tech_report(
    *,
    report_jsonl: Path,
    works_path: Path,
    md_path: Path,
    api_key: str,
    api_base: str,
    model: str,
    research_interest: str,
    delay_sec: float = 0.0,
    elite_min_score: int = 95,
) -> tuple[int, int]:
    """
    For each ``match_score >= elite_min_score`` row in ``report_jsonl``, call DeepSeek again and
    write ``md_path``. Returns ``(success_count, fail_count)``.
    """
    elite = _elite_report_records(report_jsonl, min_score=elite_min_score)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    lookup = _build_works_lookup(works_path)

    lines: list[str] = [
        "# Deep Dive 技术深度报告",
        "",
        f"- 生成时间：{now}",
        f"- 数据源：`{report_jsonl.name}`（match_score ≥ {elite_min_score}）",
        f"- 研究兴趣：{md_table_cell(research_interest)}",
        "",
    ]

    if not elite:
        lines.append("当前无 match_score ≥ 95 的顶尖论文，未执行二次分析。")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0, 0

    base = api_base
    results: list[tuple[dict[str, Any], Optional[dict[str, Any]], Optional[str], list[str]]] = []
    ok, fail = 0, 0
    for rec in elite:
        wid = rec.get("work_id") or rec.get("doi") or "unknown"
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        prior = str(ai0.get("summary_zh", "") or "").strip() or "（无首轮摘要）"
        raw = _lookup_raw_work(lookup, rec)
        if raw is not None:
            paper_block = work_to_prompt_payload(raw)
        else:
            paper_block = (
                f"标题: {md_table_cell(rec.get('title'))}\n"
                f"期刊: {md_table_cell(rec.get('journal_name'))}\n"
                f"年份: {md_table_cell(rec.get('publication_year'))}\n"
                f"DOI: {md_table_cell(rec.get('doi'))}\n"
                "摘要: （未在 works JSONL 中找到对应全文，仅基于首轮摘要深度推断）\n"
                f"首轮摘要:\n{prior}"
            )
        dive: Optional[dict[str, Any]] = None
        err: Optional[str] = None
        try:
            dive = deep_dive_paper(
                api_key=api_key,
                api_base=base,
                model=model,
                research_interest=research_interest,
                prior_summary_zh=prior,
                paper_detail_block=paper_block,
            )
            ok += 1
        except Exception as e:
            err = str(e)
            logger.error("Deep Dive 失败 work=%s: %s", wid, e)
            fail += 1

        doi_val = rec.get("doi")
        figure_paths = try_extract_figures_for_elite_paper(
            str(doi_val).strip() if isinstance(doi_val, str) else None
        )
        wkey = rec.get("work_id")
        if isinstance(wkey, str) and figure_paths:
            merge_ai_extracted_figures(report_jsonl, wkey, figure_paths)

        results.append((rec, dive, err, figure_paths))
        if delay_sec > 0:
            time.sleep(delay_sec)

    lines.append("## 技术指标对比表")
    lines.append("")
    lines.append(
        "| 匹配分 | 标题 | 核心数值指标（一眼对比） |\n"
        "| ---: | --- | --- |"
    )
    for rec, dive, err, _figure_paths in results:
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        sc = ai0.get("match_score", "")
        title = rec.get("title") or rec.get("work_id")
        if dive is not None:
            metrics = dive.get("comparison_metrics_one_line", "")
        else:
            metrics = f"（分析失败：{md_table_cell(err)}）"
        lines.append(
            "| "
            + " | ".join(
                [
                    md_table_cell(sc),
                    md_table_cell(title),
                    md_table_cell(metrics),
                ]
            )
            + " |"
        )
    lines.extend(["", "---", ""])

    for i, (rec, dive, err, figure_paths) in enumerate(results, 1):
        title = rec.get("title") or f"论文 {i}"
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        sc = ai0.get("match_score", "")
        lines.append(f"## {i}. {md_table_cell(title)}")
        lines.append("")
        lines.append(
            f"- **匹配分**：{sc}　**期刊**：{md_table_cell(rec.get('journal_name'))}　"
            f"**年份**：{md_table_cell(rec.get('publication_year'))}　**DOI**：{md_table_cell(rec.get('doi'))}"
        )
        lines.append("")
        if dive is None:
            lines.append(f"*Deep Dive 调用失败：{md_table_cell(err)}*")
            lines.append("")
            _append_figure_markdown(lines, figure_paths)
            continue
        lines.append("### 核心创新点（Innovation）")
        lines.append("")
        for j, pt in enumerate(dive.get("innovation_points") or [], 1):
            lines.append(f"{j}. {md_table_cell(pt)}")
        lines.append("")
        lines.append("### 关键技术指标（Tech Specs）")
        lines.append("")
        specs = dive.get("tech_specs") or []
        if specs:
            lines.append("| 指标 | 数值 | 依据 |\n| --- | --- | --- |")
            for s in specs:
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            md_table_cell(s.get("metric")),
                            md_table_cell(s.get("value")),
                            md_table_cell(s.get("evidence")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("*摘要中未提取到明确量化指标。*")
        lines.append("")
        lines.append("### 技术路线简述")
        lines.append("")
        lines.append(md_table_cell(dive.get("technical_route_zh")))
        lines.append("")
        _append_figure_markdown(lines, figure_paths)

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ok, fail
