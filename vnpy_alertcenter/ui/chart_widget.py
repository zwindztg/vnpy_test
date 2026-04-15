"""实时提醒中心的轻量 K 线图控件。"""

from __future__ import annotations

from collections import defaultdict
from math import isclose

from vnpy.trader.ui import QtCore, QtGui, QtWidgets

from ..core import ChartMarkerData, ChartSnapshotData, get_strategy_display_name


class AlertChartWidget(QtWidgets.QWidget):
    """在提醒中心右侧显示最近一段 K 线和提醒标记。"""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.snapshot: ChartSnapshotData | None = None
        self.placeholder_text: str = "暂无图表数据，请先执行单次测试或启动提醒"
        self.setMinimumHeight(340)

    def clear_snapshot(self, message: str | None = None) -> None:
        """清空当前图表快照，并显示占位提示。"""
        self.snapshot = None
        if message:
            self.placeholder_text = message
        self.update()

    def set_snapshot(self, snapshot: ChartSnapshotData) -> None:
        """替换当前图表快照。"""
        self.snapshot = snapshot
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:      # noqa: N802
        """用 QPainter 画出最简版 K 线和红绿提醒标记。"""
        super().paintEvent(event)

        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing)

        outer_rect = self.rect().adjusted(1, 1, -1, -1)
        painter.fillRect(outer_rect, QtGui.QColor("#16212b"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#334155"), 1))
        painter.drawRoundedRect(QtCore.QRectF(outer_rect), 8, 8)

        if not self.snapshot or not self.snapshot.bars:
            self.draw_placeholder(painter, outer_rect)
            return

        title_rect = QtCore.QRectF(outer_rect.left() + 12, outer_rect.top() + 10, outer_rect.width() - 24, 26)
        plot_rect = QtCore.QRectF(
            outer_rect.left() + 14,
            outer_rect.top() + 46,
            outer_rect.width() - 28,
            outer_rect.height() - 62,
        )
        if plot_rect.width() <= 40 or plot_rect.height() <= 40:
            self.draw_placeholder(painter, outer_rect)
            return

        self.draw_title(painter, title_rect)
        self.draw_plot(painter, plot_rect)

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

    def draw_plot(self, painter: QtGui.QPainter, rect: QtCore.QRectF) -> None:
        """绘制 K 线主体和提醒标记。"""
        assert self.snapshot is not None

        bars = list(self.snapshot.bars)
        markers = list(self.snapshot.markers)
        marker_prices = [marker.price for marker in markers]
        high_prices = [bar.high_price for bar in bars]
        low_prices = [bar.low_price for bar in bars]
        max_price = max(high_prices + marker_prices) if marker_prices else max(high_prices)
        min_price = min(low_prices + marker_prices) if marker_prices else min(low_prices)
        if isclose(max_price, min_price):
            max_price += max_price * 0.01 if max_price else 1
            min_price -= min_price * 0.01 if min_price else 1

        price_padding = (max_price - min_price) * 0.08
        max_price += price_padding
        min_price -= price_padding

        self.draw_grid(painter, rect, min_price, max_price)

        step_x = rect.width() / max(len(bars), 1)
        candle_width = max(4.0, min(step_x * 0.62, 16.0))
        dt_to_index = {bar.dt: index for index, bar in enumerate(bars)}

        for index, bar in enumerate(bars):
            x_center = rect.left() + step_x * (index + 0.5)
            high_y = self.price_to_y(bar.high_price, min_price, max_price, rect)
            low_y = self.price_to_y(bar.low_price, min_price, max_price, rect)
            open_y = self.price_to_y(bar.open_price, min_price, max_price, rect)
            close_y = self.price_to_y(bar.close_price, min_price, max_price, rect)

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
            x_center = rect.left() + step_x * (index + 0.5)
            for offset, marker in enumerate(marker_list):
                base_y = self.price_to_y(marker.price, min_price, max_price, rect)
                marker_y = base_y + (offset * 12 if marker.direction == "buy" else -offset * 12)
                self.draw_marker(painter, x_center, marker_y, marker.direction)

    def draw_grid(
        self,
        painter: QtGui.QPainter,
        rect: QtCore.QRectF,
        min_price: float,
        max_price: float,
    ) -> None:
        """绘制背景网格和高低价标签，帮助快速定位区间。"""
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

    def draw_marker(self, painter: QtGui.QPainter, x_center: float, y_center: float, direction: str) -> None:
        """绘制红绿三角标记，分别表示观察/风控提醒。"""
        if direction == "buy":
            color = QtGui.QColor("#22c55e")
            points = [
                QtCore.QPointF(x_center, y_center - 7),
                QtCore.QPointF(x_center - 6, y_center + 5),
                QtCore.QPointF(x_center + 6, y_center + 5),
            ]
        else:
            color = QtGui.QColor("#ef4444")
            points = [
                QtCore.QPointF(x_center, y_center + 7),
                QtCore.QPointF(x_center - 6, y_center - 5),
                QtCore.QPointF(x_center + 6, y_center - 5),
            ]

        painter.setPen(QtGui.QPen(color, 1))
        painter.setBrush(color)
        painter.drawPolygon(QtGui.QPolygonF(points))

    def price_to_y(self, price: float, min_price: float, max_price: float, rect: QtCore.QRectF) -> float:
        """把价格映射成图上的纵坐标。"""
        if isclose(max_price, min_price):
            return rect.center().y()
        ratio = (price - min_price) / (max_price - min_price)
        return rect.bottom() - ratio * rect.height()
