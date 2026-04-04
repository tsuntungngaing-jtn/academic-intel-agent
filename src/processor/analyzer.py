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
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"
MAX_ABSTRACT_CHARS = 8000
REQUEST_TIMEOUT = (15, 120)


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
    doi = work.get("doi")
    inv = work.get("abstract_inverted_index")
    abstract = ""
    if isinstance(inv, dict):
        abstract = reconstruct_abstract(inv)  # type: ignore[arg-type]
    if len(abstract) > MAX_ABSTRACT_CHARS:
        abstract = abstract[: MAX_ABSTRACT_CHARS - 3] + "..."
    lines = [
        f"标题: {title}",
        f"年份: {year}" if year is not None else "年份: 未知",
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
        "match_score（整数 0-100，表示与用户研究需求的匹配程度）。"
        "三句需连贯、信息密度高；match_score 需与 summary 一致。"
    )
    user_msg = (
        f"【用户研究需求/兴趣】\n{research_interest}\n\n"
        f"【论文信息】\n{paper_block}\n\n"
        '请只输出一个 JSON 对象，例如：{"summary_zh":"……","match_score":72}'
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
    return {"summary_zh": summary.strip(), "match_score": score}


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
    Returns (success_count, failure_count).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    base = api_base.rstrip("/")
    if "deepseek.com" in base and not base.rstrip("/").endswith("/v1"):
        base = base.rstrip("/") + "/v1"

    out_mode = "a" if append_out else "w"
    with out_path.open(out_mode, encoding="utf-8") as out:
        for work in iter_jsonl(works_path):
            wid = work.get("id") or work.get("doi") or "unknown"
            record: dict[str, Any] = {
                "work_id": wid,
                "title": work.get("title") or work.get("display_name"),
                "publication_year": work.get("publication_year"),
                "doi": work.get("doi"),
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
            if delay_sec > 0:
                time.sleep(delay_sec)
    return ok, fail


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Optional[list[str]] = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
