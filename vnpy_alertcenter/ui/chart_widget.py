"""CTA 实时监控中心的轻量 K线图控件。"""

from __future__ import annotations

from collections import defaultdict
from math import isclose
from time import monotonic

from vnpy.trader.ui import QtCore, QtGui, QtWidgets

from ..core import (
    ChartBarData,
    ChartMarkerData,
    ChartSnapshotData,
    format_a_share_volume_axis_value,
    format_a_share_volume_value,
    get_strategy_display_name,
)
from .chart_view import (
    apply_drag_pan,
    apply_pan_delta,
    apply_pan_left,
    apply_pan_right,
    apply_zoom_scale,
    apply_zoom_in,
    apply_zoom_out,
    can_pan_left,
    can_pan_right,
    can_zoom,
    can_zoom_in,
    can_zoom_out,
    classify_wheel_intent,
    classify_continuous_zoom_action,
    extract_pinch_zoom_factor,
    extract_pinch_zoom_delta,
    build_wheel_device_signature,
    infer_wheel_input_kind,
    looks_like_smooth_mouse_wheel,
    build_visible_range_text,
    get_default_visible_window,
    get_reset_visible_window,
    get_available_bars,
    get_available_markers,
    get_render_window,
    get_right_offset,
    get_view_key,
    merge_marker_bucket,
    should_preserve_wheel_intent,
    sync_view_state,
)


class AlertChartWidget(QtWidgets.QWidget):
    """在提醒中心右侧显示最近一段 K线和提醒标记。"""

    view_state_changed: QtCore.Signal = QtCore.Signal()
    interaction_debug_changed: QtCore.Signal = QtCore.Signal(str)
    raw_event_debug_changed: QtCore.Signal = QtCore.Signal(str)

    TIME_AXIS_HEIGHT = 34.0
    TITLE_HEIGHT = 42.0
    TITLE_BOTTOM_GAP = 12.0
    MIN_VISIBLE_BARS = 12
    VOLUME_PANEL_MIN_HEIGHT = 96.0
    VOLUME_INFO_HEIGHT = 24.0
    VOLUME_PANEL_RATIO = 0.30
    PRICE_PANEL_MIN_HEIGHT = 180.0
    PANEL_RADIUS = 12.0
    PANEL_GAP = 14.0
    PANEL_PADDING_X = 16.0
    PANEL_PADDING_TOP = 14.0
    PANEL_PADDING_BOTTOM = 12.0
    Y_AXIS_LABEL_WIDTH = 68.0
    TRACKPAD_PAN_SPEED = 0.35

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        interactive: bool = False,
        intraday_only: bool = False,
        show_volume_panel: bool = False,
    ) -> None:
        super().__init__(parent)
        self.snapshot: ChartSnapshotData | None = None
        self.placeholder_text: str = "暂无图表数据，请先执行单次测试或启动监控"
        self.interactive = interactive
        self.intraday_only = intraday_only
        self.show_volume_panel = show_volume_panel
        self.visible_start: float = 0.0
        self.visible_count: int = 0
        self.dragging: bool = False
        self.drag_origin_x: float = 0.0
        self.drag_origin_start: float = 0.0
        self.gesture_zoom_base_start: float | None = None
        self.gesture_zoom_base_count: int | None = None
        self.gesture_zoom_anchor_ratio: float = 0.5
        self.native_zoom_factor: float = 1.0
        self.last_interaction_debug: str = "最近输入：等待手势"
        self.last_raw_event_debug: str = "原始事件：等待输入"
        self.active_wheel_intent: str | None = None
        self.active_wheel_signature: str = ""
        self.active_wheel_timestamp: float = 0.0
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding,
        )
        if self.interactive:
            # K线图详情窗口保持大画布，避免被主界面的紧凑尺寸限制误伤。
            self.setMinimumHeight(420)
            self.setMouseTracking(True)
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            try:
                self.setAttribute(QtCore.Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
            except Exception:
                pass
            pinch_gesture = getattr(getattr(QtCore.Qt, "GestureType", object), "PinchGesture", None)
            if pinch_gesture is not None:
                try:
                    self.grabGesture(pinch_gesture)
                except Exception:
                    pass
        else:
            # 主界面里的嵌入图表只保留最小高度保护，高度交给 splitter 自己分配。
            self.setMinimumHeight(120)

    def clear_snapshot(self, message: str | None = None) -> None:
        """清空当前图表快照，并显示占位提示。"""
        self.snapshot = None
        self.visible_start = 0.0
        self.visible_count = 0
        self.dragging = False
        self.reset_gesture_zoom_state()
        self.reset_wheel_intent_state()
        self.set_interaction_debug("最近输入：等待手势")
        self.set_raw_event_debug("原始事件：等待输入")
        if message:
            self.placeholder_text = message
        self.refresh_interaction_cursor()
        self.update()
        self.view_state_changed.emit()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:      # noqa: N802
        """按住左键后进入抓取拖动模式，方便左右平移。"""
        if (
            self.interactive
            and self.can_zoom()
            and event.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            self.dragging = True
            self.drag_origin_x = event.position().x()
            self.drag_origin_start = self.visible_start
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:      # noqa: N802
        """拖动时按水平位移换算成 bar 平移，未拖动时显示抓手。"""
        if self.dragging and self.can_zoom():
            total = len(self.get_available_bars())
            if total > 0 and self.visible_count > 0:
                pixels_per_bar = self.get_pixels_per_bar()
                delta_x = event.position().x() - self.drag_origin_x
                bar_shift = delta_x / pixels_per_bar
                new_start = apply_drag_pan(
                    self.drag_origin_start,
                    total,
                    self.visible_count,
                    bar_shift,
                )
                if new_start != self.visible_start:
                    self.visible_start = new_start
                    self.update()
                    self.view_state_changed.emit()
            event.accept()
            return

        if self.interactive:
            self.refresh_interaction_cursor()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:      # noqa: N802
        """结束抓取拖动，恢复打开手型。"""
        if self.dragging and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.dragging = False
            self.refresh_interaction_cursor()
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def set_snapshot(self, snapshot: ChartSnapshotData) -> None:
        """替换当前图表快照，并尽量保留现有缩放/平移状态。"""
        previous_key = self.get_view_key(self.snapshot)
        previous_total = len(self.get_available_bars())
        previous_right_offset = self.get_right_offset(previous_total)

        self.snapshot = snapshot
        current_key = self.get_view_key(snapshot)
        should_reset = previous_key != current_key
        self.sync_view_state(reset=should_reset, right_offset=None if should_reset else previous_right_offset)
        self.dragging = False
        self.reset_gesture_zoom_state()
        self.reset_wheel_intent_state()
        self.refresh_interaction_cursor()
        self.update()
        self.view_state_changed.emit()

    def event(self, event: QtCore.QEvent) -> bool:      # noqa: N802
        """优先处理 mac 触摸板捏合手势，再交给 QWidget 默认分发。"""
        if self.handle_pinch_gesture(event):
            return True
        if self.handle_native_zoom_gesture(event):
            return True
        return super().event(event)

    def handle_pinch_gesture(self, event: QtCore.QEvent) -> bool:
        """处理 Qt Gesture 路径上的双指捏合。"""
        if not self.can_zoom():
            return False

        gesture_event_type = getattr(QtCore.QEvent.Type, "Gesture", None)
        gesture_override_type = getattr(QtCore.QEvent.Type, "GestureOverride", None)
        if event.type() not in {gesture_event_type, gesture_override_type}:
            return False

        pinch_gesture_type = getattr(getattr(QtCore.Qt, "GestureType", object), "PinchGesture", None)
        gesture_getter = getattr(event, "gesture", None)
        if pinch_gesture_type is None or not callable(gesture_getter):
            return False

        try:
            pinch_gesture = gesture_getter(pinch_gesture_type)
        except Exception:
            return False
        if pinch_gesture is None:
            return False

        if gesture_override_type is not None and event.type() == gesture_override_type:
            self.begin_gesture_zoom(anchor_ratio=self.get_pinch_anchor_ratio(pinch_gesture))
            self.set_raw_event_debug(self.describe_pinch_gesture_event(pinch_gesture, event_type="GestureOverride"))
            accept_method = getattr(event, "accept", None)
            if callable(accept_method):
                try:
                    accept_method(pinch_gesture)
                except TypeError:
                    accept_method()
            self.set_interaction_debug("最近输入：检测到双指捏合，等待缩放增量")
            return True

        zoom_factor = extract_pinch_zoom_factor(pinch_gesture)
        if zoom_factor is None:
            return False

        self.set_raw_event_debug(self.describe_pinch_gesture_event(pinch_gesture, event_type="Gesture"))
        self.begin_gesture_zoom(anchor_ratio=self.get_pinch_anchor_ratio(pinch_gesture))
        self.apply_continuous_zoom(
            zoom_factor,
            debug_prefix="最近输入：双指捏合缩放",
        )

        gesture_state_getter = getattr(pinch_gesture, "state", None)
        finished_state = getattr(getattr(QtCore.Qt, "GestureState", object), "GestureFinished", None)
        canceled_state = getattr(getattr(QtCore.Qt, "GestureState", object), "GestureCanceled", None)
        if callable(gesture_state_getter):
            try:
                gesture_state = gesture_state_getter()
                if gesture_state in {finished_state, canceled_state}:
                    self.reset_gesture_zoom_state()
                    self.set_interaction_debug("最近输入：双指捏合结束")
            except Exception:
                pass

        event.accept()
        return True

    def handle_native_zoom_gesture(self, event: QtCore.QEvent) -> bool:
        """把 mac 触摸板双指捏合映射成图表放大/缩小。"""
        if not self.can_zoom():
            return False

        native_gesture_type = getattr(QtCore.QEvent.Type, "NativeGesture", None)
        if native_gesture_type is None or event.type() != native_gesture_type:
            return False

        native_gesture_enum = getattr(QtCore.Qt, "NativeGestureType", object)
        begin_native_type = getattr(native_gesture_enum, "BeginNativeGesture", None)
        end_native_type = getattr(native_gesture_enum, "EndNativeGesture", None)
        native_zoom_type = getattr(native_gesture_enum, "ZoomNativeGesture", None)
        gesture_type_getter = getattr(event, "gestureType", None)
        if native_zoom_type is not None and callable(gesture_type_getter):
            try:
                gesture_type = gesture_type_getter()
                if gesture_type == begin_native_type:
                    self.set_raw_event_debug(self.describe_native_gesture_event(event))
                    self.begin_gesture_zoom(anchor_ratio=self.get_event_anchor_ratio(event))
                    self.set_interaction_debug("最近输入：mac 原生捏合开始")
                    event.accept()
                    return True
                if gesture_type == end_native_type:
                    self.set_raw_event_debug(self.describe_native_gesture_event(event))
                    self.reset_gesture_zoom_state()
                    self.set_interaction_debug("最近输入：mac 原生捏合结束")
                    event.accept()
                    return True
                if gesture_type != native_zoom_type:
                    return False
            except Exception:
                return False

        value_getter = getattr(event, "value", None)
        if not callable(value_getter):
            return False

        try:
            value = float(value_getter())
        except Exception:
            return False

        self.set_raw_event_debug(self.describe_native_gesture_event(event))
        self.begin_gesture_zoom(anchor_ratio=self.get_event_anchor_ratio(event))
        self.native_zoom_factor *= max(0.2, 1.0 + value)
        self.apply_continuous_zoom(
            self.native_zoom_factor,
            debug_prefix="最近输入：mac 原生捏合缩放",
        )

        event.accept()
        return True

    def reset_view(self) -> None:
        """把可见范围恢复为默认视图。"""
        self.sync_view_state(reset=True)
        self.update()
        self.view_state_changed.emit()

    def zoom_in(self) -> None:
        """缩小可见 K线数量，放大当天局部区域。"""
        if not self.can_zoom_in():
            return

        total = len(self.get_available_bars())
        self.visible_start, self.visible_count = apply_zoom_in(
            self.visible_start,
            self.visible_count,
            total,
            self.MIN_VISIBLE_BARS,
        )
        self.update()
        self.view_state_changed.emit()

    def zoom_out(self) -> None:
        """扩大可见 K线数量，缩小当前局部区域。"""
        if not self.can_zoom_out():
            return

        total = len(self.get_available_bars())
        self.visible_start, self.visible_count = apply_zoom_out(
            self.visible_start,
            self.visible_count,
            total,
            self.MIN_VISIBLE_BARS,
        )
        self.update()
        self.view_state_changed.emit()

    def pan_left(self) -> None:
        """把可视窗口向左平移，查看当天更早的 K线。"""
        if not self.can_pan_left():
            return

        self.visible_start = apply_pan_left(self.visible_start, self.visible_count)
        self.update()
        self.view_state_changed.emit()

    def pan_right(self) -> None:
        """把可视窗口向右平移，回到更新的 K线区域。"""
        if not self.can_pan_right():
            return

        total = len(self.get_available_bars())
        self.visible_start = apply_pan_right(self.visible_start, self.visible_count, total)
        self.update()
        self.view_state_changed.emit()

    def get_pixels_per_bar(self) -> float:
        """估算当前一根 K线对应的像素宽度，供连续平移和拖动复用。"""
        visible_slots = max(self.visible_count, 1)
        outer_width = max(float(self.width()) - 28.0, 1.0)
        inner_width = max(outer_width - self.PANEL_PADDING_X * 2 - self.Y_AXIS_LABEL_WIDTH, 1.0)
        return max(inner_width / visible_slots, 1.0)

    def can_zoom(self) -> bool:
        """判断当前是否允许缩放。"""
        return can_zoom(self.interactive, self.snapshot, len(self.get_available_bars()))

    def can_zoom_in(self) -> bool:
        """判断当前是否还能继续放大。"""
        return can_zoom_in(self.can_zoom(), self.visible_count, len(self.get_available_bars()), self.MIN_VISIBLE_BARS)

    def can_zoom_out(self) -> bool:
        """判断当前是否还能继续缩小。"""
        return can_zoom_out(self.can_zoom(), self.visible_count, len(self.get_available_bars()))

    def can_pan_left(self) -> bool:
        """判断当前是否还能向左平移。"""
        return can_pan_left(self.can_zoom(), self.visible_start)

    def can_pan_right(self) -> bool:
        """判断当前是否还能向右平移。"""
        return can_pan_right(self.can_zoom(), self.visible_start, self.visible_count, len(self.get_available_bars()))

    def get_available_bars(self) -> list[ChartBarData]:
        """返回当前图表允许浏览的完整 bar 集合。"""
        return get_available_bars(self.snapshot, self.intraday_only)

    def get_available_markers(self) -> list[ChartMarkerData]:
        """返回当前图表允许浏览的完整标记集合。"""
        return get_available_markers(self.snapshot, self.intraday_only)

    def get_visible_bars(self) -> tuple[ChartBarData, ...]:
        """返回当前窗口真正要绘制的 bar 集合。"""
        bars = self.get_available_bars()
        if not bars:
            return ()
        if not self.can_zoom():
            default_window = get_default_visible_window(self.snapshot, total=len(bars))
            if default_window is not None:
                start_index, visible_count = default_window
                return tuple(bars[start_index:start_index + visible_count])
            return tuple(bars)

        start_index, end_index = self.get_render_window(len(bars))
        return tuple(bars[start_index:end_index])

    def get_visible_markers(self) -> tuple[ChartMarkerData, ...]:
        """返回当前窗口真正要绘制的标记集合。"""
        markers = self.get_available_markers()
        if not markers:
            return ()

        visible_dt = {bar.dt for bar in self.get_visible_bars()}
        return tuple(marker for marker in markers if marker.dt in visible_dt)

    def get_view_key(self, snapshot: ChartSnapshotData | None) -> tuple[str, str, object] | None:
        """生成判断是否需要重置视图的关键字。"""
        return get_view_key(snapshot, self.intraday_only)

    def get_right_offset(self, total: int) -> int:
        """记录当前窗口距离最右侧的偏移，方便快照刷新后尽量保持位置。"""
        return get_right_offset(total, self.visible_start, self.visible_count)

    def get_render_window(self, total: int | None = None) -> tuple[int, int]:
        """返回当前绘图需要覆盖的 bar 索引窗口。"""
        available_total = len(self.get_available_bars()) if total is None else total
        return get_render_window(available_total, self.visible_start, self.visible_count)

    def sync_view_state(self, reset: bool = False, right_offset: int | None = None) -> None:
        """根据最新快照和当前交互状态归一化可见区间。"""
        total = len(self.get_available_bars())
        if reset:
            preferred_window = get_reset_visible_window(
                self.snapshot,
                interactive=self.interactive,
                total=total,
                min_visible_bars=self.MIN_VISIBLE_BARS,
            )
            if preferred_window is not None:
                self.visible_start, self.visible_count = preferred_window
                return

        self.visible_start, self.visible_count = sync_view_state(
            total=total,
            visible_start=self.visible_start,
            visible_count=self.visible_count,
            reset=reset,
            zoom_enabled=self.can_zoom(),
            min_visible_bars=self.MIN_VISIBLE_BARS,
            right_offset=right_offset,
        )

    def refresh_interaction_cursor(self) -> None:
        """按当前交互能力刷新鼠标手型。"""
        if not self.interactive:
            return
        if self.dragging and self.can_zoom():
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        elif self.can_zoom():
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(QtCore.Qt.CursorShape.ArrowCursor)

    def set_interaction_debug(self, message: str) -> None:
        """记录最近一次交互输入，方便在详情窗口内直接诊断手势来源。"""
        if message == self.last_interaction_debug:
            return
        self.last_interaction_debug = message
        self.interaction_debug_changed.emit(message)

    def set_raw_event_debug(self, message: str) -> None:
        """记录最近一次原始事件画像，帮助判断 Qt 到底上报了什么。"""
        if message == self.last_raw_event_debug:
            return
        self.last_raw_event_debug = message
        self.raw_event_debug_changed.emit(message)

    def reset_gesture_zoom_state(self) -> None:
        """清空当前这次连续缩放手势的基线状态。"""
        self.gesture_zoom_base_start = None
        self.gesture_zoom_base_count = None
        self.gesture_zoom_anchor_ratio = 0.5
        self.native_zoom_factor = 1.0

    def reset_wheel_intent_state(self) -> None:
        """清空当前这一轮 wheel 输入已经锁定的意图。"""
        self.active_wheel_intent = None
        self.active_wheel_signature = ""
        self.active_wheel_timestamp = 0.0

    def begin_gesture_zoom(self, anchor_ratio: float = 0.5) -> None:
        """记录当前连续缩放手势的起始视窗，只在手势开始时设置一次。"""
        if self.gesture_zoom_base_start is not None and self.gesture_zoom_base_count is not None:
            return
        if self.visible_count <= 0:
            self.sync_view_state(reset=False)
        total = len(self.get_available_bars())
        if total <= 0:
            return
        self.gesture_zoom_base_start = self.visible_start
        self.gesture_zoom_base_count = self.visible_count if self.visible_count > 0 else total
        self.gesture_zoom_anchor_ratio = min(max(anchor_ratio, 0.0), 1.0)
        self.native_zoom_factor = 1.0

    def get_anchor_ratio_from_x(self, x_value: float | None) -> float:
        """把事件横坐标映射成 0~1 的缩放锚点比例。"""
        if x_value is None or self.width() <= 1:
            return 0.5
        return min(max(float(x_value) / float(self.width()), 0.0), 1.0)

    def get_event_anchor_ratio(self, event: QtCore.QEvent) -> float:
        """从原生事件位置推断缩放锚点。"""
        position_getter = getattr(event, "position", None)
        if not callable(position_getter):
            return 0.5
        try:
            position = position_getter()
        except Exception:
            return 0.5
        x_getter = getattr(position, "x", None)
        return self.get_anchor_ratio_from_x(x_getter() if callable(x_getter) else None)

    def get_pinch_anchor_ratio(self, pinch_gesture) -> float:
        """优先使用 pinch 中心点作为当前缩放锚点。"""
        center_getter = getattr(pinch_gesture, "centerPoint", None)
        if callable(center_getter):
            try:
                center_point = center_getter()
                x_getter = getattr(center_point, "x", None)
                return self.get_anchor_ratio_from_x(x_getter() if callable(x_getter) else None)
            except Exception:
                pass
        return 0.5

    def apply_continuous_zoom(self, zoom_factor: float, *, debug_prefix: str) -> None:
        """按手势总缩放比例连续调整可视窗口，而不是固定跳档。"""
        if zoom_factor <= 0:
            return
        total = len(self.get_available_bars())
        if total <= 0:
            return
        if self.gesture_zoom_base_start is None or self.gesture_zoom_base_count is None:
            self.begin_gesture_zoom()
        if self.gesture_zoom_base_start is None or self.gesture_zoom_base_count is None:
            return

        new_start, new_count = apply_zoom_scale(
            self.gesture_zoom_base_start,
            self.gesture_zoom_base_count,
            total,
            self.MIN_VISIBLE_BARS,
            zoom_factor,
            anchor_ratio=self.gesture_zoom_anchor_ratio,
        )
        changed = (new_start, new_count) != (self.visible_start, self.visible_count)
        self.visible_start, self.visible_count = new_start, new_count
        self.set_interaction_debug(
            f"{debug_prefix}，比例={zoom_factor:.3f}，显示K线={self.visible_count}"
        )
        if changed:
            self.update()
            self.view_state_changed.emit()

    def get_wheel_event_source_name(self, event: QtGui.QWheelEvent) -> str:
        """读取 QWheelEvent 的 source 名称，便于区分系统合成事件。"""
        source_getter = getattr(event, "source", None)
        if not callable(source_getter):
            return "-"
        try:
            source = source_getter()
        except Exception:
            return "-"
        return getattr(source, "name", str(source))

    def get_wheel_event_device_type_name(self, event: QtGui.QWheelEvent) -> str:
        """读取 QWheelEvent 的设备类型名称。"""
        device = self.get_wheel_event_device(event)
        if device is None:
            return "-"
        type_getter = getattr(device, "type", None)
        if not callable(type_getter):
            return "-"
        try:
            device_type = type_getter()
        except Exception:
            return "-"
        return getattr(device_type, "name", str(device_type))

    def get_wheel_event_pointer_type_name(self, event: QtGui.QWheelEvent) -> str:
        """读取 QWheelEvent 的 pointing pointer 类型名称。"""
        device = self.get_wheel_event_device(event)
        if device is None:
            return "-"
        pointer_getter = getattr(device, "pointerType", None)
        if not callable(pointer_getter):
            return "-"
        try:
            pointer_type = pointer_getter()
        except Exception:
            return "-"
        return getattr(pointer_type, "name", str(pointer_type))

    def get_wheel_event_device(self, event: QtGui.QWheelEvent):
        """统一读取 wheel 事件关联的 pointing device。"""
        for getter_name in ("pointingDevice", "device"):
            getter = getattr(event, getter_name, None)
            if not callable(getter):
                continue
            try:
                device = getter()
            except Exception:
                continue
            if device is not None:
                return device
        return None

    def get_wheel_event_device_name(self, event: QtGui.QWheelEvent) -> str:
        """读取设备名称，帮助区分内建触摸板和外接鼠标。"""
        device = self.get_wheel_event_device(event)
        if device is None:
            return "-"
        name_getter = getattr(device, "name", None)
        if not callable(name_getter):
            return "-"
        try:
            name_value = name_getter()
        except Exception:
            return "-"
        return str(name_value or "-")

    def get_wheel_event_device_system_id(self, event: QtGui.QWheelEvent) -> str:
        """读取设备 systemId，便于确认 Qt 是否真的把两类设备混成同一个对象。"""
        device = self.get_wheel_event_device(event)
        if device is None:
            return "-"
        system_id_getter = getattr(device, "systemId", None)
        if not callable(system_id_getter):
            return "-"
        try:
            return str(system_id_getter())
        except Exception:
            return "-"

    def get_wheel_event_device_capabilities(self, event: QtGui.QWheelEvent) -> str:
        """读取设备 capability 标记，方便判断是否具备多点/滚动等特征。"""
        device = self.get_wheel_event_device(event)
        if device is None:
            return "-"
        capabilities_getter = getattr(device, "capabilities", None)
        if not callable(capabilities_getter):
            return "-"
        try:
            capabilities = capabilities_getter()
        except Exception:
            return "-"
        return getattr(capabilities, "name", str(capabilities))

    def get_event_phase_name(self, event: QtCore.QEvent) -> str:
        """读取滚轮/手势事件阶段名称。"""
        phase_getter = getattr(event, "phase", None)
        if not callable(phase_getter):
            return "-"
        try:
            phase = phase_getter()
        except Exception:
            return "-"
        return getattr(phase, "name", str(phase))

    def describe_wheel_event(
        self,
        event: QtGui.QWheelEvent,
        *,
        source_name: str,
        device_type_name: str,
        pointer_type_name: str,
        input_kind: str,
    ) -> str:
        """格式化滚轮事件原始画像，方便区分鼠标和触摸板。"""
        pixel_delta = event.pixelDelta()
        angle_delta = event.angleDelta()
        begin_event = getattr(event, "isBeginEvent", lambda: False)()
        update_event = getattr(event, "isUpdateEvent", lambda: False)()
        end_event = getattr(event, "isEndEvent", lambda: False)()
        device_name = self.get_wheel_event_device_name(event)
        device_system_id = self.get_wheel_event_device_system_id(event)
        device_capabilities = self.get_wheel_event_device_capabilities(event)
        return (
            "原始滚轮："
            f"kind={input_kind}; "
            f"pixel=({pixel_delta.x()},{pixel_delta.y()}); "
            f"angle=({angle_delta.x()},{angle_delta.y()}); "
            f"phase={self.get_event_phase_name(event)}; "
            f"begin={int(bool(begin_event))}; "
            f"update={int(bool(update_event))}; "
            f"end={int(bool(end_event))}; "
            f"source={source_name}; "
            f"device={device_type_name}; "
            f"pointer={pointer_type_name}; "
            f"name={device_name}; "
            f"systemId={device_system_id}; "
            f"caps={device_capabilities}"
        )

    def describe_native_gesture_event(self, event: QtCore.QEvent) -> str:
        """格式化 mac 原生手势事件原始信息。"""
        gesture_type_getter = getattr(event, "gestureType", None)
        value_getter = getattr(event, "value", None)
        gesture_type_name = "-"
        value_text = "-"
        if callable(gesture_type_getter):
            try:
                gesture_type = gesture_type_getter()
                gesture_type_name = getattr(gesture_type, "name", str(gesture_type))
            except Exception:
                pass
        if callable(value_getter):
            try:
                value_text = f"{float(value_getter()):+.4f}"
            except Exception:
                pass
        return (
            "原始原生手势："
            f"type={gesture_type_name}; "
            f"value={value_text}; "
            f"phase={self.get_event_phase_name(event)}"
        )

    def describe_pinch_gesture_event(self, pinch_gesture, *, event_type: str) -> str:
        """格式化 PinchGesture 的原始比例信息。"""
        state_getter = getattr(pinch_gesture, "state", None)
        scale_getter = getattr(pinch_gesture, "scaleFactor", None)
        last_scale_getter = getattr(pinch_gesture, "lastScaleFactor", None)
        total_scale_getter = getattr(pinch_gesture, "totalScaleFactor", None)
        state_name = "-"
        scale_text = "-"
        last_scale_text = "-"
        total_scale_text = "-"
        if callable(state_getter):
            try:
                state = state_getter()
                state_name = getattr(state, "name", str(state))
            except Exception:
                pass
        if callable(scale_getter):
            try:
                scale_text = f"{float(scale_getter()):.4f}"
            except Exception:
                pass
        if callable(last_scale_getter):
            try:
                last_scale_text = f"{float(last_scale_getter()):.4f}"
            except Exception:
                pass
        if callable(total_scale_getter):
            try:
                total_scale_text = f"{float(total_scale_getter()):.4f}"
            except Exception:
                pass
        return (
            "原始捏合："
            f"event={event_type}; "
            f"state={state_name}; "
            f"scale={scale_text}; "
            f"last={last_scale_text}; "
            f"total={total_scale_text}"
        )

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:      # noqa: N802
        """在弹窗里把触摸板和鼠标滚轮拆开处理。

        交互约定：
        - 触摸板左右滑：平移
        - 触摸板上下滑：不再触发缩放，避免抢占双指捏合语义
        - 鼠标滚轮：继续负责缩放
        """
        if not self.can_zoom():
            event.ignore()
            return

        pixel_delta = event.pixelDelta()
        angle_delta = event.angleDelta()
        source_name = self.get_wheel_event_source_name(event)
        device_type_name = self.get_wheel_event_device_type_name(event)
        pointer_type_name = self.get_wheel_event_pointer_type_name(event)
        phase_name = self.get_event_phase_name(event)
        begin_event = getattr(event, "isBeginEvent", lambda: False)()
        update_event = getattr(event, "isUpdateEvent", lambda: False)()
        end_event = getattr(event, "isEndEvent", lambda: False)()
        device_name = self.get_wheel_event_device_name(event)
        device_system_id = self.get_wheel_event_device_system_id(event)
        input_kind = infer_wheel_input_kind(
            source_name=source_name,
            device_type_name=device_type_name,
            pointer_type_name=pointer_type_name,
            pixel_x=pixel_delta.x(),
            pixel_y=pixel_delta.y(),
            angle_x=angle_delta.x(),
            angle_y=angle_delta.y(),
        )
        self.set_raw_event_debug(
            self.describe_wheel_event(
                event,
                source_name=source_name,
                device_type_name=device_type_name,
                pointer_type_name=pointer_type_name,
                input_kind=input_kind,
            )
        )
        current_signature = build_wheel_device_signature(
            source_name=source_name,
            device_type_name=device_type_name,
            pointer_type_name=pointer_type_name,
            device_name=device_name,
            device_system_id=device_system_id,
        )
        current_timestamp = monotonic()
        classified_intent = classify_wheel_intent(
            input_kind=input_kind,
            pixel_x=pixel_delta.x(),
            pixel_y=pixel_delta.y(),
            angle_x=angle_delta.x(),
            angle_y=angle_delta.y(),
            phase_name=phase_name,
            begin_event=bool(begin_event),
            update_event=bool(update_event),
            end_event=bool(end_event),
        )
        if should_preserve_wheel_intent(
            active_intent=self.active_wheel_intent,
            current_intent=classified_intent,
            active_signature=self.active_wheel_signature,
            active_timestamp=self.active_wheel_timestamp,
            current_signature=current_signature,
            current_timestamp=current_timestamp,
        ):
            intent = self.active_wheel_intent
        else:
            intent = classified_intent
            if intent is None:
                self.reset_wheel_intent_state()
            else:
                self.active_wheel_intent = intent
                self.active_wheel_signature = current_signature
                self.active_wheel_timestamp = current_timestamp

        if intent == "smooth_wheel_zoom":
            self.reset_gesture_zoom_state()
            self.begin_gesture_zoom(anchor_ratio=0.5)
            smooth_zoom_factor = 1.0 + min(abs(pixel_delta.y()) / 240.0, 0.8)
            if pixel_delta.y() < 0:
                smooth_zoom_factor = 1.0 / smooth_zoom_factor
            self.apply_continuous_zoom(
                smooth_zoom_factor,
                debug_prefix="最近输入：平滑滚轮缩放",
            )
            self.reset_gesture_zoom_state()
            self.active_wheel_timestamp = current_timestamp
            event.accept()
            return
        if intent == "mouse_wheel_zoom" and angle_delta.y():
            if angle_delta.y() > 0:
                self.zoom_in()
                self.set_interaction_debug(
                    f"最近输入：鼠标滚轮，图表放大（source={source_name}, device={device_type_name}）"
                )
            else:
                self.zoom_out()
                self.set_interaction_debug(
                    f"最近输入：鼠标滚轮，图表缩小（source={source_name}, device={device_type_name}）"
                )
            self.active_wheel_timestamp = current_timestamp
            event.accept()
            return
        if intent == "trackpad_pan":
            total = len(self.get_available_bars())
            if total > 0 and self.visible_count > 0:
                # 触摸板横向滑动只保留连续平移一条路径，不再落到按钮式跳档平移。
                delta_bars = (pixel_delta.x() / self.get_pixels_per_bar()) * self.TRACKPAD_PAN_SPEED
                new_start = apply_pan_delta(self.visible_start, total, self.visible_count, delta_bars)
                if not isclose(new_start, self.visible_start, abs_tol=1e-6):
                    self.visible_start = new_start
                    self.update()
                    self.view_state_changed.emit()
            if pixel_delta.x() > 0:
                direction_text = "右移"
            else:
                direction_text = "左移"
            self.set_interaction_debug(
                (
                    f"最近输入：触摸板连续平移，图表{direction_text}"
                    f"（Δx={pixel_delta.x():+d}, start={self.visible_start:.2f}）"
                )
            )
            self.active_wheel_timestamp = current_timestamp
            event.accept()
            return
        if intent == "trackpad_ignore":
            self.set_interaction_debug(
                (
                    "最近输入：触摸板滚动已忽略，缩放请用双指捏合"
                    f"（source={source_name}, device={device_type_name}, pointer={pointer_type_name}）"
                )
            )
            self.active_wheel_timestamp = current_timestamp
            event.accept()
            return
        event.ignore()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:      # noqa: N802
        """用 QPainter 画出最简版 K线、买卖点、成交量和时间轴。"""
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        outer_rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(outer_rect, QtGui.QColor("#0b1622"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#2a4157"), 1))
        painter.drawRoundedRect(QtCore.QRectF(outer_rect), 12, 12)

        visible_bars = list(self.get_visible_bars())
        if not self.snapshot or not visible_bars:
            self.draw_placeholder(painter, outer_rect)
            return

        title_rect = QtCore.QRectF(outer_rect.left() + 12, outer_rect.top() + 10, outer_rect.width() - 24, 26)
        title_rect = QtCore.QRectF(
            outer_rect.left() + 12,
            outer_rect.top() + 10,
            outer_rect.width() - 24,
            self.TITLE_HEIGHT,
        )
        plot_rect = QtCore.QRectF(
            outer_rect.left() + 14,
            title_rect.bottom() + self.TITLE_BOTTOM_GAP,
            outer_rect.width() - 28,
            outer_rect.bottom() - (title_rect.bottom() + self.TITLE_BOTTOM_GAP) - 10,
        )
        if plot_rect.width() <= 40 or plot_rect.height() <= 40:
            self.draw_placeholder(painter, outer_rect)
            return

        self.draw_title(painter, title_rect)
        self.draw_plot(painter, plot_rect, visible_bars, list(self.get_visible_markers()))

    def draw_placeholder(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        """在空态时显示提示文本。"""
        painter.setPen(QtGui.QColor("#8ea2b8"))
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), self.placeholder_text)

    def draw_title(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """绘制图表顶部信息条。"""
        assert self.snapshot is not None
        visible_range_text = build_visible_range_text(list(self.get_visible_bars()))
        mode_text = "实时运行" if self.snapshot.mode == "live" else "单次测试"
        title = (
            f"{self.snapshot.vt_symbol}  |  "
            f"{get_strategy_display_name(self.snapshot.strategy_name)}  |  "
            f"{self.snapshot.interval}  |  "
            f"{self.snapshot.data_source}  |  "
            f"{mode_text}"
        )
        painter.setPen(QtGui.QColor("#e5edf7"))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        main_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.52)
        painter.drawText(
            main_rect,
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            title,
        )

        detail_font = QtGui.QFont(font)
        detail_font.setPointSize(8)
        detail_font.setBold(False)
        painter.setFont(detail_font)
        painter.setPen(QtGui.QColor("#8ea2b8"))
        painter.drawText(
            QtCore.QRectF(rect.left(), rect.top() + rect.height() * 0.5, rect.width(), rect.height() * 0.45),
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            f"当前显示区间：{visible_range_text}",
        )

    def draw_plot(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        bars: list[ChartBarData],
        markers: list[ChartMarkerData],
    ) -> None:
        """绘制价格主图、成交量副图和底部时间轴。"""
        axis_height = min(self.TIME_AXIS_HEIGHT, max(22.0, rect.height() * 0.16))
        if self.show_volume_panel:
            axis_height = min(self.TIME_AXIS_HEIGHT, max(24.0, rect.height() * 0.12))
            available_height = max(0.0, rect.height() - axis_height - self.PANEL_GAP)
            # 成交量副图按比例随窗口一起放大，避免价格主图过高把副图压得只剩一条窄带。
            volume_height = max(self.VOLUME_PANEL_MIN_HEIGHT, available_height * self.VOLUME_PANEL_RATIO)
            price_height = max(self.PRICE_PANEL_MIN_HEIGHT, available_height - volume_height)
            volume_height = max(self.VOLUME_PANEL_MIN_HEIGHT, available_height - price_height)

            price_panel_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), price_height)
            volume_panel_rect = QtCore.QRectF(
                rect.left(),
                price_panel_rect.bottom() + self.PANEL_GAP,
                rect.width(),
                max(0.0, rect.bottom() - axis_height - (price_panel_rect.bottom() + self.PANEL_GAP)),
            )
            self.draw_panel_frame(painter, price_panel_rect)
            self.draw_panel_frame(painter, volume_panel_rect)

            price_rect = price_panel_rect.adjusted(
                self.PANEL_PADDING_X,
                self.PANEL_PADDING_TOP,
                -self.PANEL_PADDING_X,
                -self.PANEL_PADDING_BOTTOM,
            )
            volume_rect = volume_panel_rect.adjusted(
                self.PANEL_PADDING_X,
                self.PANEL_PADDING_TOP,
                -self.PANEL_PADDING_X,
                -self.PANEL_PADDING_BOTTOM,
            )
        else:
            price_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.height() - axis_height)
            volume_rect = QtCore.QRectF()
        price_plot_rect = price_rect.adjusted(self.Y_AXIS_LABEL_WIDTH, 0, 0, 0)
        volume_plot_rect = volume_rect.adjusted(self.Y_AXIS_LABEL_WIDTH, 0, 0, 0) if not volume_rect.isNull() else QtCore.QRectF()
        time_rect = QtCore.QRectF(
            price_plot_rect.left(),
            rect.bottom() - axis_height,
            price_plot_rect.width(),
            axis_height,
        )

        marker_prices = [marker.price for marker in markers]
        high_prices = [bar.high_price for bar in bars]
        low_prices = [bar.low_price for bar in bars]
        max_price = max(high_prices + marker_prices) if marker_prices else max(high_prices)
        min_price = min(low_prices + marker_prices) if marker_prices else min(low_prices)
        if isclose(max_price, min_price):
            max_price += max_price * 0.01 if max_price else 1
            min_price -= min_price * 0.01 if min_price else 1

        price_padding_ratio = 0.12 if markers else 0.08
        price_padding = (max_price - min_price) * price_padding_ratio
        max_price += price_padding
        min_price -= price_padding

        self.draw_price_grid(painter, price_rect, price_plot_rect, min_price, max_price)

        render_start, _ = self.get_render_window(len(self.get_available_bars()))
        step_divisor = max(self.visible_count, 1) if self.can_zoom() else max(len(bars), 1)
        step_x = price_plot_rect.width() / max(step_divisor, 1)
        candle_width = max(4.0, min(step_x * 0.62, 18.0))
        compact_marker = step_x < 16.0
        marker_spacing = 18.0 if compact_marker else 24.0
        dt_to_index = {bar.dt: render_start + index for index, bar in enumerate(bars)}

        for index, bar in enumerate(bars):
            actual_index = render_start + index
            x_center = price_plot_rect.left() + step_x * ((actual_index + 0.5) - self.visible_start)
            high_y = self.price_to_y(bar.high_price, min_price, max_price, price_plot_rect)
            low_y = self.price_to_y(bar.low_price, min_price, max_price, price_plot_rect)
            open_y = self.price_to_y(bar.open_price, min_price, max_price, price_plot_rect)
            close_y = self.price_to_y(bar.close_price, min_price, max_price, price_plot_rect)

            painter.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 1))
            painter.drawLine(QtCore.QPointF(x_center, high_y), QtCore.QPointF(x_center, low_y))

            is_up = bar.close_price >= bar.open_price
            color = QtGui.QColor("#16a34a" if is_up else "#ef4444")
            top_y = min(open_y, close_y)
            body_height = max(1.5, abs(close_y - open_y))
            body_rect = QtCore.QRectF(
                x_center - candle_width / 2,
                top_y,
                candle_width,
                body_height,
            )
            painter.setPen(QtGui.QPen(color, 1))
            painter.setBrush(color)
            painter.drawRect(body_rect)

        if self.show_volume_panel and volume_rect.height() > 20:
            self.draw_volume_panel(painter, volume_rect, volume_plot_rect, bars, step_x, candle_width)

        marker_map: dict[int, list[ChartMarkerData]] = defaultdict(list)
        for marker in markers:
            index = dt_to_index.get(marker.dt)
            if index is None:
                continue
            marker_map[index].append(marker)

        for index, marker_list in marker_map.items():
            x_center = price_plot_rect.left() + step_x * ((index + 0.5) - self.visible_start)
            for offset, marker in enumerate(merge_marker_bucket(marker_list)):
                base_y = self.price_to_y(marker.price, min_price, max_price, price_plot_rect)
                direction_offset = marker_spacing * (offset + 1)
                marker_y = (
                    base_y + direction_offset
                    if marker.direction == "buy"
                    else base_y - direction_offset
                )
                self.draw_marker(
                    painter,
                    x_center,
                    base_y,
                    marker_y,
                    marker.direction,
                    compact=compact_marker,
                )

        self.draw_time_axis(painter, time_rect, bars)

    def draw_panel_frame(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """把价格主图和成交量副图画成两个独立面板，强化上下分区关系。"""
        painter.setPen(QtGui.QPen(QtGui.QColor("#223447"), 1))
        painter.setBrush(QtGui.QColor("#0b1622"))
        painter.drawRoundedRect(rect, self.PANEL_RADIUS, self.PANEL_RADIUS)

    def draw_price_grid(
        self,
        painter: QtGui.QPainter,
        label_rect: QtCore.QRectF,
        plot_rect: QtCore.QRectF,
        min_price: float,
        max_price: float,
    ) -> None:
        """绘制背景网格和价格标签，帮助快速定位区间。"""
        grid_pen = QtGui.QPen(QtGui.QColor("#1a2a3a"), 1, QtCore.Qt.PenStyle.DashLine)
        text_pen = QtGui.QPen(QtGui.QColor("#8ea2b8"), 1)
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)

        for index in range(5):
            ratio = index / 4
            y = plot_rect.top() + plot_rect.height() * ratio
            painter.setPen(grid_pen)
            painter.drawLine(QtCore.QPointF(plot_rect.left(), y), QtCore.QPointF(plot_rect.right(), y))

            price = max_price - (max_price - min_price) * ratio
            painter.setPen(text_pen)
            painter.drawText(
                QtCore.QRectF(label_rect.left(), y - 10, self.Y_AXIS_LABEL_WIDTH - 8, 20),
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                f"{price:.3f}",
            )

    def draw_time_axis(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        bars: list[ChartBarData],
    ) -> None:
        """绘制底部时间轴，避免只看 K 线却看不清对应时刻。"""
        if not bars or rect.height() <= 10:
            return

        baseline_y = rect.top() + 6
        axis_pen = QtGui.QPen(QtGui.QColor("#334a60"), 1)
        text_pen = QtGui.QPen(QtGui.QColor("#8ea2b8"), 1)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(axis_pen)
        painter.drawLine(QtCore.QPointF(rect.left(), baseline_y), QtCore.QPointF(rect.right(), baseline_y))

        tick_indices = self.build_time_tick_indices(bars, rect.width())
        if not tick_indices:
            return

        render_start, _ = self.get_render_window(len(self.get_available_bars()))
        step_divisor = max(self.visible_count, 1) if self.can_zoom() else max(len(bars), 1)
        step_x = rect.width() / max(step_divisor, 1)
        metrics = QtGui.QFontMetrics(painter.font())
        for index in tick_indices:
            actual_index = render_start + index
            x_center = rect.left() + step_x * ((actual_index + 0.5) - self.visible_start)
            painter.setPen(axis_pen)
            painter.drawLine(QtCore.QPointF(x_center, baseline_y), QtCore.QPointF(x_center, baseline_y + 5))

            label = self.format_time_tick_label(bars, index)
            label_width = float(metrics.horizontalAdvance(label) + 8)
            label_left = max(rect.left(), min(x_center - label_width / 2, rect.right() - label_width))
            label_rect = QtCore.QRectF(label_left, baseline_y + 6, label_width, max(12.0, rect.height() - 8))
            painter.setPen(text_pen)
            painter.drawText(
                label_rect,
                int(QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignTop),
                label,
            )

    def draw_volume_panel(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        plot_rect: QtCore.QRectF,
        bars: list[ChartBarData],
        step_x: float,
        candle_width: float,
    ) -> None:
        """绘制成交量副图，帮助在详情窗口里快速判断放量与缩量。"""
        info_rect = QtCore.QRectF(
            rect.left(),
            rect.top(),
            rect.width(),
            self.VOLUME_INFO_HEIGHT,
        )
        self.draw_volume_info(painter, info_rect, bars[-1].volume if bars else 0.0)

        content_rect = QtCore.QRectF(
            plot_rect.left(),
            info_rect.bottom() + 4,
            plot_rect.width(),
            max(0.0, plot_rect.bottom() - info_rect.bottom() - 4),
        )
        max_volume = max((bar.volume for bar in bars), default=0.0)
        if max_volume <= 0 or content_rect.height() <= 10:
            return

        grid_pen = QtGui.QPen(QtGui.QColor("#1a2a3a"), 1, QtCore.Qt.PenStyle.DashLine)
        text_pen = QtGui.QPen(QtGui.QColor("#8ea2b8"), 1)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for index in range(3):
            ratio = index / 2
            y = content_rect.top() + content_rect.height() * ratio
            painter.setPen(grid_pen)
            painter.drawLine(QtCore.QPointF(content_rect.left(), y), QtCore.QPointF(content_rect.right(), y))
            volume_value = max_volume * (1 - ratio)
            painter.setPen(text_pen)
            painter.drawText(
                QtCore.QRectF(rect.left(), y - 10, self.Y_AXIS_LABEL_WIDTH - 8, 20),
                int(QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter),
                self.format_volume_axis_value(volume_value),
            )

        render_start, _ = self.get_render_window(len(self.get_available_bars()))
        for index, bar in enumerate(bars):
            actual_index = render_start + index
            x_center = content_rect.left() + step_x * ((actual_index + 0.5) - self.visible_start)
            volume_ratio = bar.volume / max_volume if max_volume > 0 else 0.0
            bar_height = max(1.5, content_rect.height() * volume_ratio)
            top_y = content_rect.bottom() - bar_height
            is_up = bar.close_price >= bar.open_price
            color = QtGui.QColor("#16a34a" if is_up else "#ef4444")
            volume_width = max(3.0, min(candle_width * 0.78, step_x * 0.82))
            volume_rect = QtCore.QRectF(
                x_center - volume_width / 2,
                top_y,
                volume_width,
                bar_height,
            )
            painter.setPen(QtGui.QPen(color, 1))
            painter.setBrush(color)
            painter.drawRect(volume_rect)

    def draw_volume_info(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        latest_volume: float,
    ) -> None:
        """在成交量副图顶部显示一条轻量量能摘要，避免用户只看柱子不知当前量级。"""
        label_font = painter.font()
        label_font.setPointSize(8)
        label_font.setBold(True)
        value_font = QtGui.QFont(label_font)
        value_font.setBold(False)

        painter.setFont(label_font)
        painter.setPen(QtGui.QColor("#aab8c8"))
        painter.drawText(
            QtCore.QRectF(rect.left(), rect.top(), 52, rect.height()),
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            "成交量",
        )

        painter.setFont(value_font)
        painter.setPen(QtGui.QColor("#e5edf7"))
        painter.drawText(
            QtCore.QRectF(rect.left() + 56, rect.top(), max(0.0, rect.width() - 56), rect.height()),
            int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
            self.format_volume_value(latest_volume),
        )

    @staticmethod
    def format_volume_value(volume: float) -> str:
        """把成交量格式化成更符合 A股阅读习惯的“手 / 万手”文本。"""
        return format_a_share_volume_value(volume)

    @staticmethod
    def format_volume_axis_value(volume: float) -> str:
        """把成交量纵轴数值格式化成紧凑文本，方便快速读量级。"""
        return format_a_share_volume_axis_value(volume)

    def build_time_tick_indices(self, bars: list[ChartBarData], axis_width: float) -> list[int]:
        """按宽度挑选时间刻度，并优先保留跨日节点和最右侧最新时间。"""
        if not bars:
            return []
        if len(bars) == 1:
            return [0]

        desired_ticks = min(len(bars), max(5, min(7, int(axis_width // 150) or 5)))
        day_starts = {
            index
            for index, bar in enumerate(bars)
            if index == 0 or bar.dt.date() != bars[index - 1].dt.date()
        }
        even_ticks = {
            round((len(bars) - 1) * step / max(desired_ticks - 1, 1))
            for step in range(desired_ticks)
        }
        candidates = sorted(day_starts | even_ticks | {len(bars) - 1})

        right_edge = float("inf")
        chosen: list[int] = []
        font = self.font()
        font.setPointSize(8)
        metrics = QtGui.QFontMetrics(font)
        step_x = axis_width / max(len(bars), 1)
        for index in reversed(candidates):
            label = self.format_time_tick_label(bars, index)
            label_width = float(metrics.horizontalAdvance(label) + 18)
            center_x = step_x * (index + 0.5)
            left = center_x - label_width / 2
            right = center_x + label_width / 2
            if right <= right_edge - 8:
                chosen.append(index)
                right_edge = left

        return sorted(chosen)

    def format_time_tick_label(self, bars: list[ChartBarData], index: int) -> str:
        """按周期和跨日情况格式化横轴时间文本。"""
        assert self.snapshot is not None
        bar = bars[index]
        is_daily = self.snapshot.interval == "d"
        if is_daily:
            return bar.dt.strftime("%m-%d")

        cross_day = bars[0].dt.date() != bars[-1].dt.date()
        is_day_start = index == 0 or bar.dt.date() != bars[index - 1].dt.date()
        if cross_day and (is_day_start or index == len(bars) - 1):
            return bar.dt.strftime("%m-%d %H:%M")
        return bar.dt.strftime("%H:%M")

    def draw_marker(
        self,
        painter: QtGui.QPainter,
        x_center: float,
        anchor_y: float,
        label_center_y: float,
        direction: str,
        *,
        compact: bool,
    ) -> None:
        """绘制圆角买卖标签，并用细引线指回实际触发价位。"""
        is_buy = direction == "buy"
        fill_color = QtGui.QColor("#16a34a" if is_buy else "#dc2626")
        border_color = QtGui.QColor("#4ade80" if is_buy else "#f87171")
        text_color = QtGui.QColor("#f8fafc")
        line_color = QtGui.QColor(border_color)
        label_text = ("B" if is_buy else "S") if compact else ("买" if is_buy else "卖")

        font = painter.font()
        font.setPointSize(8 if compact else 9)
        font.setBold(True)
        painter.setFont(font)
        metrics = QtGui.QFontMetrics(font)
        text_width = float(metrics.horizontalAdvance(label_text))
        label_width = max(22.0 if compact else 28.0, text_width + (10.0 if compact else 14.0))
        label_height = 16.0 if compact else 20.0
        label_rect = QtCore.QRectF(
            x_center - label_width / 2,
            label_center_y - label_height / 2,
            label_width,
            label_height,
        )

        line_end_y = label_rect.top() if is_buy else label_rect.bottom()
        line_start_y = anchor_y + 3 if is_buy else anchor_y - 3
        painter.setPen(QtGui.QPen(line_color, 1))
        painter.drawLine(
            QtCore.QPointF(x_center, line_start_y),
            QtCore.QPointF(x_center, line_end_y),
        )

        painter.setPen(QtGui.QPen(border_color, 1))
        painter.setBrush(fill_color)
        painter.drawRoundedRect(label_rect, 7, 7)

        painter.setPen(text_color)
        painter.drawText(
            label_rect,
            int(QtCore.Qt.AlignmentFlag.AlignCenter),
            label_text,
        )

    def price_to_y(self, price: float, min_price: float, max_price: float, rect: QtCore.QRectF) -> float:
        """把价格映射成图上的纵坐标。"""
        if isclose(max_price, min_price):
            return rect.center().y()
        ratio = (price - min_price) / (max_price - min_price)
        return rect.bottom() - ratio * rect.height()


class AlertKLineDetailWindow(QtWidgets.QWidget):
    """承载 K线图详情窗口交互能力的独立窗口。"""

    # 这些调试标签只在排查触摸板/鼠标事件时才有价值，默认不应出现在正式界面里。
    SHOW_INTERACTION_DEBUG = False

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("K线图详情窗口")
        self.resize(1280, 860)

        self.close_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Close, self)
        self.close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.close_shortcut.activated.connect(self.close)

        self.chart_widget = AlertChartWidget(
            interactive=True,
            # 详情窗口应该和主界面使用同一份快照范围，只比主界面多交互能力，
            # 不能再额外偷偷裁成“仅最新交易日”，否则 5m/15m/30m 会和主界面看到不同内容。
            intraday_only=False,
            show_volume_panel=True,
        )
        self.zoom_in_button = QtWidgets.QPushButton("放大")
        self.zoom_out_button = QtWidgets.QPushButton("缩小")
        self.reset_button = QtWidgets.QPushButton("还原")
        self.pan_left_button = QtWidgets.QPushButton("左移")
        self.pan_right_button = QtWidgets.QPushButton("右移")
        self.input_hint_label: QtWidgets.QLabel | None = None
        self.input_debug_label: QtWidgets.QLabel | None = None
        self.raw_event_label: QtWidgets.QLabel | None = None
        if self.SHOW_INTERACTION_DEBUG:
            self.input_hint_label = QtWidgets.QLabel("触摸板：左右滑=平移，双指捏合=缩放，上下滑不再缩放；鼠标滚轮继续缩放。")
            self.input_debug_label = QtWidgets.QLabel("最近输入：等待手势")
            self.raw_event_label = QtWidgets.QLabel("原始事件：等待输入")
            self.input_hint_label.setStyleSheet("color: #8ea2b8; font-size: 12px;")
            self.input_debug_label.setStyleSheet("color: #60a5fa; font-size: 12px;")
            self.raw_event_label.setStyleSheet("color: #93c5fd; font-size: 11px;")
            self.raw_event_label.setWordWrap(True)

        self.zoom_in_button.clicked.connect(self.chart_widget.zoom_in)
        self.zoom_out_button.clicked.connect(self.chart_widget.zoom_out)
        self.reset_button.clicked.connect(self.chart_widget.reset_view)
        self.pan_left_button.clicked.connect(self.chart_widget.pan_left)
        self.pan_right_button.clicked.connect(self.chart_widget.pan_right)
        self.chart_widget.view_state_changed.connect(self.refresh_button_states)
        if self.input_debug_label is not None and self.raw_event_label is not None:
            self.chart_widget.interaction_debug_changed.connect(self.input_debug_label.setText)
            self.chart_widget.raw_event_debug_changed.connect(self.raw_event_label.setText)

        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.pan_left_button)
        controls_layout.addWidget(self.pan_right_button)
        controls_layout.addSpacing(12)
        controls_layout.addWidget(self.zoom_out_button)
        controls_layout.addWidget(self.zoom_in_button)
        controls_layout.addWidget(self.reset_button)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(controls_layout)
        if self.input_hint_label is not None:
            layout.addWidget(self.input_hint_label)
        if self.input_debug_label is not None:
            layout.addWidget(self.input_debug_label)
        if self.raw_event_label is not None:
            layout.addWidget(self.raw_event_label)
        layout.addWidget(self.chart_widget, stretch=1)
        self.setLayout(layout)
        self.refresh_button_states()

    def set_snapshot(self, snapshot: ChartSnapshotData) -> None:
        """刷新 K线图详情窗口里的图表，并同步窗口标题。"""
        self.chart_widget.set_snapshot(snapshot)
        self.setWindowTitle(
            f"K线图详情窗口 - {snapshot.vt_symbol} | {snapshot.interval} | "
            f"{get_strategy_display_name(snapshot.strategy_name)}"
        )
        self.refresh_button_states()

    def clear_snapshot(self, message: str) -> None:
        """清空详情窗口图表，占位等待下一次图表快照。"""
        self.chart_widget.clear_snapshot(message)
        self.setWindowTitle("K线图详情窗口")
        self.refresh_button_states()

    def show_and_activate(self) -> None:
        """显示并激活已有 K线图详情窗口实例。"""
        self.show()
        self.raise_()
        self.activateWindow()

    def refresh_button_states(self) -> None:
        """根据当前图表状态刷新缩放和平移按钮。"""
        self.zoom_in_button.setEnabled(self.chart_widget.can_zoom_in())
        self.zoom_out_button.setEnabled(self.chart_widget.can_zoom_out())
        self.reset_button.setEnabled(self.chart_widget.can_zoom())
        self.pan_left_button.setEnabled(self.chart_widget.can_pan_left())
        self.pan_right_button.setEnabled(self.chart_widget.can_pan_right())

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:      # noqa: N802
        """兜底处理 macOS 下的 Command+W，确保详情窗口能直接关闭。"""
        if event.matches(QtGui.QKeySequence.StandardKey.Close):
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
