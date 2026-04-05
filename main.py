#!/usr/bin/env python3
"""
Unified CLI entry for Academic Intel Agent.

Usage::

    python main.py crawl
    python main.py analyze [--interest ...]
    python main.py interactive [--report path]
    python main.py figures <pdf> <doi> [--out-dir path]
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
