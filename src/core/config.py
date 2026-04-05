"""Project paths and defaults derived from the environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# OpenAlex polite-pool email (mirrors OPENALEX_MAILTO after sync / CLI override).
POLITE_POOL_EMAIL: str = ""


def sync_polite_pool_email_from_environment() -> None:
    """Refresh ``POLITE_POOL_EMAIL`` from ``OPENALEX_MAILTO`` (e.g. after ``load_dotenv``)."""
    global POLITE_POOL_EMAIL
    POLITE_POOL_EMAIL = (os.getenv("OPENALEX_MAILTO") or "").strip()


def apply_cli_email_override(email: Optional[str]) -> None:
    """
    If ``email`` is non-empty, set ``POLITE_POOL_EMAIL`` and ``OPENALEX_MAILTO``.
    Otherwise re-sync from the environment (``.env`` / shell).
    """
    global POLITE_POOL_EMAIL
    if email is None:
        sync_polite_pool_email_from_environment()
        return
    mail = str(email).strip()
    if mail:
        POLITE_POOL_EMAIL = mail
        os.environ["OPENALEX_MAILTO"] = mail
    else:
        sync_polite_pool_email_from_environment()


def project_root() -> Path:
    """Repository root (parent of ``src``)."""
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return project_root() / "data"
