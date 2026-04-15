#!/usr/bin/env python3
"""对比提醒中心可用的数据源，帮助排查价格口径问题。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from vnpy_alertcenter.core import (
    CHINA_TZ,
    disable_process_proxy_env,
    ensure_china_tz,
    fetch_eastmoney_minute_dataframe,
    fetch_pytdx_minute_dataframe,
    floor_to_interval,
    get_interval_minutes,
    get_project_database_path,
    install_requests_no_proxy,
    normalize_interval,
    split_vt_symbol,
)


@dataclass
class SourceSnapshot:
    """保存单个数据源的最新 K 线快照。"""

    source_name: str
    success: bool
    latest_dt: str = "-"
    latest_close: str = "-"
    row_count: int = 0
    note: str = ""


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""
    parser = argparse.ArgumentParser(description="对比 pytdx、东财和本地数据库的价格口径。")
    parser.add_argument("--vt-symbol", default="601869.SSE", help="vn.py 风格代码，例如 601869.SSE")
    parser.add_argument("--interval", default="5m", help="周期，支持 1m/5m/15m/30m")
    parser.add_argument(
        "--reference",
        default="2026-04-13 09:30:00",
        help="参考时间，格式 YYYY-MM-DD HH:MM:SS，默认使用最近调试过的时间点。",
    )
    return parser.parse_args()


def parse_reference_time(text: str) -> datetime:
    """把输入时间解析为上海时区。"""
    dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    return ensure_china_tz(dt)


def build_snapshot_from_bars(
    source_name: str,
    bars: pd.DataFrame,
    now: datetime,
    interval: str,
    *,
    timestamp_mode: str,
) -> SourceSnapshot:
    """从 DataFrame 中提取最后一根完整 K 线。"""
    if bars.empty:
        return SourceSnapshot(source_name=source_name, success=False, note="没有返回任何数据。")

    df = bars.copy()
    time_column = "datetime" if "datetime" in df.columns else "时间"
    close_column = "close" if "close" in df.columns else "收盘"
    df[time_column] = pd.to_datetime(df[time_column])
    df = df.sort_values(time_column).drop_duplicates(subset=[time_column], keep="last")

    cutoff = floor_to_interval(now, get_interval_minutes(interval)).replace(tzinfo=None)
    if timestamp_mode == "close":
        completed_df = df[df[time_column] <= cutoff]
    else:
        completed_df = df[df[time_column] < cutoff]

    if completed_df.empty:
        return SourceSnapshot(source_name=source_name, success=False, note="没有可用的完整 K 线。")

    latest = completed_df.iloc[-1]
    return SourceSnapshot(
        source_name=source_name,
        success=True,
        latest_dt=str(latest[time_column]),
        latest_close=f"{float(latest[close_column]):.3f}",
        row_count=len(completed_df),
    )


def query_local_snapshot(vt_symbol: str, interval: str, now: datetime) -> SourceSnapshot:
    """从项目本地 sqlite 中读取一份对比快照。"""
    symbol, exchange = split_vt_symbol(vt_symbol)
    database_path = get_project_database_path()
    if not database_path.exists():
        return SourceSnapshot(source_name="本地数据库", success=False, note="本地数据库文件不存在。")

    lookback_days = 10 if interval != "d" else 400
    start_dt = now - timedelta(days=lookback_days)
    sql = (
        "select datetime, close_price from dbbardata "
        "where symbol = ? and exchange = ? and interval = ? "
        "and datetime >= ? and datetime <= ? "
        "order by datetime asc"
    )
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            sql,
            (
                symbol,
                exchange,
                interval,
                start_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchall()

    if not rows:
        return SourceSnapshot(
            source_name=f"本地数据库({interval})",
            success=False,
            note="当前周期没有本地数据。",
        )

    latest_dt, latest_close = rows[-1]
    return SourceSnapshot(
        source_name=f"本地数据库({interval})",
        success=True,
        latest_dt=str(latest_dt),
        latest_close=f"{float(latest_close):.3f}",
        row_count=len(rows),
    )


def print_snapshot(snapshot: SourceSnapshot) -> None:
    """打印单个数据源摘要。"""
    status = "成功" if snapshot.success else "失败"
    print(f"[{snapshot.source_name}] {status}")
    print(f"  最新时间: {snapshot.latest_dt}")
    print(f"  最新收盘: {snapshot.latest_close}")
    print(f"  可用条数: {snapshot.row_count}")
    if snapshot.note:
        print(f"  备注: {snapshot.note}")


def print_ratio_comparison(snapshots: list[SourceSnapshot]) -> None:
    """输出各数据源之间的价格比例，便于发现 10 倍、100 倍等缩放问题。"""
    valid = [item for item in snapshots if item.success and item.latest_close != "-"]
    if len(valid) < 2:
        print("\n价格比例: 可用数据源不足，无法比较。")
        return

    print("\n价格比例:")
    for base in valid:
        base_price = float(base.latest_close)
        for target in valid:
            if base.source_name >= target.source_name:
                continue
            target_price = float(target.latest_close)
            if base_price <= 0 or target_price <= 0:
                continue
            ratio = target_price / base_price
            note = ""
            if 9.5 <= ratio <= 10.5 or 0.095 <= ratio <= 0.105:
                note = "，疑似 10 倍缩放差异"
            elif 99 <= ratio <= 101 or 0.0099 <= ratio <= 0.0101:
                note = "，疑似 100 倍缩放差异"
            print(f"  {target.source_name} / {base.source_name} = {ratio:.4f}{note}")


def main() -> int:
    """执行三路数据源对比。"""
    args = parse_args()
    disable_process_proxy_env()
    install_requests_no_proxy()

    interval = normalize_interval(args.interval)
    reference_dt = parse_reference_time(args.reference)
    symbol, exchange = split_vt_symbol(args.vt_symbol)
    start_dt = reference_dt - timedelta(days=5)
    end_dt = reference_dt + timedelta(minutes=1)

    print(f"对比标的: {args.vt_symbol}")
    print(f"对比周期: {interval}")
    print(f"参考时间: {reference_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    snapshots: list[SourceSnapshot] = []

    try:
        pytdx_df, source_name = fetch_pytdx_minute_dataframe(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        snapshots.append(
            build_snapshot_from_bars(
                source_name,
                pytdx_df,
                reference_dt,
                interval,
                timestamp_mode="close",
            )
        )
    except Exception as exc:
        snapshots.append(SourceSnapshot(source_name="pytdx", success=False, note=str(exc)))

    try:
        eastmoney_df = fetch_eastmoney_minute_dataframe(
            symbol=symbol,
            period=str(get_interval_minutes(interval)),
            adjust="qfq",
            start_dt=start_dt,
            end_dt=end_dt,
        )
        snapshots.append(
            build_snapshot_from_bars(
                "东财分钟线",
                eastmoney_df,
                reference_dt,
                interval,
                timestamp_mode="open",
            )
        )
    except Exception as exc:
        snapshots.append(SourceSnapshot(source_name="东财分钟线", success=False, note=str(exc)))

    snapshots.append(query_local_snapshot(args.vt_symbol, interval, reference_dt))
    if interval != "d":
        snapshots.append(query_local_snapshot(args.vt_symbol, "d", reference_dt))

    print()
    for snapshot in snapshots:
        print_snapshot(snapshot)

    print_ratio_comparison(snapshots)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
