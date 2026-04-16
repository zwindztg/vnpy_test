from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from vnpy_alertcenter.core import (
    BASIC_ALERT_STRATEGY,
    CHINA_TZ,
    LESSON_A_SHARE_LONG_ONLY,
    LESSON_DONCHIAN,
    LESSON_VOLUME_BREAKOUT,
    AlertBar,
    build_chart_markers,
)


def make_bar(
    index: int,
    close_price: float,
    *,
    open_price: float | None = None,
    high_price: float | None = None,
    low_price: float | None = None,
    volume: float = 100.0,
) -> AlertBar:
    """构造测试用 K 线，默认按 5 分钟递增。"""
    base_dt = datetime(2026, 4, 15, 9, 30, tzinfo=CHINA_TZ)
    return AlertBar(
        dt=base_dt + timedelta(minutes=5 * index),
        open_price=close_price if open_price is None else open_price,
        high_price=close_price if high_price is None else high_price,
        low_price=close_price if low_price is None else low_price,
        close_price=close_price,
        volume=volume,
    )


class ChartMarkerBuilderTest(unittest.TestCase):
    """验证图表理论买卖点的生成口径。"""

    def test_basic_strategy_only_marks_breakout_state_transition(self) -> None:
        bars = [
            make_bar(0, 9.0, high_price=9.2, low_price=8.8),
            make_bar(1, 9.4, high_price=9.5, low_price=9.1),
            make_bar(2, 10.2, high_price=10.3, low_price=9.8),
            make_bar(3, 10.6, high_price=10.8, low_price=10.1),
        ]
        markers = build_chart_markers(
            BASIC_ALERT_STRATEGY,
            {
                "breakout_price": 10.0,
                "stop_loss_price": 8.0,
                "fast_ma_window": 2,
                "slow_ma_window": 3,
            },
            bars,
        )

        breakout_markers = [marker for marker in markers if marker.rule_name == "breakout"]
        self.assertEqual(1, len(breakout_markers))
        self.assertEqual([bars[2].dt], [marker.dt for marker in breakout_markers])

    def test_basic_strategy_marks_stop_loss_only_on_first_break(self) -> None:
        bars = [
            make_bar(0, 10.0, high_price=10.2, low_price=9.8),
            make_bar(1, 9.7, high_price=9.9, low_price=9.6),
            make_bar(2, 8.4, high_price=8.6, low_price=8.2),
            make_bar(3, 8.2, high_price=8.4, low_price=8.0),
            make_bar(4, 8.8, high_price=9.0, low_price=8.6),
            make_bar(5, 8.3, high_price=8.5, low_price=8.1),
        ]
        markers = build_chart_markers(
            BASIC_ALERT_STRATEGY,
            {
                "breakout_price": 11.0,
                "stop_loss_price": 8.5,
                "fast_ma_window": 2,
                "slow_ma_window": 3,
            },
            bars,
        )

        stop_markers = [marker for marker in markers if marker.rule_name == "stop_loss"]
        self.assertEqual(2, len(stop_markers))
        self.assertEqual([bars[2].dt, bars[5].dt], [marker.dt for marker in stop_markers])

    def test_ma_strategy_only_marks_cross_events(self) -> None:
        bars = [
            make_bar(0, 10.0),
            make_bar(1, 9.0),
            make_bar(2, 8.0),
            make_bar(3, 9.0),
            make_bar(4, 10.0),
            make_bar(5, 9.0),
            make_bar(6, 8.0),
        ]
        markers = build_chart_markers(
            LESSON_A_SHARE_LONG_ONLY,
            {
                "fast_window": 2,
                "slow_window": 3,
            },
            bars,
        )

        self.assertEqual(["golden_cross", "death_cross"], [marker.rule_name for marker in markers])
        self.assertEqual(["buy", "sell"], [marker.direction for marker in markers])

    def test_donchian_strategy_never_exits_before_entry(self) -> None:
        bars = [
            make_bar(0, 10.0, high_price=10.0, low_price=9.0),
            make_bar(1, 11.0, high_price=11.0, low_price=10.0),
            make_bar(2, 12.0, high_price=12.0, low_price=11.0),
            make_bar(3, 13.0, high_price=13.0, low_price=12.0),
            make_bar(4, 10.0, high_price=10.2, low_price=9.0),
        ]
        markers = build_chart_markers(
            LESSON_DONCHIAN,
            {
                "entry_window": 2,
                "exit_window": 2,
            },
            bars,
        )

        self.assertEqual(["donchian_breakout", "donchian_exit"], [marker.rule_name for marker in markers])
        self.assertEqual([bars[2].dt, bars[4].dt], [marker.dt for marker in markers])

    def test_volume_breakout_strategy_requires_entry_before_exit(self) -> None:
        bars = [
            make_bar(0, 10.0, high_price=10.0, low_price=9.0, volume=100.0),
            make_bar(1, 11.0, high_price=11.0, low_price=10.0, volume=100.0),
            make_bar(2, 12.0, high_price=12.0, low_price=11.0, volume=320.0),
            make_bar(3, 13.0, high_price=13.0, low_price=12.0, volume=120.0),
            make_bar(4, 10.0, high_price=10.2, low_price=9.0, volume=110.0),
        ]
        markers = build_chart_markers(
            LESSON_VOLUME_BREAKOUT,
            {
                "breakout_window": 2,
                "exit_window": 2,
                "volume_window": 2,
                "volume_ratio": 1.5,
            },
            bars,
        )

        self.assertEqual(["volume_breakout", "volume_exit"], [marker.rule_name for marker in markers])
        self.assertEqual(["buy", "sell"], [marker.direction for marker in markers])


if __name__ == "__main__":
    unittest.main()
