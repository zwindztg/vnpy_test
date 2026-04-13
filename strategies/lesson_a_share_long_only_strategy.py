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
    """A股学习用长仓策略，只开多仓和平多仓。"""

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
        """初始化策略。"""
        # 记录日志，方便在界面里看到策略初始化过程。
        self.write_log("LessonAShareLongOnlyStrategy initialized")

        # BarGenerator 用来把 Tick 数据整理成 K 线，再交给 on_bar 处理。
        self.bg: BarGenerator = BarGenerator(self.on_bar)
        # ArrayManager 用来缓存最近一段时间的 K 线，便于计算均线等指标。
        # 这里把缓存窗口设成“慢均线周期 + 2”，这样既能算出前一根和当前一根均线，
        # 也避免默认 100 根窗口太大，导致短区间回测一直处于“未初始化完成”状态。
        self.am: ArrayManager = ArrayManager(self.slow_window + 2)

        # 预加载足够多的历史 K 线，保证慢均线也能顺利算出来。
        self.load_bar(self.slow_window + 10)

    def on_start(self) -> None:
        """启动策略时调用。"""
        # 记录启动日志，并刷新界面状态。
        self.write_log("LessonAShareLongOnlyStrategy started")
        self.put_event()

    def on_stop(self) -> None:
        """停止策略时调用。"""
        # 记录停止日志，并刷新界面状态。
        self.write_log("LessonAShareLongOnlyStrategy stopped")
        self.put_event()

    def on_tick(self, tick: TickData) -> None:
        """收到 Tick 时调用。"""
        # 这里不直接交易，而是先把 Tick 更新给 BarGenerator，
        # 等它合成完整 K 线后，再进入 on_bar 统一处理。
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        """收到新 K 线时调用，按均线金叉死叉做多和离场。"""
        # 每次处理新 K 线前，先撤掉上一根 K 线还未成交的委托，
        # 避免旧信号残留影响当前判断。
        self.cancel_all()

        # 把最新 K 线推进缓存，供后续计算均线。
        self.am.update_bar(bar)
        if not self.am.inited:
            # 如果缓存的 K 线数量还不够，就先不计算信号。
            return

        # 计算快慢均线，array=True 表示返回整段数组，
        # 这样我们就能同时取“当前值”和“上一根K线的值”。
        fast_ma = self.am.sma(self.fast_window, array=True)
        slow_ma = self.am.sma(self.slow_window, array=True)

        # 保存当前和上一根 K 线的均线值，方便在界面中观察策略状态。
        self.fast_ma0 = fast_ma[-1]
        self.fast_ma1 = fast_ma[-2]
        self.slow_ma0 = slow_ma[-1]
        self.slow_ma1 = slow_ma[-2]

        # 金叉：上一根快线在慢线下方，这一根快线来到慢线上方。
        cross_over: bool = self.fast_ma0 >= self.slow_ma0 and self.fast_ma1 < self.slow_ma1
        # 死叉：上一根快线在慢线上方，这一根快线回到慢线下方。
        cross_below: bool = self.fast_ma0 <= self.slow_ma0 and self.fast_ma1 > self.slow_ma1

        if self.pos < 0:
            # 这套策略理论上不做空。
            # 如果因为历史状态或异常原因出现空仓，这里先强制平掉。
            self.cover(bar.close_price, abs(self.pos))
        elif cross_over and self.pos == 0:
            # 金叉且当前空仓时，按固定股数买入，建立多头仓位。
            self.buy(bar.close_price, self.fixed_size)
        elif cross_below and self.pos > 0:
            # 死叉且当前持有多仓时，卖出全部持仓离场。
            self.sell(bar.close_price, self.pos)

        # 刷新界面，让均线和持仓变量及时显示最新结果。
        self.put_event()

    def on_order(self, order: OrderData) -> None:
        """委托状态更新时调用。"""
        # 这里先保留空实现，方便以后扩展查看委托变化。
        return

    def on_trade(self, trade: TradeData) -> None:
        """成交回报更新时调用。"""
        # 成交后刷新界面，这样持仓和变量会立即更新。
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        """停止单状态更新时调用。"""
        # 当前示例没有自定义停止单逻辑，先保留空实现。
        return
