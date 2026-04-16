from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from vnpy_alertcenter.core import (
    CHINA_TZ,
    AlertBar,
    ChartBarData,
    ChartMarkerData,
    ChartSnapshotData,
    build_chart_bar_data,
)
from vnpy_alertcenter.ui.chart_view import (
    apply_drag_pan,
    apply_pan_left,
    apply_pan_right,
    apply_zoom_in,
    can_pan_left,
    can_pan_right,
    can_zoom,
    can_zoom_in,
    get_available_bars,
    get_available_markers,
    get_view_key,
    sync_view_state,
)


def make_chart_bar(base_dt: datetime, index: int, close_price: float, volume: float = 100.0) -> ChartBarData:
    """按固定分钟间隔生成测试用图表 bar。"""
    current_dt = base_dt + timedelta(minutes=5 * index)
    return ChartBarData(
        dt=current_dt,
        open_price=close_price - 0.5,
        high_price=close_price + 0.8,
        low_price=close_price - 1.0,
        close_price=close_price,
        volume=volume,
    )


class AlertChartViewTest(unittest.TestCase):
    """验证弹窗图表的日内视图辅助逻辑。"""

    def build_snapshot(self, interval: str = "5m") -> ChartSnapshotData:
        """构造两天数据的图表快照，便于验证只看最新交易日。"""
        first_day = datetime(2026, 4, 14, 9, 30, tzinfo=CHINA_TZ)
        second_day = datetime(2026, 4, 15, 9, 30, tzinfo=CHINA_TZ)
        bars = [
            *(make_chart_bar(first_day, index, 100 + index) for index in range(6)),
            *(make_chart_bar(second_day, index, 200 + index) for index in range(20)),
        ]
        markers = (
            ChartMarkerData(
                dt=bars[1].dt,
                price=bars[1].close_price,
                direction="buy",
                rule_name="day1_signal",
                message="旧交易日标记",
            ),
            ChartMarkerData(
                dt=bars[-2].dt,
                price=bars[-2].close_price,
                direction="sell",
                rule_name="day2_signal",
                message="最新交易日标记",
            ),
        )
        return ChartSnapshotData(
            config_id="test-chart-view",
            vt_symbol="601869.SSE",
            strategy_name="BasicAlertStrategy",
            interval=interval,
            data_source="测试数据",
            mode="preview",
            bars=tuple(bars),
            markers=markers,
            reference_time=bars[-1].dt,
        )

    def test_intraday_snapshot_only_keeps_latest_trade_day(self) -> None:
        snapshot = self.build_snapshot()

        available_bars = get_available_bars(snapshot, intraday_only=True)
        available_markers = get_available_markers(snapshot, intraday_only=True)

        self.assertEqual(20, len(available_bars))
        self.assertTrue(all(bar.dt.date() == available_bars[-1].dt.date() for bar in available_bars))
        self.assertEqual(["day2_signal"], [marker.rule_name for marker in available_markers])

    def test_zoom_and_pan_state_stay_within_intraday_bounds(self) -> None:
        total = 20
        zoom_enabled = can_zoom(True, self.build_snapshot(), total)
        visible_start, visible_count = sync_view_state(
            total=total,
            visible_start=0,
            visible_count=0,
            reset=True,
            zoom_enabled=zoom_enabled,
            min_visible_bars=12,
        )

        self.assertTrue(can_zoom_in(zoom_enabled, visible_count, total, 12))

        visible_start, visible_count = apply_zoom_in(visible_start, visible_count, total, 12)
        self.assertEqual((4, 16), (visible_start, visible_count))
        self.assertTrue(can_pan_left(zoom_enabled, visible_start))

        visible_start = apply_pan_left(visible_start, visible_count)
        self.assertEqual(0, visible_start)

        visible_start = apply_pan_right(visible_start, visible_count, total)
        self.assertTrue(can_pan_right(zoom_enabled, 0, visible_count, total))
        self.assertGreater(visible_start, 0)

    def test_drag_pan_direction_and_bounds(self) -> None:
        total = 20
        visible_count = 16

        # 右拖时应查看更早的数据，visible_start 变小。
        self.assertEqual(1, apply_drag_pan(4, total, visible_count, 3))
        # 左拖时应回到更近的新数据区域，visible_start 变大。
        self.assertEqual(4, apply_drag_pan(1, total, visible_count, -3))
        # 越界时需要被裁剪到合法区间。
        self.assertEqual(0, apply_drag_pan(2, total, visible_count, 10))
        self.assertEqual(4, apply_drag_pan(2, total, visible_count, -10))

    def test_daily_snapshot_disables_intraday_zoom(self) -> None:
        snapshot = self.build_snapshot(interval="d")
        available_bars = get_available_bars(snapshot, intraday_only=True)
        zoom_enabled = can_zoom(True, snapshot, len(available_bars))

        self.assertEqual(("601869.SSE", "d", snapshot.reference_time.date()), get_view_key(snapshot, True))
        self.assertFalse(zoom_enabled)

    def test_build_chart_bar_data_keeps_volume(self) -> None:
        bars = [
            AlertBar(
                dt=datetime(2026, 4, 15, 9, 30, tzinfo=CHINA_TZ),
                open_price=10.0,
                close_price=10.5,
                high_price=10.8,
                low_price=9.8,
                volume=12345.0,
            )
        ]

        chart_bars = build_chart_bar_data(bars)

        self.assertEqual(1, len(chart_bars))
        self.assertEqual(12345.0, chart_bars[0].volume)


if __name__ == "__main__":
    unittest.main()
