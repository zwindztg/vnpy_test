"""实时提醒中心的轻量 K 线图控件。"""

from __future__ import annotations

from collections import defaultdict
from math import isclose

from vnpy.trader.ui import QtCore, QtGui, QtWidgets

from ..core import ChartBarData, ChartMarkerData, ChartSnapshotData, get_strategy_display_name
from .chart_view import (
    apply_drag_pan,
    apply_pan_left,
    apply_pan_right,
    apply_zoom_in,
    apply_zoom_out,
    can_pan_left,
    can_pan_right,
    can_zoom,
    can_zoom_in,
    can_zoom_out,
    get_available_bars,
    get_available_markers,
    get_right_offset,
    get_view_key,
    sync_view_state,
)


class AlertChartWidget(QtWidgets.QWidget):
    """在提醒中心右侧显示最近一段 K 线和提醒标记。"""

    view_state_changed: QtCore.Signal = QtCore.Signal()

    TIME_AXIS_HEIGHT = 34.0
    MIN_VISIBLE_BARS = 12

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        *,
        interactive: bool = False,
        intraday_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self.snapshot: ChartSnapshotData | None = None
        self.placeholder_text: str = "暂无图表数据，请先执行单次测试或启动提醒"
        self.interactive = interactive
        self.intraday_only = intraday_only
        self.visible_start: int = 0
        self.visible_count: int = 0
        self.dragging: bool = False
        self.drag_origin_x: float = 0.0
        self.drag_origin_start: int = 0
        self.setMinimumHeight(340)
        if self.interactive:
            self.setMouseTracking(True)
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

    def clear_snapshot(self, message: str | None = None) -> None:
        """清空当前图表快照，并显示占位提示。"""
        self.snapshot = None
        self.visible_start = 0
        self.visible_count = 0
        self.dragging = False
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
                pixels_per_bar = max(self.width() / max(self.visible_count, 1), 1.0)
                delta_x = event.position().x() - self.drag_origin_x
                bar_shift = int(delta_x / pixels_per_bar)
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
        self.refresh_interaction_cursor()
        self.update()
        self.view_state_changed.emit()

    def reset_view(self) -> None:
        """把可见范围恢复为默认视图。"""
        self.sync_view_state(reset=True)
        self.update()
        self.view_state_changed.emit()

    def zoom_in(self) -> None:
        """缩小可见 K 线数量，放大当天局部区域。"""
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
        """扩大可见 K 线数量，缩小当前局部区域。"""
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
        """把可视窗口向左平移，查看当天更早的 K 线。"""
        if not self.can_pan_left():
            return

        self.visible_start = apply_pan_left(self.visible_start, self.visible_count)
        self.update()
        self.view_state_changed.emit()

    def pan_right(self) -> None:
        """把可视窗口向右平移，回到更新的 K 线区域。"""
        if not self.can_pan_right():
            return

        total = len(self.get_available_bars())
        self.visible_start = apply_pan_right(self.visible_start, self.visible_count, total)
        self.update()
        self.view_state_changed.emit()

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
            return tuple(bars)

        end_index = min(len(bars), self.visible_start + self.visible_count)
        return tuple(bars[self.visible_start:end_index])

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

    def sync_view_state(self, reset: bool = False, right_offset: int | None = None) -> None:
        """根据最新快照和当前交互状态归一化可见区间。"""
        self.visible_start, self.visible_count = sync_view_state(
            total=len(self.get_available_bars()),
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

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:      # noqa: N802
        """在弹窗里用滚轮控制日内缩放。"""
        if not self.can_zoom():
            event.ignore()
            return

        if event.angleDelta().y() > 0:
            self.zoom_in()
        elif event.angleDelta().y() < 0:
            self.zoom_out()
        event.accept()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:      # noqa: N802
        """用 QPainter 画出最简版 K 线、买卖点和时间轴。"""
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        outer_rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(outer_rect, QtGui.QColor("#16212b"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
        painter.drawRoundedRect(QtCore.QRectF(outer_rect), 8, 8)

        visible_bars = list(self.get_visible_bars())
        if not self.snapshot or not visible_bars:
            self.draw_placeholder(painter, outer_rect)
            return

        title_rect = QtCore.QRectF(outer_rect.left() + 12, outer_rect.top() + 10, outer_rect.width() - 24, 26)
        plot_rect = QtCore.QRectF(
            outer_rect.left() + 14,
            outer_rect.top() + 46,
            outer_rect.width() - 28,
            outer_rect.height() - 66,
        )
        if plot_rect.width() <= 40 or plot_rect.height() <= 40:
            self.draw_placeholder(painter, outer_rect)
            return

        self.draw_title(painter, title_rect)
        self.draw_plot(painter, plot_rect, visible_bars, list(self.get_visible_markers()))

    def draw_placeholder(self, painter: QtGui.QPainter, rect: QtCore.QRect) -> None:
        """在空态时显示提示文本。"""
        painter.setPen(QtGui.QColor("#94a3b8"))
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), self.placeholder_text)

    def draw_title(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """绘制图表顶部信息条。"""
        assert self.snapshot is not None
        mode_text = "实时运行" if self.snapshot.mode == "live" else "单次测试"
        title = (
            f"{self.snapshot.vt_symbol}  |  "
            f"{get_strategy_display_name(self.snapshot.strategy_name)}  |  "
            f"{self.snapshot.interval}  |  "
            f"{self.snapshot.data_source}  |  "
            f"{mode_text}"
        )
        painter.setPen(QtGui.QColor("#e2e8f0"))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter), title)

    def draw_plot(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        bars: list[ChartBarData],
        markers: list[ChartMarkerData],
    ) -> None:
        """绘制 K 线主体、理论买卖点和底部时间轴。"""
        axis_height = min(self.TIME_AXIS_HEIGHT, max(22.0, rect.height() * 0.16))
        price_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), rect.height() - axis_height)
        time_rect = QtCore.QRectF(rect.left(), price_rect.bottom(), rect.width(), axis_height)

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

        self.draw_price_grid(painter, price_rect, min_price, max_price)

        step_x = price_rect.width() / max(len(bars), 1)
        candle_width = max(4.0, min(step_x * 0.62, 18.0))
        compact_marker = step_x < 16.0
        marker_spacing = 18.0 if compact_marker else 24.0
        dt_to_index = {bar.dt: index for index, bar in enumerate(bars)}

        for index, bar in enumerate(bars):
            x_center = price_rect.left() + step_x * (index + 0.5)
            high_y = self.price_to_y(bar.high_price, min_price, max_price, price_rect)
            low_y = self.price_to_y(bar.low_price, min_price, max_price, price_rect)
            open_y = self.price_to_y(bar.open_price, min_price, max_price, price_rect)
            close_y = self.price_to_y(bar.close_price, min_price, max_price, price_rect)

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

        marker_map: dict[int, list[ChartMarkerData]] = defaultdict(list)
        for marker in markers:
            index = dt_to_index.get(marker.dt)
            if index is None:
                continue
            marker_map[index].append(marker)

        for index, marker_list in marker_map.items():
            x_center = price_rect.left() + step_x * (index + 0.5)
            for offset, marker in enumerate(marker_list):
                base_y = self.price_to_y(marker.price, min_price, max_price, price_rect)
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

    def draw_price_grid(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        min_price: float,
        max_price: float,
    ) -> None:
        """绘制背景网格和价格标签，帮助快速定位区间。"""
        grid_pen = QtGui.QPen(QtGui.QColor("#223041"), 1, QtCore.Qt.PenStyle.DashLine)
        text_pen = QtGui.QPen(QtGui.QColor("#94a3b8"), 1)
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)

        for index in range(5):
            ratio = index / 4
            y = rect.top() + rect.height() * ratio
            painter.setPen(grid_pen)
            painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))

            price = max_price - (max_price - min_price) * ratio
            painter.setPen(text_pen)
            painter.drawText(
                QtCore.QRectF(rect.left(), y - 10, 120, 20),
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
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
        axis_pen = QtGui.QPen(QtGui.QColor("#334155"), 1)
        text_pen = QtGui.QPen(QtGui.QColor("#94a3b8"), 1)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(axis_pen)
        painter.drawLine(QtCore.QPointF(rect.left(), baseline_y), QtCore.QPointF(rect.right(), baseline_y))

        tick_indices = self.build_time_tick_indices(bars, rect.width())
        if not tick_indices:
            return

        step_x = rect.width() / max(len(bars), 1)
        metrics = QtGui.QFontMetrics(painter.font())
        for index in tick_indices:
            x_center = rect.left() + step_x * (index + 0.5)
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


class AlertChartPopupWindow(QtWidgets.QWidget):
    """用于放大查看 K 线图的独立窗口。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("放大查看 K 线图")
        self.resize(1280, 860)

        self.close_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Close, self)
        self.close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.close_shortcut.activated.connect(self.close)

        self.chart_widget = AlertChartWidget(interactive=True, intraday_only=True)
        self.zoom_in_button = QtWidgets.QPushButton("放大")
        self.zoom_out_button = QtWidgets.QPushButton("缩小")
        self.reset_button = QtWidgets.QPushButton("还原")
        self.pan_left_button = QtWidgets.QPushButton("左移")
        self.pan_right_button = QtWidgets.QPushButton("右移")

        self.zoom_in_button.clicked.connect(self.chart_widget.zoom_in)
        self.zoom_out_button.clicked.connect(self.chart_widget.zoom_out)
        self.reset_button.clicked.connect(self.chart_widget.reset_view)
        self.pan_left_button.clicked.connect(self.chart_widget.pan_left)
        self.pan_right_button.clicked.connect(self.chart_widget.pan_right)
        self.chart_widget.view_state_changed.connect(self.refresh_button_states)

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
        layout.addWidget(self.chart_widget, stretch=1)
        self.setLayout(layout)
        self.refresh_button_states()

    def set_snapshot(self, snapshot: ChartSnapshotData) -> None:
        """刷新弹窗内的 K 线图，并同步窗口标题。"""
        self.chart_widget.set_snapshot(snapshot)
        self.setWindowTitle(
            f"放大查看 - {snapshot.vt_symbol} | {snapshot.interval} | "
            f"{get_strategy_display_name(snapshot.strategy_name)}"
        )
        self.refresh_button_states()

    def clear_snapshot(self, message: str) -> None:
        """清空弹窗图表，占位等待下一次图表快照。"""
        self.chart_widget.clear_snapshot(message)
        self.setWindowTitle("放大查看 K 线图")
        self.refresh_button_states()

    def show_and_activate(self) -> None:
        """显示并激活已有弹窗实例。"""
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
        """兜底处理 macOS 下的 Command+W，确保弹窗能直接关闭。"""
        if event.matches(QtGui.QKeySequence.StandardKey.Close):
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
