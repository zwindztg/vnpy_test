from datetime import date, datetime, time, timedelta
from hashlib import blake2b
from math import cos, sin
from collections.abc import Callable, Iterator

from vnpy.trader.constant import Interval
from vnpy.trader.datafeed import BaseDatafeed
from vnpy.trader.object import BarData, HistoryRequest, TickData
from vnpy.trader.utility import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")


class LocalDemoDatafeed(BaseDatafeed):
    """Offline demo datafeed for local study without external credentials."""

    def init(self, output: Callable = print) -> bool:
        return True

    def query_bar_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[BarData]:
        if req.interval not in {Interval.MINUTE, Interval.HOUR, Interval.DAILY}:
            output("本地学习模式仅支持 1m、1h、d 三种K线周期。")
            return []

        end = req.end or datetime.now(CHINA_TZ)
        start = req.start

        datetimes = list(self._iter_datetimes(start, end, req.interval))
        if not datetimes:
            output(f"本地学习模式未生成任何{req.interval.value}示例数据，请检查日期范围。")
            return []

        seed = int(blake2b(req.vt_symbol.encode("utf-8"), digest_size=4).hexdigest(), 16)
        base_price = 3200 + seed % 800
        price = float(base_price)
        open_interest = 100000 + seed % 5000

        bars: list[BarData] = []
        for index, dt in enumerate(datetimes):
            trend = index * 0.03
            cycle = sin(index / 18) * 12 + cos(index / 57) * 20
            noise = sin((seed % 29 + index) / 5) * 4 + cos((seed % 17 + index) / 11) * 3

            close_price = max(100.0, base_price + trend + cycle + noise)
            open_price = price
            high_price = max(open_price, close_price) + abs(sin(index / 3)) * 3 + 0.6
            low_price = min(open_price, close_price) - abs(cos(index / 4)) * 3 - 0.6

            if req.interval == Interval.MINUTE:
                volume = 800 + (index % 240) * 6 + abs(sin(index / 10)) * 400
            elif req.interval == Interval.HOUR:
                volume = 12000 + (index % 4) * 1500 + abs(sin(index / 7)) * 3000
            else:
                volume = 80000 + (index % 5) * 12000 + abs(sin(index / 9)) * 15000

            turnover = (open_price + close_price) / 2 * volume
            open_interest += int(sin(index / 12) * 30)

            bars.append(
                BarData(
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    interval=req.interval,
                    volume=round(volume, 2),
                    turnover=round(turnover, 2),
                    open_interest=float(open_interest),
                    open_price=round(open_price, 2),
                    high_price=round(high_price, 2),
                    low_price=round(low_price, 2),
                    close_price=round(close_price, 2),
                    gateway_name="LOCALDEMO",
                )
            )
            price = close_price

        output(
            f"本地学习模式已生成{len(bars)}条{req.vt_symbol}-{req.interval.value}示例K线数据。"
        )
        return bars

    def query_tick_history(
        self, req: HistoryRequest, output: Callable = print
    ) -> list[TickData]:
        output("本地学习模式暂不提供 Tick 下载，请使用 1m、1h 或 d 周期。")
        return []

    def _iter_datetimes(
        self, start: datetime, end: datetime, interval: Interval
    ) -> Iterator[datetime]:
        start = self._ensure_tz(start)
        end = self._ensure_tz(end)
        if end < start:
            return

        if interval == Interval.MINUTE:
            yield from self._iter_minute_bars(start, end)
        elif interval == Interval.HOUR:
            yield from self._iter_hour_bars(start, end)
        elif interval == Interval.DAILY:
            yield from self._iter_daily_bars(start, end)

    def _iter_minute_bars(self, start: datetime, end: datetime) -> Iterator[datetime]:
        current_day = start.date()
        last_day = end.date()

        sessions = [
            (time(9, 30), 120),
            (time(13, 0), 120),
        ]

        while current_day <= last_day:
            if current_day.weekday() < 5:
                for session_start, count in sessions:
                    base = datetime.combine(current_day, session_start, CHINA_TZ)
                    for offset in range(count):
                        dt = base + timedelta(minutes=offset)
                        if start <= dt <= end:
                            yield dt
            current_day += timedelta(days=1)

    def _iter_hour_bars(self, start: datetime, end: datetime) -> Iterator[datetime]:
        current_day = start.date()
        last_day = end.date()
        hours = [time(9, 30), time(10, 30), time(13, 0), time(14, 0)]

        while current_day <= last_day:
            if current_day.weekday() < 5:
                for bar_time in hours:
                    dt = datetime.combine(current_day, bar_time, CHINA_TZ)
                    if start <= dt <= end:
                        yield dt
            current_day += timedelta(days=1)

    def _iter_daily_bars(self, start: datetime, end: datetime) -> Iterator[datetime]:
        current_day = start.date()
        last_day = end.date()

        while current_day <= last_day:
            if current_day.weekday() < 5:
                dt = datetime.combine(current_day, time(15, 0), CHINA_TZ)
                if start <= dt <= end:
                    yield dt
            current_day += timedelta(days=1)

    def _ensure_tz(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=CHINA_TZ)
        return dt.astimezone(CHINA_TZ)
