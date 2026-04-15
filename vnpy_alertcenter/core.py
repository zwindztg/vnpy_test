"""实时提醒 BaseApp 的核心配置、策略、轮询和通知逻辑。"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Callable, TypeAlias

import pandas as pd
import requests
from zoneinfo import ZoneInfo

try:
    from pytdx.config.hosts import hq_hosts as PYTDX_HQ_HOSTS
    from pytdx.hq import TdxHq_API
    from pytdx.params import TDXParams

    PYTDX_AVAILABLE = True
except Exception:
    PYTDX_HQ_HOSTS = []
    TdxHq_API = None
    TDXParams = None
    PYTDX_AVAILABLE = False

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
PYTDX_HOST_TIMEOUT = 1
PYTDX_BATCH_SIZE = 800
PYTDX_DEFAULT_HOST = ("上证云成都电信一", "218.6.170.47", 7709)
PYTDX_BACKUP_HOSTS: tuple[tuple[str, str, int], ...] = (
    ("上海电信主站Z1", "180.153.18.170", 7709),
    ("深圳电信主站Z1", "14.17.75.71", 7709),
    ("招商证券深圳行情", "119.147.212.81", 7709),
    ("华泰证券(南京电信)", "221.231.141.60", 7709),
    ("北京联通主站Z1", "202.108.253.130", 7709),
    ("杭州电信主站J1", "60.191.117.167", 7709),
)
SUPPORTED_INTERVALS: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
}
EASTMONEY_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "close",
}
PROJECT_PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
PYTDX_WORKING_HOST: tuple[str, str, int] | None = None
OPEN_PRICE_CACHE: dict[tuple[str, str], tuple[float, str]] = {}

BASIC_ALERT_STRATEGY = "BasicAlertStrategy"
LESSON_A_SHARE_LONG_ONLY = "LessonAShareLongOnlyStrategy"
LESSON_DONCHIAN = "LessonDonchianAShareStrategy"
LESSON_VOLUME_BREAKOUT = "LessonVolumeBreakoutAShareStrategy"

ParamValue: TypeAlias = int | float


@dataclass(frozen=True)
class ParamSpec:
    """描述单个策略参数的界面展示与数值约束。"""

    name: str
    label: str
    kind: str
    default: ParamValue
    minimum: float = 0.0
    maximum: float = 100000.0
    decimals: int = 0
    step: float = 1.0


@dataclass(frozen=True)
class SymbolConfig:
    """保存单只股票的提醒参数。"""

    vt_symbol: str
    strategy_name: str
    params: dict[str, ParamValue]
    enabled: bool = True


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
    high_price: float
    low_price: float
    volume: float


@dataclass
class RuleRuntimeState:
    """保存通用规则的当前状态和冷却时间。"""

    is_triggered: bool = False
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
    strategy_name: str
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
    strategy_name: str
    data_source: str = "待获取"
    latest_bar_dt: str = ""
    latest_close: str = ""
    signal_state: str = "未触发"
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


@dataclass
class AlertSignal:
    """统一表示一条真正要发出的提醒。"""

    rule_name: str
    level: AlertLevel
    message: str
    rule_value: float
    triggered_bar_dt: datetime


@dataclass
class StrategyEvaluationResult:
    """策略本轮计算结果。"""

    signal_state: str
    alerts: list[AlertSignal] = field(default_factory=list)


@dataclass(frozen=True)
class StrategyDefinition:
    """描述提醒策略的展示、参数和执行器。"""

    strategy_name: str
    display_name: str
    param_specs: tuple[ParamSpec, ...]
    validator: Callable[[dict[str, ParamValue]], None]
    evaluator_cls: type["BaseAlertEvaluator"]


def get_strategy_display_name(strategy_name: str) -> str:
    """把策略名转换成和 CTA 界面一致的双语显示文本。"""
    definition = STRATEGY_REGISTRY.get(strategy_name)
    if definition:
        return definition.display_name
    return STRATEGY_REGISTRY[BASIC_ALERT_STRATEGY].display_name


def normalize_strategy_name(strategy_name: str) -> str:
    """把未知策略名回退到基础提醒策略。"""
    if strategy_name in STRATEGY_REGISTRY:
        return strategy_name
    return BASIC_ALERT_STRATEGY


def get_strategy_definition(strategy_name: str) -> StrategyDefinition:
    """按名称读取策略元数据。"""
    return STRATEGY_REGISTRY[normalize_strategy_name(strategy_name)]


def get_strategy_param_specs(strategy_name: str) -> tuple[ParamSpec, ...]:
    """读取某个策略的参数说明。"""
    return get_strategy_definition(strategy_name).param_specs


def get_default_strategy_params(strategy_name: str) -> dict[str, ParamValue]:
    """返回策略默认参数，供 GUI 和配置迁移复用。"""
    return {
        spec.name: spec.default
        for spec in get_strategy_definition(strategy_name).param_specs
    }


def coerce_param_value(spec: ParamSpec, value: object) -> ParamValue:
    """把参数值转换成策略要求的数值类型。"""
    raw_value = spec.default if value is None else value
    try:
        numeric_value = float(raw_value)
    except (TypeError, ValueError):
        numeric_value = float(spec.default)

    if spec.kind == "int":
        return int(round(numeric_value))
    return round(numeric_value, spec.decimals)


def merge_strategy_params(strategy_name: str, raw_params: dict[str, object] | None) -> dict[str, ParamValue]:
    """按策略参数表合并默认值和当前配置。"""
    params = raw_params or {}
    merged: dict[str, ParamValue] = {}
    for spec in get_strategy_param_specs(strategy_name):
        merged[spec.name] = coerce_param_value(spec, params.get(spec.name, spec.default))
    return merged


def validate_basic_params(params: dict[str, ParamValue]) -> None:
    """校验基础提醒策略参数。"""
    breakout_price = float(params["breakout_price"])
    stop_loss_price = float(params["stop_loss_price"])
    fast_ma_window = int(params["fast_ma_window"])
    slow_ma_window = int(params["slow_ma_window"])

    if breakout_price <= 0:
        raise ValueError("突破价必须大于 0。")
    if stop_loss_price <= 0:
        raise ValueError("止损价必须大于 0。")
    if breakout_price <= stop_loss_price:
        raise ValueError("突破价应高于止损价。")
    if fast_ma_window <= 0 or slow_ma_window <= 0:
        raise ValueError("均线周期必须大于 0。")
    if fast_ma_window >= slow_ma_window:
        raise ValueError("快均线周期必须小于慢均线周期。")


def validate_ma_params(params: dict[str, ParamValue]) -> None:
    """校验均线提醒策略参数。"""
    fast_window = int(params["fast_window"])
    slow_window = int(params["slow_window"])
    if fast_window <= 0 or slow_window <= 0:
        raise ValueError("均线周期必须大于 0。")
    if fast_window >= slow_window:
        raise ValueError("快均线周期必须小于慢均线周期。")


def validate_donchian_params(params: dict[str, ParamValue]) -> None:
    """校验唐奇安提醒策略参数。"""
    entry_window = int(params["entry_window"])
    exit_window = int(params["exit_window"])
    if entry_window <= 1 or exit_window <= 1:
        raise ValueError("唐奇安窗口至少需要 2 根 K 线。")
    if entry_window <= exit_window:
        raise ValueError("突破观察周期应大于离场观察周期。")


def validate_volume_breakout_params(params: dict[str, ParamValue]) -> None:
    """校验放量突破提醒策略参数。"""
    breakout_window = int(params["breakout_window"])
    exit_window = int(params["exit_window"])
    volume_window = int(params["volume_window"])
    volume_ratio = float(params["volume_ratio"])

    if breakout_window <= 1 or exit_window <= 1 or volume_window <= 1:
        raise ValueError("突破、离场和成交量窗口至少需要 2 根 K 线。")
    if breakout_window <= exit_window:
        raise ValueError("突破观察周期应大于离场观察周期。")
    if volume_ratio <= 0:
        raise ValueError("放量倍数阈值必须大于 0。")


class BaseAlertEvaluator:
    """提醒策略 evaluator 的统一基类。"""

    def evaluate(
        self,
        service: "SymbolAlertService",
        bars: list[AlertBar],
        now: datetime,
    ) -> StrategyEvaluationResult:
        raise NotImplementedError

    def close_series(self, bars: list[AlertBar]) -> pd.Series:
        """提取收盘价序列。"""
        return pd.Series([bar.close_price for bar in bars], dtype="float64")

    def high_series(self, bars: list[AlertBar]) -> pd.Series:
        """提取最高价序列。"""
        return pd.Series([bar.high_price for bar in bars], dtype="float64")

    def low_series(self, bars: list[AlertBar]) -> pd.Series:
        """提取最低价序列。"""
        return pd.Series([bar.low_price for bar in bars], dtype="float64")

    def volume_series(self, bars: list[AlertBar]) -> pd.Series:
        """提取成交量序列。"""
        return pd.Series([bar.volume for bar in bars], dtype="float64")


class BasicAlertEvaluator(BaseAlertEvaluator):
    """价格突破、止损和均线观察的基础提醒。"""

    def evaluate(
        self,
        service: "SymbolAlertService",
        bars: list[AlertBar],
        now: datetime,
    ) -> StrategyEvaluationResult:
        params = service.config.params
        latest_bar = bars[-1]
        alerts: list[AlertSignal] = []

        breakout_price = float(params["breakout_price"])
        stop_loss_price = float(params["stop_loss_price"])
        fast_ma_window = int(params["fast_ma_window"])
        slow_ma_window = int(params["slow_ma_window"])

        breakout_state = service.get_rule_state("breakout")
        stop_loss_state = service.get_rule_state("stop_loss")
        ma_relation_state = service.get_rule_state("ma_relation")
        golden_cross_state = service.get_rule_state("golden_cross")

        is_above = latest_bar.close_price >= breakout_price
        if is_above and not breakout_state.is_triggered and service.can_alert(breakout_state, now):
            alerts.append(
                AlertSignal(
                    rule_name="breakout",
                    level=AlertLevel.OBSERVE,
                    message=(
                        f"{service.config.vt_symbol} {service.app_config.interval} 收盘站上 {breakout_price:.3f}，"
                        f"最新收盘价={latest_bar.close_price:.3f}，建议观察是否继续走强。"
                    ),
                    rule_value=latest_bar.close_price,
                    triggered_bar_dt=latest_bar.dt,
                )
            )
            breakout_state.last_alert_at = now
        breakout_state.is_triggered = is_above

        is_below = latest_bar.close_price <= stop_loss_price
        if is_below and not stop_loss_state.is_triggered and service.can_alert(stop_loss_state, now):
            alerts.append(
                AlertSignal(
                    rule_name="stop_loss",
                    level=AlertLevel.RISK,
                    message=(
                        f"{service.config.vt_symbol} {service.app_config.interval} 收盘跌破 {stop_loss_price:.3f}，"
                        f"最新收盘价={latest_bar.close_price:.3f}，建议检查止损纪律。"
                    ),
                    rule_value=latest_bar.close_price,
                    triggered_bar_dt=latest_bar.dt,
                )
            )
            stop_loss_state.last_alert_at = now
        stop_loss_state.is_triggered = is_below

        if len(bars) < slow_ma_window + 1:
            ma_state = "均线数据不足"
        else:
            close_series = self.close_series(bars)
            fast_ma = close_series.rolling(fast_ma_window).mean()
            slow_ma = close_series.rolling(slow_ma_window).mean()

            fast_ma0 = fast_ma.iloc[-1]
            fast_ma1 = fast_ma.iloc[-2]
            slow_ma0 = slow_ma.iloc[-1]
            slow_ma1 = slow_ma.iloc[-2]

            if pd.isna(fast_ma0) or pd.isna(fast_ma1) or pd.isna(slow_ma0) or pd.isna(slow_ma1):
                ma_state = "均线数据不足"
            else:
                current_above = bool(fast_ma0 >= slow_ma0)
                cross_over = current_above and fast_ma1 < slow_ma1

                if cross_over and service.can_alert(golden_cross_state, now):
                    alerts.append(
                        AlertSignal(
                            rule_name="golden_cross",
                            level=AlertLevel.OBSERVE,
                            message=(
                                f"{service.config.vt_symbol} {service.app_config.interval} 出现均线金叉，"
                                f"快线={fast_ma0:.3f}，慢线={slow_ma0:.3f}，建议观察趋势是否转强。"
                            ),
                            rule_value=float(fast_ma0 - slow_ma0),
                            triggered_bar_dt=latest_bar.dt,
                        )
                    )
                    golden_cross_state.last_alert_at = now

                ma_relation_state.is_triggered = current_above
                ma_state = "均线多头" if current_above else "均线空头"

        signal_parts = [
            "突破已触发" if breakout_state.is_triggered else "未突破",
            "跌破止损" if stop_loss_state.is_triggered else "未破止损",
            ma_state,
        ]
        return StrategyEvaluationResult(signal_state=" | ".join(signal_parts), alerts=alerts)


class MaCrossAlertEvaluator(BaseAlertEvaluator):
    """A 股长仓学习策略的提醒版 evaluator。"""

    def evaluate(
        self,
        service: "SymbolAlertService",
        bars: list[AlertBar],
        now: datetime,
    ) -> StrategyEvaluationResult:
        params = service.config.params
        fast_window = int(params["fast_window"])
        slow_window = int(params["slow_window"])
        latest_bar = bars[-1]

        if len(bars) < slow_window + 1:
            return StrategyEvaluationResult(signal_state="均线数据不足")

        close_series = self.close_series(bars)
        fast_ma = close_series.rolling(fast_window).mean()
        slow_ma = close_series.rolling(slow_window).mean()

        fast_ma0 = fast_ma.iloc[-1]
        fast_ma1 = fast_ma.iloc[-2]
        slow_ma0 = slow_ma.iloc[-1]
        slow_ma1 = slow_ma.iloc[-2]

        if pd.isna(fast_ma0) or pd.isna(fast_ma1) or pd.isna(slow_ma0) or pd.isna(slow_ma1):
            return StrategyEvaluationResult(signal_state="均线数据不足")

        relation_state = service.get_rule_state("ma_relation")
        golden_state = service.get_rule_state("golden_cross")
        death_state = service.get_rule_state("death_cross")
        alerts: list[AlertSignal] = []

        current_above = bool(fast_ma0 >= slow_ma0)
        cross_over = current_above and fast_ma1 < slow_ma1
        cross_below = bool(fast_ma0 <= slow_ma0 and fast_ma1 > slow_ma1)

        if cross_over and service.can_alert(golden_state, now):
            alerts.append(
                AlertSignal(
                    rule_name="golden_cross",
                    level=AlertLevel.OBSERVE,
                    message=(
                        f"{service.config.vt_symbol} {service.app_config.interval} 出现均线金叉，"
                        f"快线={fast_ma0:.3f}，慢线={slow_ma0:.3f}，建议关注多头趋势是否成立。"
                    ),
                    rule_value=float(fast_ma0 - slow_ma0),
                    triggered_bar_dt=latest_bar.dt,
                )
            )
            golden_state.last_alert_at = now
        elif cross_below and service.can_alert(death_state, now):
            alerts.append(
                AlertSignal(
                    rule_name="death_cross",
                    level=AlertLevel.RISK,
                    message=(
                        f"{service.config.vt_symbol} {service.app_config.interval} 出现均线死叉，"
                        f"快线={fast_ma0:.3f}，慢线={slow_ma0:.3f}，建议检查离场节奏。"
                    ),
                    rule_value=float(fast_ma0 - slow_ma0),
                    triggered_bar_dt=latest_bar.dt,
                )
            )
            death_state.last_alert_at = now

        relation_state.is_triggered = current_above
        signal_state = "均线多头" if current_above else "均线空头"
        return StrategyEvaluationResult(signal_state=signal_state, alerts=alerts)


class DonchianAlertEvaluator(BaseAlertEvaluator):
    """A 股唐奇安突破策略的提醒版 evaluator。"""

    def evaluate(
        self,
        service: "SymbolAlertService",
        bars: list[AlertBar],
        now: datetime,
    ) -> StrategyEvaluationResult:
        params = service.config.params
        entry_window = int(params["entry_window"])
        exit_window = int(params["exit_window"])
        latest_bar = bars[-1]

        required_window = max(entry_window, exit_window) + 1
        if len(bars) < required_window:
            return StrategyEvaluationResult(signal_state="唐奇安数据不足")

        high_series = self.high_series(bars)
        low_series = self.low_series(bars)

        entry_up = high_series.shift(1).rolling(entry_window).max().iloc[-1]
        exit_down = low_series.shift(1).rolling(exit_window).min().iloc[-1]

        if pd.isna(entry_up) or pd.isna(exit_down):
            return StrategyEvaluationResult(signal_state="唐奇安数据不足")

        position_state = service.get_rule_state("donchian_long_active")
        entry_state = service.get_rule_state("donchian_entry")
        exit_state = service.get_rule_state("donchian_exit")
        alerts: list[AlertSignal] = []

        current_long = position_state.is_triggered
        if not current_long and latest_bar.close_price > float(entry_up):
            if service.can_alert(entry_state, now):
                alerts.append(
                    AlertSignal(
                        rule_name="donchian_breakout",
                        level=AlertLevel.OBSERVE,
                        message=(
                            f"{service.config.vt_symbol} {service.app_config.interval} 收盘突破唐奇安上轨，"
                            f"收盘价={latest_bar.close_price:.3f}，突破线={float(entry_up):.3f}，建议关注强势延续。"
                        ),
                        rule_value=latest_bar.close_price,
                        triggered_bar_dt=latest_bar.dt,
                    )
                )
                entry_state.last_alert_at = now
            current_long = True
        elif current_long and latest_bar.close_price < float(exit_down):
            if service.can_alert(exit_state, now):
                alerts.append(
                    AlertSignal(
                        rule_name="donchian_exit",
                        level=AlertLevel.RISK,
                        message=(
                            f"{service.config.vt_symbol} {service.app_config.interval} 收盘跌破唐奇安离场线，"
                            f"收盘价={latest_bar.close_price:.3f}，离场线={float(exit_down):.3f}，建议检查离场执行。"
                        ),
                        rule_value=latest_bar.close_price,
                        triggered_bar_dt=latest_bar.dt,
                    )
                )
                exit_state.last_alert_at = now
            current_long = False

        position_state.is_triggered = current_long
        if current_long:
            signal_state = f"突破有效 | 离场线={float(exit_down):.3f}"
        else:
            signal_state = f"观察中 | 突破线={float(entry_up):.3f}"
        return StrategyEvaluationResult(signal_state=signal_state, alerts=alerts)


class VolumeBreakoutAlertEvaluator(BaseAlertEvaluator):
    """A 股短线放量突破策略的提醒版 evaluator。"""

    def evaluate(
        self,
        service: "SymbolAlertService",
        bars: list[AlertBar],
        now: datetime,
    ) -> StrategyEvaluationResult:
        params = service.config.params
        breakout_window = int(params["breakout_window"])
        exit_window = int(params["exit_window"])
        volume_window = int(params["volume_window"])
        volume_ratio_threshold = float(params["volume_ratio"])
        latest_bar = bars[-1]

        required_window = max(breakout_window, exit_window, volume_window) + 1
        if len(bars) < required_window:
            return StrategyEvaluationResult(signal_state="放量突破数据不足")

        high_series = self.high_series(bars)
        low_series = self.low_series(bars)
        volume_series = self.volume_series(bars)

        entry_up = high_series.shift(1).rolling(breakout_window).max().iloc[-1]
        exit_down = low_series.shift(1).rolling(exit_window).min().iloc[-1]
        volume_ma = volume_series.shift(1).rolling(volume_window).mean().iloc[-1]

        if pd.isna(entry_up) or pd.isna(exit_down) or pd.isna(volume_ma):
            return StrategyEvaluationResult(signal_state="放量突破数据不足")

        volume_ma = float(volume_ma)
        ratio_value = latest_bar.volume / volume_ma if volume_ma > 0 else 0.0

        position_state = service.get_rule_state("volume_long_active")
        breakout_state = service.get_rule_state("volume_breakout")
        exit_state = service.get_rule_state("volume_exit")
        alerts: list[AlertSignal] = []

        breakout_signal = latest_bar.close_price > float(entry_up) and ratio_value >= volume_ratio_threshold
        exit_signal = latest_bar.close_price < float(exit_down)

        current_long = position_state.is_triggered
        if not current_long and breakout_signal:
            if service.can_alert(breakout_state, now):
                alerts.append(
                    AlertSignal(
                        rule_name="volume_breakout",
                        level=AlertLevel.OBSERVE,
                        message=(
                            f"{service.config.vt_symbol} {service.app_config.interval} 出现放量突破，"
                            f"收盘价={latest_bar.close_price:.3f}，突破线={float(entry_up):.3f}，"
                            f"量比={ratio_value:.2f}，建议关注短线跟随机会。"
                        ),
                        rule_value=ratio_value,
                        triggered_bar_dt=latest_bar.dt,
                    )
                )
                breakout_state.last_alert_at = now
            current_long = True
        elif current_long and exit_signal:
            if service.can_alert(exit_state, now):
                alerts.append(
                    AlertSignal(
                        rule_name="volume_exit",
                        level=AlertLevel.RISK,
                        message=(
                            f"{service.config.vt_symbol} {service.app_config.interval} 跌破短线离场线，"
                            f"收盘价={latest_bar.close_price:.3f}，离场线={float(exit_down):.3f}，建议关注节奏回撤。"
                        ),
                        rule_value=latest_bar.close_price,
                        triggered_bar_dt=latest_bar.dt,
                    )
                )
                exit_state.last_alert_at = now
            current_long = False

        position_state.is_triggered = current_long
        if current_long:
            signal_state = f"放量突破有效 | 离场线={float(exit_down):.3f}"
        else:
            signal_state = (
                f"观察中 | 突破线={float(entry_up):.3f} | 量比={ratio_value:.2f}/{volume_ratio_threshold:.2f}"
            )
        return StrategyEvaluationResult(signal_state=signal_state, alerts=alerts)


STRATEGY_REGISTRY: dict[str, StrategyDefinition] = {
    BASIC_ALERT_STRATEGY: StrategyDefinition(
        strategy_name=BASIC_ALERT_STRATEGY,
        display_name="基础提醒策略（BasicAlertStrategy）",
        param_specs=(
            ParamSpec("breakout_price", "突破价", "float", 6.80, minimum=0.001, decimals=3, step=0.01),
            ParamSpec("stop_loss_price", "止损价", "float", 6.55, minimum=0.001, decimals=3, step=0.01),
            ParamSpec("fast_ma_window", "快均线", "int", 3, minimum=1, maximum=250, step=1),
            ParamSpec("slow_ma_window", "慢均线", "int", 8, minimum=2, maximum=250, step=1),
        ),
        validator=validate_basic_params,
        evaluator_cls=BasicAlertEvaluator,
    ),
    LESSON_A_SHARE_LONG_ONLY: StrategyDefinition(
        strategy_name=LESSON_A_SHARE_LONG_ONLY,
        display_name="A股长仓学习策略（LessonAShareLongOnlyStrategy）",
        param_specs=(
            ParamSpec("fast_window", "快均线", "int", 5, minimum=1, maximum=250, step=1),
            ParamSpec("slow_window", "慢均线", "int", 20, minimum=2, maximum=250, step=1),
        ),
        validator=validate_ma_params,
        evaluator_cls=MaCrossAlertEvaluator,
    ),
    LESSON_DONCHIAN: StrategyDefinition(
        strategy_name=LESSON_DONCHIAN,
        display_name="A股唐奇安突破策略（LessonDonchianAShareStrategy）",
        param_specs=(
            ParamSpec("entry_window", "突破周期", "int", 20, minimum=2, maximum=250, step=1),
            ParamSpec("exit_window", "离场周期", "int", 10, minimum=2, maximum=250, step=1),
        ),
        validator=validate_donchian_params,
        evaluator_cls=DonchianAlertEvaluator,
    ),
    LESSON_VOLUME_BREAKOUT: StrategyDefinition(
        strategy_name=LESSON_VOLUME_BREAKOUT,
        display_name="A股短线放量突破策略（LessonVolumeBreakoutAShareStrategy）",
        param_specs=(
            ParamSpec("breakout_window", "突破周期", "int", 5, minimum=2, maximum=250, step=1),
            ParamSpec("exit_window", "离场周期", "int", 3, minimum=2, maximum=250, step=1),
            ParamSpec("volume_window", "均量周期", "int", 5, minimum=2, maximum=250, step=1),
            ParamSpec("volume_ratio", "放量倍数", "float", 1.5, minimum=0.1, maximum=100.0, decimals=2, step=0.1),
        ),
        validator=validate_volume_breakout_params,
        evaluator_cls=VolumeBreakoutAlertEvaluator,
    ),
}

STRATEGY_ORDER: tuple[str, ...] = (
    BASIC_ALERT_STRATEGY,
    LESSON_A_SHARE_LONG_ONLY,
    LESSON_DONCHIAN,
    LESSON_VOLUME_BREAKOUT,
)


DEFAULT_SYMBOL_CONFIGS: tuple[SymbolConfig, ...] = (
    SymbolConfig(
        vt_symbol="601869.SSE",
        strategy_name=BASIC_ALERT_STRATEGY,
        params=get_default_strategy_params(BASIC_ALERT_STRATEGY),
    ),
    SymbolConfig(
        vt_symbol="600000.SSE",
        strategy_name=BASIC_ALERT_STRATEGY,
        params={
            "breakout_price": 12.60,
            "stop_loss_price": 12.10,
            "fast_ma_window": 3,
            "slow_ma_window": 8,
        },
        enabled=False,
    ),
)


class AlertHistoryWriter:
    """把触发过的提醒写入本地 CSV，方便收盘后复盘。"""

    HEADER: tuple[str, ...] = (
        "occurred_at",
        "vt_symbol",
        "strategy_name",
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
            return

        with self.path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.reader(file)
            current_header = tuple(next(reader, []))

        if current_header != self.HEADER:
            self.migrate_legacy_file()

    def write(self, record: RecordData) -> None:
        """按统一字段顺序追加一条提醒记录。"""
        with self.path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    record.occurred_at,
                    record.vt_symbol,
                    record.strategy_name,
                    record.interval,
                    record.rule_name,
                    record.level,
                    record.rule_value,
                    record.triggered_bar_dt,
                    record.message,
                ]
            )

    def migrate_legacy_file(self) -> None:
        """把旧版缺少策略列的 CSV 迁移到新结构。"""
        records = read_recent_records(self.path, limit=100000)
        with self.path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(self.HEADER)
            for record in reversed(records):
                writer.writerow(
                    [
                        record.occurred_at,
                        record.vt_symbol,
                        record.strategy_name,
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
                strategy_name=str(row.get("strategy_name") or BASIC_ALERT_STRATEGY),
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


def get_project_trader_dir() -> Path:
    """返回项目内的 .vntrader 目录。"""
    trader_dir = BASE_DIR / ".vntrader"
    trader_dir.mkdir(parents=True, exist_ok=True)
    return trader_dir


def get_project_database_path() -> Path:
    """读取项目当前使用的 SQLite 数据库文件路径。"""
    trader_dir = get_project_trader_dir()
    setting_path = trader_dir / "vt_setting.json"
    current = load_json_dict(setting_path)
    database_name = str(current.get("database.database") or "database.db")
    return trader_dir / database_name


def load_app_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """从 JSON 读取提醒参数，缺失项回退到默认值。"""
    current = load_json_dict(path)

    interval = normalize_interval(str(current.get("interval") or DEFAULT_INTERVAL))
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
        "interval": normalize_interval(config.interval),
        "poll_seconds": config.poll_seconds,
        "adjust": config.adjust,
        "cooldown_seconds": config.cooldown_seconds,
        "notification_enabled": config.notification_enabled,
        "alert_history_path": stringify_path(config.alert_history_path),
        "symbols": [
            {
                "vt_symbol": symbol.vt_symbol,
                "strategy_name": symbol.strategy_name,
                "params": merge_strategy_params(symbol.strategy_name, symbol.params),
                "enabled": symbol.enabled,
            }
            for symbol in config.symbol_configs[:MAX_SYMBOL_COUNT]
        ],
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_symbol_config_from_json(item: dict[str, object]) -> SymbolConfig | None:
    """兼容旧配置和新配置，构建单只股票参数。"""
    vt_symbol = str(item.get("vt_symbol") or "").strip().upper()
    if not vt_symbol:
        return None

    strategy_name = normalize_strategy_name(str(item.get("strategy_name") or BASIC_ALERT_STRATEGY))
    params_from_payload = item.get("params")
    raw_params: dict[str, object] = {}
    if isinstance(params_from_payload, dict):
        raw_params.update(params_from_payload)

    # 兼容旧版固定字段配置，自动迁移为基础提醒策略。
    for legacy_key in ("breakout_price", "stop_loss_price", "fast_ma_window", "slow_ma_window"):
        if legacy_key in item and legacy_key not in raw_params:
            raw_params[legacy_key] = item.get(legacy_key)

    config = SymbolConfig(
        vt_symbol=vt_symbol,
        strategy_name=strategy_name,
        params=raw_params,
        enabled=bool(item.get("enabled", True)),
    )

    try:
        return normalize_symbol_config(config)
    except ValueError:
        return None


def parse_symbol_configs(raw_symbols: object) -> tuple[SymbolConfig, ...]:
    """把 JSON 里的 symbols 列表转换成内部配置对象。"""
    if not isinstance(raw_symbols, list) or not raw_symbols:
        return DEFAULT_SYMBOL_CONFIGS

    configs: list[SymbolConfig] = []
    seen_symbols: set[str] = set()
    for item in raw_symbols[:MAX_SYMBOL_COUNT]:
        if not isinstance(item, dict):
            continue

        config = build_symbol_config_from_json(item)
        if config is None:
            continue
        if config.vt_symbol in seen_symbols:
            continue

        seen_symbols.add(config.vt_symbol)
        configs.append(config)

    return tuple(configs) if configs else DEFAULT_SYMBOL_CONFIGS


def build_default_state(config: SymbolConfig) -> SymbolStateData:
    """根据股票配置创建默认状态。"""
    return SymbolStateData(
        vt_symbol=config.vt_symbol,
        enabled=config.enabled,
        strategy_name=config.strategy_name,
        data_source="待获取" if config.enabled else "-",
        status="已禁用" if not config.enabled else "未启动",
        signal_state="待运行" if config.enabled else "已禁用",
    )


def split_vt_symbol(vt_symbol: str) -> tuple[str, str]:
    """把 vn.py 风格代码拆成纯数字代码和交易所后缀。"""
    parts = vt_symbol.strip().upper().split(".", 1)
    if len(parts) != 2:
        raise ValueError(f"本地代码格式不正确：{vt_symbol}")

    symbol, exchange = parts
    if not symbol.isdigit():
        raise ValueError(f"当前提醒中心仅支持纯数字股票代码：{vt_symbol}")
    return symbol, exchange


def normalize_symbol_config(config: SymbolConfig) -> SymbolConfig:
    """把单只股票配置规范化，并在出错时给出明确原因。"""
    symbol, exchange = split_vt_symbol(config.vt_symbol)
    if exchange not in {"SSE", "SZSE", "BSE"}:
        raise ValueError(f"{config.vt_symbol} 的交易所后缀不受支持。")

    strategy_name = normalize_strategy_name(config.strategy_name)
    params = merge_strategy_params(strategy_name, config.params)
    definition = get_strategy_definition(strategy_name)
    definition.validator(params)

    return SymbolConfig(
        vt_symbol=f"{symbol}.{exchange}",
        strategy_name=strategy_name,
        params=params,
        enabled=bool(config.enabled),
    )


def ensure_valid_symbol_config(config: SymbolConfig) -> SymbolConfig:
    """对外暴露带异常信息的配置校验接口。"""
    return normalize_symbol_config(config)


def validate_symbol_config(config: SymbolConfig) -> bool:
    """保留布尔型校验接口，方便旧代码复用。"""
    try:
        normalize_symbol_config(config)
    except ValueError:
        return False
    return True


def normalize_interval(interval: str) -> str:
    """把周期限制在当前支持的分钟集合里。"""
    normalized = interval.strip().lower()
    if normalized in SUPPORTED_INTERVALS:
        return normalized
    return DEFAULT_INTERVAL


def get_interval_minutes(interval: str) -> int:
    """根据配置周期返回对应分钟数。"""
    normalized = normalize_interval(interval)
    return SUPPORTED_INTERVALS[normalized]


def ensure_china_tz(dt: datetime) -> datetime:
    """统一把时间转换为上海时区。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=CHINA_TZ)
    return dt.astimezone(CHINA_TZ)


def floor_to_interval(dt: datetime, minutes: int) -> datetime:
    """把当前时间按分钟周期向下取整，用来排除未走完的当根 K 线。"""
    minute = dt.minute - dt.minute % minutes
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
    """从候选字段名里找出当前数据源实际返回的列名。"""
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


def extract_session_open_price(df: pd.DataFrame) -> float:
    """从分钟线里提取最近一个交易日的开盘价。"""
    if df is None or df.empty:
        raise ValueError("分钟线为空，无法提取开盘价。")

    time_column = find_first_existing_column(df, ["时间", "datetime", "day"])
    open_column = find_first_existing_column(df, ["开盘", "open"])
    if time_column is None or open_column is None:
        raise ValueError("分钟线缺少时间列或开盘价列。")

    current = df.copy()
    current[time_column] = pd.to_datetime(current[time_column])
    current = current.sort_values(time_column).drop_duplicates(subset=[time_column], keep="last")
    latest_trade_date = current[time_column].dt.date.max()
    session_df = current[current[time_column].dt.date == latest_trade_date]
    if session_df.empty:
        raise ValueError("最近一个交易日没有可用分钟线。")

    open_price = float(session_df.iloc[0][open_column])
    if open_price <= 0:
        raise ValueError("最近一个交易日开盘价无效。")
    return round(open_price, 3)


def query_local_daily_open_price(vt_symbol: str, now: datetime) -> float:
    """从本地 sqlite 日线中读取最近一个交易日的开盘价。"""
    symbol, exchange = split_vt_symbol(vt_symbol)
    database_path = get_project_database_path()
    if not database_path.exists():
        raise ValueError("本地数据库文件不存在。")

    sql = (
        "select datetime, open_price from dbbardata "
        "where symbol = ? and exchange = ? and interval = 'd' and datetime <= ? "
        "order by datetime desc limit 1"
    )
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            sql,
            (
                symbol,
                exchange,
                now.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ).fetchone()

    if not row:
        raise ValueError("本地数据库没有可用日线。")

    open_price = float(row[1])
    if open_price <= 0:
        raise ValueError("本地数据库日线开盘价无效。")
    return round(open_price, 3)


def fetch_reference_open_price(vt_symbol: str, now: datetime | None = None) -> tuple[float, str]:
    """按 pytdx -> 东财 -> 本地日线 的顺序获取最近交易日开盘价。"""
    current_dt = ensure_china_tz(now or datetime.now(CHINA_TZ))
    cache_key = (vt_symbol.strip().upper(), current_dt.strftime("%Y-%m-%d"))
    cached = OPEN_PRICE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    symbol, exchange = split_vt_symbol(vt_symbol)
    start_dt = current_dt - timedelta(days=5)
    end_dt = current_dt + timedelta(minutes=1)
    errors: list[str] = []

    try:
        pytdx_df, source_name = fetch_pytdx_minute_dataframe(
            symbol=symbol,
            exchange=exchange,
            interval="1m",
            start_dt=start_dt,
            end_dt=end_dt,
        )
        result = (extract_session_open_price(pytdx_df), source_name)
        OPEN_PRICE_CACHE[cache_key] = result
        return result
    except Exception as exc:
        errors.append(f"pytdx={exc}")

    try:
        eastmoney_df = fetch_eastmoney_minute_dataframe(
            symbol=symbol,
            period="1",
            adjust="",
            start_dt=start_dt,
            end_dt=end_dt,
        )
        result = (extract_session_open_price(eastmoney_df), "东财分钟线")
        OPEN_PRICE_CACHE[cache_key] = result
        return result
    except Exception as exc:
        errors.append(f"东财={exc}")

    try:
        result = (query_local_daily_open_price(vt_symbol, current_dt), "本地日线")
        OPEN_PRICE_CACHE[cache_key] = result
        return result
    except Exception as exc:
        errors.append(f"本地日线={exc}")

    raise ValueError("；".join(errors))


def get_pytdx_market(exchange: str) -> int:
    """把交易所映射成 pytdx 需要的市场编码。"""
    if not PYTDX_AVAILABLE or TDXParams is None:
        raise ValueError("pytdx 当前不可用。")
    if exchange == "SSE":
        return int(TDXParams.MARKET_SH)
    if exchange == "SZSE":
        return int(TDXParams.MARKET_SZ)
    raise ValueError(f"{exchange} 暂不支持 pytdx 行情。")


def get_pytdx_kline_type(interval: str) -> int:
    """把分钟周期映射成 pytdx 的 K 线类别。"""
    if not PYTDX_AVAILABLE or TDXParams is None:
        raise ValueError("pytdx 当前不可用。")

    normalized = normalize_interval(interval)
    category_map = {
        "1m": int(TDXParams.KLINE_TYPE_1MIN),
        "5m": int(TDXParams.KLINE_TYPE_5MIN),
        "15m": int(TDXParams.KLINE_TYPE_15MIN),
        "30m": int(TDXParams.KLINE_TYPE_30MIN),
    }
    try:
        return category_map[normalized]
    except KeyError as exc:
        raise ValueError(f"{interval} 暂不支持 pytdx 分钟线。") from exc


def estimate_pytdx_bar_count(interval: str, start_dt: datetime, end_dt: datetime) -> int:
    """按交易日长度粗略估算应抓取的 K 线数量，减少不必要分页。"""
    interval_minutes = get_interval_minutes(interval)
    trading_days = max(1, (end_dt.date() - start_dt.date()).days + 1)
    bars_per_day = (240 + interval_minutes - 1) // interval_minutes
    estimated = trading_days * bars_per_day + 80
    return max(240, min(estimated, PYTDX_BATCH_SIZE * 4))


def iter_pytdx_host_candidates() -> list[tuple[str, str, int]]:
    """按“已验证节点 -> 默认节点 -> 少量备选”的顺序组织 pytdx 主站。"""
    ordered_hosts: list[tuple[str, str, int]] = []
    seen: set[tuple[str, int]] = set()

    for candidate in (PYTDX_WORKING_HOST, PYTDX_DEFAULT_HOST, *PYTDX_BACKUP_HOSTS):
        if candidate is None:
            continue
        name, host, port = candidate
        if (host, port) in seen:
            continue
        seen.add((host, port))
        ordered_hosts.append((str(name), str(host), int(port)))

    # 如果少量备选都不可用，再补少量 pytdx 内置主站，避免完全丢失兜底能力。
    if len(ordered_hosts) < 10:
        for candidate in PYTDX_HQ_HOSTS:
            name, host, port = candidate
            if (host, port) in seen:
                continue
            seen.add((host, port))
            ordered_hosts.append((str(name), str(host), int(port)))
            if len(ordered_hosts) >= 10:
                break
    return ordered_hosts


def fetch_pytdx_minute_dataframe(
    symbol: str,
    exchange: str,
    interval: str,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[pd.DataFrame, str]:
    """优先通过 pytdx 获取免费分钟线，减少对东财接口的依赖。"""
    if not PYTDX_AVAILABLE or TdxHq_API is None:
        raise ValueError("pytdx 未安装，无法使用通达信分钟线。")

    market = get_pytdx_market(exchange)
    category = get_pytdx_kline_type(interval)
    requested_count = estimate_pytdx_bar_count(interval, start_dt, end_dt)
    last_error: Exception | None = None
    global PYTDX_WORKING_HOST

    for name, host, port in iter_pytdx_host_candidates():
        api = TdxHq_API(heartbeat=False, auto_retry=False, raise_exception=True)
        try:
            connected = api.connect(host, port, time_out=PYTDX_HOST_TIMEOUT)
            if not connected:
                continue

            raw_rows: list[dict] = []
            start = 0
            while start < requested_count:
                batch_count = min(PYTDX_BATCH_SIZE, requested_count - start)
                batch = api.get_security_bars(category, market, symbol, start, batch_count)
                if not batch:
                    break
                raw_rows.extend(batch)
                if len(batch) < batch_count:
                    break
                start += len(batch)

            if not raw_rows:
                raise ValueError(f"pytdx 主站 {name} 没有返回 {symbol} 的分钟线数据。")

            df = api.to_df(raw_rows)
            if df is None or df.empty or "datetime" not in df.columns:
                raise ValueError(f"pytdx 主站 {name} 返回了无法解析的分钟线数据。")

            PYTDX_WORKING_HOST = (name, host, port)
            df["datetime"] = pd.to_datetime(df["datetime"])
            # pytdx 返回的是本地时区的 naive 时间，这里统一用上海时区的本地时间比较区间。
            local_start = ensure_china_tz(start_dt).replace(tzinfo=None)
            local_end = ensure_china_tz(end_dt).replace(tzinfo=None)
            df = df.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
            filtered_df = df[(df["datetime"] >= local_start) & (df["datetime"] <= local_end)].copy()
            return filtered_df, f"pytdx:{name}"
        except Exception as exc:
            last_error = exc
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    if last_error is not None:
        raise last_error
    raise ValueError("未找到可用的 pytdx 行情主站。")


def fetch_eastmoney_minute_dataframe(
    symbol: str,
    period: str,
    adjust: str,
    start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    """直接请求东财分钟线接口，并补齐更像浏览器的请求头。"""
    local_start = ensure_china_tz(start_dt).replace(tzinfo=None)
    local_end = ensure_china_tz(end_dt).replace(tzinfo=None)
    market_code = 1 if symbol.startswith("6") else 0
    session = requests.Session()
    session.trust_env = False
    session.headers.update(EASTMONEY_HEADERS)

    if period == "1":
        url = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "ndays": "5",
            "iscr": "0",
            "secid": f"{market_code}.{symbol}",
        }
        response = session.get(url, params=params, timeout=15)
        response.raise_for_status()
        data_json = response.json()
        trends = data_json.get("data", {}).get("trends") or []
        if not trends:
            return pd.DataFrame()

        df = pd.DataFrame([item.split(",") for item in trends])
        df.columns = [
            "时间",
            "开盘",
            "收盘",
            "最高",
            "最低",
            "成交量",
            "成交额",
            "均价",
        ]
        df["时间"] = pd.to_datetime(df["时间"])
        df = df[(df["时间"] >= local_start) & (df["时间"] <= local_end)].copy()
        df["时间"] = df["时间"].astype(str)
        for column in ("开盘", "收盘", "最高", "最低", "成交量", "成交额", "均价"):
            df[column] = pd.to_numeric(df[column], errors="coerce")
        return df

    adjust_map = {"": "0", "qfq": "1", "hfq": "2"}
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": period,
        "fqt": adjust_map.get(adjust, "0"),
        "secid": f"{market_code}.{symbol}",
        "beg": "0",
        "end": "20500000",
    }
    response = session.get(url, params=params, timeout=15)
    response.raise_for_status()
    data_json = response.json()
    klines = data_json.get("data", {}).get("klines") or []
    if not klines:
        return pd.DataFrame()

    df = pd.DataFrame([item.split(",") for item in klines])
    df.columns = [
        "时间",
        "开盘",
        "收盘",
        "最高",
        "最低",
        "成交量",
        "成交额",
        "振幅",
        "涨跌幅",
        "涨跌额",
        "换手率",
    ]
    df["时间"] = pd.to_datetime(df["时间"])
    df = df[(df["时间"] >= local_start) & (df["时间"] <= local_end)].copy()
    df["时间"] = df["时间"].astype(str)
    for column in ("开盘", "收盘", "最高", "最低", "成交量", "成交额", "振幅", "涨跌幅", "涨跌额", "换手率"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df[
        [
            "时间",
            "开盘",
            "收盘",
            "最高",
            "最低",
            "涨跌幅",
            "涨跌额",
            "成交量",
            "成交额",
            "振幅",
            "换手率",
        ]
    ]


def disable_process_proxy_env() -> list[str]:
    """仅在当前 Python 进程内清理代理变量，避免行情请求误走本地代理。"""
    cleared_keys: list[str] = []
    for key in PROJECT_PROXY_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared_keys.append(key)
    return cleared_keys


def install_requests_no_proxy() -> bool:
    """让当前项目进程里的 requests 永远忽略环境代理。"""
    try:
        import requests.sessions
    except Exception:
        return False

    session_cls = requests.sessions.Session
    if getattr(session_cls, "_vnpy_no_proxy_installed", False):
        return False

    original = session_cls.merge_environment_settings

    def merge_environment_settings(self, url, proxies, stream, verify, cert):
        settings = original(self, url, {}, stream, verify, cert)
        settings["proxies"] = {}
        return settings

    session_cls.merge_environment_settings = merge_environment_settings
    session_cls._vnpy_no_proxy_installed = True
    return True


@contextmanager
def temporary_proxy_bypass():
    """临时绕过代理执行网络请求，结束后恢复进程内其他环境变量。"""
    saved_values: dict[str, str] = {}
    for key in PROJECT_PROXY_ENV_KEYS:
        value = os.environ.pop(key, None)
        if value is not None:
            saved_values[key] = value

    try:
        yield
    finally:
        os.environ.update(saved_values)


# 模块加载时就把 requests 的环境代理读取关掉，避免 GUI 已启动后仍走旧代理。
install_requests_no_proxy()


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


def filter_completed_bars(
    bars: list[AlertBar],
    now: datetime,
    interval_minutes: int,
    *,
    timestamp_mode: str,
) -> list[AlertBar]:
    """按不同数据源的时间戳语义过滤出已完成 K 线。"""
    cutoff = floor_to_interval(now, interval_minutes)
    if timestamp_mode == "close":
        return [bar for bar in bars if bar.dt <= cutoff]
    return [bar for bar in bars if bar.dt < cutoff]


class SymbolAlertService:
    """管理单只股票的提醒轮询、去重和策略分发。"""

    def __init__(
        self,
        config: SymbolConfig,
        app_config: AppConfig,
        history_writer: AlertHistoryWriter,
        log_callback: Callable[[LogData], None],
        record_callback: Callable[[RecordData], None],
        state_callback: Callable[[SymbolStateData], None],
    ) -> None:
        self.config: SymbolConfig = normalize_symbol_config(config)
        self.app_config: AppConfig = app_config
        self.history_writer: AlertHistoryWriter = history_writer
        self.log_callback = log_callback
        self.record_callback = record_callback
        self.state_callback = state_callback

        definition = get_strategy_definition(self.config.strategy_name)
        self.evaluator: BaseAlertEvaluator = definition.evaluator_cls()
        self.rule_states: dict[str, RuleRuntimeState] = {}
        self.last_completed_bar_dt: datetime | None = None
        self.stale_logged: bool = False
        self.state: SymbolStateData = build_default_state(self.config)

    def set_data_source(self, source_name: str) -> None:
        """记录当前这只股票本轮实际使用的数据源。"""
        self.state.data_source = source_name

    def get_rule_state(self, key: str) -> RuleRuntimeState:
        """按名称获取某个规则的运行态。"""
        state = self.rule_states.get(key)
        if state is None:
            state = RuleRuntimeState()
            self.rule_states[key] = state
        return state

    def emit_state(self) -> None:
        """推送当前状态快照。"""
        self.state_callback(self.state)

    def log(self, level: str, message: str) -> None:
        """输出带来源的结构化日志。"""
        self.log_callback(make_log(level, self.config.vt_symbol, message))

    def run_once(self, now: datetime, allow_local_fallback: bool = False) -> None:
        """执行单只股票的单轮检查。"""
        if not self.config.enabled:
            self.state.enabled = False
            self.state.status = "已禁用"
            self.state.signal_state = "已禁用"
            self.state.data_source = "-"
            self.emit_state()
            return

        bars = self.fetch_completed_bars(now, allow_local_fallback=allow_local_fallback)
        if not bars:
            self.state.status = "暂无完整K线"
            self.state.last_error = ""
            self.state.signal_state = "等待数据"
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
                f"轮询成功，数据源={self.state.data_source}，"
                f"策略={get_strategy_display_name(self.config.strategy_name)}，"
                f"K线时间={self.state.latest_bar_dt}，最新收盘价={self.state.latest_close}。"
            ),
        )

        result = self.evaluator.evaluate(self, bars, now)
        self.state.signal_state = result.signal_state
        for alert in result.alerts:
            self.emit_rule_alert(alert)
        self.emit_state()

    def fetch_completed_bars(self, now: datetime, allow_local_fallback: bool = False) -> list[AlertBar]:
        """拉取最近一批分钟数据，并取已完成 K 线。"""
        symbol, exchange = split_vt_symbol(self.config.vt_symbol)
        interval_minutes = get_interval_minutes(self.app_config.interval)
        period = str(interval_minutes)
        start_dt = now - timedelta(days=5)
        end_dt = now + timedelta(minutes=1)

        last_remote_error: Exception | None = None

        try:
            # 分钟线优先走 pytdx，免费且在当前机器上比东财接口更稳定。
            df, source_name = fetch_pytdx_minute_dataframe(
                symbol=symbol,
                exchange=exchange,
                interval=self.app_config.interval,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            parsed = self.parse_bars(df)
            self.set_data_source(source_name)
            return filter_completed_bars(
                parsed,
                now,
                interval_minutes,
                timestamp_mode="close",
            )
        except Exception as exc:
            last_remote_error = exc

        try:
            # 东财作为第二优先级兜底，适合 pytdx 暂时失联时继续尝试。
            with temporary_proxy_bypass():
                df = fetch_eastmoney_minute_dataframe(
                    symbol=symbol,
                    period=period,
                    adjust=self.app_config.adjust,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
            self.set_data_source("东财分钟线")
        except Exception as exc:
            if last_remote_error is not None:
                exc = ValueError(f"pytdx 失败：{last_remote_error}；东财失败：{exc}")
            if not allow_local_fallback:
                raise

            local_bars, local_interval = self.fetch_local_database_bars(now)
            if local_bars:
                self.set_data_source(f"本地{local_interval}")
                self.log(
                    "INFO",
                    (
                        f"远程分钟线获取失败，单次测试已回退到本地 {local_interval} 数据：{exc}"
                    ),
                )
                return local_bars
            self.set_data_source("远程失败")
            raise ValueError(f"{exc}；同时本地数据库中也没有可用历史数据。") from exc

        if df is None or df.empty:
            if not self.state.data_source:
                self.set_data_source("无数据")
            return []

        parsed = self.parse_bars(df)
        if not parsed:
            return []

        return filter_completed_bars(
            parsed,
            now,
            interval_minutes,
            timestamp_mode="open",
        )

    def fetch_local_database_bars(self, now: datetime) -> tuple[list[AlertBar], str]:
        """单次测试失败时，优先回退到项目本地数据库。"""
        symbol, exchange = split_vt_symbol(self.config.vt_symbol)
        database_path = get_project_database_path()
        if not database_path.exists():
            return [], ""

        interval_candidates = [normalize_interval(self.app_config.interval), "d"]
        seen: set[str] = set()
        for interval in interval_candidates:
            if interval in seen:
                continue
            seen.add(interval)

            lookback_days = 400 if interval == "d" else 10
            start_dt = now - timedelta(days=lookback_days)
            rows = self.query_local_bar_rows(
                database_path=database_path,
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_dt=start_dt,
                end_dt=now,
            )
            if not rows:
                continue

            bars = [
                AlertBar(
                    dt=ensure_china_tz(datetime.fromisoformat(str(row[0]))),
                    close_price=float(row[4]),
                    high_price=float(row[2]),
                    low_price=float(row[3]),
                    volume=float(row[5]),
                )
                for row in rows
            ]
            bars.sort(key=lambda item: item.dt)
            return bars, interval

        return [], ""

    def query_local_bar_rows(
        self,
        database_path: Path,
        symbol: str,
        exchange: str,
        interval: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> list[tuple]:
        """从项目内 SQLite 数据库读取指定区间 K 线。"""
        sql = (
            "select datetime, open_price, high_price, low_price, close_price, volume "
            "from dbbardata "
            "where symbol = ? and exchange = ? and interval = ? "
            "and datetime >= ? and datetime <= ? "
            "order by datetime asc"
        )
        with sqlite3.connect(database_path) as connection:
            cursor = connection.execute(
                sql,
                (
                    symbol,
                    exchange,
                    interval,
                    start_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                    end_dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            return list(cursor.fetchall())

    def parse_bars(self, df: pd.DataFrame) -> list[AlertBar]:
        """兼容 pytdx、东财等数据源字段名，提取提醒需要的时间、价格和成交量。"""
        time_column = find_first_existing_column(df, ["时间", "datetime", "day"])
        close_column = find_first_existing_column(df, ["收盘", "close"])
        high_column = find_first_existing_column(df, ["最高", "high"])
        low_column = find_first_existing_column(df, ["最低", "low"])
        volume_column = find_first_existing_column(df, ["成交量", "volume"])

        if time_column is None or close_column is None:
            raise ValueError("返回数据缺少时间列或收盘价列，无法解析 K 线。")

        bars: list[AlertBar] = []
        for _, row in df.iterrows():
            dt = ensure_china_tz(pd.to_datetime(row[time_column]).to_pydatetime())
            close_price = float(row[close_column])
            high_price = float(row[high_column]) if high_column else close_price
            low_price = float(row[low_column]) if low_column else close_price
            volume = float(row[volume_column]) if volume_column else 0.0
            bars.append(
                AlertBar(
                    dt=dt,
                    close_price=close_price,
                    high_price=high_price,
                    low_price=low_price,
                    volume=volume,
                )
            )

        bars.sort(key=lambda item: item.dt)
        return bars

    def can_alert(self, state: RuleRuntimeState, now: datetime) -> bool:
        """为同类提醒增加最小冷却时间，减少来回波动造成的刷屏。"""
        if state.last_alert_at is None:
            return True
        return (now - state.last_alert_at).total_seconds() >= self.app_config.cooldown_seconds

    def emit_rule_alert(self, signal: AlertSignal) -> None:
        """统一输出提醒并写入本地记录，方便后续复盘。"""
        self.log("INFO", f"{signal.level.value}提醒：{signal.message}")

        record = RecordData(
            occurred_at=datetime.now(CHINA_TZ).isoformat(),
            vt_symbol=self.config.vt_symbol,
            strategy_name=self.config.strategy_name,
            interval=self.app_config.interval,
            rule_name=signal.rule_name,
            level=signal.level.value,
            rule_value=f"{signal.rule_value:.6f}",
            triggered_bar_dt=signal.triggered_bar_dt.isoformat(),
            message=signal.message,
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

    def get_enabled_services(self) -> list[SymbolAlertService]:
        """仅返回当前启用的股票服务。"""
        return [service for service in self.services if service.config.enabled]

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
        enabled_services = self.get_enabled_services()
        symbol_text = "、".join(
            f"{service.config.vt_symbol}[{service.config.strategy_name}]"
            for service in enabled_services
        )
        self.log(
            "INFO",
            (
                f"实时提醒已启动，标的={symbol_text}，周期={self.config.interval}，"
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

    def run_preview_once(self, reference_time: datetime) -> None:
        """按指定历史时间执行单次回放测试，方便在非交易时段验证 GUI。"""
        now = ensure_china_tz(reference_time)
        enabled_services = self.get_enabled_services()
        symbols = "、".join(
            f"{service.config.vt_symbol}[{service.config.strategy_name}]"
            for service in enabled_services
        )
        self.log(
            "INFO",
            (
                f"开始执行历史回放测试，模拟时间={now.strftime('%Y-%m-%d %H:%M:%S')}，"
                f"标的={symbols}，周期={self.config.interval}。"
            ),
        )
        self.emit_initial_states()
        self.emit_status(False, True, f"测试中：{now.strftime('%Y-%m-%d %H:%M')}")
        self.run_once(reference_time=now, ignore_trading_time=True, allow_local_fallback=True)
        self.emit_status(False, False, f"测试完成：{now.strftime('%Y-%m-%d %H:%M')}")
        self.log("INFO", "历史回放测试已完成。")

    def run_once(
        self,
        reference_time: datetime | None = None,
        ignore_trading_time: bool = False,
        allow_local_fallback: bool = False,
    ) -> None:
        """执行一轮全市场小范围轮询。"""
        now = ensure_china_tz(reference_time) if reference_time else datetime.now(CHINA_TZ)
        if not ignore_trading_time and not is_a_share_trading_time(now):
            if not self.paused_logged:
                self.log("INFO", "当前非交易时段，暂停提醒。")
                self.emit_status(True, True, "非交易时段暂停")
                self.paused_logged = True
            return

        if not ignore_trading_time and self.paused_logged:
            self.emit_status(True, False, "运行中")
        if not ignore_trading_time:
            self.paused_logged = False

        for service in self.services:
            try:
                service.run_once(now, allow_local_fallback=allow_local_fallback)
            except Exception as exc:
                service.set_error(f"行情数据拉取失败：{exc}")
