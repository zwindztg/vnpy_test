"""实时提醒 BaseApp 的核心配置、规则、轮询和通知逻辑。"""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Callable

import akshare as ak
import pandas as pd
from zoneinfo import ZoneInfo

try:
    from vnpy.trader.utility import TRADER_DIR

    BASE_DIR = Path(TRADER_DIR)
except Exception:
    BASE_DIR = Path.cwd()


CHINA_TZ = ZoneInfo("Asia/Shanghai")
MAX_SYMBOL_COUNT = 3
DEFAULT_CONFIG_PATH = BASE_DIR / "config" / "akshare_realtime_alert.json"
DEFAULT_HISTORY_PATH = BASE_DIR / "logs" / "akshare_realtime_alerts.csv"
DEFAULT_INTERVAL = "5m"
DEFAULT_POLL_SECONDS = 20
DEFAULT_ADJUST = "qfq"
DEFAULT_COOLDOWN_SECONDS = 300


@dataclass(frozen=True)
class SymbolConfig:
    """保存单只股票的提醒参数。"""

    vt_symbol: str
    breakout_price: float
    stop_loss_price: float
    fast_ma_window: int = 3
    slow_ma_window: int = 8
    enabled: bool = True


DEFAULT_SYMBOL_CONFIGS: tuple[SymbolConfig, ...] = (
    SymbolConfig(vt_symbol="601869.SSE", breakout_price=6.80, stop_loss_price=6.55),
    SymbolConfig(vt_symbol="600000.SSE", breakout_price=12.60, stop_loss_price=12.10),
)


@dataclass(frozen=True)
class AppConfig:
    """保存提醒应用的全局配置和标的列表。"""

    interval: str
    poll_seconds: int
    adjust: str
    cooldown_seconds: int
    alert_history_path: Path
    notification_enabled: bool
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


@dataclass
class LogData:
    """GUI 日志事件载荷。"""

    timestamp: str
    level: str
    source: str
    message: str


@dataclass
class RecordData:
    """触发记录事件载荷。"""

    occurred_at: str
    vt_symbol: str
    interval: str
    rule_name: str
    level: str
    rule_value: str
    triggered_bar_dt: str
    message: str


@dataclass
class SymbolStateData:
    """单只股票运行态快照。"""

    vt_symbol: str
    enabled: bool
    latest_bar_dt: str = ""
    latest_close: str = ""
    breakout_state: str = "未触发"
    stop_loss_state: str = "未触发"
    cross_state: str = "未触发"
    last_alert_at: str = ""
    last_error: str = ""
    status: str = "未启动"


@dataclass
class RunnerStatusData:
    """整体运行状态快照。"""

    running: bool
    paused: bool
    message: str
    updated_at: str


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

    def write(self, record: RecordData) -> None:
        """按统一字段顺序追加一条提醒记录。"""
        with self.path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    record.occurred_at,
                    record.vt_symbol,
                    record.interval,
                    record.rule_name,
                    record.level,
                    record.rule_value,
                    record.triggered_bar_dt,
                    record.message,
                ]
            )


def read_recent_records(path: Path, limit: int = 100) -> list[RecordData]:
    """读取最近若干条提醒记录，供 GUI 初始展示。"""
    if not path.exists():
        return []

    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)

    records: list[RecordData] = []
    for row in rows[-limit:]:
        records.append(
            RecordData(
                occurred_at=str(row.get("occurred_at", "")),
                vt_symbol=str(row.get("vt_symbol", "")),
                interval=str(row.get("interval", "")),
                rule_name=str(row.get("rule_name", "")),
                level=str(row.get("level", "")),
                rule_value=str(row.get("rule_value", "")),
                triggered_bar_dt=str(row.get("triggered_bar_dt", "")),
                message=str(row.get("message", "")),
            )
        )

    records.reverse()
    return records


def load_json_dict(path: Path) -> dict:
    """读取 JSON 配置，解析失败时回退到空字典。"""
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def load_app_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """从 JSON 读取提醒参数，缺失项回退到默认值。"""
    current = load_json_dict(path)

    interval = str(current.get("interval") or DEFAULT_INTERVAL)
    poll_seconds = int(current.get("poll_seconds") or DEFAULT_POLL_SECONDS)
    adjust = str(current.get("adjust") or DEFAULT_ADJUST)
    cooldown_seconds = int(current.get("cooldown_seconds") or DEFAULT_COOLDOWN_SECONDS)
    notification_enabled = bool(current.get("notification_enabled", True))

    history_path_text = str(current.get("alert_history_path") or DEFAULT_HISTORY_PATH)
    history_path = Path(history_path_text)
    if not history_path.is_absolute():
        history_path = BASE_DIR / history_path

    raw_symbols = current.get("symbols")
    symbol_configs = parse_symbol_configs(raw_symbols)

    return AppConfig(
        interval=interval,
        poll_seconds=poll_seconds,
        adjust=adjust,
        cooldown_seconds=cooldown_seconds,
        alert_history_path=history_path,
        notification_enabled=notification_enabled,
        symbol_configs=symbol_configs,
    )


def save_app_config(config: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """把当前配置保存回 JSON 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "interval": config.interval,
        "poll_seconds": config.poll_seconds,
        "adjust": config.adjust,
        "cooldown_seconds": config.cooldown_seconds,
        "notification_enabled": config.notification_enabled,
        "alert_history_path": stringify_path(config.alert_history_path),
        "symbols": [
            {
                "vt_symbol": symbol.vt_symbol,
                "breakout_price": symbol.breakout_price,
                "stop_loss_price": symbol.stop_loss_price,
                "fast_ma_window": symbol.fast_ma_window,
                "slow_ma_window": symbol.slow_ma_window,
                "enabled": symbol.enabled,
            }
            for symbol in config.symbol_configs[:MAX_SYMBOL_COUNT]
        ],
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_symbol_configs(raw_symbols: object) -> tuple[SymbolConfig, ...]:
    """把 JSON 里的 symbols 列表转换成内部配置对象。"""
    if not isinstance(raw_symbols, list) or not raw_symbols:
        return DEFAULT_SYMBOL_CONFIGS

    configs: list[SymbolConfig] = []
    for item in raw_symbols[:MAX_SYMBOL_COUNT]:
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
                fast_ma_window=max(1, int(item.get("fast_ma_window") or 3)),
                slow_ma_window=max(2, int(item.get("slow_ma_window") or 8)),
                enabled=bool(item.get("enabled", True)),
            )
        except (TypeError, ValueError):
            continue

        configs.append(config)

    return tuple(configs) if configs else DEFAULT_SYMBOL_CONFIGS


def build_default_state(config: SymbolConfig) -> SymbolStateData:
    """根据股票配置创建默认状态。"""
    return SymbolStateData(
        vt_symbol=config.vt_symbol,
        enabled=config.enabled,
        status="已禁用" if not config.enabled else "未启动",
    )


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


def stringify_path(path: Path) -> str:
    """尽量把路径写成相对工程根目录的形式，便于用户阅读。"""
    try:
        return str(path.relative_to(BASE_DIR))
    except ValueError:
        return str(path)


def make_log(level: str, source: str, message: str) -> LogData:
    """创建统一格式的日志载荷。"""
    return LogData(
        timestamp=datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        level=level,
        source=source,
        message=message,
    )


def make_runner_status(running: bool, paused: bool, message: str) -> RunnerStatusData:
    """创建统一格式的运行状态载荷。"""
    return RunnerStatusData(
        running=running,
        paused=paused,
        message=message,
        updated_at=datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    )


def send_desktop_notification(title: str, message: str) -> str | None:
    """在 macOS 上发送系统通知，失败时返回错误信息。"""
    if sys.platform != "darwin":
        return "当前系统不是 macOS，已跳过桌面通知。"

    script = (
        'display notification "{}" with title "{}"'
        .format(escape_applescript(message), escape_applescript(title))
    )

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return str(exc)

    if result.returncode != 0:
        error_text = result.stderr.strip() or result.stdout.strip() or "未知错误"
        return error_text
    return None


def escape_applescript(text: str) -> str:
    """对 AppleScript 字符串做最小转义。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class SymbolAlertService:
    """管理单只股票的所有提醒规则。"""

    def __init__(
        self,
        config: SymbolConfig,
        app_config: AppConfig,
        history_writer: AlertHistoryWriter,
        log_callback: Callable[[LogData], None],
        record_callback: Callable[[RecordData], None],
        state_callback: Callable[[SymbolStateData], None],
    ) -> None:
        self.config: SymbolConfig = config
        self.app_config: AppConfig = app_config
        self.history_writer: AlertHistoryWriter = history_writer
        self.log_callback = log_callback
        self.record_callback = record_callback
        self.state_callback = state_callback

        self.breakout_state: PriceAlertState = PriceAlertState()
        self.stop_loss_state: PriceAlertState = PriceAlertState()
        self.cross_state: CrossAlertState = CrossAlertState()
        self.last_completed_bar_dt: datetime | None = None
        self.stale_logged: bool = False
        self.state: SymbolStateData = build_default_state(config)

    def emit_state(self) -> None:
        """推送当前状态快照。"""
        self.state_callback(self.state)

    def log(self, level: str, message: str) -> None:
        """输出带来源的结构化日志。"""
        self.log_callback(make_log(level, self.config.vt_symbol, message))

    def run_once(self, now: datetime) -> None:
        """执行单只股票的单轮检查。"""
        if not self.config.enabled:
            self.state.enabled = False
            self.state.status = "已禁用"
            self.emit_state()
            return

        bars = self.fetch_completed_bars(now)
        if not bars:
            self.state.status = "暂无完整K线"
            self.state.last_error = ""
            self.log("INFO", "暂无可用的完整K线，跳过本轮。")
            self.emit_state()
            return

        latest_bar = bars[-1]
        self.state.latest_bar_dt = latest_bar.dt.strftime("%Y-%m-%d %H:%M")
        self.state.latest_close = f"{latest_bar.close_price:.3f}"
        self.state.last_error = ""

        is_new_bar = self.last_completed_bar_dt != latest_bar.dt
        if not is_new_bar:
            self.state.status = "等待新K线"
            if not self.stale_logged:
                self.log("INFO", f"最新完整K线仍为 {self.state.latest_bar_dt}，本轮不重复计算。")
                self.stale_logged = True
            self.emit_state()
            return

        self.stale_logged = False
        self.last_completed_bar_dt = latest_bar.dt
        self.state.status = "运行中"

        self.log(
            "INFO",
            (
                f"轮询成功，K线时间={self.state.latest_bar_dt}，"
                f"最新收盘价={self.state.latest_close}，"
                f"突破阈值={self.config.breakout_price:.3f}，"
                f"止损阈值={self.config.stop_loss_price:.3f}"
            ),
        )

        self.check_breakout_rule(latest_bar, now)
        self.check_stop_loss_rule(latest_bar, now)
        self.check_golden_cross_rule(bars, now)
        self.emit_state()

    def fetch_completed_bars(self, now: datetime) -> list[AlertBar]:
        """拉取最近一批分钟数据，并取已完成 K 线。"""
        symbol, _exchange = split_vt_symbol(self.config.vt_symbol)
        period = self.app_config.interval.removesuffix("m")
        start_dt = now - timedelta(days=3)
        end_dt = now + timedelta(minutes=1)

        df = ak.stock_zh_a_hist_min_em(
            symbol=symbol,
            start_date=start_dt.strftime("%Y-%m-%d %H:%M:%S"),
            end_date=end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            period=period,
            adjust=self.app_config.adjust,
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
            raise ValueError("AKShare 返回字段缺少时间列或收盘价列，无法解析分钟 K 线。")

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
                    f"{self.config.vt_symbol} {self.app_config.interval} 收盘站上 {self.config.breakout_price:.3f}，"
                    f"最新收盘价={latest_bar.close_price:.3f}，建议观察是否继续走强。"
                ),
                rule_value=latest_bar.close_price,
                triggered_bar_dt=latest_bar.dt,
            )
            self.breakout_state.last_alert_at = now

        self.breakout_state.is_triggered = is_above
        self.state.breakout_state = "已触发" if is_above else "未触发"

    def check_stop_loss_rule(self, latest_bar: AlertBar, now: datetime) -> None:
        """首次跌破止损阈值时输出风控型提醒。"""
        is_below = latest_bar.close_price <= self.config.stop_loss_price
        if is_below and not self.stop_loss_state.is_triggered and self.can_alert(self.stop_loss_state, now):
            self.emit_rule_alert(
                rule_name="stop_loss",
                level=AlertLevel.RISK,
                message=(
                    f"{self.config.vt_symbol} {self.app_config.interval} 收盘跌破 {self.config.stop_loss_price:.3f}，"
                    f"最新收盘价={latest_bar.close_price:.3f}，建议检查止损纪律。"
                ),
                rule_value=latest_bar.close_price,
                triggered_bar_dt=latest_bar.dt,
            )
            self.stop_loss_state.last_alert_at = now

        self.stop_loss_state.is_triggered = is_below
        self.state.stop_loss_state = "已触发" if is_below else "未触发"

    def check_golden_cross_rule(self, bars: list[AlertBar], now: datetime) -> None:
        """短均线上穿长均线时输出观察型提醒。"""
        if len(bars) < self.config.slow_ma_window + 1:
            self.state.cross_state = "数据不足"
            return

        close_series = pd.Series([bar.close_price for bar in bars], dtype="float64")
        fast_ma = close_series.rolling(self.config.fast_ma_window).mean()
        slow_ma = close_series.rolling(self.config.slow_ma_window).mean()

        fast_ma0 = fast_ma.iloc[-1]
        fast_ma1 = fast_ma.iloc[-2]
        slow_ma0 = slow_ma.iloc[-1]
        slow_ma1 = slow_ma.iloc[-2]

        if pd.isna(fast_ma0) or pd.isna(fast_ma1) or pd.isna(slow_ma0) or pd.isna(slow_ma1):
            self.state.cross_state = "数据不足"
            return

        cross_over = fast_ma0 >= slow_ma0 and fast_ma1 < slow_ma1
        cross_below = fast_ma0 <= slow_ma0 and fast_ma1 > slow_ma1

        if cross_over and not self.cross_state.golden_cross_triggered and self.can_alert(self.cross_state, now):
            self.emit_rule_alert(
                rule_name="golden_cross",
                level=AlertLevel.OBSERVE,
                message=(
                    f"{self.config.vt_symbol} {self.app_config.interval} 出现均线金叉，"
                    f"快线={fast_ma0:.3f}，慢线={slow_ma0:.3f}，建议观察趋势是否转强。"
                ),
                rule_value=float(fast_ma0 - slow_ma0),
                triggered_bar_dt=bars[-1].dt,
            )
            self.cross_state.golden_cross_triggered = True
            self.cross_state.last_alert_at = now
        elif cross_below:
            self.cross_state.golden_cross_triggered = False

        self.state.cross_state = "已触发" if self.cross_state.golden_cross_triggered else "未触发"

    def can_alert(self, state: PriceAlertState | CrossAlertState, now: datetime) -> bool:
        """为同类提醒增加最小冷却时间，减少来回波动造成的刷屏。"""
        if state.last_alert_at is None:
            return True
        return (now - state.last_alert_at).total_seconds() >= self.app_config.cooldown_seconds

    def emit_rule_alert(
        self,
        rule_name: str,
        level: AlertLevel,
        message: str,
        rule_value: float,
        triggered_bar_dt: datetime,
    ) -> None:
        """统一输出提醒并写入本地记录，方便后续复盘。"""
        self.log("INFO", f"{level.value}提醒：{message}")

        record = RecordData(
            occurred_at=datetime.now(CHINA_TZ).isoformat(),
            vt_symbol=self.config.vt_symbol,
            interval=self.app_config.interval,
            rule_name=rule_name,
            level=level.value,
            rule_value=f"{rule_value:.6f}",
            triggered_bar_dt=triggered_bar_dt.isoformat(),
            message=message,
        )
        self.history_writer.write(record)
        self.record_callback(record)
        self.state.last_alert_at = datetime.now(CHINA_TZ).strftime("%Y-%m-%d %H:%M:%S")

    def set_error(self, message: str) -> None:
        """记录拉数异常，让状态面板能看到最近错误。"""
        self.state.last_error = message
        self.state.status = "异常"
        self.log("ERROR", message)
        self.emit_state()


class AlertCenterRunner:
    """集中管理多只股票的轮询、交易时段控制和公共状态。"""

    def __init__(
        self,
        config: AppConfig,
        log_callback: Callable[[LogData], None],
        status_callback: Callable[[RunnerStatusData], None],
        record_callback: Callable[[RecordData], None],
        state_callback: Callable[[SymbolStateData], None],
    ) -> None:
        self.config: AppConfig = config
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.record_callback = record_callback
        self.state_callback = state_callback

        self.paused_logged: bool = False
        self.history_writer = AlertHistoryWriter(config.alert_history_path)
        self.services: list[SymbolAlertService] = [
            SymbolAlertService(
                config=symbol_config,
                app_config=config,
                history_writer=self.history_writer,
                log_callback=log_callback,
                record_callback=record_callback,
                state_callback=state_callback,
            )
            for symbol_config in config.symbol_configs[:MAX_SYMBOL_COUNT]
        ]

    def emit_status(self, running: bool, paused: bool, message: str) -> None:
        """推送整体运行状态。"""
        self.status_callback(make_runner_status(running, paused, message))

    def log(self, level: str, message: str) -> None:
        """推送 Runner 日志。"""
        self.log_callback(make_log(level, "Runner", message))

    def emit_initial_states(self) -> None:
        """在启动前推送一次所有标的的默认状态。"""
        for service in self.services:
            service.emit_state()

    def run_forever(self, stop_event: ThreadEvent) -> None:
        """持续轮询所有股票，直到外部请求停止。"""
        symbols = "、".join(service.config.vt_symbol for service in self.services)
        self.log(
            "INFO",
            (
                f"AKShare 实时提醒已启动，标的={symbols}，周期={self.config.interval}，"
                f"轮询间隔={self.config.poll_seconds}秒，冷却时间={self.config.cooldown_seconds}秒。"
            ),
        )
        self.log("INFO", f"提醒记录文件：{self.config.alert_history_path}")
        self.log("INFO", "当前仅输出 GUI 日志、CSV 记录和桌面通知，不会触发任何下单或委托动作。")
        self.emit_initial_states()
        self.emit_status(True, False, "运行中")

        while not stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self.log("ERROR", f"轮询出现未处理异常：{exc}")
                self.emit_status(True, False, f"轮询异常：{exc}")

            stop_event.wait(self.config.poll_seconds)

        self.emit_status(False, False, "已停止")
        self.log("INFO", "提醒轮询线程已停止。")

    def run_once(self) -> None:
        """执行一轮全市场小范围轮询。"""
        now = datetime.now(CHINA_TZ)
        if not is_a_share_trading_time(now):
            if not self.paused_logged:
                self.log("INFO", "当前非交易时段，暂停提醒。")
                self.emit_status(True, True, "非交易时段暂停")
                self.paused_logged = True
            return

        if self.paused_logged:
            self.emit_status(True, False, "运行中")
        self.paused_logged = False

        for service in self.services:
            try:
                service.run_once(now)
            except Exception as exc:
                service.set_error(f"AKShare 拉取失败：{exc}")

