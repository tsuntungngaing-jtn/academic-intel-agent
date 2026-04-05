"""
Read OpenAlex works from JSONL, score and summarize vs user research interest via DeepSeek.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MAX_ABSTRACT_CHARS = 8000
REQUEST_TIMEOUT = (15, 120)


FULL_RELEVANCE = "完全相关"
PARTIAL_RELEVANCE = "部分相关"


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


def _project_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


def _read_interest_from_env() -> Optional[str]:
    for key in ("RESEARCH_INTEREST", "USER_RESEARCH_NEED"):
        v = os.getenv(key, "").strip()
        if v:
            return v
    return None


def resolve_research_interest(explicit: Optional[str] = None) -> str:
    """CLI arg > env (RESEARCH_INTEREST / USER_RESEARCH_NEED) > stdin prompt."""
    if explicit and explicit.strip():
        return explicit.strip()
    env_val = _read_interest_from_env()
    if env_val:
        return env_val
    if not sys.stdin.isatty():
        raise SystemExit(
            "未设置研究需求：请设置环境变量 RESEARCH_INTEREST，或使用 --interest 传入（非交互环境无法提示输入）。"
        )
    try:
        line = input("请输入您的研究需求/兴趣（回车结束）: ").strip()
    except EOFError:
        line = ""
    if not line:
        raise SystemExit("研究需求为空，已退出。")
    return line


def _work_to_prompt_payload(work: dict[str, Any]) -> str:
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


def _parse_model_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def _normalize_score(raw: Any) -> int:
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def _normalize_relevance_level(raw: Any) -> str:
    if not isinstance(raw, str):
        return PARTIAL_RELEVANCE
    s = raw.strip()
    if s == FULL_RELEVANCE or ("完全" in s and "相关" in s):
        return FULL_RELEVANCE
    if s == PARTIAL_RELEVANCE or ("部分" in s and "相关" in s):
        return PARTIAL_RELEVANCE
    return PARTIAL_RELEVANCE


def chat_completions_url(api_base: str) -> str:
    b = api_base.strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def call_deepseek_analyze(
    *,
    api_key: str,
    api_base: str,
    model: str,
    research_interest: str,
    paper_block: str,
) -> dict[str, Any]:
    system = (
        "你是学术文献匹配助手。根据用户的研究兴趣，阅读单篇论文信息，输出严格 JSON（不要 Markdown），"
        "字段：summary_zh（字符串，恰好三句中文，概括论文精华与和用户兴趣的关系）、"
        "match_score（整数 0-100，表示与用户研究需求的匹配程度）、"
        "relevance_level（字符串，只能为「完全相关」或「部分相关」）。"
        "「完全相关」：论文核心技术/方法直接针对用户需求；"
        "「部分相关」：方法论可借鉴、或处于相关产业链/上下游、或仅间接相关。"
        "三句需连贯、信息密度高；match_score 与 relevance_level、summary 须一致。"
    )
    user_msg = (
        f"【用户研究需求/兴趣】\n{research_interest}\n\n"
        f"【论文信息】\n{paper_block}\n\n"
        '请只输出一个 JSON 对象，例如：'
        '{"summary_zh":"……","match_score":72,"relevance_level":"部分相关"}'
    )
    url = chat_completions_url(api_base)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    outer = resp.json()
    choices = outer.get("choices")
    if not choices or not isinstance(choices, list):
        raise RuntimeError("DeepSeek response missing choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek empty content")
    parsed = _parse_model_json(content)
    summary = parsed.get("summary_zh")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("missing summary_zh")
    score = _normalize_score(parsed.get("match_score"))
    rel = _normalize_relevance_level(parsed.get("relevance_level"))
    return {
        "summary_zh": summary.strip(),
        "match_score": score,
        "relevance_level": rel,
    }


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


def _next_index_no_for_report(out_path: Path, *, append_out: bool) -> int:
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


def analyze_works_file(
    *,
    research_interest: str,
    works_path: Path,
    out_path: Path,
    api_key: str,
    api_base: str = DEFAULT_API_BASE,
    model: str = DEFAULT_MODEL,
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
    base = api_base.rstrip("/")
    if "deepseek.com" in base and not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"

    index_no = _next_index_no_for_report(out_path, append_out=append_out)
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
                block = _work_to_prompt_payload(work)
                ai = call_deepseek_analyze(
                    api_key=api_key,
                    api_base=base,
                    model=model,
                    research_interest=research_interest,
                    paper_block=block,
                )
                record["ai"] = ai
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


def _md_table_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|")
    s = " ".join(line.strip() for line in s.split("\n") if line.strip())
    return s.strip()


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
        lines.append(f"- 研究兴趣：{_md_table_cell(research_interest)}")
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
                idx_cell = _md_table_cell(idx)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_table_cell(idx_cell),
                        _md_table_cell(score),
                        _md_table_cell(ai.get("relevance_level", label)),
                        _md_table_cell(r.get("title")),
                        _md_table_cell(r.get("journal_name")),
                        _md_table_cell(r.get("publication_year")),
                        _md_table_cell(r.get("doi")),
                        _md_table_cell(summ),
                    ]
                )
                + " |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return len(rows)


def _elite_report_records(report_jsonl: Path, *, min_score: int = 95) -> list[dict[str, Any]]:
    """Entries with successful analysis and ``match_score >= min_score``."""
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
    """Map OpenAlex ``id`` and ``doi:...`` keys to raw work objects."""
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


def _fallback_metrics_one_line(specs: list[dict[str, str]]) -> str:
    parts = [f"{s['metric']}:{s['value']}" for s in specs[:8] if s.get("metric") or s.get("value")]
    return "; ".join(parts) if parts else "无明显量化指标"


def _normalize_deep_dive_json(parsed: dict[str, Any]) -> dict[str, Any]:
    raw_pts = parsed.get("innovation_points")
    pts: list[str] = []
    if isinstance(raw_pts, list):
        pts = [str(p).strip() for p in raw_pts if str(p).strip()]
    elif isinstance(raw_pts, str) and raw_pts.strip():
        pts = [p.strip() for p in re.split(r"[\n；;]|(?<=[。!?])\s+", raw_pts) if p.strip()]
    while len(pts) < 3:
        pts.append("（未提供）")
    pts = pts[:3]

    specs_out: list[dict[str, str]] = []
    specs = parsed.get("tech_specs")
    if isinstance(specs, list):
        for it in specs:
            if not isinstance(it, dict):
                continue
            specs_out.append(
                {
                    "metric": str(it.get("metric", "")).strip() or "指标",
                    "value": str(it.get("value", "")).strip() or "—",
                    "evidence": str(it.get("evidence", "")).strip() or "—",
                }
            )

    route = parsed.get("technical_route_zh")
    if not isinstance(route, str) or not route.strip():
        route = "（未提供）"
    else:
        route = route.strip()

    one_line = parsed.get("comparison_metrics_one_line")
    if not isinstance(one_line, str) or not one_line.strip():
        one_line = _fallback_metrics_one_line(specs_out)
    else:
        one_line = one_line.strip()

    return {
        "innovation_points": pts,
        "tech_specs": specs_out,
        "technical_route_zh": route,
        "comparison_metrics_one_line": one_line,
    }


def call_deepseek_deep_dive(
    *,
    api_key: str,
    api_base: str,
    model: str,
    research_interest: str,
    prior_summary_zh: str,
    paper_detail_block: str,
) -> dict[str, Any]:
    system = (
        "你是资深技术情报分析师。基于用户研究兴趣与论文材料，做「Deep Dive」深度解构。"
        "只输出一个 JSON 对象（不要 Markdown），字段：\n"
        "innovation_points：字符串数组，恰好 3 条；每条用中文说明一个核心创新点，并点出对应产业/工程痛点与解决思路；\n"
        "tech_specs：对象数组，从摘要等材料中提取可核对的具体数值指标（如温度降幅%、能效提升率、响应时间、功率密度等），"
        "每项含 metric（指标名）、value（数值+单位或范围）、evidence（一句原文或摘要依据；无法定位则写「据摘要推断」）；"
        "若无明确数值，数组可为空；\n"
        "technical_route_zh：1–3 句中文，概括物理机制或技术路线（例如主动液冷与相变材料耦合、热电协同路径等）；\n"
        "comparison_metrics_one_line：单行字符串，用英文分号分隔 3–6 个「指标:数值」，用于横向对比不同论文，务必简短。"
    )
    user_msg = (
        f"【用户研究兴趣】\n{research_interest}\n\n"
        f"【首轮 AI 摘要（供对齐语境）】\n{prior_summary_zh}\n\n"
        f"【论文材料】\n{paper_detail_block}\n\n"
        "请严格输出 JSON，键名必须与上述一致。"
    )
    url = chat_completions_url(api_base)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.25,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {(resp.text or '')[:500]}")
    outer = resp.json()
    choices = outer.get("choices")
    if not choices or not isinstance(choices, list):
        raise RuntimeError("DeepSeek response missing choices")
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DeepSeek empty content")
    parsed = _parse_model_json(content)
    return _normalize_deep_dive_json(parsed)


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
        f"- 研究兴趣：{_md_table_cell(research_interest)}",
        "",
    ]

    if not elite:
        lines.append("当前无 match_score ≥ 95 的顶尖论文，未执行二次分析。")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0, 0

    base = api_base.rstrip("/")
    if "deepseek.com" in base and not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"

    results: list[tuple[dict[str, Any], Optional[dict[str, Any]], Optional[str]]] = []
    ok, fail = 0, 0
    for rec in elite:
        wid = rec.get("work_id") or rec.get("doi") or "unknown"
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        prior = str(ai0.get("summary_zh", "") or "").strip() or "（无首轮摘要）"
        raw = _lookup_raw_work(lookup, rec)
        if raw is not None:
            paper_block = _work_to_prompt_payload(raw)
        else:
            paper_block = (
                f"标题: {_md_table_cell(rec.get('title'))}\n"
                f"期刊: {_md_table_cell(rec.get('journal_name'))}\n"
                f"年份: {_md_table_cell(rec.get('publication_year'))}\n"
                f"DOI: {_md_table_cell(rec.get('doi'))}\n"
                "摘要: （未在 works JSONL 中找到对应全文，仅基于首轮摘要深度推断）\n"
                f"首轮摘要:\n{prior}"
            )
        dive: Optional[dict[str, Any]] = None
        err: Optional[str] = None
        try:
            dive = call_deepseek_deep_dive(
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
        results.append((rec, dive, err))
        if delay_sec > 0:
            time.sleep(delay_sec)

    lines.append("## 技术指标对比表")
    lines.append("")
    lines.append(
        "| 匹配分 | 标题 | 核心数值指标（一眼对比） |\n"
        "| ---: | --- | --- |"
    )
    for rec, dive, err in results:
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        sc = ai0.get("match_score", "")
        title = rec.get("title") or rec.get("work_id")
        if dive is not None:
            metrics = dive.get("comparison_metrics_one_line", "")
        else:
            metrics = f"（分析失败：{_md_table_cell(err)}）"
        lines.append(
            "| "
            + " | ".join(
                [
                    _md_table_cell(sc),
                    _md_table_cell(title),
                    _md_table_cell(metrics),
                ]
            )
            + " |"
        )
    lines.extend(["", "---", ""])

    for i, (rec, dive, err) in enumerate(results, 1):
        title = rec.get("title") or f"论文 {i}"
        ai0 = rec.get("ai") if isinstance(rec.get("ai"), dict) else {}
        sc = ai0.get("match_score", "")
        lines.append(f"## {i}. {_md_table_cell(title)}")
        lines.append("")
        lines.append(
            f"- **匹配分**：{sc}　**期刊**：{_md_table_cell(rec.get('journal_name'))}　"
            f"**年份**：{_md_table_cell(rec.get('publication_year'))}　**DOI**：{_md_table_cell(rec.get('doi'))}"
        )
        lines.append("")
        if dive is None:
            lines.append(f"*Deep Dive 调用失败：{_md_table_cell(err)}*")
            lines.append("")
            continue
        lines.append("### 核心创新点（Innovation）")
        lines.append("")
        for j, pt in enumerate(dive.get("innovation_points") or [], 1):
            lines.append(f"{j}. {_md_table_cell(pt)}")
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
                            _md_table_cell(s.get("metric")),
                            _md_table_cell(s.get("value")),
                            _md_table_cell(s.get("evidence")),
                        ]
                    )
                    + " |"
                )
        else:
            lines.append("*摘要中未提取到明确量化指标。*")
        lines.append("")
        lines.append("### 技术路线简述")
        lines.append("")
        lines.append(_md_table_cell(dive.get("technical_route_zh")))
        lines.append("")

    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ok, fail


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _ensure_utf8_stdio() -> None:
    """Avoid mojibake / Latin-1 issues when printing Chinese in Windows consoles."""
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv: Optional[list[str]] = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    _ensure_utf8_stdio()
    _configure_logging()
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
        default=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
    )
    parser.add_argument(
        "--api-base",
        type=str,
        default=os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
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

    data_dir = _project_data_dir()
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


if __name__ == "__main__":
    raise SystemExit(main())
