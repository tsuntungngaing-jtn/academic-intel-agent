"""
HTTP client for DeepSeek chat completions (OpenAI-compatible).

This module is the only place outside ``crawler`` that uses ``requests`` for network I/O.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

REQUEST_TIMEOUT = (15, 120)


def chat_completions_url(api_base: str) -> str:
    b = api_base.strip().rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    return f"{b}/chat/completions"


def normalize_deepseek_api_base(api_base: str) -> str:
    base = api_base.rstrip("/")
    if "deepseek.com" in base and not base.rstrip("/").endswith("/v1"):
        return base.rstrip("/") + "/v1"
    return base


def parse_model_json_content(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return data


def post_deepseek_json_response(
    *,
    api_key: str,
    api_base: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    timeout: tuple[float, float] = REQUEST_TIMEOUT,
) -> dict[str, Any]:
    """
    Call chat completions with ``response_format: json_object`` and return the parsed inner JSON dict.
    """
    url = chat_completions_url(api_base)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
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
    return parse_model_json_content(content)
