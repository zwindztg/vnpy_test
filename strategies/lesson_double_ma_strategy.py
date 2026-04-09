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
    """双均线学习策略，既可以做多，也可以做空。"""

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
        """初始化策略。"""
        # 记录初始化日志，方便在界面中观察策略生命周期。
        self.write_log("LessonDoubleMaStrategy initialized")

        # BarGenerator 负责把 Tick 数据整理成 K 线，再交给 on_bar 处理。
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        # ArrayManager 用来缓存最近一段时间的 K 线，并计算均线指标。
        self.am: ArrayManager = ArrayManager()

        # 先加载一小段历史 K 线，避免均线一开始没有足够数据。
        self.load_bar(10)

    def on_start(self) -> None:
        """启动策略时调用。"""
        # 记录启动日志，并刷新界面状态。
        self.write_log("LessonDoubleMaStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """停止策略时调用。"""
        # 记录停止日志，并刷新界面状态。
        self.write_log("LessonDoubleMaStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 Tick 时调用。"""
        # 这里不直接下单，而是先把 Tick 交给 BarGenerator 合成 K 线。
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到新 K 线时调用，按双均线金叉死叉交易。"""
        # 先撤掉上一根 K 线遗留的未成交委托，避免旧信号干扰。
        self.cancel_all()

        # 更新 K 线缓存，供后续计算快慢均线。
        self.am.update_bar(bar)
        if not self.am.inited:
            # 数据不够时先不交易。
            return

        # 计算快慢均线，返回数组后可以同时读取当前值和上一根值。
        fast_ma = self.am.sma(self.fast_window, array=True)
        slow_ma = self.am.sma(self.slow_window, array=True)

        # 保存均线的当前值和上一根值，便于在界面中直接观察。
        self.fast_ma0 = fast_ma[-1]
        self.fast_ma1 = fast_ma[-2]
        self.slow_ma0 = slow_ma[-1]
        self.slow_ma1 = slow_ma[-2]

        # 金叉：快均线从慢均线下方穿到上方。
        cross_over: bool = self.fast_ma0 >= self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        # 死叉：快均线从慢均线上方穿到下方。
        cross_below: bool = self.fast_ma0 <= self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        if cross_over:
            if self.pos < 0:
                # 如果当前持有空仓，先平空。
                self.cover(bar.close_price, abs(self.pos))
            if self.pos <= 0:
                # 平空后或原本空仓时，开多仓。
                self.buy(bar.close_price, self.fixed_size)

        elif cross_below:
            if self.pos > 0:
                # 如果当前持有多仓，先平多。
                self.sell(bar.close_price, abs(self.pos))
            if self.pos >= 0:
                # 平多后或原本空仓时，开空仓。
                self.short(bar.close_price, self.fixed_size)

        # 刷新界面，让均线和持仓状态及时更新。
        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """委托状态更新时调用。"""
        # 这里先保留空实现，方便以后扩展学习委托变化。
        return

    def on_trade(self, trade: TradeData) -> None:
        """成交回报更新时调用。"""
        # 有成交后刷新界面，便于立即看到持仓和变量变化。
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单状态更新时调用。"""
        # 当前示例没有自定义停止单处理逻辑。
        return
