"""Second-pass structured technical deep dive via DeepSeek."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from core.llm_client import normalize_deepseek_api_base, post_deepseek_json_response

logger = logging.getLogger(__name__)


def _fallback_metrics_one_line(specs: list[dict[str, str]]) -> str:
    parts = [f"{s['metric']}:{s['value']}" for s in specs[:8] if s.get("metric") or s.get("value")]
    return "; ".join(parts) if parts else "无明显量化指标"


def normalize_deep_dive_json(parsed: dict[str, Any]) -> dict[str, Any]:
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


def deep_dive_paper(
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
    base = normalize_deepseek_api_base(api_base)
    parsed = post_deepseek_json_response(
        api_key=api_key,
        api_base=base,
        model=model,
        system=system,
        user=user_msg,
        temperature=0.25,
    )
    return normalize_deep_dive_json(parsed)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_elite_pdf_path(doi: Optional[str]) -> Optional[Path]:
    """
    ``data/pdfs/{sanitize_doi}.pdf`` under the repository root, if the file exists.
    """
    if not doi or not str(doi).strip():
        return None
    try:
        from utils.pdf_visuals import sanitize_doi_for_filename
    except ImportError:
        return None
    stem = sanitize_doi_for_filename(str(doi).strip())
    pdf = _repo_root() / "data" / "pdfs" / f"{stem}.pdf"
    return pdf if pdf.is_file() else None


def try_extract_figures_for_elite_paper(doi: Optional[str]) -> list[str]:
    """
    If a PDF exists under ``data/pdfs/`` for this DOI, run heuristic figure extraction.

    Returns paths relative to repository root (posix), suitable for JSON / Next.js.
    """
    pdf_path = resolve_elite_pdf_path(doi)
    if pdf_path is None:
        return []
    try:
        from utils.pdf_visuals import extract_heuristic_figures
    except ImportError as e:
        logger.warning("figure extraction skipped (pymupdf): %s", e)
        return []

    root = _repo_root()
    out_dir = root / "data" / "figures"
    try:
        written = extract_heuristic_figures(
            pdf_path,
            str(doi).strip(),
            out_dir=out_dir,
        )
    except Exception as e:
        logger.warning("extract_heuristic_figures failed for %s: %s", pdf_path, e)
        return []

    rel: list[str] = []
    for p in written:
        try:
            rel.append(p.resolve().relative_to(root.resolve()).as_posix())
        except ValueError:
            rel.append(Path(p).as_posix())
    if rel:
        logger.info("extracted %s figure(s) for DOI-derived file %s", len(rel), pdf_path.name)
    return rel
