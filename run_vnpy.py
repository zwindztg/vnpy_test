import json
import os
import platform
import signal
import shutil
import traceback
from datetime import date, datetime, time, timedelta
from pathlib import Path


A_SHARE_EXCHANGES: tuple[str, ...] = (".SSE", ".SZSE", ".BSE")
LEGACY_A_SHARE_TEST_SYMBOLS: tuple[str, ...] = ("000001.SZSE",)
A_SHARE_DATABASE_MAX_MB: int = 128
A_SHARE_MINUTE_RETENTION_DAYS: int = 14
A_SHARE_HOUR_RETENTION_DAYS: int = 365
A_SHARE_BACKTEST_DEFAULTS: dict[str, object] = {
    "class_name": "LessonAShareLongOnlyStrategy",
    "vt_symbol": "601869.SSE",
    "interval": "d",
    "rate": 0.0005,
    "slippage": 0.01,
    "size": 1,
    "pricetick": 0.01,
    "capital": 100000,
}
FUTURES_STYLE_BACKTEST_DEFAULTS: dict[str, float] = {
    "rate": 0.000025,
    "slippage": 0.2,
    "size": 300,
    "pricetick": 0.2,
    "capital": 1000000,
}
STRATEGY_PARAMETER_LABELS: dict[str, dict[str, str]] = {
    "LessonAShareLongOnlyStrategy": {
        "fast_window": "快均线周期（整数，默认5）",
        "slow_window": "慢均线周期（整数，默认20）",
        "fixed_size": "每次买入股数（整数，默认100股）",
    },
    "LessonDonchianAShareStrategy": {
        "entry_window": "突破观察周期（整数，默认20）",
        "exit_window": "离场观察周期（整数，默认10）",
        "fixed_size": "每次买入股数（整数，默认100股）",
    },
    "LessonVolumeBreakoutAShareStrategy": {
        "breakout_window": "短线突破观察周期（整数，默认5）",
        "exit_window": "短线离场观察周期（整数，默认3）",
        "volume_window": "成交量均值周期（整数，默认5）",
        "volume_ratio": "放量倍数阈值（浮点数，默认1.5）",
        "fixed_size": "每次买入股数（整数，默认100股）",
    },
    "LessonDoubleMaStrategy": {
        "fast_window": "快均线周期（整数，默认10）",
        "slow_window": "慢均线周期（整数，默认20）",
        "fixed_size": "每次下单数量（整数，默认1）",
    },
}
STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "AtrRsiStrategy": "ATR-RSI 策略（AtrRsiStrategy）",
    "BollChannelStrategy": "布林通道策略（BollChannelStrategy）",
    "DoubleMaStrategy": "双均线策略（DoubleMaStrategy）",
    "DualThrustStrategy": "Dual Thrust 策略（DualThrustStrategy）",
    "KingKeltnerStrategy": "肯特纳通道策略（KingKeltnerStrategy）",
    "LessonAShareLongOnlyStrategy": "A股长仓学习策略（LessonAShareLongOnlyStrategy）",
    "LessonDonchianAShareStrategy": "A股唐奇安突破策略（LessonDonchianAShareStrategy）",
    "LessonVolumeBreakoutAShareStrategy": "A股短线放量突破策略（LessonVolumeBreakoutAShareStrategy）",
    "LessonDoubleMaStrategy": "双均线教学策略（LessonDoubleMaStrategy）",
    "MultiSignalStrategy": "多信号策略（MultiSignalStrategy）",
    "MultiTimeframeStrategy": "多周期策略（MultiTimeframeStrategy）",
    "TestStrategy": "测试策略（TestStrategy）",
    "TurtleSignalStrategy": "海龟交易策略（TurtleSignalStrategy）",
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


def disable_project_proxy_env() -> list[str]:
    """仅在当前项目进程里清理代理变量，不影响系统和 Codex 对话。"""
    cleared_keys: list[str] = []
    for key in PROJECT_PROXY_ENV_KEYS:
        if os.environ.pop(key, None) is not None:
            cleared_keys.append(key)
    return cleared_keys


def install_project_requests_no_proxy() -> bool:
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


def normalize_a_share_vt_symbol(vt_symbol: str) -> str:
    """把常见的股票后缀别名统一转换成 vn.py 使用的交易所后缀。"""
    normalized = vt_symbol.strip().upper()
    if "." not in normalized:
        return normalized

    symbol, suffix = normalized.rsplit(".", 1)
    suffix_map = {
        "SH": "SSE",
        "SZ": "SZSE",
        "BJ": "BSE",
    }
    normalized_suffix = suffix_map.get(suffix, suffix)
    return f"{symbol}.{normalized_suffix}"


def format_backtesting_log_lines(
    class_name: str,
    vt_symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    rate: float,
    slippage: float,
    size: float,
    pricetick: float,
    capital: float,
    setting: dict,
) -> list[str]:
    """把本次回测配置整理成适合写入日志的中文摘要。"""
    display_name = STRATEGY_DISPLAY_NAMES.get(class_name, class_name)
    parameter_labels = STRATEGY_PARAMETER_LABELS.get(class_name, {})

    lines = [
        (
            "本次回测配置："
            f"策略={display_name}；代码={vt_symbol}；周期={interval}；"
            f"开始={start.strftime('%Y-%m-%d')}；结束={end.strftime('%Y-%m-%d')}"
        ),
        (
            "回测基础参数："
            f"手续费率={rate}；滑点={slippage}；合约乘数={size}；"
            f"价格跳动={pricetick}；回测资金={capital}"
        ),
    ]

    if setting:
        parameter_parts: list[str] = []
        for key, value in setting.items():
            label = parameter_labels.get(key, key)
            parameter_parts.append(f"{label}={value}")

        if parameter_parts:
            lines.append("策略参数：" + "；".join(parameter_parts))

    return lines


def patch_qt_stylesheet(qapp) -> None:
    """Apply small macOS-specific fixes for qdarkstyle combo boxes."""
    if platform.system() != "Darwin":
        return

    qapp.setStyleSheet(
        qapp.styleSheet()
        + """
QComboBox {
    min-width: 6em;
    color: #D8E1EA;
}
QComboBox QAbstractItemView {
    min-width: 6em;
    color: #D8E1EA;
    background-color: #19232D;
    selection-color: #FFFFFF;
}
"""
    )


def install_gui_signal_handlers(qapp) -> None:
    """收到中断信号时，先关闭所有窗口，再退出 Qt 事件循环。"""

    def shutdown_gui(*_) -> None:
        """把终端里的 Ctrl+C 转成 Qt 的正常关窗流程。"""
        for widget in qapp.topLevelWidgets():
            widget.close()
        qapp.quit()

    signal.signal(signal.SIGINT, shutdown_gui)
    signal.signal(signal.SIGTERM, shutdown_gui)


def load_json_dict(path: Path) -> dict:
    """Read a JSON object from disk and fall back to an empty dict."""
    if not path.exists():
        return {}

    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

    if isinstance(current, dict):
        return current
    return {}


def write_json_dict(path: Path, data: dict) -> None:
    """Write a JSON object to disk using readable formatting."""
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_a_share_symbol(vt_symbol: str) -> bool:
    """Return whether the local symbol points to a mainland A-share exchange."""
    return vt_symbol.endswith(A_SHARE_EXCHANGES)


def is_same_number(value: object, expected: float) -> bool:
    """Compare persisted numeric values without caring about int/float strings."""
    try:
        return abs(float(value) - expected) < 1e-9
    except (TypeError, ValueError):
        return False


def should_reset_backtester_settings(current: dict) -> bool:
    """Reset CTA backtester defaults when they still look like futures presets."""
    if not current:
        return True

    vt_symbol = str(current.get("vt_symbol", "")).strip()
    if vt_symbol in {"", "IF88.CFFEX"}:
        return True

    # 如果本地还停留在旧的学习标的模板，就自动切到新的默认股票。
    if (
        vt_symbol in LEGACY_A_SHARE_TEST_SYMBOLS
        and str(current.get("class_name", "")) == str(A_SHARE_BACKTEST_DEFAULTS["class_name"])
        and str(current.get("interval", "")) == str(A_SHARE_BACKTEST_DEFAULTS["interval"])
        and is_same_number(current.get("rate"), float(A_SHARE_BACKTEST_DEFAULTS["rate"]))
        and is_same_number(current.get("slippage"), float(A_SHARE_BACKTEST_DEFAULTS["slippage"]))
        and is_same_number(current.get("size"), float(A_SHARE_BACKTEST_DEFAULTS["size"]))
        and is_same_number(current.get("pricetick"), float(A_SHARE_BACKTEST_DEFAULTS["pricetick"]))
        and is_same_number(current.get("capital"), float(A_SHARE_BACKTEST_DEFAULTS["capital"]))
    ):
        return True

    if is_a_share_symbol(vt_symbol):
        return all(
            is_same_number(current.get(key), expected)
            for key, expected in FUTURES_STYLE_BACKTEST_DEFAULTS.items()
        )

    return False


def get_trader_dir() -> Path:
    """返回工程内的 vn.py 配置目录，避免写入用户根目录。"""
    # 固定使用脚本所在工程目录，确保配置、缓存和日志都留在项目内。
    project_dir = Path(__file__).resolve().parent
    trader_dir = project_dir / ".vntrader"
    trader_dir.mkdir(parents=True, exist_ok=True)
    return trader_dir


def get_database_path(trader_dir: Path) -> Path:
    """根据本地配置返回数据库文件路径。"""
    setting_path = trader_dir / "vt_setting.json"
    current = load_json_dict(setting_path)
    database_name = str(current.get("database.database") or "database.db")
    return trader_dir / database_name


def ensure_vnpy_settings() -> None:
    """Create a sane first-run vnpy config without overwriting user choices."""
    trader_dir = get_trader_dir()

    setting_path = trader_dir / "vt_setting.json"
    current = load_json_dict(setting_path)

    system = platform.system()
    if system == "Darwin":
        default_font = "PingFang SC"
    elif system == "Windows":
        default_font = "Microsoft YaHei"
    else:
        default_font = "Noto Sans CJK SC"

    defaults = {
        "font.family": default_font,
        "font.size": 12,
        "database.name": "sqlite",
        "database.database": "database.db",
        "datafeed.name": "localdemo",
        "datafeed.username": "",
        "datafeed.password": "",
        "datafeed.adjust": "qfq",
    }

    changed = False
    for key, value in defaults.items():
        if key not in current:
            current[key] = value
            changed = True

    if changed or not setting_path.exists():
        write_json_dict(setting_path, current)


def ensure_backtester_settings() -> None:
    """Seed CTA backtesting defaults that are friendlier to A-share cash study."""
    trader_dir = get_trader_dir()

    setting_path = trader_dir / "cta_backtester_setting.json"
    current = load_json_dict(setting_path)

    defaults = dict(A_SHARE_BACKTEST_DEFAULTS)
    defaults["start"] = (date.today() - timedelta(days=365)).isoformat()

    changed = False

    if should_reset_backtester_settings(current):
        current = defaults
        changed = True
    else:
        for key, value in defaults.items():
            if key not in current:
                current[key] = value
                changed = True

    if changed or not setting_path.exists():
        write_json_dict(setting_path, current)


def cleanup_database_storage() -> None:
    """启动时做轻量数据库清理，避免库体积无限增长。"""
    from vnpy.trader.constant import Interval
    from vnpy.trader.database import get_database

    trader_dir = get_trader_dir()
    database_path = get_database_path(trader_dir)
    if not database_path.exists():
        return

    database = get_database()
    minute_cutoff = datetime.combine(
        date.today() - timedelta(days=A_SHARE_MINUTE_RETENTION_DAYS),
        time.min,
    )
    hour_cutoff = datetime.combine(
        date.today() - timedelta(days=A_SHARE_HOUR_RETENTION_DAYS),
        time.min,
    )

    # 优先删除“已经很久没更新”的分钟和小时数据组。
    for overview in list(database.get_bar_overview()):
        if overview.end is None:
            continue

        if overview.interval == Interval.MINUTE and overview.end < minute_cutoff:
            deleted = database.delete_bar_data(
                overview.symbol,
                overview.exchange,
                overview.interval,
            )
            if deleted:
                print(f"启动清理旧1m数据：{overview.symbol}.{overview.exchange.value} 删除{deleted}条")

        elif overview.interval == Interval.HOUR and overview.end < hour_cutoff:
            deleted = database.delete_bar_data(
                overview.symbol,
                overview.exchange,
                overview.interval,
            )
            if deleted:
                print(f"启动清理旧1h数据：{overview.symbol}.{overview.exchange.value} 删除{deleted}条")

    max_size_bytes = A_SHARE_DATABASE_MAX_MB * 1024 * 1024
    if not database_path.exists() or database_path.stat().st_size <= max_size_bytes:
        return

    # 如果库仍然偏大，再额外清理分钟和 tick 数据，但保留日线数据。
    for overview in list(database.get_bar_overview()):
        if overview.interval == Interval.MINUTE:
            deleted = database.delete_bar_data(
                overview.symbol,
                overview.exchange,
                overview.interval,
            )
            if deleted:
                print(f"压缩数据库体积：删除1m数据 {overview.symbol}.{overview.exchange.value} {deleted}条")

    for overview in list(database.get_tick_overview()):
        deleted = database.delete_tick_data(
            overview.symbol,
            overview.exchange,
        )
        if deleted:
            print(f"压缩数据库体积：删除Tick数据 {overview.symbol}.{overview.exchange.value} {deleted}条")


def patch_backtester_engine() -> None:
    """给 CTA 回测引擎增加自动补数和定向清理能力。"""
    from vnpy.trader.constant import Interval
    from vnpy.trader.database import convert_tz
    from vnpy.trader.object import BarData, ContractData, HistoryRequest, TickData
    from vnpy.trader.utility import extract_vt_symbol
    from vnpy_ctabacktester.engine import (
        APP_NAME,
        EVENT_BACKTESTER_BACKTESTING_FINISHED,
        BacktesterEngine,
        Event,
    )
    from vnpy_ctastrategy import CtaTemplate
    from vnpy_ctastrategy.backtesting import BacktestingEngine, BacktestingMode

    if getattr(BacktesterEngine, "_vnpy_test_patched", False):
        return

    def normalize_db_datetime(dt: datetime) -> datetime:
        """把时间统一转换到数据库使用的比较口径。"""
        if dt.tzinfo is None:
            return dt
        return convert_tz(dt)

    def find_bar_overview(self, vt_symbol: str, interval: str):
        """查找当前品种和周期在本地数据库里的汇总信息。"""
        symbol, exchange = extract_vt_symbol(vt_symbol)
        target_interval = Interval(interval)

        for overview in self.database.get_bar_overview():
            if (
                overview.symbol == symbol
                and overview.exchange == exchange
                and overview.interval == target_interval
            ):
                return overview
        return None

    def query_history_from_source(
        self,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> tuple[str | None, object | None, object | None, list[BarData] | list[TickData]]:
        """从在线数据源查询指定品种和周期的历史数据。"""
        try:
            symbol, exchange = extract_vt_symbol(vt_symbol)
        except ValueError:
            self.write_log(f"{vt_symbol}解析失败，请检查交易所后缀")
            return None, None, None, []

        req: HistoryRequest = HistoryRequest(
            symbol=symbol,
            exchange=exchange,
            interval=Interval(interval),
            start=start,
            end=end,
        )

        if interval == Interval.TICK.value:
            tick_data: list[TickData] = self.datafeed.query_tick_history(req, self.write_log)
            return symbol, exchange, None, tick_data

        contract: ContractData | None = self.main_engine.get_contract(vt_symbol)
        if contract and contract.history_data:
            bar_data: list[BarData] = self.main_engine.query_history(req, contract.gateway_name)
        else:
            bar_data = self.datafeed.query_bar_history(req, self.write_log)

        return symbol, exchange, Interval(interval), bar_data

    def refresh_history_cache(
        self,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        reason: str,
    ) -> bool:
        """重新下载并覆盖当前品种同周期的本地缓存。"""
        self.write_log(f"{vt_symbol}-{interval}{reason}")

        try:
            symbol, exchange, interval_enum, data = query_history_from_source(
                self,
                vt_symbol,
                interval,
                start,
                end,
            )
            if not symbol or not exchange:
                return False

            if not data:
                self.write_log(f"数据下载失败，无法获取{vt_symbol}的历史数据")
                return False

            if interval == Interval.TICK.value:
                deleted = self.database.delete_tick_data(symbol, exchange)
                if deleted:
                    self.write_log(f"{vt_symbol}-tick已删除旧缓存：{deleted}条")
                self.database.save_tick_data(data)
            else:
                deleted = self.database.delete_bar_data(symbol, exchange, interval_enum)
                if deleted:
                    self.write_log(f"{vt_symbol}-{interval}已删除旧缓存：{deleted}条")
                self.database.save_bar_data(data)

            self.write_log(f"{vt_symbol}-{interval}历史数据下载完成")
            return True
        except Exception:
            msg = "数据下载失败，触发异常：\n{}".format(traceback.format_exc())
            self.write_log(msg)
            return False

    def ensure_history_for_backtest(
        self,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        """回测前检查本地数据，不够时自动联网补数。"""
        if interval == Interval.TICK.value:
            return refresh_history_cache(
                self,
                vt_symbol,
                interval,
                start,
                end,
                "回测前自动下载Tick历史数据",
            )

        overview = find_bar_overview(self, vt_symbol, interval)
        start_db = normalize_db_datetime(start)
        end_db = normalize_db_datetime(end)

        # UI 里按日期选择回测区间，这里按日期覆盖范围判断是否需要重下。
        if (
            overview
            and overview.start
            and overview.end
            and overview.start.date() <= start_db.date()
            and overview.end.date() >= end_db.date()
        ):
            return True

        return refresh_history_cache(
            self,
            vt_symbol,
            interval,
            start,
            end,
            "回测前发现本地历史数据不足，开始自动补数",
        )

    def patched_run_backtesting(
        self,
        class_name: str,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
        rate: float,
        slippage: float,
        size: int,
        pricetick: float,
        capital: int,
        setting: dict,
    ) -> None:
        """运行回测，必要时先自动补齐本地历史数据。"""
        self.result_df = None
        self.result_statistics = None

        engine: BacktestingEngine = self.backtesting_engine
        engine.clear_data()

        if interval == Interval.TICK.value:
            mode: BacktestingMode = BacktestingMode.TICK
        else:
            mode = BacktestingMode.BAR

        engine.set_parameters(
            vt_symbol=vt_symbol,
            interval=interval,
            start=start,
            end=end,
            rate=rate,
            slippage=slippage,
            size=size,
            pricetick=pricetick,
            capital=capital,
            mode=mode,
        )

        strategy_class: type[CtaTemplate] = self.classes[class_name]
        engine.add_strategy(strategy_class, setting)

        if not ensure_history_for_backtest(self, vt_symbol, interval, start, end):
            self.write_log("策略回测失败，历史数据自动补数失败")
            self.thread = None
            return

        engine.load_data()
        if not engine.history_data:
            self.write_log("策略回测失败，历史数据为空")
            self.thread = None
            return

        try:
            engine.run_backtesting()
        except Exception:
            msg = "策略回测失败，触发异常：\n{}".format(traceback.format_exc())
            self.write_log(msg)
            self.thread = None
            return

        self.result_df = engine.calculate_result()
        self.result_statistics = engine.calculate_statistics(output=False)

        for log_line in format_backtesting_log_lines(
            class_name,
            vt_symbol,
            interval,
            start,
            end,
            rate,
            slippage,
            size,
            pricetick,
            capital,
            setting,
        ):
            self.write_log(log_line)

        self.thread = None

        event: Event = Event(EVENT_BACKTESTER_BACKTESTING_FINISHED)
        self.event_engine.put(event)

    def patched_run_downloading(
        self,
        vt_symbol: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> None:
        """执行手动下载时，先定向清掉当前品种同周期旧缓存。"""
        refresh_history_cache(
            self,
            vt_symbol,
            interval,
            start,
            end,
            "开始下载历史数据",
        )
        self.thread = None

    BacktesterEngine.find_bar_overview = find_bar_overview
    BacktesterEngine.query_history_from_source = query_history_from_source
    BacktesterEngine.refresh_history_cache = refresh_history_cache
    BacktesterEngine.ensure_history_for_backtest = ensure_history_for_backtest
    BacktesterEngine.run_backtesting = patched_run_backtesting
    BacktesterEngine.run_downloading = patched_run_downloading
    BacktesterEngine._vnpy_test_patched = True


def patch_backtester_manager() -> None:
    """让 CTA 回测界面兼容 SH/SZ/BJ 等常见股票后缀输入。"""
    from vnpy.trader.constant import Exchange
    from vnpy.trader.database import DB_TZ
    from vnpy.trader.ui import QtCore
    from vnpy_ctabacktester.ui.widget import (
        BacktesterManager,
        BacktestingSettingEditor,
        OptimizationSettingEditor,
    )

    if getattr(BacktesterManager, "_vnpy_test_symbol_patched", False):
        return

    original_edit_strategy_code = BacktesterManager.edit_strategy_code

    def sync_symbol_input(self) -> str:
        """把输入框里的股票代码统一成 vn.py 内部格式。"""
        current_symbol = self.symbol_line.text()
        normalized_symbol = normalize_a_share_vt_symbol(current_symbol)
        if normalized_symbol != current_symbol:
            self.symbol_line.setText(normalized_symbol)
        return normalized_symbol

    def get_current_class_name(self) -> str:
        """返回当前下拉框真正对应的英文类名。"""
        current_data = self.class_combo.currentData()
        if isinstance(current_data, str) and current_data:
            return current_data
        return self.class_combo.currentText()

    def find_class_index(self, class_name: str) -> int:
        """根据英文类名查找当前策略下拉框索引。"""
        for index in range(self.class_combo.count()):
            item_data = self.class_combo.itemData(index)
            if item_data == class_name:
                return index
        return -1

    def init_strategy_settings(self) -> None:
        """把策略下拉框改成中文+英文双语显示。"""
        self.class_names = self.backtester_engine.get_strategy_class_names()
        self.class_names.sort()
        self.settings = {}
        self.class_combo.clear()

        for class_name in self.class_names:
            setting: dict = self.backtester_engine.get_default_setting(class_name)
            self.settings[class_name] = setting
            display_name = STRATEGY_DISPLAY_NAMES.get(class_name, class_name)
            self.class_combo.addItem(display_name, class_name)

    def load_backtesting_setting(self) -> None:
        """加载本地回测配置，并恢复双语策略名显示。"""
        setting_path = get_trader_dir() / self.setting_filename
        setting: dict = load_json_dict(setting_path)
        if not setting:
            return

        class_name = str(setting.get("class_name", ""))
        index = find_class_index(self, class_name)
        if index >= 0:
            self.class_combo.setCurrentIndex(index)

        vt_symbol = str(setting.get("vt_symbol", ""))
        if vt_symbol:
            self.symbol_line.setText(normalize_a_share_vt_symbol(vt_symbol))

        interval = str(setting.get("interval", ""))
        if interval:
            interval_index = self.interval_combo.findText(interval)
            if interval_index >= 0:
                self.interval_combo.setCurrentIndex(interval_index)

        start_str = str(setting.get("start", ""))
        if start_str:
            start_dt = QtCore.QDate.fromString(start_str, "yyyy-MM-dd")
            self.start_date_edit.setDate(start_dt)

        for key, widget in [
            ("rate", self.rate_line),
            ("slippage", self.slippage_line),
            ("size", self.size_line),
            ("pricetick", self.pricetick_line),
            ("capital", self.capital_line),
        ]:
            if key in setting:
                widget.setText(str(setting[key]))

    def save_backtesting_setting(self, data: dict) -> None:
        """把回测参数保存到本地配置文件。"""
        setting_path = get_trader_dir() / self.setting_filename
        write_json_dict(setting_path, data)

    def start_backtesting(self) -> None:
        """开始回测前，先规范股票后缀并使用英文类名执行。"""
        class_name = get_current_class_name(self)
        if not class_name:
            self.write_log("请选择要回测的策略")
            return

        vt_symbol = sync_symbol_input(self)
        interval = self.interval_combo.currentText()
        start = self.start_date_edit.dateTime().toPython()
        end = self.end_date_edit.dateTime().toPython()
        rate = float(self.rate_line.text())
        slippage = float(self.slippage_line.text())
        size = float(self.size_line.text())
        pricetick = float(self.pricetick_line.text())
        capital = float(self.capital_line.text())

        if "." not in vt_symbol:
            self.write_log("本地代码缺失交易所后缀，请检查")
            return

        __, exchange_str = vt_symbol.split(".")
        if exchange_str not in Exchange.__members__:
            self.write_log("本地代码的交易所后缀不正确，请检查")
            return

        save_backtesting_setting(
            self,
            {
                "class_name": class_name,
                "vt_symbol": vt_symbol,
                "interval": interval,
                "start": start.strftime("%Y-%m-%d"),
                "rate": rate,
                "slippage": slippage,
                "size": size,
                "pricetick": pricetick,
                "capital": capital,
            },
        )

        old_setting: dict = self.settings[class_name]
        dialog: BacktestingSettingEditor = BacktestingSettingEditor(class_name, old_setting)
        result_code: int = dialog.exec()
        if result_code != dialog.DialogCode.Accepted:
            return

        new_setting: dict = dialog.get_setting()
        self.settings[class_name] = new_setting

        result = self.backtester_engine.start_backtesting(
            class_name,
            vt_symbol,
            interval,
            start,
            end,
            rate,
            slippage,
            size,
            pricetick,
            capital,
            new_setting,
        )

        if result:
            self.statistics_monitor.clear_data()
            self.chart.clear_data()

            self.trade_button.setEnabled(False)
            self.order_button.setEnabled(False)
            self.daily_button.setEnabled(False)
            self.candle_button.setEnabled(False)

            self.trade_dialog.clear_data()
            self.order_dialog.clear_data()
            self.daily_dialog.clear_data()
            self.candle_dialog.clear_data()

    def start_optimization(self) -> None:
        """开始优化前，先规范股票后缀并使用英文类名执行。"""
        class_name = get_current_class_name(self)
        vt_symbol = sync_symbol_input(self)
        interval = self.interval_combo.currentText()
        start = self.start_date_edit.dateTime().toPython()
        end = self.end_date_edit.dateTime().toPython()
        rate = float(self.rate_line.text())
        slippage = float(self.slippage_line.text())
        size = float(self.size_line.text())
        pricetick = float(self.pricetick_line.text())
        capital = float(self.capital_line.text())

        parameters: dict = self.settings[class_name]
        dialog: OptimizationSettingEditor = OptimizationSettingEditor(class_name, parameters)
        result_code: int = dialog.exec()
        if result_code != dialog.DialogCode.Accepted:
            return

        optimization_setting, use_ga, max_workers = dialog.get_setting()
        self.target_display = dialog.target_display

        self.backtester_engine.start_optimization(
            class_name,
            vt_symbol,
            interval,
            start,
            end,
            rate,
            slippage,
            size,
            pricetick,
            capital,
            optimization_setting,
            use_ga,
            max_workers,
        )

        self.result_button.setEnabled(False)

    def start_downloading(self) -> None:
        """下载数据前，先规范股票后缀输入。"""
        vt_symbol = sync_symbol_input(self)
        interval = self.interval_combo.currentText()
        start_date = self.start_date_edit.date()
        end_date = self.end_date_edit.date()

        start = datetime(
            start_date.year(),
            start_date.month(),
            start_date.day(),
        )
        start = start.replace(tzinfo=DB_TZ)

        end = datetime(
            end_date.year(),
            end_date.month(),
            end_date.day(),
            23,
            59,
            59,
        )
        end = end.replace(tzinfo=DB_TZ)

        self.backtester_engine.start_downloading(
            vt_symbol,
            interval,
            start,
            end,
        )

    def edit_strategy_code(self) -> None:
        """打开代码编辑器时，使用真实英文类名定位文件。"""
        class_name = get_current_class_name(self)
        index = find_class_index(self, class_name)
        if index >= 0:
            self.class_combo.setCurrentIndex(index)
        original_edit_strategy_code(self)

    def reload_strategy_class(self) -> None:
        """重载策略后，继续保持双语显示。"""
        current_class_name = get_current_class_name(self)
        self.backtester_engine.reload_strategy_class()
        init_strategy_settings(self)
        index = find_class_index(self, current_class_name)
        if index >= 0:
            self.class_combo.setCurrentIndex(index)

    def write_log(self, msg: str) -> None:
        """写入日志，并在未查看日志区时自动滚动到底部。"""
        timestamp: str = datetime.now().strftime("%H:%M:%S")
        msg = f"{timestamp}\t{msg}"
        self.log_monitor.append(msg)

        # 如果鼠标没有停在日志框上，说明用户大概率不是在翻旧日志，
        # 这时自动滚到底部，方便直接看到最新输出。
        if not self.log_monitor.underMouse():
            scrollbar = self.log_monitor.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    BacktesterManager.get_current_class_name = get_current_class_name
    BacktesterManager.find_class_index = find_class_index
    BacktesterManager.init_strategy_settings = init_strategy_settings
    BacktesterManager.sync_symbol_input = sync_symbol_input
    BacktesterManager.load_backtesting_setting = load_backtesting_setting
    BacktesterManager.save_backtesting_setting = save_backtesting_setting
    BacktesterManager.start_backtesting = start_backtesting
    BacktesterManager.start_optimization = start_optimization
    BacktesterManager.start_downloading = start_downloading
    BacktesterManager.edit_strategy_code = edit_strategy_code
    BacktesterManager.reload_strategy_class = reload_strategy_class
    BacktesterManager.write_log = write_log
    BacktesterManager._vnpy_test_symbol_patched = True


def patch_backtesting_setting_editor() -> None:
    """把策略参数弹窗改成更适合学习的中文显示。"""
    from vnpy.trader.ui import QtGui, QtWidgets
    from vnpy_ctabacktester.ui.widget import BacktestingSettingEditor
    from vnpy_ctabacktester.locale import _

    if getattr(BacktestingSettingEditor, "_vnpy_test_label_patched", False):
        return

    def init_ui(self) -> None:
        """使用中文友好的参数标签重建回测参数弹窗。"""
        form: QtWidgets.QFormLayout = QtWidgets.QFormLayout()

        self.setWindowTitle(_("策略参数配置：{}").format(self.class_name))
        button_text: str = _("确定")
        parameter_labels = STRATEGY_PARAMETER_LABELS.get(self.class_name, {})

        for name, value in self.parameters.items():
            type_ = type(value)

            edit: QtWidgets.QLineEdit = QtWidgets.QLineEdit(str(value))
            if type_ is int:
                int_validator: QtGui.QIntValidator = QtGui.QIntValidator()
                edit.setValidator(int_validator)
            elif type_ is float:
                double_validator: QtGui.QDoubleValidator = QtGui.QDoubleValidator()
                edit.setValidator(double_validator)

            label_text = parameter_labels.get(name, name)
            form.addRow(label_text, edit)
            self.edits[name] = (edit, type_)

        button: QtWidgets.QPushButton = QtWidgets.QPushButton(button_text)
        button.clicked.connect(self.accept)
        form.addRow(button)

        widget: QtWidgets.QWidget = QtWidgets.QWidget()
        widget.setLayout(form)

        scroll: QtWidgets.QScrollArea = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)

        vbox: QtWidgets.QVBoxLayout = QtWidgets.QVBoxLayout()
        vbox.addWidget(scroll)
        self.setLayout(vbox)

    BacktestingSettingEditor.init_ui = init_ui
    BacktestingSettingEditor._vnpy_test_label_patched = True


def patch_main_window_behavior() -> None:
    """改善 macOS 下功能窗口打开后不前置的问题。"""
    from vnpy.trader.ui.mainwindow import MainWindow
    from vnpy.trader.ui import QtWidgets

    if getattr(MainWindow, "_vnpy_test_open_widget_patched", False):
        return

    def open_widget(self, widget_class: type[QtWidgets.QWidget], name: str) -> None:
        """打开功能窗口，并尽量把窗口带到前台。"""
        widget: QtWidgets.QWidget | None = self.widgets.get(name, None)
        if not widget:
            widget = widget_class(self.main_engine, self.event_engine)      # type: ignore
            self.widgets[name] = widget

        if isinstance(widget, QtWidgets.QDialog):
            widget.raise_()
            widget.activateWindow()
            widget.exec()
        else:
            # 在 macOS 下，单纯 show() 有时会创建窗口但不前置，
            # 表现出来就像“菜单点了没反应”。这里显式恢复、前置并激活。
            widget.showNormal()
            widget.raise_()
            widget.activateWindow()
            widget.show()

    MainWindow.open_widget = open_widget
    MainWindow._vnpy_test_open_widget_patched = True


def sync_local_strategies() -> None:
    """Copy repo strategies into vnpy discovery folders."""
    project_strategy_dir = Path(__file__).resolve().parent / "strategies"
    if not project_strategy_dir.exists():
        return

    # 只同步到工程内 .vntrader，避免写入 ~/strategies 或 ~/.vntrader。
    target_dirs = [get_trader_dir() / "strategies"]

    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)

        for source in project_strategy_dir.glob("*.py"):
            target = target_dir / source.name
            shutil.copy2(source, target)


def sync_local_packages() -> None:
    """Copy repo-local vnpy extension packages into vnpy import paths."""
    project_dir = Path(__file__).resolve().parent
    package_names = ["vnpy_localdemo", "vnpy_akshare", "vnpy_alertcenter"]
    # 只同步到工程内 .vntrader，避免污染用户根目录。
    target_dirs = [get_trader_dir()]

    for package_name in package_names:
        source_dir = project_dir / package_name
        if not source_dir.exists():
            continue

        for target_base in target_dirs:
            target_dir = target_base / package_name
            shutil.copytree(
                source_dir,
                target_dir,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )


def main() -> int:
    cleared_proxy_keys = disable_project_proxy_env()
    install_project_requests_no_proxy()
    if cleared_proxy_keys:
        print("项目进程已自动绕过代理：", ", ".join(cleared_proxy_keys))

    ensure_vnpy_settings()
    ensure_backtester_settings()
    cleanup_database_storage()
    sync_local_strategies()
    sync_local_packages()

    from vnpy.trader.engine import MainEngine
    from vnpy.trader.ui import MainWindow, create_qapp
    from vnpy_alertcenter import AlertCenterApp
    from vnpy_ctabacktester import CtaBacktesterApp
    from vnpy_ctastrategy import CtaStrategyApp
    from vnpy_datamanager import DataManagerApp

    patch_backtester_engine()
    patch_backtester_manager()
    patch_backtesting_setting_editor()
    patch_main_window_behavior()

    qapp = create_qapp("vnpy_test")
    patch_qt_stylesheet(qapp)
    install_gui_signal_handlers(qapp)

    main_engine = MainEngine()
    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)
    main_engine.add_app(AlertCenterApp)

    main_window = MainWindow(main_engine, main_engine.event_engine)
    main_window.showMaximized()

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
