#!/usr/bin/env python3
"""修复项目本地 sqlite 中单个股票周期的历史缓存。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import HistoryRequest

from run_vnpy import ensure_vnpy_settings
from vnpy_akshare.datafeed import AkshareDatafeed


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""
    parser = argparse.ArgumentParser(description="修复项目本地 sqlite 的单个股票缓存。")
    parser.add_argument("--vt-symbol", default="601869.SSE", help="vn.py 风格代码，例如 601869.SSE")
    parser.add_argument("--interval", default="d", help="周期，例如 d、1m")
    parser.add_argument(
        "--fill-1m",
        action="store_true",
        help="直接补本地 1m 基础缓存。离线 5m/15m/30m 回放会优先依赖本地 1m 聚合。",
    )
    parser.add_argument("--start", default="2025-01-01 00:00:00", help="开始时间，格式 YYYY-MM-DD HH:MM:SS")
    parser.add_argument("--end", default="2026-04-15 00:00:00", help="结束时间，格式 YYYY-MM-DD HH:MM:SS")
    return parser.parse_args()


def parse_vt_symbol(vt_symbol: str) -> tuple[str, Exchange]:
    """把 vn.py 风格代码拆成 symbol 和 Exchange。"""
    normalized = vt_symbol.strip().upper()
    try:
        symbol, exchange_text = normalized.split(".", 1)
    except ValueError as exc:
        raise ValueError(f"股票代码格式不正确：{vt_symbol}") from exc

    if not symbol.isdigit():
        raise ValueError(f"当前脚本仅支持纯数字 A 股代码：{vt_symbol}")

    try:
        exchange = Exchange(exchange_text)
    except ValueError as exc:
        raise ValueError(f"交易所后缀不支持：{exchange_text}") from exc
    return symbol, exchange


def parse_dt(text: str) -> datetime:
    """把字符串解析成 datetime。"""
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def main() -> int:
    """执行缓存修复。"""
    args = parse_args()
    symbol, exchange = parse_vt_symbol(args.vt_symbol)
    interval_text = "1m" if args.fill_1m else args.interval
    interval = Interval(interval_text)
    start = parse_dt(args.start)
    end = parse_dt(args.end)

    ensure_vnpy_settings()
    database = get_database()
    datafeed = AkshareDatafeed()

    request = HistoryRequest(
        symbol=symbol,
        exchange=exchange,
        interval=interval,
        start=start,
        end=end,
    )

    print(f"开始修复 {args.vt_symbol}-{interval_text} 本地缓存。")
    if args.fill_1m:
        print("当前使用 --fill-1m：这会补齐本地分钟基础缓存，后续 5m/15m/30m 优先从本地 1m 聚合。")
    elif interval_text != "1m":
        print("提示：离线分钟回放优先依赖本地 1m 缓存；如果要补基础分钟缓存，可追加 --fill-1m。")
    bars = datafeed.query_bar_history(request, print)
    if not bars:
        print("未能从 AkShare 获取任何历史数据，修复中止。")
        return 1

    deleted = database.delete_bar_data(symbol, exchange, interval)
    print(f"已删除旧缓存：{deleted} 条")

    database.save_bar_data(bars)
    print(f"已写入新缓存：{len(bars)} 条")
    print(
        "最新一条："
        f"{bars[-1].datetime.strftime('%Y-%m-%d %H:%M:%S')} "
        f"close={bars[-1].close_price:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
