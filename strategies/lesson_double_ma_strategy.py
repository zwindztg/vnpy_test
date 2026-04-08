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


class LessonDoubleMaStrategy(CtaTemplate):
    """A minimal moving-average crossover strategy for vn.py study."""

    author: str = "zezhang"

    fast_window: int = 10
    slow_window: int = 20
    fixed_size: int = 1

    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0

    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]

    def on_init(self) -> None:
        """Initialize indicators and preload recent bars."""
        self.write_log("LessonDoubleMaStrategy initialized")

        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.load_bar(10)

    def on_start(self) -> None:
        """Called when the strategy starts trading."""
        self.write_log("LessonDoubleMaStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """Called when the strategy stops."""
        self.write_log("LessonDoubleMaStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """Convert tick stream into bar updates."""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """Trade when the fast SMA crosses the slow SMA."""
        self.cancel_all()

        self.am.update_bar(bar)
        if not self.am.inited:
            return

        fast_ma = self.am.sma(self.fast_window, array=True)
        slow_ma = self.am.sma(self.slow_window, array=True)

        self.fast_ma0 = fast_ma[-1]
        self.fast_ma1 = fast_ma[-2]
        self.slow_ma0 = slow_ma[-1]
        self.slow_ma1 = slow_ma[-2]

        cross_over: bool = self.fast_ma0 >= self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        cross_below: bool = self.fast_ma0 <= self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        if cross_over:
            if self.pos < 0:
                self.cover(bar.close_price, abs(self.pos))
            if self.pos <= 0:
                self.buy(bar.close_price, self.fixed_size)

        elif cross_below:
            if self.pos > 0:
                self.sell(bar.close_price, abs(self.pos))
            if self.pos >= 0:
                self.short(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """Keep the template hook for later inspection."""
        return

    def on_trade(self, trade: TradeData) -> None:
        """Refresh the UI after a fill."""
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """No custom stop-order handling in this example."""
        return

