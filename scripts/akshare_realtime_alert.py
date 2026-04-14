#!/usr/bin/env python3
"""使用 AKShare 实现 A 股准实时提醒的独立脚本。"""

from __future__ import annotations

import csv
import json
import time as time_module
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path

import akshare as ak
import pandas as pd
from zoneinfo import ZoneInfo


CHINA_TZ = ZoneInfo("Asia/Shanghai")
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "akshare_realtime_alert.json"
INTERVAL = "5m"
POLL_SECONDS = 20
ADJUST = "qfq"
COOLDOWN_SECONDS = 300
ALERT_HISTORY_PATH = Path(__file__).resolve().parents[1] / "logs" / "akshare_realtime_alerts.csv"


@dataclass(frozen=True)
class SymbolConfig:
    """保存单只股票的提醒参数。"""

    vt_symbol: str
    breakout_price: float
    stop_loss_price: float
    fast_ma_window: int = 3
    slow_ma_window: int = 8


SYMBOL_CONFIGS: tuple[SymbolConfig, ...] = (
    SymbolConfig(vt_symbol="601869.SSE", breakout_price=6.80, stop_loss_price=6.55),
    SymbolConfig(vt_symbol="600000.SSE", breakout_price=12.60, stop_loss_price=12.10),
)


@dataclass(frozen=True)
class AppConfig:
    """保存提醒脚本的全局配置和标的列表。"""

    interval: str
    poll_seconds: int
    adjust: str
    cooldown_seconds: int
    alert_history_path: Path
    symbol_configs: tuple[SymbolConfig, ...]


class AlertLevel(str, Enum):
    """提醒级别，帮助区分观察类和风控类提示。"""

    OBSERVE = "观察型"
    RISK = "风控型"


@dataclass
class AlertBar:
    """保存提醒逻辑需要的最小 K 线字段。"""

    dt: datetime
    close_price: float


@dataclass
class PriceAlertState:
    """保存价格型提醒的当前状态和冷却时间。"""

    is_triggered: bool = False
    last_alert_at: datetime | None = None


@dataclass
class CrossAlertState:
    """保存均线提醒的当前状态和冷却时间。"""

    golden_cross_triggered: bool = False
    last_alert_at: datetime | None = None


class SymbolAlertService:
    """管理单只股票的所有提醒规则。"""

    def __init__(
        self,
        config: SymbolConfig,
        interval: str,
        adjust: str,
        cooldown_seconds: int,
        history_writer: "AlertHistoryWriter",
    ) -> None:
        self.config: SymbolConfig = config
        self.interval: str = interval
        self.adjust: str = adjust
        self.cooldown_seconds: int = cooldown_seconds
        self.history_writer: AlertHistoryWriter = history_writer

        self.breakout_state: PriceAlertState = PriceAlertState()
        self.stop_loss_state: PriceAlertState = PriceAlertState()
        self.cross_state: CrossAlertState = CrossAlertState()
        self.last_completed_bar_dt: datetime | None = None
        self.stale_logged: bool = False

    def run_once(self, now: datetime) -> None:
        """执行单只股票的单轮检查。"""
        bars = self.fetch_completed_bars(now)
        if not bars:
            self.log_plain("暂无可用的完整K线，跳过本轮。")
            return

        latest_bar = bars[-1]
        is_new_bar = self.last_completed_bar_dt != latest_bar.dt
        if not is_new_bar:
            if not self.stale_logged:
                self.log_plain(
                    f"最新完整K线仍为 {latest_bar.dt.strftime('%Y-%m-%d %H:%M')}，本轮不重复计算。"
                )
                self.stale_logged = True
            return

        self.stale_logged = False
        self.last_completed_bar_dt = latest_bar.dt

        self.log_plain(
            (
                f"轮询成功，K线时间={latest_bar.dt.strftime('%Y-%m-%d %H:%M')}，"
                f"最新收盘价={latest_bar.close_price:.3f}，"
                f"突破阈值={self.config.breakout_price:.3f}，"
                f"止损阈值={self.config.stop_loss_price:.3f}"
            )
        )

        self.check_breakout_rule(latest_bar, now)
        self.check_stop_loss_rule(latest_bar, now)
        self.check_golden_cross_rule(bars, now)

    def fetch_completed_bars(self, now: datetime) -> list[AlertBar]:
        """拉取最近一批 5 分钟数据，并取已完成 K 线。"""
        symbol, _exchange = split_vt_symbol(self.config.vt_symbol)
        start_dt = now - timedelta(days=3)
        end_dt = now + timedelta(minutes=1)

        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol,
            start_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            period="5",
            adjust=self.adjust,
        )

        if df is None or df.empty:
            return []

        parsed = self.parse_bars(df)
        if not parsed:
            return []

        cutoff = floor_to_5m(now)
        return [bar for bar in parsed if bar.dt < cutoff]

    def parse_bars(self, df: pd.DataFrame) -> list[AlertBar]:
        """兼容 AKShare 常见字段名，提取提醒需要的时间和收盘价。"""
        time_column = find_first_existing_column(df, ["时间", "datetime", "day"])
        close_column = find_first_existing_column(df, ["收盘", "close"])

        if time_column is None or close_column is None:
            raise ValueError("AKShare 返回字段缺少时间列或收盘价列，无法解析 5 分钟 K 线。")

        bars: list[AlertBar] = []
        for _, row in df.iterrows():
            dt = ensure_china_tz(pd.to_datetime(row[time_column]).to_pydatetime())
            close_price = float(row[close_column])
            bars.append(AlertBar(dt=dt, close_price=close_price))

        bars.sort(key=lambda item: item.dt)
        return bars

    def check_breakout_rule(self, latest_bar: AlertBar, now: datetime) -> None:
        """首次站上突破阈值时输出观察型提醒。"""
        is_above = latest_bar.close_price >= self.config.breakout_price
        if is_above and not self.breakout_state.is_triggered and self.can_alert(self.breakout_state, now):
            self.emit_rule_alert(
                rule_name="breakout",
                level=AlertLevel.OBSERVE,
                message=(
                    f"{self.config.vt_symbol} {self.interval} 收盘站上 {self.config.breakout_price:.3f}，"
                    f"最新收盘价={latest_bar.close_price:.3f}，建议观察是否继续走强。"
                ),
                rule_value=latest_bar.close_price,
                triggered_bar_dt=latest_bar.dt,
            )
            self.breakout_state.last_alert_at = now

        self.breakout_state.is_triggered = is_above

    def check_stop_loss_rule(self, latest_bar: AlertBar, now: datetime) -> None:
        """首次跌破止损阈值时输出风控型提醒。"""
        is_below = latest_bar.close_price <= self.config.stop_loss_price
        if is_below and not self.stop_loss_state.is_triggered and self.can_alert(self.stop_loss_state, now):
            self.emit_rule_alert(
                rule_name="stop_loss",
                level=AlertLevel.RISK,
                message=(
                    f"{self.config.vt_symbol} {self.interval} 收盘跌破 {self.config.stop_loss_price:.3f}，"
                    f"最新收盘价={latest_bar.close_price:.3f}，建议检查止损纪律。"
                ),
                rule_value=latest_bar.close_price,
                triggered_bar_dt=latest_bar.dt,
            )
            self.stop_loss_state.last_alert_at = now

        self.stop_loss_state.is_triggered = is_below

    def check_golden_cross_rule(self, bars: list[AlertBar], now: datetime) -> None:
        """短均线上穿长均线时输出观察型提醒。"""
        if len(bars) < self.config.slow_ma_window + 1:
            return

        close_series = pd.Series([bar.close_price for bar in bars], dtype="float64")
        fast_ma = close_series.rolling(self.config.fast_ma_window).mean()
        slow_ma = close_series.rolling(self.config.slow_ma_window).mean()

        fast_ma0 = fast_ma.iloc[-1]
        fast_ma1 = fast_ma.iloc[-2]
        slow_ma0 = slow_ma.iloc[-1]
        slow_ma1 = slow_ma.iloc[-2]

        if pd.isna(fast_ma0) or pd.isna(fast_ma1) or pd.isna(slow_ma0) or pd.isna(slow_ma1):
            return

        cross_over = fast_ma0 >= slow_ma0 and fast_ma1 < slow_ma1
        cross_below = fast_ma0 <= slow_ma0 and fast_ma1 > slow_ma1

        if cross_over and not self.cross_state.golden_cross_triggered and self.can_alert(self.cross_state, now):
            self.emit_rule_alert(
                rule_name="golden_cross",
                level=AlertLevel.OBSERVE,
                message=(
                    f"{self.config.vt_symbol} {self.interval} 出现均线金叉，"
                    f"快线={fast_ma0:.3f}，慢线={slow_ma0:.3f}，建议观察趋势是否转强。"
                ),
                rule_value=float(fast_ma0 - slow_ma0),
                triggered_bar_dt=bars[-1].dt,
            )
            self.cross_state.golden_cross_triggered = True
            self.cross_state.last_alert_at = now
        elif cross_below:
            # 死叉出现后再允许下一次金叉提醒，避免持续停留在金叉状态时重复提醒。
            self.cross_state.golden_cross_triggered = False

    def can_alert(self, state: PriceAlertState | CrossAlertState, now: datetime) -> bool:
        """为同类提醒增加最小冷却时间，减少来回波动造成的刷屏。"""
        if state.last_alert_at is None:
            return True
        return (now - state.last_alert_at).total_seconds() >= self.cooldown_seconds

    def emit_rule_alert(
        self,
        rule_name: str,
        level: AlertLevel,
        message: str,
        rule_value: float,
        triggered_bar_dt: datetime,
    ) -> None:
        """统一输出提醒并写入本地记录，方便后续复盘。"""
        self.log_plain(f"{level.value}提醒：{message}")
        self.history_writer.write(
            occurred_at=datetime.now(CHINA_TZ),
            vt_symbol=self.config.vt_symbol,
            interval=self.interval,
            rule_name=rule_name,
            level=level.value,
            rule_value=rule_value,
            triggered_bar_dt=triggered_bar_dt,
            message=message,
        )

    def log_plain(self, message: str) -> None:
        """输出带时间戳和股票代码的中文单行日志。"""
        timestamp = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{self.config.vt_symbol}] {message}", flush=True)


class AlertHistoryWriter:
    """把触发过的提醒写入本地 CSV，方便收盘后复盘。"""

    HEADER: tuple[str, ...] = (
        "occurred_at",
        "vt_symbol",
        "interval",
        "rule_name",
        "level",
        "rule_value",
        "triggered_bar_dt",
        "message",
    )

    def __init__(self, path: Path) -> None:
        self.path: Path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with self.path.open("w", encoding="utf-8", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(self.HEADER)

    def write(
        self,
        occurred_at: datetime,
        vt_symbol: str,
        interval: str,
        rule_name: str,
        level: str,
        rule_value: float,
        triggered_bar_dt: datetime,
        message: str,
    ) -> None:
        """按统一字段顺序追加一条提醒记录。"""
        with self.path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    occurred_at.isoformat(),
                    vt_symbol,
                    interval,
                    rule_name,
                    level,
                    f"{rule_value:.6f}",
                    triggered_bar_dt.isoformat(),
                    message,
                ]
            )


class MultiSymbolAlertRunner:
    """集中管理多只股票的轮询、交易时段控制和公共日志。"""

    def __init__(
        self,
        symbol_configs: tuple[SymbolConfig, ...],
        interval: str,
        poll_seconds: int,
        adjust: str,
        cooldown_seconds: int,
        history_path: Path,
    ) -> None:
        self.interval: str = interval
        self.poll_seconds: int = poll_seconds
        self.paused_logged: bool = False
        self.history_writer = AlertHistoryWriter(history_path)
        self.services: list[SymbolAlertService] = [
            SymbolAlertService(
                config=config,
                interval=interval,
                adjust=adjust,
                cooldown_seconds=cooldown_seconds,
                history_writer=self.history_writer,
            )
            for config in symbol_configs
        ]

    def run(self) -> None:
        """持续轮询所有股票，直到用户手动中断。"""
        symbols = "、".join(service.config.vt_symbol for service in self.services)
        self.log(
            (
                f"AKShare 准实时提醒已启动，标的={symbols}，周期={self.interval}，"
                f"轮询间隔={self.poll_seconds}秒，冷却时间={COOLDOWN_SECONDS}秒。"
            )
        )
        self.log(f"提醒记录文件：{self.history_writer.path}")
        self.log("当前仅输出终端日志和本地CSV记录，不会触发任何下单或委托动作。")

        while True:
            try:
                self.run_once()
            except KeyboardInterrupt:
                self.log("收到手动中断，提醒脚本已停止。")
                raise SystemExit(0)
            except Exception as exc:
                self.log(f"轮询出现未处理异常：{exc}")

            time_module.sleep(self.poll_seconds)

    def run_once(self) -> None:
        """执行一轮全市场小范围轮询。"""
        now = datetime.now(CHINA_TZ)
        if not is_a_share_trading_time(now):
            if not self.paused_logged:
                self.log("当前非交易时段，暂停提醒。")
                self.paused_logged = True
            return

        self.paused_logged = False
        for service in self.services:
            try:
                service.run_once(now)
            except Exception as exc:
                self.log(f"[{service.config.vt_symbol}] AKShare 拉取失败：{exc}")

    def log(self, message: str) -> None:
        """输出全局运行日志。"""
        timestamp = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [Runner] {message}", flush=True)


def load_json_dict(path: Path) -> dict:
    """读取 JSON 配置，解析失败时回退到空字典。"""
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def load_app_config(path: Path) -> AppConfig:
    """优先从配置文件读取提醒参数，缺失项回退到脚本默认值。"""
    current = load_json_dict(path)

    interval = str(current.get("interval") or INTERVAL)
    poll_seconds = int(current.get("poll_seconds") or POLL_SECONDS)
    adjust = str(current.get("adjust") or ADJUST)
    cooldown_seconds = int(current.get("cooldown_seconds") or COOLDOWN_SECONDS)

    history_path_text = str(current.get("alert_history_path") or ALERT_HISTORY_PATH)
    history_path = Path(history_path_text)
    if not history_path.is_absolute():
        history_path = path.resolve().parents[1] / history_path

    raw_symbols = current.get("symbols")
    symbol_configs = parse_symbol_configs(raw_symbols)

    return AppConfig(
        interval=interval,
        poll_seconds=poll_seconds,
        adjust=adjust,
        cooldown_seconds=cooldown_seconds,
        alert_history_path=history_path,
        symbol_configs=symbol_configs,
    )


def parse_symbol_configs(raw_symbols: object) -> tuple[SymbolConfig, ...]:
    """把 JSON 里的 symbols 列表转换成内部配置对象。"""
    if not isinstance(raw_symbols, list) or not raw_symbols:
        return SYMBOL_CONFIGS

    configs: list[SymbolConfig] = []
    for item in raw_symbols:
        if not isinstance(item, dict):
            continue

        vt_symbol = str(item.get("vt_symbol") or "").strip().upper()
        if not vt_symbol:
            continue

        try:
            config = SymbolConfig(
                vt_symbol=vt_symbol,
                breakout_price=float(item.get("breakout_price")),
                stop_loss_price=float(item.get("stop_loss_price")),
                fast_ma_window=int(item.get("fast_ma_window") or 3),
                slow_ma_window=int(item.get("slow_ma_window") or 8),
            )
        except (TypeError, ValueError):
            continue

        configs.append(config)

    return tuple(configs) if configs else SYMBOL_CONFIGS


def split_vt_symbol(vt_symbol: str) -> tuple[str, str]:
    """把 vn.py 风格代码拆成纯数字代码和交易所后缀。"""
    parts = vt_symbol.strip().upper().split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"本地代码格式不正确：{vt_symbol}")

    symbol, exchange = parts
    if not symbol.isdigit():
        raise ValueError(f"AKShare 当前仅支持纯数字股票代码：{vt_symbol}")
    return symbol, exchange


def ensure_china_tz(dt: datetime) -> datetime:
    """统一把时间转换为上海时区。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CHINA_TZ)
    return dt.astimezone(CHINA_TZ)


def floor_to_5m(dt: datetime) -> datetime:
    """把当前时间向下取整到 5 分钟，用来排除未走完的当根 K 线。"""
    minute = dt.minute - dt.minute % 5
    return dt.replace(minute=minute, second=0, microsecond=0)


def is_a_share_trading_time(dt: datetime) -> bool:
    """仅在 A 股工作日交易时段内执行提醒逻辑。"""
    local_dt = ensure_china_tz(dt)
    if local_dt.weekday() >= 5:
        return False

    current_time = local_dt.time()
    morning_open = time(9, 30)
    morning_close = time(11, 30)
    afternoon_open = time(13, 0)
    afternoon_close = time(15, 0)

    return (
        morning_open <= current_time < morning_close
        or afternoon_open <= current_time < afternoon_close
    )


def find_first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """从候选字段名里找出 AKShare 当前版本实际返回的列名。"""
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def main() -> int:
    app_config = load_app_config(CONFIG_PATH)
    runner = MultiSymbolAlertRunner(
        symbol_configs=app_config.symbol_configs,
        interval=app_config.interval,
        poll_seconds=app_config.poll_seconds,
        adjust=app_config.adjust,
        cooldown_seconds=app_config.cooldown_seconds,
        history_path=app_config.alert_history_path,
    )
    runner.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
