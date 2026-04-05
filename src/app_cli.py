"""
Unified argparse CLI (used by repo root ``main.py`` and ``src/main.py``).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="academic-intel-agent",
        description="Academic Intel Agent — OpenAlex 抓取、DeepSeek 分析、交互浏览、PDF 插图提取。",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("crawl", help="从 OpenAlex 抓取论文写入 data/works_sample.jsonl（读环境变量）")

    pa = sub.add_parser("analyze", help="用 DeepSeek 分析 works JSONL，生成 final_report 与 Markdown")
    pa.add_argument("--interest", type=str, default=None)
    pa.add_argument(
        "--email",
        type=str,
        default=None,
        help="OpenAlex 礼貌池邮箱（设置 OPENALEX_MAILTO，可选）",
    )
    pa.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=("recent", "related"),
        help="recent=追踪前沿；related=深度探索（由 API / Slurm 传入）",
    )
    pa.add_argument("--works", type=Path, default=None)
    pa.add_argument("--out", type=Path, default=None)
    pa.add_argument("--model", type=str, default=None)
    pa.add_argument("--api-base", type=str, default=None)
    pa.add_argument("--delay", type=float, default=None)
    pa.add_argument("--append", action="store_true")

    pi = sub.add_parser("interactive", help="按编号浏览 final_report.jsonl，可选 --cite 查引用")
    pi.add_argument("--report", type=Path, default=None)

    pf = sub.add_parser("figures", help="从 PDF 启发式提取插图到 data/figures/")
    pf.add_argument("pdf", type=Path, help="PDF 文件路径")
    pf.add_argument("doi", type=str, help="对应 DOI（用于命名与元数据）")
    pf.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="输出目录（默认 data/figures）",
    )

    return p


def run_figures(pdf: Path, doi: str, out_dir: Path | None = None) -> int:
    from core.env import load_environment
    from utils.pdf_visuals import extract_heuristic_figures
    from utils.stdio import configure_logging, ensure_utf8_stdio

    load_environment()
    ensure_utf8_stdio()
    configure_logging()
    log = logging.getLogger(__name__)

    if not pdf.is_file():
        log.error("PDF 不存在: %s", pdf)
        return 1
    try:
        out = extract_heuristic_figures(pdf, doi, out_dir=out_dir)
    except ImportError as e:
        log.error("%s", e)
        return 1
    except Exception as e:
        log.exception("提取失败: %s", e)
        return 1
    for path in out:
        print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "crawl":
        from storage.crawl_jsonl import run_crawl_main

        run_crawl_main()
        return 0

    if args.command == "analyze":
        from engine.pipeline import run_analyze_cli

        extra: list[str] = []
        if args.interest is not None:
            extra += ["--interest", args.interest]
        if args.email is not None:
            extra += ["--email", args.email]
        if args.mode is not None:
            extra += ["--mode", args.mode]
        if args.works is not None:
            extra += ["--works", str(args.works)]
        if args.out is not None:
            extra += ["--out", str(args.out)]
        if args.model is not None:
            extra += ["--model", args.model]
        if args.api_base is not None:
            extra += ["--api-base", args.api_base]
        if args.delay is not None:
            extra += ["--delay", str(args.delay)]
        if args.append:
            extra.append("--append")
        return run_analyze_cli(extra)

    if args.command == "interactive":
        from ui.interactive_terminal import main as interactive_main

        extra: list[str] = []
        if args.report is not None:
            extra += ["--report", str(args.report)]
        return interactive_main(extra)

    if args.command == "figures":
        return run_figures(args.pdf, args.doi, args.out_dir)

    raise AssertionError("unhandled command")


if __name__ == "__main__":
    raise SystemExit(main())
