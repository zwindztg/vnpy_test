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
    apply_pan_delta,
    apply_pan_left,
    apply_pan_right,
    apply_zoom_scale,
    apply_zoom_in,
    build_visible_range_text,
    can_pan_left,
    can_pan_right,
    can_zoom,
    can_zoom_in,
    classify_wheel_intent,
    classify_continuous_zoom_action,
    classify_wheel_navigation,
    build_wheel_device_signature,
    extract_pinch_zoom_factor,
    extract_pinch_zoom_delta,
    infer_wheel_input_kind,
    looks_like_smooth_mouse_wheel,
    get_default_visible_window,
    get_render_window,
    get_available_bars,
    get_available_markers,
    get_reset_visible_window,
    get_view_key,
    merge_marker_bucket,
    should_preserve_wheel_intent,
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

    def test_apply_zoom_scale_supports_continuous_ratio_zoom(self) -> None:
        new_start, new_count = apply_zoom_scale(
            visible_start=10,
            visible_count=20,
            total=100,
            min_visible_bars=1,
            zoom_factor=2.0,
            anchor_ratio=0.5,
        )

        self.assertEqual((15, 10), (new_start, new_count))

        new_start, new_count = apply_zoom_scale(
            visible_start=10,
            visible_count=20,
            total=100,
            min_visible_bars=12,
            zoom_factor=0.5,
            anchor_ratio=0.5,
        )

        self.assertEqual((0, 40), (new_start, new_count))

    def test_drag_pan_direction_and_bounds(self) -> None:
        total = 20
        visible_count = 16

        # 右拖时应查看更早的数据，visible_start 变小。
        self.assertEqual(1.0, apply_drag_pan(4.0, total, visible_count, 3.0))
        # 左拖时应回到更近的新数据区域，visible_start 变大。
        self.assertEqual(4.0, apply_drag_pan(1.0, total, visible_count, -3.0))
        # 越界时需要被裁剪到合法区间。
        self.assertEqual(0.0, apply_drag_pan(2.0, total, visible_count, 10.0))
        self.assertEqual(4.0, apply_drag_pan(2.0, total, visible_count, -10.0))

    def test_apply_pan_delta_supports_sub_bar_trackpad_motion(self) -> None:
        self.assertAlmostEqual(10.25, apply_pan_delta(10.0, 100, 20, 0.25), places=6)
        self.assertAlmostEqual(9.75, apply_pan_delta(10.0, 100, 20, -0.25), places=6)
        self.assertAlmostEqual(0.0, apply_pan_delta(0.1, 100, 20, -1.0), places=6)

    def test_get_render_window_extends_to_partially_visible_bars(self) -> None:
        self.assertEqual((10, 21), get_render_window(100, 10.25, 10))
        self.assertEqual((0, 10), get_render_window(100, 0.0, 10))

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

    def test_build_visible_range_text_outputs_start_and_end(self) -> None:
        snapshot = self.build_snapshot()
        visible_bars = get_available_bars(snapshot, intraday_only=False)

        text = build_visible_range_text(visible_bars[-3:])

        self.assertIn("2026-04-15", text)
        self.assertIn("10:55", text)
        self.assertIn("11:05", text)

    def test_merge_marker_bucket_keeps_one_marker_per_direction(self) -> None:
        snapshot = self.build_snapshot()
        first_dt = snapshot.bars[-1].dt
        markers = [
            ChartMarkerData(dt=first_dt, price=1.0, direction="buy", rule_name="buy_a", message="A"),
            ChartMarkerData(dt=first_dt, price=1.1, direction="buy", rule_name="buy_b", message="B"),
            ChartMarkerData(dt=first_dt, price=1.2, direction="sell", rule_name="sell_a", message="C"),
        ]

        merged = merge_marker_bucket(markers)

        self.assertEqual(["buy", "sell"], [marker.direction for marker in merged])
        self.assertEqual(["buy_a", "sell_a"], [marker.rule_name for marker in merged])

    def test_default_visible_window_uses_snapshot_declared_recent_segment(self) -> None:
        snapshot = ChartSnapshotData(
            config_id="main-1m",
            vt_symbol="601869.SSE",
            strategy_name="BasicAlertStrategy",
            interval="1m",
            data_source="测试数据",
            mode="preview",
            bars=tuple(make_chart_bar(datetime(2026, 4, 15, 9, 30, tzinfo=CHINA_TZ), index, 200 + index) for index in range(20)),
            markers=(),
            reference_time=datetime(2026, 4, 15, 9, 49, tzinfo=CHINA_TZ),
            default_visible_count=12,
        )

        preferred = get_default_visible_window(
            snapshot,
            total=len(snapshot.bars),
        )

        self.assertEqual((8, 12), preferred)

    def test_reset_visible_window_matches_same_snapshot_default_rule(self) -> None:
        snapshot = ChartSnapshotData(
            config_id="detail-1m",
            vt_symbol="601869.SSE",
            strategy_name="BasicAlertStrategy",
            interval="1m",
            data_source="测试数据",
            mode="preview",
            bars=tuple(make_chart_bar(datetime(2026, 4, 15, 9, 30, tzinfo=CHINA_TZ), index, 200 + index) for index in range(20)),
            markers=(),
            reference_time=datetime(2026, 4, 15, 9, 49, tzinfo=CHINA_TZ),
            default_visible_count=12,
        )

        preferred = get_reset_visible_window(
            snapshot,
            interactive=True,
            total=len(snapshot.bars),
            min_visible_bars=12,
        )

        self.assertEqual((8, 12), preferred)

    def test_classify_wheel_navigation_prefers_horizontal_pan_for_trackpad(self) -> None:
        self.assertEqual(
            "pan_right",
            classify_wheel_navigation(pixel_x=36, pixel_y=8, angle_x=0, angle_y=0),
        )
        self.assertEqual(
            "pan_left",
            classify_wheel_navigation(pixel_x=-36, pixel_y=6, angle_x=0, angle_y=0),
        )

    def test_classify_wheel_navigation_keeps_vertical_zoom_for_mouse_wheel(self) -> None:
        self.assertEqual(
            "zoom_in",
            classify_wheel_navigation(pixel_x=0, pixel_y=0, angle_x=0, angle_y=120),
        )
        self.assertEqual(
            "zoom_out",
            classify_wheel_navigation(pixel_x=0, pixel_y=0, angle_x=0, angle_y=-120),
        )

    def test_classify_wheel_navigation_ignores_trackpad_vertical_swipe(self) -> None:
        self.assertIsNone(
            classify_wheel_navigation(pixel_x=0, pixel_y=36, angle_x=0, angle_y=120),
        )
        self.assertIsNone(
            classify_wheel_navigation(pixel_x=4, pixel_y=-40, angle_x=0, angle_y=-120),
        )

    def test_classify_wheel_navigation_ignores_tiny_jitter(self) -> None:
        self.assertIsNone(
            classify_wheel_navigation(pixel_x=1, pixel_y=1, angle_x=0, angle_y=0),
        )

    def test_infer_wheel_input_kind_prefers_qt_device_metadata(self) -> None:
        self.assertEqual(
            "trackpad",
            infer_wheel_input_kind(
                source_name="MouseEventSynthesizedBySystem",
                device_type_name="-",
                pointer_type_name="-",
                pixel_x=0,
                pixel_y=0,
                angle_x=0,
                angle_y=0,
            ),
        )
        self.assertEqual(
            "trackpad",
            infer_wheel_input_kind(
                source_name="MouseEventNotSynthesized",
                device_type_name="TouchPad",
                pointer_type_name="Finger",
                pixel_x=0,
                pixel_y=0,
                angle_x=0,
                angle_y=0,
            ),
        )
        self.assertEqual(
            "mouse",
            infer_wheel_input_kind(
                source_name="MouseEventNotSynthesized",
                device_type_name="Mouse",
                pointer_type_name="Cursor",
                pixel_x=0,
                pixel_y=0,
                angle_x=0,
                angle_y=0,
            ),
        )
        self.assertEqual(
            "mouse",
            infer_wheel_input_kind(
                source_name="MouseEventSynthesizedBySystem",
                device_type_name="TouchPad",
                pointer_type_name="Generic",
                pixel_x=8,
                pixel_y=12,
                angle_x=0,
                angle_y=120,
            ),
        )
        self.assertEqual(
            "trackpad",
            infer_wheel_input_kind(
                source_name="MouseEventNotSynthesized",
                device_type_name="TouchPad",
                pointer_type_name="Finger",
                pixel_x=0,
                pixel_y=24,
                angle_x=0,
                angle_y=0,
            ),
        )

    def test_looks_like_smooth_mouse_wheel_matches_observed_magic_mouse_shape(self) -> None:
        self.assertTrue(
            looks_like_smooth_mouse_wheel(
                pixel_x=0,
                pixel_y=-127,
                angle_x=0,
                angle_y=-254,
                phase_name="NoScrollPhase",
                begin_event=False,
                update_event=False,
                end_event=False,
            )
        )

    def test_looks_like_smooth_mouse_wheel_allows_smaller_logitech_scroll(self) -> None:
        self.assertTrue(
            looks_like_smooth_mouse_wheel(
                pixel_x=2,
                pixel_y=-20,
                angle_x=0,
                angle_y=-36,
                phase_name="NoScrollPhase",
                begin_event=False,
                update_event=False,
                end_event=False,
            )
        )

    def test_classify_wheel_intent_uses_single_path(self) -> None:
        self.assertEqual(
            "trackpad_pan",
            classify_wheel_intent(
                input_kind="trackpad",
                pixel_x=36,
                pixel_y=4,
                angle_x=12,
                angle_y=8,
                phase_name="NoScrollPhase",
                begin_event=False,
                update_event=False,
                end_event=False,
            ),
        )
        self.assertEqual(
            "smooth_wheel_zoom",
            classify_wheel_intent(
                input_kind="trackpad",
                pixel_x=0,
                pixel_y=-127,
                angle_x=0,
                angle_y=-254,
                phase_name="NoScrollPhase",
                begin_event=False,
                update_event=False,
                end_event=False,
            ),
        )
        self.assertEqual(
            "trackpad_ignore",
            classify_wheel_intent(
                input_kind="trackpad",
                pixel_x=0,
                pixel_y=32,
                angle_x=0,
                angle_y=64,
                phase_name="ScrollUpdate",
                begin_event=False,
                update_event=True,
                end_event=False,
            ),
        )

    def test_classify_wheel_intent_does_not_turn_vertical_wheel_noise_into_pan(self) -> None:
        self.assertEqual(
            "smooth_wheel_zoom",
            classify_wheel_intent(
                input_kind="trackpad",
                pixel_x=18,
                pixel_y=-40,
                angle_x=10,
                angle_y=-84,
                phase_name="NoScrollPhase",
                begin_event=False,
                update_event=False,
                end_event=False,
            ),
        )

    def test_wheel_intent_lock_preserves_same_device_briefly(self) -> None:
        signature = build_wheel_device_signature(
            source_name="MouseEventSynthesizedBySystem",
            device_type_name="TouchPad",
            pointer_type_name="Generic",
            device_name="trackpad or magic mouse",
            device_system_id="1",
        )
        self.assertTrue(
            should_preserve_wheel_intent(
                active_intent="trackpad_pan",
                current_intent=None,
                active_signature=signature,
                active_timestamp=10.0,
                current_signature=signature,
                current_timestamp=10.1,
            )
        )
        self.assertFalse(
            should_preserve_wheel_intent(
                active_intent="trackpad_pan",
                current_intent=None,
                active_signature=signature,
                active_timestamp=10.0,
                current_signature=signature,
                current_timestamp=10.3,
            )
        )

    def test_wheel_intent_lock_does_not_let_ignore_override_real_zoom(self) -> None:
        signature = build_wheel_device_signature(
            source_name="MouseEventSynthesizedBySystem",
            device_type_name="TouchPad",
            pointer_type_name="Generic",
            device_name="trackpad or magic mouse",
            device_system_id="1",
        )
        self.assertFalse(
            should_preserve_wheel_intent(
                active_intent="trackpad_ignore",
                current_intent="smooth_wheel_zoom",
                active_signature=signature,
                active_timestamp=10.0,
                current_signature=signature,
                current_timestamp=10.05,
            )
        )
        self.assertTrue(
            should_preserve_wheel_intent(
                active_intent="smooth_wheel_zoom",
                current_intent="trackpad_ignore",
                active_signature=signature,
                active_timestamp=10.0,
                current_signature=signature,
                current_timestamp=10.05,
            )
        )
        self.assertFalse(
            looks_like_smooth_mouse_wheel(
                pixel_x=0,
                pixel_y=-80,
                angle_x=0,
                angle_y=-40,
                phase_name="ScrollUpdate",
                begin_event=False,
                update_event=True,
                end_event=False,
            )
        )

    def test_classify_continuous_zoom_action_requires_threshold(self) -> None:
        self.assertIsNone(classify_continuous_zoom_action(0.02))
        self.assertIsNone(classify_continuous_zoom_action(-0.02))

    def test_classify_continuous_zoom_action_maps_pinch_to_zoom(self) -> None:
        self.assertEqual("zoom_in", classify_continuous_zoom_action(0.06))
        self.assertEqual("zoom_out", classify_continuous_zoom_action(-0.06))

    def test_extract_pinch_zoom_delta_prefers_current_minus_previous(self) -> None:
        class FakeGesture:
            def scaleFactor(self) -> float:
                return 1.18

            def lastScaleFactor(self) -> float:
                return 1.05

        self.assertAlmostEqual(0.13, extract_pinch_zoom_delta(FakeGesture()), places=6)

    def test_extract_pinch_zoom_delta_falls_back_to_total_scale(self) -> None:
        class FakeGesture:
            def totalScaleFactor(self) -> float:
                return 0.84

        self.assertAlmostEqual(-0.16, extract_pinch_zoom_delta(FakeGesture()), places=6)

    def test_extract_pinch_zoom_factor_prefers_total_scale(self) -> None:
        class FakeGesture:
            def totalScaleFactor(self) -> float:
                return 1.36

            def scaleFactor(self) -> float:
                return 1.12

        self.assertAlmostEqual(1.36, extract_pinch_zoom_factor(FakeGesture()), places=6)


if __name__ == "__main__":
    unittest.main()
