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


class LessonAShareLongOnlyStrategy(CtaTemplate):
    """A-share learning strategy that only opens and closes long positions."""

    author: str = "zezhang"

    fast_window: int = 5
    slow_window: int = 20
    fixed_size: int = 100

    fast_ma0: float = 0.0
    fast_ma1: float = 0.0
    slow_ma0: float = 0.0
    slow_ma1: float = 0.0

    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma0", "fast_ma1", "slow_ma0", "slow_ma1"]

    def on_init(self) -> None:
        """Initialize indicators and preload enough bars for the slow SMA."""
        self.write_log("LessonAShareLongOnlyStrategy initialized")

        self.bg: BarGenerator = BarGenerator(self.on_bar)
        self.am: ArrayManager = ArrayManager()

        self.load_bar(self.slow_window + 10)

    def on_start(self) -> None:
        """Called when the strategy starts trading."""
        self.write_log("LessonAShareLongOnlyStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """Called when the strategy stops."""
        self.write_log("LessonAShareLongOnlyStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """Convert tick stream into bar updates."""
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """Buy on a golden cross and exit on a death cross without shorting."""
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

        if self.pos < 0:
            self.cover(bar.close_price, abs(self.pos))
        elif cross_over and self.pos == 0:
            self.buy(bar.close_price, self.fixed_size)
        elif cross_below and self.pos > 0:
            self.sell(bar.close_price, self.pos)

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
