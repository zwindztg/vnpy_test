#!/usr/bin/env python3
"""只读体检项目本地 sqlite 的分钟缓存。"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from vnpy_alertcenter.core import SUPPORTED_INTERVALS, get_project_database_path


@dataclass(frozen=True)
class CacheHealthReport:
    """保存单个 vt_symbol/interval 的缓存体检结果。"""

    vt_symbol: str
    interval: str
    row_count: int
    start_text: str
    end_text: str
    duplicate_count: int
    reversed_count: int
    gap_count: int
    price_anomaly_count: int
    volume_anomaly_count: int
    duplicate_samples: tuple[str, ...] = ()
    reversed_samples: tuple[str, ...] = ()
    gap_samples: tuple[str, ...] = ()
    price_anomaly_samples: tuple[str, ...] = ()
    volume_anomaly_samples: tuple[str, ...] = ()
    note: str = ""

    @property
    def healthy(self) -> bool:
        """判断当前报告是否完全正常。"""
        return (
            self.row_count > 0
            and self.duplicate_count == 0
            and self.reversed_count == 0
            and self.gap_count == 0
            and self.price_anomaly_count == 0
            and self.volume_anomaly_count == 0
        )


def parse_args() -> argparse.Namespace:
    """读取命令行参数。"""
    parser = argparse.ArgumentParser(description="只读体检本地 sqlite 分钟缓存，默认优先检查 1m。")
    parser.add_argument("--vt-symbol", default="", help="可选，限定某个 vn.py 风格代码，例如 601869.SSE")
    parser.add_argument("--interval", default="1m", help="周期，默认检查 1m，也支持 5m/15m/30m")
    parser.add_argument("--limit", type=int, default=0, help="仅输出前 N 个报告，0 表示全部")
    return parser.parse_args()


def normalize_interval_text(interval: str) -> str:
    """把脚本周期参数限制到支持的分钟集合。"""
    normalized = str(interval).strip().lower()
    if normalized not in SUPPORTED_INTERVALS:
        raise ValueError(f"仅支持分钟周期：{', '.join(SUPPORTED_INTERVALS)}")
    return normalized


def parse_vt_symbol(vt_symbol: str) -> tuple[str, str]:
    """把 vn.py 风格代码拆成 symbol 和 exchange。"""
    normalized = str(vt_symbol).strip().upper()
    if not normalized:
        return "", ""

    try:
        symbol, exchange = normalized.split(".", 1)
    except ValueError as exc:
        raise ValueError(f"股票代码格式不正确：{vt_symbol}") from exc
    return symbol, exchange


def is_expected_session_break(previous_dt: datetime, current_dt: datetime) -> bool:
    """忽略 A股午休和跨日天然空档，只统计明显缺口。"""
    if previous_dt.date() != current_dt.date():
        return True

    noon_close = time(11, 30)
    afternoon_open = time(13, 0)
    if previous_dt.time() <= noon_close and current_dt.time() >= afternoon_open:
        return True
    return False


def build_vt_symbol(symbol: str, exchange: str) -> str:
    """把 symbol/exchange 拼回 vn.py 风格代码。"""
    return f"{symbol}.{exchange}"


def load_distinct_targets(
    connection: sqlite3.Connection,
    *,
    interval: str,
    vt_symbol: str,
) -> list[tuple[str, str]]:
    """读取当前数据库里需要体检的股票列表。"""
    if vt_symbol:
        symbol, exchange = parse_vt_symbol(vt_symbol)
        rows = connection.execute(
            "select distinct symbol, exchange from dbbardata where interval = ? and symbol = ? and exchange = ? order by symbol, exchange",
            (interval, symbol, exchange),
        ).fetchall()
        return [(str(item[0]), str(item[1])) for item in rows]

    rows = connection.execute(
        "select distinct symbol, exchange from dbbardata where interval = ? order by symbol, exchange",
        (interval,),
    ).fetchall()
    return [(str(item[0]), str(item[1])) for item in rows]


def load_bar_frame(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    exchange: str,
    interval: str,
) -> pd.DataFrame:
    """读取单个 symbol/exchange/interval 的原始分钟缓存。"""
    sql = (
        "select rowid, datetime, open_price, high_price, low_price, close_price, volume "
        "from dbbardata where symbol = ? and exchange = ? and interval = ? order by rowid asc"
    )
    frame = pd.read_sql_query(sql, connection, params=(symbol, exchange, interval))
    if frame.empty:
        return frame

    frame["datetime"] = pd.to_datetime(frame["datetime"])
    return frame


def analyze_bar_frame(vt_symbol: str, interval: str, frame: pd.DataFrame) -> CacheHealthReport:
    """对单个缓存分片做只读体检。"""
    if frame.empty:
        return CacheHealthReport(
            vt_symbol=vt_symbol,
            interval=interval,
            row_count=0,
            start_text="-",
            end_text="-",
            duplicate_count=0,
            reversed_count=0,
            gap_count=0,
            price_anomaly_count=0,
            volume_anomaly_count=0,
            note="当前分片没有任何本地缓存。",
        )

    expected_delta = pd.Timedelta(minutes=SUPPORTED_INTERVALS[interval])
    raw_datetimes = list(frame["datetime"])
    sorted_frame = frame.sort_values("datetime", kind="stable").reset_index(drop=True)
    sorted_datetimes = list(sorted_frame["datetime"])

    duplicate_series = sorted_frame["datetime"][sorted_frame["datetime"].duplicated(keep=False)]
    duplicate_samples = tuple(sorted({item.strftime("%Y-%m-%d %H:%M:%S") for item in duplicate_series[:6]}))

    reversed_samples: list[str] = []
    reversed_count = 0
    for previous, current in zip(raw_datetimes, raw_datetimes[1:]):
        if current < previous:
            reversed_count += 1
            if len(reversed_samples) < 3:
                reversed_samples.append(
                    f"{previous.strftime('%Y-%m-%d %H:%M:%S')} -> {current.strftime('%Y-%m-%d %H:%M:%S')}"
                )

    gap_samples: list[str] = []
    gap_count = 0
    unique_sorted_datetimes = sorted(dict.fromkeys(sorted_datetimes))
    for previous, current in zip(unique_sorted_datetimes, unique_sorted_datetimes[1:]):
        delta = current - previous
        if delta <= expected_delta or is_expected_session_break(previous, current):
            continue
        gap_count += 1
        if len(gap_samples) < 3:
            gap_samples.append(
                f"{previous.strftime('%Y-%m-%d %H:%M:%S')} -> {current.strftime('%Y-%m-%d %H:%M:%S')}"
            )

    price_median = float(sorted_frame["close_price"].median()) if not sorted_frame.empty else 0.0
    price_mask = (
        (sorted_frame["open_price"] <= 0)
        | (sorted_frame["high_price"] <= 0)
        | (sorted_frame["low_price"] <= 0)
        | (sorted_frame["close_price"] <= 0)
        | (sorted_frame["high_price"] < sorted_frame[["open_price", "close_price"]].max(axis=1))
        | (sorted_frame["low_price"] > sorted_frame[["open_price", "close_price"]].min(axis=1))
    )
    if price_median > 0:
        price_mask = price_mask | (sorted_frame["close_price"] > price_median * 5) | (sorted_frame["close_price"] < price_median / 5)
    price_anomaly_rows = sorted_frame[price_mask]
    price_anomaly_samples = tuple(
        row["datetime"].strftime("%Y-%m-%d %H:%M:%S")
        for _, row in price_anomaly_rows.head(3).iterrows()
    )

    positive_volumes = sorted_frame.loc[sorted_frame["volume"] > 0, "volume"]
    volume_median = float(positive_volumes.median()) if not positive_volumes.empty else 0.0
    volume_mask = sorted_frame["volume"] < 0
    if volume_median > 0:
        volume_mask = volume_mask | (sorted_frame["volume"] > volume_median * 1000)
    volume_anomaly_rows = sorted_frame[volume_mask]
    volume_anomaly_samples = tuple(
        row["datetime"].strftime("%Y-%m-%d %H:%M:%S")
        for _, row in volume_anomaly_rows.head(3).iterrows()
    )

    return CacheHealthReport(
        vt_symbol=vt_symbol,
        interval=interval,
        row_count=len(sorted_frame),
        start_text=sorted_datetimes[0].strftime("%Y-%m-%d %H:%M:%S"),
        end_text=sorted_datetimes[-1].strftime("%Y-%m-%d %H:%M:%S"),
        duplicate_count=int(duplicate_series.shape[0]),
        reversed_count=reversed_count,
        gap_count=gap_count,
        price_anomaly_count=int(price_anomaly_rows.shape[0]),
        volume_anomaly_count=int(volume_anomaly_rows.shape[0]),
        duplicate_samples=duplicate_samples,
        reversed_samples=tuple(reversed_samples),
        gap_samples=tuple(gap_samples),
        price_anomaly_samples=price_anomaly_samples,
        volume_anomaly_samples=volume_anomaly_samples,
    )


def format_report(report: CacheHealthReport) -> str:
    """把体检结果格式化成稳定、可人工阅读的文本。"""
    status = "正常" if report.healthy else "需处理"
    lines = [
        f"[{report.vt_symbol}][{report.interval}] {status}",
        f"  时间范围: {report.start_text} ~ {report.end_text}",
        f"  条数: {report.row_count}",
        f"  重复时间: {report.duplicate_count}",
        f"  倒序时间: {report.reversed_count}",
        f"  明显缺口: {report.gap_count}",
        f"  价格异常: {report.price_anomaly_count}",
        f"  成交量异常: {report.volume_anomaly_count}",
    ]
    if report.note:
        lines.append(f"  备注: {report.note}")
    if report.duplicate_samples:
        lines.append(f"  重复样本: {', '.join(report.duplicate_samples)}")
    if report.reversed_samples:
        lines.append(f"  倒序样本: {', '.join(report.reversed_samples)}")
    if report.gap_samples:
        lines.append(f"  缺口样本: {', '.join(report.gap_samples)}")
    if report.price_anomaly_samples:
        lines.append(f"  价格异常样本: {', '.join(report.price_anomaly_samples)}")
    if report.volume_anomaly_samples:
        lines.append(f"  成交量异常样本: {', '.join(report.volume_anomaly_samples)}")
    return "\n".join(lines)


def collect_reports(database_path: Path, *, interval: str, vt_symbol: str = "") -> list[CacheHealthReport]:
    """从 sqlite 里收集所有匹配分片的体检报告。"""
    if not database_path.exists():
        return [
            CacheHealthReport(
                vt_symbol=vt_symbol or "*",
                interval=interval,
                row_count=0,
                start_text="-",
                end_text="-",
                duplicate_count=0,
                reversed_count=0,
                gap_count=0,
                price_anomaly_count=0,
                volume_anomaly_count=0,
                note=f"数据库文件不存在：{database_path}",
            )
        ]

    reports: list[CacheHealthReport] = []
    with sqlite3.connect(database_path) as connection:
        try:
            targets = load_distinct_targets(connection, interval=interval, vt_symbol=vt_symbol)
        except sqlite3.Error as exc:
            return [
                CacheHealthReport(
                    vt_symbol=vt_symbol or "*",
                    interval=interval,
                    row_count=0,
                    start_text="-",
                    end_text="-",
                    duplicate_count=0,
                    reversed_count=0,
                    gap_count=0,
                    price_anomaly_count=0,
                    volume_anomaly_count=0,
                    note=f"读取 dbbardata 失败：{exc}",
                )
            ]

        if not targets:
            return [
                CacheHealthReport(
                    vt_symbol=vt_symbol or "*",
                    interval=interval,
                    row_count=0,
                    start_text="-",
                    end_text="-",
                    duplicate_count=0,
                    reversed_count=0,
                    gap_count=0,
                    price_anomaly_count=0,
                    volume_anomaly_count=0,
                    note="没有找到匹配的分钟缓存。",
                )
            ]

        for symbol, exchange in targets:
            frame = load_bar_frame(connection, symbol=symbol, exchange=exchange, interval=interval)
            reports.append(analyze_bar_frame(build_vt_symbol(symbol, exchange), interval, frame))
    return reports


def main() -> int:
    """执行分钟缓存体检。"""
    args = parse_args()
    interval = normalize_interval_text(args.interval)
    reports = collect_reports(get_project_database_path(), interval=interval, vt_symbol=str(args.vt_symbol).strip().upper())
    if args.limit > 0:
        reports = reports[: args.limit]

    print(f"本地分钟缓存体检：interval={interval}")
    print(f"数据库路径: {get_project_database_path()}")
    print()
    for index, report in enumerate(reports):
        if index > 0:
            print()
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
