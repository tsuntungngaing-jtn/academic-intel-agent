"""
OpenAlex API client with retries, backoff, timeouts, and cursor pagination.

Polite pool: set OPENALEX_MAILTO in the environment (see https://docs.openalex.org/).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterator, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.openalex.org"
DEFAULT_TIMEOUT = (10, 60)  # connect, read
MAX_PER_PAGE = 200


def build_recent_publication_filter(days: int = 90) -> str:
    """OpenAlex ``from_publication_date`` / ``to_publication_date`` filter (inclusive)."""
    if days < 1:
        days = 1
    end = date.today()
    start = end - timedelta(days=days)
    return f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()}"


class OpenAlexError(Exception):
    """Raised when the API returns an error or an unexpected payload."""


def _env_mailto() -> Optional[str]:
    v = os.getenv("OPENALEX_MAILTO", "").strip()
    return v or None


@dataclass
class OpenAlexClient:
    """HTTP client for OpenAlex with connection pooling and resilient GETs."""

    base_url: str = DEFAULT_BASE_URL
    timeout: tuple[float, float] = DEFAULT_TIMEOUT
    mailto: Optional[str] = field(default_factory=_env_mailto)
    session: requests.Session = field(default_factory=requests.Session, repr=False)
    max_retries: int = 5
    backoff_factor: float = 1.0
    status_forcelist: tuple[int, ...] = (429, 500, 502, 503, 504)

    def __post_init__(self) -> None:
        retry = Retry(
            total=self.max_retries,
            connect=self.max_retries,
            read=self.max_retries,
            backoff_factor=self.backoff_factor,
            status_forcelist=list(self.status_forcelist),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self._user_agent(),
            }
        )

    def _user_agent(self) -> str:
        base = "academic-intel-agent/0.1 (OpenAlex crawler; +https://github.com/openalex)"
        if self.mailto:
            return f"{base} mailto:{self.mailto}"
        logger.warning(
            "OPENALEX_MAILTO not set; use polite pool for better rate limits "
            "(https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication)"
        )
        return base

    def _params(self, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.mailto:
            out["mailto"] = self.mailto
        if extra:
            for k, v in extra.items():
                if v is None:
                    continue
                out[k] = v
        return out

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        """GET JSON object; raises OpenAlexError on failure or invalid JSON."""
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        merged = self._params(dict(params) if params else {})
        try:
            resp = self.session.get(url, params=merged, timeout=self.timeout)
        except requests.RequestException as e:
            raise OpenAlexError(f"request failed: {url}") from e

        if resp.status_code >= 400:
            body = (resp.text or "")[:500]
            raise OpenAlexError(f"HTTP {resp.status_code} for {url}: {body}")

        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise OpenAlexError(f"invalid JSON from {url}") from e

        if not isinstance(data, dict):
            raise OpenAlexError(f"expected JSON object from {url}, got {type(data).__name__}")
        return data

    def iter_work_pages(
        self,
        *,
        filter_expr: Optional[str] = None,
        search: Optional[str] = None,
        per_page: int = 25,
        cursor: Optional[str] = None,
        extra_params: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[tuple[list[dict[str, Any]], dict[str, Any]]]:
        """Yield ``(results, meta)`` for each /works page."""
        page_size = max(1, min(per_page, MAX_PER_PAGE))
        params: dict[str, Any] = {"per_page": page_size}
        if filter_expr:
            params["filter"] = filter_expr
        if search:
            params["search"] = search
        if extra_params:
            params.update(dict(extra_params))

        next_cursor: Optional[str] = cursor
        while True:
            if next_cursor:
                params["cursor"] = next_cursor
            elif "cursor" in params:
                del params["cursor"]

            payload = self.get_json("/works", params)
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            raw = payload.get("results")
            if not isinstance(raw, list):
                raise OpenAlexError("works response missing 'results' list")

            results = [x for x in raw if isinstance(x, dict)]
            yield results, meta

            next_cursor = meta.get("next_cursor")
            if not next_cursor:
                break

    def iter_works(
        self,
        *,
        filter_expr: Optional[str] = None,
        search: Optional[str] = None,
        per_page: int = 25,
        cursor: Optional[str] = None,
        extra_params: Optional[Mapping[str, Any]] = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield work records from /works with cursor pagination."""
        for page, _meta in self.iter_work_pages(
            filter_expr=filter_expr,
            search=search,
            per_page=per_page,
            cursor=cursor,
            extra_params=extra_params,
        ):
            yield from page


def normalize_openalex_work_id(work_id: str) -> str:
    """Accept OpenAlex URL or ``W123``-style id; return short id for API paths."""
    s = (work_id or "").strip()
    if not s:
        raise ValueError("work_id is empty")
    if "openalex.org/" in s:
        tail = s.rstrip("/").split("/")[-1]
        if tail:
            return tail
    return s


def get_references(
    work_id: str,
    *,
    limit: int = 5,
    client: Optional[OpenAlexClient] = None,
) -> list[dict[str, Any]]:
    """
    Return up to ``limit`` referenced works for the given work, in ``referenced_works`` order.

    Each entry is a full OpenAlex work object from ``GET /works/{id}``.
    """
    c = client or OpenAlexClient()
    wid = normalize_openalex_work_id(work_id)
    data = c.get_json(f"/works/{wid}")
    raw_refs = data.get("referenced_works")
    if not isinstance(raw_refs, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in raw_refs:
        if len(out) >= limit:
            break
        if not isinstance(entry, str) or not entry.strip():
            continue
        rid = normalize_openalex_work_id(entry)
        try:
            w = c.get_json(f"/works/{rid}")
        except OpenAlexError:
            continue
        out.append(w)
    return out


def get_cited_by(
    work_id: str,
    *,
    limit: int = 5,
    client: Optional[OpenAlexClient] = None,
) -> list[dict[str, Any]]:
    """
    Return up to ``limit`` works that cite the given work (OpenAlex ``cites`` filter).
    """
    c = client or OpenAlexClient()
    wid = normalize_openalex_work_id(work_id)
    page = max(1, min(limit, MAX_PER_PAGE))
    payload = c.get_json("/works", params={"filter": f"cites:{wid}", "per_page": page})
    raw = payload.get("results")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)][:limit]
