"""Run analysis pipeline (legacy entry; prefer ``python main.py analyze``)."""

import sys

from engine.pipeline import run_analyze_cli


def _argv_without_runpy_prefix(argv: list[str]) -> list[str]:
    """Strip ``-m processor`` / ``-m pkg.processor`` style prefix when present."""
    if len(argv) >= 2 and argv[0] == "-m":
        return argv[2:]
    return argv


if __name__ == "__main__":
    raise SystemExit(run_analyze_cli(_argv_without_runpy_prefix(sys.argv[1:])))
