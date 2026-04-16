from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import vnpy_alertcenter.core as alert_core
from vnpy_alertcenter.core import (
    BASIC_ALERT_STRATEGY,
    CHINA_TZ,
    AlertBar,
    AlertHistoryWriter,
    AppConfig,
    SymbolAlertService,
    SymbolConfig,
    aggregate_minute_bars_from_1m,
    filter_completed_bars,
    format_a_share_volume_value,
    get_default_strategy_params,
)


def make_service(
    history_path: Path,
    *,
    interval: str = "1m",
    log_messages: list | None = None,
) -> SymbolAlertService:
    """构造一个最小可运行的服务，方便验证分钟契约。"""
    config = SymbolConfig(
        vt_symbol="601869.SSE",
        strategy_name=BASIC_ALERT_STRATEGY,
        params=get_default_strategy_params(BASIC_ALERT_STRATEGY),
    )
    app_config = AppConfig(
        interval=interval,
        poll_seconds=20,
        adjust="qfq",
        cooldown_seconds=300,
        alert_history_path=history_path,
        notification_enabled=False,
        symbol_configs=(config,),
    )
    return SymbolAlertService(
        config=config,
        app_config=app_config,
        history_writer=AlertHistoryWriter(history_path),
        log_callback=(log_messages.append if log_messages is not None else (lambda _log: None)),
        record_callback=lambda _record: None,
        state_callback=lambda _state: None,
        chart_callback=lambda _chart: None,
    )


def make_minute_bar(index: int, *, close_price: float, base_dt: datetime) -> AlertBar:
    """按 1m 递增构造测试 bar。"""
    dt = base_dt + timedelta(minutes=index)
    return AlertBar(
        dt=dt,
        open_price=close_price - 0.2,
        high_price=close_price + 0.4,
        low_price=close_price - 0.5,
        close_price=close_price,
        volume=100.0 + index,
    )


class AlertCenterMinuteContractTest(unittest.TestCase):
    """验证分钟线时间戳、聚合和日志契约。"""

    def test_filter_completed_bars_keeps_close_timestamp_boundary(self) -> None:
        now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)
        cases = {
            "1m": [datetime(2026, 4, 16, 9, 59, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 1, tzinfo=CHINA_TZ)],
            "5m": [datetime(2026, 4, 16, 9, 55, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 5, tzinfo=CHINA_TZ)],
            "15m": [datetime(2026, 4, 16, 9, 45, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 15, tzinfo=CHINA_TZ)],
            "30m": [datetime(2026, 4, 16, 9, 30, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 30, tzinfo=CHINA_TZ)],
        }

        for interval, timestamps in cases.items():
            with self.subTest(interval=interval):
                bars = [
                    AlertBar(
                        dt=dt,
                        open_price=10.0,
                        close_price=10.1,
                        high_price=10.2,
                        low_price=9.9,
                        volume=100.0,
                    )
                    for dt in timestamps
                ]

                completed = filter_completed_bars(
                    bars,
                    now,
                    int(interval.removesuffix("m")),
                    timestamp_mode="close",
                )

                self.assertEqual(timestamps[:2], [bar.dt for bar in completed])

    def test_aggregate_minute_bars_from_1m_keeps_ohlcv_contract(self) -> None:
        base_dt = datetime(2026, 4, 16, 9, 31, tzinfo=CHINA_TZ)
        base_bars = [make_minute_bar(index, close_price=10.0 + index * 0.1, base_dt=base_dt) for index in range(30)]

        aggregated_5m = aggregate_minute_bars_from_1m(base_bars, "5m")
        aggregated_15m = aggregate_minute_bars_from_1m(base_bars, "15m")
        aggregated_30m = aggregate_minute_bars_from_1m(base_bars, "30m")

        self.assertEqual(6, len(aggregated_5m))
        self.assertEqual(datetime(2026, 4, 16, 9, 35, tzinfo=CHINA_TZ), aggregated_5m[0].dt)
        self.assertEqual(datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), aggregated_5m[-1].dt)
        first_5m_source = base_bars[:5]
        self.assertEqual(first_5m_source[0].open_price, aggregated_5m[0].open_price)
        self.assertEqual(first_5m_source[-1].close_price, aggregated_5m[0].close_price)
        self.assertEqual(max(bar.high_price for bar in first_5m_source), aggregated_5m[0].high_price)
        self.assertEqual(min(bar.low_price for bar in first_5m_source), aggregated_5m[0].low_price)
        self.assertEqual(sum(bar.volume for bar in first_5m_source), aggregated_5m[0].volume)

        self.assertEqual(2, len(aggregated_15m))
        self.assertEqual(
            [datetime(2026, 4, 16, 9, 45, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)],
            [bar.dt for bar in aggregated_15m],
        )

        self.assertEqual(1, len(aggregated_30m))
        self.assertEqual(datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), aggregated_30m[0].dt)

    def test_preview_local_fallback_aggregates_requested_interval_from_local_1m(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path, interval="5m")
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)
            base_dt = datetime(2026, 4, 16, 9, 31, tzinfo=CHINA_TZ)
            rows = [
                (
                    (base_dt + timedelta(minutes=index)).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    10.0 + index * 0.1,
                    10.2 + index * 0.1,
                    9.8 + index * 0.1,
                    10.1 + index * 0.1,
                    100.0 + index,
                )
                for index in range(30)
            ]

            with patch.object(service, "query_local_bar_rows", return_value=rows) as query_mock:
                bars, source_name = service.fetch_local_database_bars(now)

            self.assertEqual(1, query_mock.call_count)
            self.assertEqual("1m", query_mock.call_args.kwargs["interval"])
            self.assertEqual("1m聚合->5m", source_name)
            self.assertEqual(6, len(bars))
            self.assertEqual(datetime(2026, 4, 16, 9, 35, tzinfo=CHINA_TZ), bars[0].dt)
            self.assertEqual(datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ), bars[-1].dt)

    def test_preview_success_logs_fixed_fetch_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            logs: list = []
            service = make_service(history_path, interval="1m", log_messages=logs)
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)
            df = pd.DataFrame(
                [
                    {"datetime": "2026-04-16 09:59", "open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9, "vol": 1001.0},
                    {"datetime": "2026-04-16 10:00", "open": 10.1, "close": 10.2, "high": 10.3, "low": 10.0, "vol": 1002.0},
                    {"datetime": "2026-04-16 10:01", "open": 10.2, "close": 10.3, "high": 10.4, "low": 10.1, "vol": 1003.0},
                ]
            )

            with patch.object(alert_core, "fetch_pytdx_minute_dataframe", return_value=(df, "pytdx:测试主站")):
                service.fetch_completed_bars(now, allow_local_fallback=True)

            self.assertTrue(logs)
            self.assertIn("分钟线抓取摘要", logs[0].message)
            self.assertIn("主站=测试主站", logs[0].message)
            self.assertIn("周期=1m", logs[0].message)
            self.assertIn("列名=datetime,open,close,high,low,vol", logs[0].message)
            self.assertIn("单次测试=是", logs[0].message)
            self.assertIn("本地fallback=否", logs[0].message)

    def test_live_success_does_not_spam_fetch_summary_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            logs: list = []
            service = make_service(history_path, interval="1m", log_messages=logs)
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)
            df = pd.DataFrame(
                [
                    {"datetime": "2026-04-16 09:59", "open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9, "vol": 1001.0},
                    {"datetime": "2026-04-16 10:00", "open": 10.1, "close": 10.2, "high": 10.3, "low": 10.0, "vol": 1002.0},
                    {"datetime": "2026-04-16 10:01", "open": 10.2, "close": 10.3, "high": 10.4, "low": 10.1, "vol": 1003.0},
                ]
            )

            with patch.object(alert_core, "fetch_pytdx_minute_dataframe", return_value=(df, "pytdx:测试主站")):
                service.fetch_completed_bars(now, allow_local_fallback=False)

            self.assertEqual([], logs)

    def test_preview_failure_message_points_to_fill_1m_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path, interval="15m")
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)

            with (
                patch.object(alert_core, "fetch_pytdx_minute_dataframe", side_effect=ValueError("pytdx timeout")),
                patch.object(service, "fetch_local_database_bars", return_value=([], "")),
            ):
                with self.assertRaisesRegex(ValueError, "--fill-1m"):
                    service.fetch_completed_bars(now, allow_local_fallback=True)

    def test_format_a_share_volume_value_supports_integer_and_decimal(self) -> None:
        self.assertEqual("123手", format_a_share_volume_value(123.0))
        self.assertEqual("1.23万手", format_a_share_volume_value(12345.0))
        self.assertEqual("123.46手", format_a_share_volume_value(123.456))


if __name__ == "__main__":
    unittest.main()
