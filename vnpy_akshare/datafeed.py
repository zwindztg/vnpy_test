from datetime import date, datetime, time
from collections.abc import Callable
from typing import Any

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import BarData, HistoryRequest, TickData
from vnpy.trader.setting import SETTINGS
from vnpy.trader.utility import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
SUPPORTED_EXCHANGES = {Exchange.SSE, Exchange.SZSE, Exchange.BSE}
MINUTE_INTERVAL_MAP = {
    Interval.MINUTE: "1",
    Interval.HOUR: "60",
}


class AkshareDatafeed(BaseDatafeed):
    """A-share datafeed backed by AkShare."""

    def __init__(self) -> None:
        self.inited: bool = False
        self.ak: Any | None = None
        self.adjust: str = SETTINGS.get("datafeed.adjust", "qfq") or "qfq"

    def init(self, output: Callable = print) -> bool:
        if self.inited:
            return True

        try:
            import akshare as ak
        except ModuleNotFoundError:
            output("AkShare数据服务初始化失败：未安装 akshare，请先执行 pip install akshare。")
            return False
        except Exception as exc:
            output(f"AkShare数据服务初始化失败：{exc}")
            return False

        self.ak = ak
        self.inited = True
        return True

    def query_bar_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[BarData]:
        if not self.init(output):
            return []

        if req.exchange not in SUPPORTED_EXCHANGES:
            output("AkShare当前仅接入沪深北 A 股数据，请使用 SSE、SZSE 或 BSE。")
            return []

        if not req.symbol.isdigit():
            output(f"AkShare当前仅支持纯数字股票代码，收到代码：{req.symbol}")
            return []

        if req.interval == Interval.DAILY:
            return self._query_daily(req, output)

        if req.interval in MINUTE_INTERVAL_MAP:
            return self._query_minute(req, output)

        output("AkShare当前仅支持 A 股的 1m、1h、d 三种K线周期。")
        return []

    def query_tick_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[TickData]:
        output("AkShare适配层当前未接入 Tick 历史数据，请先使用 1m、1h 或 d 周期。")
        return []

    def _query_daily(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[BarData]:
        start_date = self._to_dt(req.start).strftime("%Y%m%d")
        end_date = self._to_dt(req.end).strftime("%Y%m%d") if req.end else datetime.now(CHINA_TZ).strftime("%Y%m%d")
        symbol = self._to_ak_symbol(req.symbol, req.exchange)

        try:
            df = self.ak.stock_zh_a_daily(symbol=symbol, start_date=start_date, end_date=end_date, adjust=self.adjust)
        except Exception as exc:
            output(f"AkShare查询A股日线失败：{exc}")
            return []

        if df is None or df.empty:
            output(f"AkShare未返回{req.vt_symbol}的日线数据。")
            return []

        bars: list[BarData] = []
        start_dt = self._to_dt(req.start)
        end_dt = self._to_dt(req.end) if req.end else datetime.now(CHINA_TZ)

        for _, row in df.iterrows():
            bar_date = row["date"]
            if isinstance(bar_date, datetime):
                bar_date = bar_date.date()
            elif not isinstance(bar_date, date):
                bar_date = datetime.fromisoformat(str(bar_date)).date()

            dt = datetime.combine(bar_date, time(15, 0), CHINA_TZ)
            if not (start_dt <= dt <= end_dt):
                continue

            bars.append(
                BarData(
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    interval=Interval.DAILY,
                    volume=float(row["volume"]),
                    turnover=float(row["amount"]),
                    open_interest=0,
                    open_price=float(row["open"]),
                    high_price=float(row["high"]),
                    low_price=float(row["low"]),
                    close_price=float(row["close"]),
                    gateway_name="AKSHARE",
                )
            )

        output(f"AkShare已获取{len(bars)}条{req.vt_symbol}-d历史数据。")
        return bars

    def _query_minute(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[BarData]:
        period = MINUTE_INTERVAL_MAP[req.interval]
        start_dt = self._to_dt(req.start)
        end_dt = self._to_dt(req.end) if req.end else datetime.now(CHINA_TZ)
        adjust = "" if period == "1" else self.adjust
        symbol = self._to_ak_symbol(req.symbol, req.exchange)

        if period == "1":
            output("AkShare的A股1分钟数据仅支持近5个交易日，且不支持复权。")

        try:
            df = self.ak.stock_zh_a_minute(symbol=symbol, period=period, adjust=adjust)
        except Exception as exc:
            output(f"AkShare查询A股分钟数据失败：{exc}")
            return []

        if df is None or df.empty:
            output(f"AkShare未返回{req.vt_symbol}的{req.interval.value}数据。")
            return []

        bars: list[BarData] = []
        for _, row in df.iterrows():
            dt = self._to_dt(row["day"])
            if not (start_dt <= dt <= end_dt):
                continue

            bars.append(
                BarData(
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    interval=req.interval,
                    volume=float(row["volume"]),
                    turnover=float(row["amount"]),
                    open_interest=0,
                    open_price=float(row["open"]),
                    high_price=float(row["high"]),
                    low_price=float(row["low"]),
                    close_price=float(row["close"]),
                    gateway_name="AKSHARE",
                )
            )

        output(f"AkShare已获取{len(bars)}条{req.vt_symbol}-{req.interval.value}历史数据。")
        return bars

    def _to_dt(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value))

        if dt.tzinfo is None:
            return dt.replace(tzinfo=CHINA_TZ)
        return dt.astimezone(CHINA_TZ)

    def _to_ak_symbol(self, symbol: str, exchange: Exchange) -> str:
        if exchange == Exchange.SSE:
            return f"sh{symbol}"
        if exchange == Exchange.SZSE:
            return f"sz{symbol}"
        if exchange == Exchange.BSE:
            return f"bj{symbol}"
        return symbol
