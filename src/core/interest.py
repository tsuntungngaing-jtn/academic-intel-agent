"""Resolve user research interest from CLI, environment, or stdin."""

from __future__ import annotations

import os
import sys
from typing import Optional


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
