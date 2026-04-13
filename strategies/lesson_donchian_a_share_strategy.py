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


class LessonDonchianAShareStrategy(CtaTemplate):
    """A股学习用唐奇安突破策略，只做多。"""

    author: str = "zezhang"

    entry_window: int = 20
    exit_window: int = 10
    fixed_size: int = 100

    entry_up: float = 0.0
    entry_down: float = 0.0
    exit_down: float = 0.0

    parameters = ["entry_window", "exit_window", "fixed_size"]
    variables = ["entry_up", "entry_down", "exit_down"]

    def on_init(self) -> None:
        """初始化策略。"""
        # 记录初始化日志，方便在界面里观察策略生命周期。
        self.write_log("LessonDonchianAShareStrategy initialized")

        # BarGenerator 负责把 Tick 整理成 K 线，再交给 on_bar 统一处理。
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        # ArrayManager 用来缓存最近的 K 线，并计算唐奇安通道。
        # 这里保留足够的窗口，保证能同时拿到“上一根”的突破线和离场线。
        window_size = max(self.entry_window, self.exit_window) + 2
        self.am: ArrayManager = ArrayManager(window_size)

        # 预加载历史 K 线，避免一开始通道还没算出来。
        self.load_bar(self.entry_window + 10)

    def on_start(self) -> None:
        """启动策略时调用。"""
        # 记录启动日志，并刷新界面状态。
        self.write_log("LessonDonchianAShareStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """停止策略时调用。"""
        # 记录停止日志，并刷新界面状态。
        self.write_log("LessonDonchianAShareStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 Tick 时调用。"""
        # 这里不直接交易，而是先把 Tick 交给 BarGenerator 合成 K 线。
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到新 K 线时调用，按唐奇安突破规则做多和离场。"""
        # 先撤掉上一根 K 线遗留的未成交委托，避免旧信号干扰。
        self.cancel_all()

        # 更新 K 线缓存，供后续计算突破通道。
        self.am.update_bar(bar)
        if not self.am.inited:
            # 数据不够时先不交易。
            return

        # 唐奇安通道会把“当前 K 线”也算进去。
        # 为了避免当前 K 线一边创新高、一边拿自己当突破线，
        # 这里使用上一根 K 线的通道值作为真正的交易阈值。
        entry_up_array, entry_down_array = self.am.donchian(self.entry_window, array=True)
        _, exit_down_array = self.am.donchian(self.exit_window, array=True)

        self.entry_up = entry_up_array[-2]
        self.entry_down = entry_down_array[-2]
        self.exit_down = exit_down_array[-2]

        if self.pos < 0:
            # 这套学习策略不做空。
            # 如果因为异常状态出现空仓，这里先平掉。
            self.cover(bar.close_price, abs(self.pos))
        elif self.pos == 0 and bar.close_price > self.entry_up:
            # 当前空仓且收盘价突破过去一段时间高点时，买入建立多仓。
            self.buy(bar.close_price, self.fixed_size)
        elif self.pos > 0 and bar.close_price < self.exit_down:
            # 持有多仓时，如果收盘价跌破较短周期低点，就全部卖出离场。
            self.sell(bar.close_price, self.pos)

        # 刷新界面，让通道值和持仓状态及时显示。
        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """委托状态更新时调用。"""
        # 这里先保留空实现，方便以后扩展查看委托变化。
        return

    def on_trade(self, trade: TradeData) -> None:
        """成交回报更新时调用。"""
        # 成交后刷新界面，这样变量和持仓能立即看到变化。
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单状态更新时调用。"""
        # 当前示例没有自定义停止单处理逻辑。
        return
