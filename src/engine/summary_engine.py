"""First-pass relevance scoring and Chinese summary via DeepSeek."""

from __future__ import annotations

from typing import Any

from core.llm_client import normalize_deepseek_api_base, post_deepseek_json_response
from core.models import FULL_RELEVANCE, PARTIAL_RELEVANCE


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


def summarize_work_for_interest(
    *,
    api_key: str,
    api_base: str,
    model: str,
    research_interest: str,
    paper_block: str,
) -> dict[str, Any]:
    """
    Return ``summary_zh``, ``match_score``, ``relevance_level`` for one paper block.
    """
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
    base = normalize_deepseek_api_base(api_base)
    parsed = post_deepseek_json_response(
        api_key=api_key,
        api_base=base,
        model=model,
        system=system,
        user=user_msg,
        temperature=0.3,
    )
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
