#!/usr/bin/env python3
"""
CLI entry when run as ``python src/main.py``（与仓库根目录 ``main.py`` 等价）。

``analyze`` 支持 ``--interest``（研究课题起始关键词）与 ``--email``（覆盖礼貌池邮箱 /
``config.POLITE_POOL_EMAIL``，并写入 ``OPENALEX_MAILTO``）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
