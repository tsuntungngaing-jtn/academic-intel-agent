"""Persist OpenAlex cursor between crawl runs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def state_path_for(out: Path) -> Path:
    return out.with_name(out.name + ".openalex_state.json")


def load_state(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("ignore corrupt state file %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def write_state(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
