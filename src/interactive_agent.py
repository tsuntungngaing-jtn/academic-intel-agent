"""Deprecated: use ``python main.py interactive`` or ``ui.interactive_terminal``."""

from __future__ import annotations

from ui.interactive_terminal import main

if __name__ == "__main__":
    raise SystemExit(main())
