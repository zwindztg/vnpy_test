from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from vnpy_alertcenter.core import (
    BASIC_ALERT_STRATEGY,
    CHINA_TZ,
    AlertBar,
    AlertHistoryWriter,
    AppConfig,
    ChartSnapshotData,
    SymbolAlertService,
    SymbolConfig,
    get_default_strategy_params,
)


def make_service(history_path: Path, chart_callback) -> SymbolAlertService:
    """构造一个最小可运行的服务，便于验证图表快照内容。"""
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
    service = SymbolAlertService(
        config=config,
        app_config=app_config,
        history_writer=AlertHistoryWriter(history_path),
        log_callback=lambda _log: None,
        record_callback=lambda _record: None,
        state_callback=lambda _state: None,
        chart_callback=chart_callback,
    )
    service.chart_enabled = True
    service.state.data_source = "pytdx:测试"
    return service


class AlertCenterChartSnapshotTest(unittest.TestCase):
    """验证 1m 图表快照会保留全天数据，但默认只看最后 2 小时。"""

    def test_emit_chart_snapshot_keeps_full_latest_day_for_1m(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            history_path = Path(temp_dir) / "alerts.csv"
            captured: list[ChartSnapshotData] = []
            service = make_service(history_path, captured.append)

            previous_day = datetime(2026, 4, 15, 14, 55, tzinfo=CHINA_TZ)
            base_dt = datetime(2026, 4, 16, 9, 30, tzinfo=CHINA_TZ)
            bars = [
                AlertBar(
                    dt=previous_day + timedelta(minutes=index),
                    open_price=9.5 + index * 0.01,
                    close_price=9.52 + index * 0.01,
                    high_price=9.55 + index * 0.01,
                    low_price=9.48 + index * 0.01,
                    volume=800 + index,
                )
                for index in range(5)
            ] + [
                AlertBar(
                    dt=base_dt + timedelta(minutes=index),
                    open_price=10.0 + index * 0.01,
                    close_price=10.02 + index * 0.01,
                    high_price=10.05 + index * 0.01,
                    low_price=9.98 + index * 0.01,
                    volume=1000 + index,
                )
                for index in range(240)
            ]

            service.emit_chart_snapshot(
                bars=bars,
                mode="preview",
                reference_time=bars[-1].dt,
            )

            self.assertEqual(1, len(captured))
            snapshot = captured[0]
            self.assertEqual(240, len(snapshot.bars))
            self.assertEqual(120, snapshot.default_visible_count)
            self.assertTrue(all(bar.dt.date() == base_dt.date() for bar in snapshot.bars))
            self.assertEqual(datetime(2026, 4, 16, 9, 30, tzinfo=CHINA_TZ), snapshot.bars[0].dt)
            self.assertEqual(datetime(2026, 4, 16, 13, 29, tzinfo=CHINA_TZ), snapshot.bars[-1].dt)


if __name__ == "__main__":
    unittest.main()
