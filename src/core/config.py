"""Project paths and defaults derived from the environment."""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    """Repository root (parent of ``src``)."""
    return Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    raw = os.getenv("DATA_DIR", "").strip()
    if raw:
        return Path(raw)
    return project_root() / "data"
