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
from pathlib import Path
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


def _state_path_for(out: Path) -> Path:
    return out.with_name(out.name + ".openalex_state.json")


def _load_state(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ignore corrupt state file %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_works_to_file(
    path: Path,
    *,
    filter_expr: Optional[str] = None,
    search: Optional[str] = None,
    max_records: Optional[int] = None,
    resume: bool = True,
) -> int:
    """
    Append works as JSON lines to ``path``.

    With ``resume``, continues from ``path.openalex_state.json`` when filter/search
    match (cursor-based). State is updated after each full page. If a run stops mid-page,
    re-running may duplicate up to one page of rows.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    state_path = _state_path_for(path)
    client = OpenAlexClient()

    start_cursor: Optional[str] = None
    mode = "a"
    if not resume:
        if state_path.exists():
            state_path.unlink(missing_ok=True)
        mode = "w"
    else:
        st = _load_state(state_path)
        if st and st.get("filter") == filter_expr and st.get("search") == search:
            start_cursor = st.get("next_cursor")
            if isinstance(start_cursor, str) and start_cursor:
                logger.info("resuming from saved cursor for %s", path.name)
            else:
                start_cursor = None
        elif st:
            logger.info("state query mismatch; starting new crawl")

    written = 0
    with path.open(mode, encoding="utf-8") as out:
        for results, meta in client.iter_work_pages(
            filter_expr=filter_expr,
            search=search,
            per_page=MAX_PER_PAGE,
            cursor=start_cursor,
        ):
            for work in results:
                out.write(json.dumps(work, ensure_ascii=False) + "\n")
                written += 1
                if max_records is not None and written >= max_records:
                    out.flush()
                    state_path.unlink(missing_ok=True)
                    logger.warning(
                        "max_records reached mid-page; checkpoint cleared so the next run "
                        "does not skip the rest of this page"
                    )
                    return written

            out.flush()
            next_cursor = meta.get("next_cursor")
            if isinstance(next_cursor, str) and next_cursor:
                _write_state(
                    state_path,
                    {
                        "filter": filter_expr,
                        "search": search,
                        "next_cursor": next_cursor,
                    },
                )
            else:
                state_path.unlink(missing_ok=True)

    return written


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """
    Fetch works: default to the last ``OPENALEX_RECENT_DAYS`` (90) days via
    ``from_publication_date`` / ``to_publication_date``. ``OPENALEX_SEARCH`` is required
    so results are keyword-bound at the API layer.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    _configure_logging()
    data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
    out_file = data_dir / "works_sample.jsonl"

    custom_filter = os.getenv("OPENALEX_FILTER", "").strip()
    recent_days_raw = os.getenv("OPENALEX_RECENT_DAYS", "90").strip()
    recent_days = int(recent_days_raw) if recent_days_raw.isdigit() else 90
    filter_expr = custom_filter or build_recent_publication_filter(recent_days)

    search = os.getenv("OPENALEX_SEARCH", "").strip() or None
    if not search:
        logger.error(
            "未设置 OPENALEX_SEARCH。抓取必须与 search 关键词绑定；请在 .env 中设置 OPENALEX_SEARCH。"
        )
        raise SystemExit(1)

    max_n = os.getenv("OPENALEX_MAX_RECORDS")
    max_records = int(max_n) if max_n and max_n.isdigit() else 50
    resume = os.getenv("OPENALEX_RESUME", "0").strip().lower() not in ("0", "false", "no")

    logger.info(
        "crawl filter=%r search=%r (override filter with OPENALEX_FILTER if needed)",
        filter_expr,
        search,
    )

    n = fetch_works_to_file(
        out_file,
        filter_expr=filter_expr,
        search=search,
        max_records=max_records,
        resume=resume,
    )
    logger.info("wrote %s works to %s", n, out_file)


if __name__ == "__main__":
    main()
