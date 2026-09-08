#!/usr/bin/env python3
"""
Live console panel for FomoPad fresh-token scanning.

Run:
    python console_panel.py

Stop:
    Ctrl+C
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import fomo_scan


RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def color(text: str, code: str, enabled: bool) -> str:
    if not enabled:
        return text
    return f"{code}{text}{RESET}"


def short_addr(address: str) -> str:
    return f"{address[:6]}...{address[-4:]}" if address else "--"


def trim(text: Any, width: int) -> str:
    value = str(text or "")
    if width <= 1:
        return value[:width]
    return value if len(value) <= width else value[: width - 1] + "."


def flow(value: float, use_color: bool) -> str:
    text = f"{value:.4f}"
    if value > 0:
        return color("+" + text, GREEN, use_color)
    if value < 0:
        return color(text, RED, use_color)
    return text


def make_scan_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        rpc=args.rpc,
        from_block=args.from_block,
        lookback_blocks=args.lookback_blocks,
        all_history=args.all_history,
        chunk_size=args.chunk_size,
        limit=args.limit,
        max_age_hours=args.max_age_hours,
        min_buys=args.min_buys,
        min_buyers=args.min_buyers,
        min_net_eth=args.min_net_eth,
        max_sell_ratio=args.max_sell_ratio,
        curve_only=args.curve_only,
        query=args.query,
        json=False,
        csv=None,
        watch=None,
        cache=args.cache,
        no_cache=args.no_cache,
        refresh_cache=False,
        min_score=args.min_score,
        timeout=args.timeout,
        retries=args.retries,
        transport=args.transport,
    )


def render(
    args: argparse.Namespace,
    result: fomo_scan.ScanResult | None,
    rows: list[dict[str, Any]],
    seen: set[str],
    error: str | None,
    next_scan_in: int,
    scan_number: int,
) -> None:
    width = shutil.get_terminal_size((120, 30)).columns
    use_color = not args.no_color
    clear_screen()

    title = "FOMOPAD LIVE SCANNER"
    status = "ERROR" if error else ("CACHE" if result and result.cache_only else "LIVE")
    status_color = RED if error else (YELLOW if status == "CACHE" else GREEN)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(color(title, BOLD + CYAN, use_color))
    print("-" * min(width, 120))
    print(
        f"status: {color(status, status_color, use_color)} | "
        f"scan: #{scan_number} | "
        f"interval: {args.interval:.0f}s | "
        f"next: {next_scan_in:02d}s | "
        f"time: {now}"
    )
    if result:
        print(
            f"source: {result.source if hasattr(result, 'source') else ('cache' if result.cache_only else 'rpc')} | "
            f"logs: {result.log_count} | "
            f"latest block: {result.latest_block or '--'} | "
            f"matches: {len(rows)}"
        )
    print(
        f"filters: buyers>={args.min_buyers} buys>={args.min_buys} "
        f"netETH>={args.min_net_eth} sellRatio<={args.max_sell_ratio} "
        f"score>={args.min_score if args.min_score is not None else 'any'} "
        f"lookback={args.lookback_blocks}"
    )

    message = error or (result.warning if result and result.warning else None)
    if message:
        print(color(f"\n{message}", RED if error else YELLOW, use_color))

    print()
    if not rows:
        print(color("No matching tokens right now. Waiting for the next scan.", DIM, use_color))
        return

    symbol_w = 12
    why_w = max(22, min(50, width - 78))
    print(
        f"{'NEW':<3} {'SCORE':>6} {'SYMBOL':<{symbol_w}} {'AGE':>7} "
        f"{'BUY':>4} {'SELL':>4} {'BUYERS':>6} {'NET ETH':>12} {'TOKEN':<13} WHY"
    )
    print("-" * min(width, 120))
    for row in rows:
        token = row["token"].lower()
        is_new = token not in seen
        marker = color("NEW", GREEN, use_color) if is_new else "   "
        print(
            marker
            + f" {row['score']:>6.1f} "
            + f"{trim(row['symbol'], symbol_w):<{symbol_w}} "
            + f"{row['age']:>7} "
            + f"{row['buys']:>4} "
            + f"{row['sells']:>4} "
            + f"{row['buyers']:>6} "
            + f"{flow(float(row['net_eth']), use_color):>12} "
            + f"{short_addr(row['token']):<13} "
            + trim(row["why"], why_w)
        )
        if args.show_urls:
            print(color(f"    {row['url']}", DIM, use_color))


def run_panel(args: argparse.Namespace) -> int:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    result: fomo_scan.ScanResult | None = None
    error: str | None = None
    scan_number = 0

    while True:
        scan_number += 1
        try:
            result = fomo_scan.scan_once(make_scan_args(args))
            rows = result.rows
            if args.min_score is not None:
                rows = [row for row in rows if row["score"] >= args.min_score]
            error = None
        except Exception as exc:
            error = str(exc)

        for countdown in range(int(args.interval), 0, -1):
            render(args, result, rows, seen, error, countdown, scan_number)
            if countdown == int(args.interval):
                seen.update(row["token"].lower() for row in rows)
            time.sleep(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a cyclic terminal panel for FomoPad fresh tokens.")
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between scans")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--rpc", default=os.getenv("FOMO_RPC", fomo_scan.DEFAULT_RPC))
    parser.add_argument("--from-block", type=lambda value: int(value, 0), default=None)
    parser.add_argument("--lookback-blocks", type=int, default=fomo_scan.DEFAULT_LOOKBACK_BLOCKS)
    parser.add_argument("--all-history", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=5_000_000)
    parser.add_argument("--max-age-hours", type=float, default=None)
    parser.add_argument("--min-buys", type=int, default=1)
    parser.add_argument("--min-buyers", type=int, default=1)
    parser.add_argument("--min-net-eth", type=float, default=0.0)
    parser.add_argument("--max-sell-ratio", type=float, default=0.65)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--curve-only", action="store_true")
    parser.add_argument("--query")
    parser.add_argument("--cache", default=fomo_scan.DEFAULT_CACHE)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--transport", choices=["auto", "urllib", "powershell"], default="auto")
    parser.add_argument("--show-urls", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval < 5:
        print("--interval must be at least 5 seconds", file=sys.stderr)
        return 2
    try:
        return run_panel(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
