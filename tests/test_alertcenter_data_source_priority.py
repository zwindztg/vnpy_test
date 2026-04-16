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
    AlertHistoryWriter,
    AppConfig,
    SymbolAlertService,
    SymbolConfig,
    get_default_strategy_params,
)


def make_service(history_path: Path) -> SymbolAlertService:
    """构造一个最小可运行的监控服务，方便验证分钟线来源优先级。"""
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
    return SymbolAlertService(
        config=config,
        app_config=app_config,
        history_writer=AlertHistoryWriter(history_path),
        log_callback=lambda _log: None,
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
            ):
                with self.assertRaisesRegex(ValueError, "pytdx 失败"):
                    service.fetch_completed_bars(now, allow_local_fallback=False)

            self.assertEqual("pytdx失败", service.state.data_source)
            eastmoney_mock.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
