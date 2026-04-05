"""
Interactive CLI: browse ``final_report.jsonl`` by stable index and optionally fetch citations from OpenAlex.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional

from core.config import default_data_dir
from core.env import load_environment
from crawler.client import OpenAlexError, get_cited_by, get_references
from storage.jsonl import iter_jsonl
from utils.stdio import ensure_utf8_stdio


def _lookup_no(rec: dict[str, Any], line_no: int) -> int:
    try:
        return int(rec["index_no"])
    except (KeyError, TypeError, ValueError):
        return line_no


def load_final_report(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"找不到报告文件: {path}")
    return list(iter_jsonl(path))


def print_index_list(records: list[dict[str, Any]]) -> None:
    print(f"共 {len(records)} 条（输入编号查看详情，「编号 --cite」抓取参考文献/施引；q 退出）\n")
    for i, rec in enumerate(records, 1):
        n = _lookup_no(rec, i)
        title = (rec.get("title") or "（无标题）").strip()
        if len(title) > 100:
            title = title[:97] + "..."
        ok = rec.get("ok", True)
        flag = "" if ok else " [失败]"
        print(f"  [{n}] {title}{flag}")


def print_detail(rec: dict[str, Any], line_no: int) -> None:
    n = _lookup_no(rec, line_no)
    print("---")
    print(f"编号: {n}")
    print(f"work_id: {rec.get('work_id', '')}")
    print(f"标题: {rec.get('title', '')}")
    print(f"年份: {rec.get('publication_year', '')}")
    print(f"期刊: {rec.get('journal_name', '')}")
    print(f"DOI: {rec.get('doi', '')}")
    if rec.get("ok") is False:
        print(f"分析状态: 失败 — {rec.get('error', '')}")
    ai = rec.get("ai")
    if isinstance(ai, dict):
        print(f"匹配分: {ai.get('match_score', '')}")
        print(f"相关度: {ai.get('relevance_level', '')}")
        print(f"摘要: {ai.get('summary_zh', '')}")
    print("---\n")


def _work_one_line(w: dict[str, Any], i: int) -> str:
    title = (w.get("title") or w.get("display_name") or "").strip() or "（无标题）"
    year = w.get("publication_year", "")
    wid = w.get("id", "")
    return f"  {i}. {title} ({year}) — {wid}"


def run_cite_fetch(work_id: str) -> None:
    try:
        refs = get_references(work_id, limit=5)
        cites = get_cited_by(work_id, limit=5)
    except OpenAlexError as e:
        print(f"OpenAlex 请求失败: {e}\n")
        return
    except ValueError as e:
        print(f"无效的 work_id: {e}\n")
        return

    print("参考文献（最多 5 条，按 OpenAlex 列表顺序）:")
    if not refs:
        print("  （无或无法解析）")
    else:
        for i, w in enumerate(refs, 1):
            print(_work_one_line(w, i))
    print("\n施引文献（最多 5 条）:")
    if not cites:
        print("  （无或暂无索引）")
    else:
        for i, w in enumerate(cites, 1):
            print(_work_one_line(w, i))
    print()


def parse_line(line: str) -> tuple[Optional[str], Optional[int], bool]:
    s = line.strip()
    if not s:
        return None, None, False
    low = s.lower()
    if low in ("q", "quit", "exit"):
        return "quit", None, False
    if low in ("h", "help", "?"):
        return "help", None, False
    parts = s.split()
    try:
        idx = int(parts[0])
    except ValueError:
        return "help", None, False
    cite = "--cite" in parts[1:]
    return "lookup", idx, cite


def find_record(records: list[dict[str, Any]], want: int) -> Optional[tuple[dict[str, Any], int]]:
    for i, rec in enumerate(records, 1):
        if _lookup_no(rec, i) == want:
            return rec, i
    return None


def repl(report_path: Path) -> None:
    records = load_final_report(report_path)
    if not records:
        print("报告为空。")
        return
    print_index_list(records)
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return
        action, idx, cite = parse_line(line)
        if action is None:
            continue
        if action == "quit":
            print("再见。")
            return
        if action == "help":
            print("用法: 输入编号查看详情；「7 --cite」抓取该条参考文献与施引；q 退出。\n")
            continue
        assert idx is not None
        found = find_record(records, idx)
        if not found:
            print(f"未找到编号 {idx}。\n")
            continue
        rec, line_no = found
        print_detail(rec, line_no)
        if cite:
            wid = rec.get("work_id")
            if not isinstance(wid, str) or not wid.strip():
                print("该记录缺少 work_id，无法查询引用。\n")
                continue
            print("正在请求 OpenAlex …")
            run_cite_fetch(wid.strip())


def main(argv: Optional[list[str]] = None) -> int:
    load_environment()
    ensure_utf8_stdio()

    parser = argparse.ArgumentParser(description="交互式浏览 final_report.jsonl 并查询 OpenAlex 引用。")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="final_report.jsonl 路径（默认 data/final_report.jsonl）",
    )
    args = parser.parse_args(argv)
    report_path = args.report or (default_data_dir() / "final_report.jsonl")

    try:
        repl(report_path)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0
