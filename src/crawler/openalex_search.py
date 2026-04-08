"""
Resolve a human research interest into an OpenAlex ``search`` string.

OpenAlex fulltext search is English-centric; CJK-only queries often return zero hits.
This module probes result counts and optionally asks DeepSeek for English keywords.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from core.llm_client import normalize_deepseek_api_base, post_deepseek_json_response
from core.models import DEFAULT_DEEPSEEK_API_BASE, DEFAULT_DEEPSEEK_MODEL

from .client import OpenAlexClient

logger = logging.getLogger(__name__)

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef]")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\-/]*")


def text_has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s or ""))


def ascii_keyword_fallback(s: str) -> str:
    """Extract contiguous Latin tokens (e.g. mixed CN/EN input) for a cheap second probe."""
    parts = _LATIN_TOKEN_RE.findall(s or "")
    return " ".join(parts) if parts else ""


def openalex_search_result_count(search: str, *, client: OpenAlexClient | None = None) -> int:
    if not (search or "").strip():
        return 0
    c = client or OpenAlexClient()
    data = c.get_json("/works", params={"search": search.strip(), "per_page": 1})
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    raw = meta.get("count")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _llm_english_openalex_query(
    interest: str,
    *,
    api_key: str,
    api_base: str,
    model: str,
) -> str:
    base = normalize_deepseek_api_base(api_base)
    system = (
        "You help researchers query OpenAlex. The search API works best with "
        "concise English technical keywords (not full sentences). "
        "Reply as JSON only."
    )
    user = (
        f'Research topic (may be Chinese or other languages): "{interest}"\n'
        "Return JSON: {\"openalex_search\": \"<6-14 English keywords, space-separated>\"}"
    )
    data = post_deepseek_json_response(
        api_key=api_key,
        api_base=base,
        model=model,
        system=system,
        user=user,
        temperature=0.2,
    )
    q = data.get("openalex_search")
    if not isinstance(q, str) or not q.strip():
        raise ValueError("model JSON missing non-empty openalex_search")
    return " ".join(q.split())


@dataclass(frozen=True)
class ResolvedOpenAlexSearch:
    query: str
    strategy: str
    count_before: int
    count_after: int


def resolve_openalex_search_for_interest(
    interest: str,
    *,
    api_key: str | None,
    api_base: str = DEFAULT_DEEPSEEK_API_BASE,
    model: str = DEFAULT_DEEPSEEK_MODEL,
) -> ResolvedOpenAlexSearch:
    """
    Pick an OpenAlex ``search`` string for ``interest``.

    Raises ``ValueError`` if no usable query could be formed.
    """
    q0 = " ".join((interest or "").split())
    if not q0:
        raise ValueError("研究兴趣为空，无法构造 OpenAlex 检索式")

    client = OpenAlexClient()
    c0 = openalex_search_result_count(q0, client=client)
    if c0 > 0:
        logger.info("OpenAlex search: direct hit count=%s query=%r", c0, q0[:200])
        return ResolvedOpenAlexSearch(query=q0, strategy="direct", count_before=c0, count_after=c0)

    llm_query: str | None = None
    key = (api_key or "").strip()
    if key:
        try:
            llm_query = _llm_english_openalex_query(
                q0, api_key=key, api_base=api_base, model=model
            )
        except Exception as e:
            logger.warning("OpenAlex search: LLM keyword expansion failed: %s", e)
        if llm_query:
            c1 = openalex_search_result_count(llm_query, client=client)
            if c1 > 0:
                logger.info(
                    "OpenAlex search: LLM expansion count=%s query=%r",
                    c1,
                    llm_query[:200],
                )
                return ResolvedOpenAlexSearch(
                    query=llm_query, strategy="llm", count_before=c0, count_after=c1
                )
            logger.warning(
                "OpenAlex search: LLM query still count=0 (will try best-effort): %r",
                llm_query[:200],
            )

    q_ascii = ascii_keyword_fallback(q0)
    if q_ascii and q_ascii.lower() != q0.lower():
        c2 = openalex_search_result_count(q_ascii, client=client)
        if c2 > 0:
            logger.info(
                "OpenAlex search: Latin-token fallback count=%s query=%r",
                c2,
                q_ascii[:200],
            )
            return ResolvedOpenAlexSearch(
                query=q_ascii, strategy="ascii_tokens", count_before=c0, count_after=c2
            )

    if llm_query:
        c1b = openalex_search_result_count(llm_query, client=client)
        return ResolvedOpenAlexSearch(
            query=llm_query, strategy="llm_best_effort", count_before=c0, count_after=c1b
        )

    if q_ascii:
        c2b = openalex_search_result_count(q_ascii, client=client)
        return ResolvedOpenAlexSearch(
            query=q_ascii, strategy="ascii_best_effort", count_before=c0, count_after=c2b
        )

    hint = (
        "OpenAlex 对当前检索无结果。"
        + (
            " 已检测到非拉丁字符：请在兴趣中补充英文关键词，或确保超算环境已配置 DEEPSEEK_API_KEY 以自动扩展检索式。"
            if text_has_cjk(q0)
            else " 请尝试更短、更标准的英文关键词。"
        )
    )
    raise ValueError(hint)
