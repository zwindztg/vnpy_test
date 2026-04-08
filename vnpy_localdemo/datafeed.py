from collections.abc import Callable

from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import BarData, HistoryRequest, TickData


class LocalDemoDatafeed(BaseDatafeed):
    """No-op datafeed used for local study without external credentials."""

    def init(self, output: Callable = print) -> bool:
        return True

    def query_bar_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[BarData]:
        output("当前使用本地学习模式数据服务，请先在数据管理中导入数据，或改用 RQData。")
        return []

    def query_tick_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[TickData]:
        output("当前使用本地学习模式数据服务，请先在数据管理中导入数据，或改用 RQData。")
        return []
