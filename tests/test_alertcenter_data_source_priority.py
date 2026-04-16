from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import vnpy_alertcenter.core as alert_core
from vnpy_alertcenter.core import (
    BASIC_ALERT_STRATEGY,
    CHINA_TZ,
    AlertBar,
    AlertCenterRunner,
    AlertHistoryWriter,
    AppConfig,
    SymbolAlertService,
    SymbolConfig,
    get_default_strategy_params,
)


class FakeDatabase:
    """记录写库调用的最小 fake database。"""

    def __init__(self) -> None:
        self.saved_batches: list[tuple[list, bool]] = []

    def save_bar_data(self, bars, stream: bool = False) -> bool:
        self.saved_batches.append((list(bars), stream))
        return True


def make_service(
    history_path: Path,
    *,
    interval: str = "1m",
    log_messages: list | None = None,
) -> SymbolAlertService:
    """构造一个最小可运行的监控服务，方便验证分钟线来源优先级。"""
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


class AlertCenterDataSourcePriorityTest(unittest.TestCase):
    """验证分钟线来源已经改成 pytdx -> 本地 sqlite。"""

    def test_falls_back_to_local_sqlite_without_calling_eastmoney(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path)
            now = datetime(2026, 4, 16, 15, 0, tzinfo=CHINA_TZ)
            local_bars = [
                AlertBar(
                    dt=datetime(2026, 4, 16, 14, 59, tzinfo=CHINA_TZ),
                    open_price=10.0,
                    high_price=10.2,
                    low_price=9.9,
                    close_price=10.1,
                    volume=1200.0,
                )
            ]

            with (
                patch.object(alert_core, "fetch_pytdx_minute_dataframe", side_effect=ValueError("pytdx down")),
                patch.object(alert_core, "fetch_eastmoney_minute_dataframe") as eastmoney_mock,
                patch.object(service, "fetch_local_database_bars", return_value=(local_bars, "1m")),
            ):
                result = service.fetch_completed_bars(now, allow_local_fallback=True)

            self.assertEqual(local_bars, result)
            self.assertEqual("本地1m", service.state.data_source)
            eastmoney_mock.assert_not_called()

    def test_live_mode_raises_pytdx_error_directly_without_trying_eastmoney(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path)
            now = datetime(2026, 4, 16, 15, 0, tzinfo=CHINA_TZ)

            with (
                patch.object(alert_core, "fetch_pytdx_minute_dataframe", side_effect=ValueError("pytdx timeout")),
                patch.object(alert_core, "fetch_eastmoney_minute_dataframe") as eastmoney_mock,
                patch.object(service, "fetch_local_database_bars") as local_mock,
            ):
                with self.assertRaisesRegex(ValueError, "实时模式不会回退到本地数据库"):
                    service.fetch_completed_bars(now, allow_local_fallback=False)

            self.assertEqual("pytdx失败", service.state.data_source)
            eastmoney_mock.assert_not_called()
            local_mock.assert_not_called()

    def test_local_fallback_does_not_silently_downgrade_1m_to_daily(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path)
            now = datetime(2026, 4, 16, 15, 0, tzinfo=CHINA_TZ)

            with patch.object(service, "query_local_bar_rows", return_value=[]) as query_mock:
                bars, interval = service.fetch_local_database_bars(now)

            self.assertEqual([], bars)
            self.assertEqual("", interval)
            self.assertEqual(1, query_mock.call_count)
            self.assertEqual("1m", query_mock.call_args.kwargs["interval"])

    def test_parse_bars_supports_pytdx_vol_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path)
            df = pd.DataFrame(
                [
                    {
                        "datetime": "2026-04-16 14:59",
                        "open": 10.0,
                        "close": 10.1,
                        "high": 10.2,
                        "low": 9.9,
                        "vol": 2181.0,
                    }
                ]
            )

            bars = service.parse_bars(df)

            self.assertEqual(1, len(bars))
            self.assertEqual(2181.0, bars[0].volume)

    def test_remote_success_writes_only_new_completed_bars_to_local_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path)
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)
            fake_database = FakeDatabase()
            df = pd.DataFrame(
                [
                    {"datetime": "2026-04-16 09:59", "open": 10.0, "close": 10.1, "high": 10.2, "low": 9.9, "vol": 1001.0},
                    {"datetime": "2026-04-16 10:00", "open": 10.1, "close": 10.2, "high": 10.3, "low": 10.0, "vol": 1002.0},
                    {"datetime": "2026-04-16 10:01", "open": 10.2, "close": 10.3, "high": 10.4, "low": 10.1, "vol": 1003.0},
                ]
            )

            with (
                patch.object(alert_core, "fetch_pytdx_minute_dataframe", return_value=(df, "pytdx:测试")),
                patch.object(service, "get_latest_local_bar_datetime", return_value=datetime(2026, 4, 16, 9, 59, tzinfo=CHINA_TZ)),
                patch.object(alert_core, "get_database", return_value=fake_database),
            ):
                result = service.fetch_completed_bars(now, allow_local_fallback=False)

            self.assertEqual(
                [datetime(2026, 4, 16, 9, 59, tzinfo=CHINA_TZ), datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)],
                [bar.dt for bar in result],
            )
            self.assertEqual(1, len(fake_database.saved_batches))
            saved_bars, stream = fake_database.saved_batches[0]
            self.assertTrue(stream)
            self.assertEqual([datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)], [bar.datetime for bar in saved_bars])
            self.assertEqual("1m", saved_bars[0].interval.value)

    def test_remote_success_keeps_custom_interval_value_when_writing_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            service = make_service(history_path, interval="5m")
            fake_database = FakeDatabase()
            bars = [
                AlertBar(
                    dt=datetime(2026, 4, 16, 14, 55, tzinfo=CHINA_TZ),
                    open_price=10.0,
                    high_price=10.2,
                    low_price=9.9,
                    close_price=10.1,
                    volume=1888.0,
                )
            ]

            with (
                patch.object(service, "get_latest_local_bar_datetime", return_value=None),
                patch.object(alert_core, "get_database", return_value=fake_database),
            ):
                service.save_remote_bars_to_local_cache(bars)

            self.assertEqual(1, len(fake_database.saved_batches))
            saved_bars, stream = fake_database.saved_batches[0]
            self.assertFalse(stream)
            self.assertEqual("5m", saved_bars[0].interval.value)

    def test_write_back_failure_only_logs_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            logs: list = []
            service = make_service(history_path, log_messages=logs)
            bars = [
                AlertBar(
                    dt=datetime(2026, 4, 16, 14, 59, tzinfo=CHINA_TZ),
                    open_price=10.0,
                    high_price=10.2,
                    low_price=9.9,
                    close_price=10.1,
                    volume=1200.0,
                )
            ]

            with (
                patch.object(service, "get_latest_local_bar_datetime", return_value=None),
                patch.object(alert_core, "get_database", side_effect=RuntimeError("db down")),
            ):
                service.save_remote_bars_to_local_cache(bars)

            self.assertTrue(logs)
            self.assertEqual("WARNING", logs[-1].level)
            self.assertIn("写回本地 sqlite 失败", logs[-1].message)

    def test_runner_live_mode_marks_error_without_fallback_or_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            config = SymbolConfig(
                vt_symbol="601869.SSE",
                strategy_name=BASIC_ALERT_STRATEGY,
                params=get_default_strategy_params(BASIC_ALERT_STRATEGY),
            )
            app_config = AppConfig(
                interval="1m",
                poll_seconds=20,
                adjust="qfq",
                cooldown_seconds=300,
                alert_history_path=history_path,
                notification_enabled=False,
                symbol_configs=(config,),
            )
            logs: list = []
            records: list = []
            states: list = []
            statuses: list = []
            charts: list = []
            runner = AlertCenterRunner(
                config=app_config,
                log_callback=logs.append,
                status_callback=statuses.append,
                record_callback=records.append,
                state_callback=states.append,
                chart_callback=charts.append,
            )
            service = runner.services[0]
            now = datetime(2026, 4, 16, 10, 0, tzinfo=CHINA_TZ)

            with (
                patch.object(alert_core, "fetch_pytdx_minute_dataframe", side_effect=ValueError("pytdx timeout")),
                patch.object(service, "fetch_local_database_bars") as local_mock,
            ):
                runner.run_once(reference_time=now, allow_local_fallback=False)

            self.assertEqual([], records)
            self.assertEqual([], charts)
            self.assertTrue(states)
            self.assertEqual("pytdx失败", states[-1].data_source)
            self.assertEqual("异常", states[-1].status)
            self.assertIn("实时模式不会回退到本地数据库", states[-1].last_error)
            self.assertTrue(logs)
            self.assertEqual("ERROR", logs[-1].level)
            self.assertIn("实时模式不会回退到本地数据库", logs[-1].message)
            local_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
