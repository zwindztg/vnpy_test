import json
import platform
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
    "LessonDoubleMaStrategy": {
        "fast_window": "快均线周期（整数，默认10）",
        "slow_window": "慢均线周期（整数，默认20）",
        "fixed_size": "每次下单数量（整数，默认1）",
    },
}


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
    """返回与 vn.py 内部一致的配置目录。"""
    # 如果当前工作目录下已经有 .vntrader，就跟随 vn.py 使用本地目录。
    local_trader_dir = Path.cwd() / ".vntrader"
    if local_trader_dir.exists():
        return local_trader_dir

    trader_dir = Path.home() / ".vntrader"
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
    from vnpy_ctabacktester.ui.widget import BacktesterManager

    if getattr(BacktesterManager, "_vnpy_test_symbol_patched", False):
        return

    original_load_backtesting_setting = BacktesterManager.load_backtesting_setting
    original_start_backtesting = BacktesterManager.start_backtesting
    original_start_optimization = BacktesterManager.start_optimization
    original_start_downloading = BacktesterManager.start_downloading

    def sync_symbol_input(self) -> str:
        """把输入框里的股票代码统一成 vn.py 内部格式。"""
        current_symbol = self.symbol_line.text()
        normalized_symbol = normalize_a_share_vt_symbol(current_symbol)
        if normalized_symbol != current_symbol:
            self.symbol_line.setText(normalized_symbol)
        return normalized_symbol

    def patched_load_backtesting_setting(self) -> None:
        """加载本地回测配置后，顺手规范一次股票后缀。"""
        original_load_backtesting_setting(self)
        sync_symbol_input(self)

    def patched_start_backtesting(self) -> None:
        """开始回测前，先规范股票后缀输入。"""
        sync_symbol_input(self)
        original_start_backtesting(self)

    def patched_start_optimization(self) -> None:
        """开始优化前，先规范股票后缀输入。"""
        sync_symbol_input(self)
        original_start_optimization(self)

    def patched_start_downloading(self) -> None:
        """下载数据前，先规范股票后缀输入。"""
        sync_symbol_input(self)
        original_start_downloading(self)

    BacktesterManager.sync_symbol_input = sync_symbol_input
    BacktesterManager.load_backtesting_setting = patched_load_backtesting_setting
    BacktesterManager.start_backtesting = patched_start_backtesting
    BacktesterManager.start_optimization = patched_start_optimization
    BacktesterManager.start_downloading = patched_start_downloading
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


def sync_local_strategies() -> None:
    """Copy repo strategies into vnpy discovery folders."""
    project_strategy_dir = Path(__file__).resolve().parent / "strategies"
    if not project_strategy_dir.exists():
        return

    # vn.py appends TRADER_DIR to sys.path. With the default startup flow this
    # resolves to the user's home directory, while runtime data stays under
    # ~/.vntrader. Sync to both locations so CTA strategy discovery works.
    target_dirs = [
        Path.home() / "strategies",
        Path.home() / ".vntrader" / "strategies",
    ]

    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)

        for source in project_strategy_dir.glob("*.py"):
            target = target_dir / source.name
            shutil.copy2(source, target)


def sync_local_packages() -> None:
    """Copy repo-local vnpy extension packages into vnpy import paths."""
    project_dir = Path(__file__).resolve().parent
    package_names = ["vnpy_localdemo", "vnpy_akshare"]
    target_dirs = [Path.home(), Path.home() / ".vntrader"]

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
    ensure_vnpy_settings()
    ensure_backtester_settings()
    cleanup_database_storage()
    sync_local_strategies()
    sync_local_packages()

    from vnpy.trader.engine import MainEngine
    from vnpy.trader.ui import MainWindow, create_qapp
    from vnpy_ctabacktester import CtaBacktesterApp
    from vnpy_ctastrategy import CtaStrategyApp
    from vnpy_datamanager import DataManagerApp

    patch_backtester_engine()
    patch_backtester_manager()
    patch_backtesting_setting_editor()

    qapp = create_qapp("vnpy_test")
    patch_qt_stylesheet(qapp)

    main_engine = MainEngine()
    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, main_engine.event_engine)
    main_window.showMaximized()

    return qapp.exec()


if __name__ == "__main__":
    raise SystemExit(main())
