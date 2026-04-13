import numpy as np

from vnpy_ctastrategy import (
    ArrayManager,
    BarData,
    BarGenerator,
    CtaTemplate,
    OrderData,
    StopOrder,
    TickData,
    TradeData,
)


class LessonVolumeBreakoutAShareStrategy(CtaTemplate):
    """A股短线学习用放量突破策略，只做多。"""

    author: str = "zezhang"

    breakout_window: int = 5
    exit_window: int = 3
    volume_window: int = 5
    volume_ratio: float = 1.5
    fixed_size: int = 100

    entry_up: float = 0.0
    exit_down: float = 0.0
    volume_ma: float = 0.0
    volume_ratio_value: float = 0.0

    parameters = [
        "breakout_window",
        "exit_window",
        "volume_window",
        "volume_ratio",
        "fixed_size",
    ]
    variables = ["entry_up", "exit_down", "volume_ma", "volume_ratio_value"]

    def on_init(self) -> None:
        """初始化策略。"""
        # 记录日志，方便在界面里观察策略生命周期。
        self.write_log("LessonVolumeBreakoutAShareStrategy initialized")

        # 仍然保留标准 CTA 结构，方便后续切换更短周期学习。
        self.bg: BarGenerator = BarGenerator(self.on_bar)

        # 这里同时要看突破窗口、离场窗口和成交量均量窗口，
        # 因此缓存长度按最大窗口再额外多留 2 根。
        window_size = max(
            self.breakout_window,
            self.exit_window,
            self.volume_window,
        ) + 2
        self.am: ArrayManager = ArrayManager(window_size)

        # 预加载足够历史 K 线，避免一开始突破线和均量都算不出来。
        self.load_bar(max(self.breakout_window, self.volume_window) + 10)

    def on_start(self) -> None:
        """启动策略时调用。"""
        self.write_log("LessonVolumeBreakoutAShareStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """停止策略时调用。"""
        self.write_log("LessonVolumeBreakoutAShareStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 Tick 时调用。"""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到新 K 线时调用，按短线放量突破规则做多和离场。"""
        # 每根新 K 线先撤掉旧委托，避免上一轮信号残留。
        self.cancel_all()

        self.am.update_bar(bar)
        if not self.am.inited:
            return

        # 仍然用上一根 K 线的通道值作为突破和离场阈值，
        # 避免当前 K 线把自己也算进突破线。
        entry_up_array, _ = self.am.donchian(self.breakout_window, array=True)
        _, exit_down_array = self.am.donchian(self.exit_window, array=True)

        self.entry_up = entry_up_array[-2]
        self.exit_down = exit_down_array[-2]

        # 均量只统计“当前 K 线之前”的若干根，避免把今天的放量又算回平均值里。
        volume_window_values = self.am.volume[-self.volume_window - 1:-1]
        self.volume_ma = float(np.mean(volume_window_values))
        if self.volume_ma > 0:
            self.volume_ratio_value = bar.volume / self.volume_ma
        else:
            self.volume_ratio_value = 0.0

        breakout_signal: bool = (
            bar.close_price > self.entry_up
            and self.volume_ratio_value >= self.volume_ratio
        )
        exit_signal: bool = bar.close_price < self.exit_down

        if self.pos < 0:
            # A股学习版默认不做空，如遇异常空仓先纠正回来。
            self.cover(bar.close_price, abs(self.pos))
        elif self.pos == 0 and breakout_signal:
            # 只有价格突破且量能达到阈值时，才建立多仓。
            self.buy(bar.close_price, self.fixed_size)
        elif self.pos > 0 and exit_signal:
            # 一旦收盘跌破更短周期低点，就按短线思路快速离场。
            self.sell(bar.close_price, self.pos)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """委托状态更新时调用。"""
        return

    def on_trade(self, trade: TradeData) -> None:
        """成交回报更新时调用。"""
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单状态更新时调用。"""
        return
