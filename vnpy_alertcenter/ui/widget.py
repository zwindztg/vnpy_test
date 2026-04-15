"""实时提醒 BaseApp 的窗口界面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import html
from pathlib import Path

from vnpy.event import Event
from vnpy.trader.ui import QtCore, QtGui, QtWidgets

from ..core import (
    BASIC_ALERT_STRATEGY,
    CHINA_TZ,
    ChartSnapshotData,
    MAX_SYMBOL_COUNT,
    STRATEGY_ORDER,
    AppConfig,
    RecordData,
    RunnerStatusData,
    SUPPORTED_INTERVALS,
    SymbolConfig,
    SymbolStateData,
    build_default_state,
    ensure_valid_symbol_config,
    fetch_reference_open_price,
    get_default_strategy_params,
    get_strategy_definition,
    get_strategy_display_name,
    normalize_strategy_name,
)
from ..engine import (
    APP_NAME,
    EVENT_ALERTCENTER_CHART,
    EVENT_ALERTCENTER_LOG,
    EVENT_ALERTCENTER_RECORD,
    EVENT_ALERTCENTER_STATE,
    EVENT_ALERTCENTER_STATUS,
    AlertCenterEngine,
)
from .chart_widget import AlertChartWidget


@dataclass
class ParamInputWidgets:
    """保存单个参数格子的标签和输入框。"""

    container: QtWidgets.QWidget
    label: QtWidgets.QLabel
    editor: QtWidgets.QDoubleSpinBox
    spec_name: str | None = None


@dataclass
class SymbolRowWidgets:
    """保存一行股票配置输入控件。"""

    enabled: QtWidgets.QCheckBox
    vt_symbol: QtWidgets.QLineEdit
    strategy_combo: QtWidgets.QComboBox
    params: list[ParamInputWidgets]
    current_strategy_name: str = BASIC_ALERT_STRATEGY
    cached_params: dict[str, dict[str, float | int]] = field(default_factory=dict)


class AlertCenterWidget(QtWidgets.QWidget):
    """用于在 vn.py 内部操作实时提醒的主窗口。"""

    signal_log: QtCore.Signal = QtCore.Signal(Event)
    signal_status: QtCore.Signal = QtCore.Signal(Event)
    signal_record: QtCore.Signal = QtCore.Signal(Event)
    signal_state: QtCore.Signal = QtCore.Signal(Event)
    signal_chart: QtCore.Signal = QtCore.Signal(Event)

    STATE_HEADERS: tuple[str, ...] = (
        "股票",
        "启用",
        "策略",
        "数据源",
        "最新K线",
        "最新收盘",
        "信号状态",
        "最近提醒",
        "最近错误",
        "运行状态",
    )
    RECORD_HEADERS: tuple[str, ...] = (
        "时间",
        "股票",
        "策略",
        "周期",
        "规则",
        "级别",
        "数值",
        "K线时间",
        "文案",
    )

    def __init__(self, main_engine, event_engine) -> None:
        super().__init__()
        self.main_engine = main_engine
        self.event_engine = event_engine
        self.alert_engine: AlertCenterEngine = main_engine.get_engine(APP_NAME)  # type: ignore
        self.row_widgets: list[SymbolRowWidgets] = []
        self.state_rows: dict[str, int] = {}
        self.current_config: AppConfig = self.alert_engine.load_config()
        self.current_log_mode: str = "neutral"

        self.init_ui()
        self.register_event()
        self.load_config_to_form(self.current_config)
        self.load_recent_records()
        self.populate_state_placeholders(self.current_config)
        self.refresh_runtime_info()

    def init_ui(self) -> None:
        """初始化整个窗口布局。"""
        self.setWindowTitle("实时提醒中心")
        self.resize(1600, 940)
        self.close_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Close, self)
        # 让 macOS 下的 Command+W 直接关闭当前提醒窗口，不影响主窗口退出逻辑。
        self.close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.close_shortcut.activated.connect(self.close)

        layout = QtWidgets.QVBoxLayout()
        layout.addLayout(self.create_control_bar())
        layout.addWidget(self.create_global_group())
        layout.addWidget(self.create_symbol_group())
        layout.addWidget(self.create_runtime_group())
        layout.addWidget(self.create_bottom_splitter(), stretch=1)
        self.setLayout(layout)

    def create_control_bar(self) -> QtWidgets.QHBoxLayout:
        """创建顶部操作栏。"""
        layout = QtWidgets.QHBoxLayout()

        self.load_button = QtWidgets.QPushButton("加载配置")
        self.save_button = QtWidgets.QPushButton("保存配置")
        self.test_button = QtWidgets.QPushButton("单次测试")
        self.start_button = QtWidgets.QPushButton("启动提醒")
        self.stop_button = QtWidgets.QPushButton("停止提醒")
        self.mode_label = QtWidgets.QLabel("空闲")
        self.mode_label.setMinimumWidth(220)
        self.status_label = QtWidgets.QLabel("未启动")
        self.status_label.setMinimumWidth(260)

        self.load_button.clicked.connect(self.load_config_from_engine)
        self.save_button.clicked.connect(self.save_form_config)
        self.test_button.clicked.connect(self.run_preview_once)
        self.start_button.clicked.connect(self.start_alerting)
        self.stop_button.clicked.connect(self.stop_alerting)

        layout.addWidget(self.load_button)
        layout.addWidget(self.save_button)
        layout.addWidget(self.test_button)
        layout.addWidget(self.start_button)
        layout.addWidget(self.stop_button)
        layout.addStretch(1)
        layout.addWidget(QtWidgets.QLabel("当前模式："))
        layout.addWidget(self.mode_label)
        layout.addWidget(QtWidgets.QLabel("整体状态："))
        layout.addWidget(self.status_label)
        return layout

    def create_global_group(self) -> QtWidgets.QGroupBox:
        """创建全局参数区域。"""
        group = QtWidgets.QGroupBox("全局设置")
        form = QtWidgets.QFormLayout()

        self.interval_combo = QtWidgets.QComboBox()
        for interval in SUPPORTED_INTERVALS:
            self.interval_combo.addItem(interval, interval)

        self.poll_spin = QtWidgets.QSpinBox()
        self.poll_spin.setRange(5, 3600)
        self.poll_spin.setSuffix(" 秒")

        self.cooldown_spin = QtWidgets.QSpinBox()
        self.cooldown_spin.setRange(0, 7200)
        self.cooldown_spin.setSuffix(" 秒")

        self.adjust_combo = QtWidgets.QComboBox()
        self.adjust_combo.addItem("前复权", "qfq")
        self.adjust_combo.addItem("后复权", "hfq")
        self.adjust_combo.addItem("不复权", "")

        self.notification_checkbox = QtWidgets.QCheckBox("启用桌面通知")
        self.preview_time_edit = QtWidgets.QDateTimeEdit()
        self.preview_time_edit.setCalendarPopup(True)
        self.preview_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.preview_time_edit.setDateTime(self.build_default_preview_qdatetime())

        form.addRow("提醒周期", self.interval_combo)
        form.addRow("轮询间隔", self.poll_spin)
        form.addRow("冷却时间", self.cooldown_spin)
        form.addRow("复权方式", self.adjust_combo)
        form.addRow("", self.notification_checkbox)
        form.addRow("模拟时间", self.preview_time_edit)

        group.setLayout(form)
        return group

    def create_symbol_group(self) -> QtWidgets.QGroupBox:
        """创建股票配置区域。"""
        group = QtWidgets.QGroupBox("股票配置（最多 3 只）")
        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(2, 1)

        headers = ("启用", "股票代码", "提醒策略", "参数1", "参数2", "参数3", "参数4")
        for column, title in enumerate(headers):
            label = QtWidgets.QLabel(title)
            label.setStyleSheet("font-weight: 600;")
            grid.addWidget(label, 0, column)

        for row in range(MAX_SYMBOL_COUNT):
            strategy_combo = QtWidgets.QComboBox()
            for strategy_name in STRATEGY_ORDER:
                strategy_combo.addItem(get_strategy_display_name(strategy_name), strategy_name)

            param_widgets: list[ParamInputWidgets] = []
            row_widgets = SymbolRowWidgets(
                enabled=QtWidgets.QCheckBox(),
                vt_symbol=QtWidgets.QLineEdit(),
                strategy_combo=strategy_combo,
                params=param_widgets,
            )
            row_widgets.vt_symbol.setPlaceholderText("例如 601869.SSE")
            self.row_widgets.append(row_widgets)

            grid.addWidget(row_widgets.enabled, row + 1, 0)
            grid.addWidget(row_widgets.vt_symbol, row + 1, 1)
            grid.addWidget(row_widgets.strategy_combo, row + 1, 2)

            for column in range(4):
                param_input = self.create_param_input()
                param_widgets.append(param_input)
                grid.addWidget(param_input.container, row + 1, column + 3)

            row_widgets.strategy_combo.currentIndexChanged.connect(
                lambda _value, row_index=row: self.on_strategy_changed(row_index)
            )
            row_widgets.vt_symbol.editingFinished.connect(
                lambda row_index=row: self.on_symbol_edited(row_index)
            )
            self.apply_strategy_to_row(row_widgets, BASIC_ALERT_STRATEGY)

        group.setLayout(grid)
        return group

    def create_runtime_group(self) -> QtWidgets.QGroupBox:
        """创建运行信息区。"""
        group = QtWidgets.QGroupBox("运行信息")
        form = QtWidgets.QFormLayout()

        self.config_path_label = QtWidgets.QLabel("")
        self.history_path_label = QtWidgets.QLabel("")
        self.thread_label = QtWidgets.QLabel("")

        form.addRow("配置文件", self.config_path_label)
        form.addRow("记录文件", self.history_path_label)
        form.addRow("线程状态", self.thread_label)
        group.setLayout(form)
        return group

    def create_bottom_splitter(self) -> QtWidgets.QSplitter:
        """创建左侧表格、右侧图表和日志的主视图。"""
        left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        left_splitter.addWidget(self.create_state_table())
        left_splitter.addWidget(self.create_record_table())
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 1)

        self.chart_widget = AlertChartWidget()

        self.log_edit = QtWidgets.QTextEdit()
        self.log_edit.setReadOnly(True)

        right_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_splitter.addWidget(self.chart_widget)
        right_splitter.addWidget(self.log_edit)
        right_splitter.setStretchFactor(0, 7)
        right_splitter.setStretchFactor(1, 3)

        outer_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        outer_splitter.addWidget(left_splitter)
        outer_splitter.addWidget(right_splitter)
        outer_splitter.setStretchFactor(0, 45)
        outer_splitter.setStretchFactor(1, 55)
        return outer_splitter

    def create_state_table(self) -> QtWidgets.QTableWidget:
        """创建状态面板表格。"""
        self.state_table = QtWidgets.QTableWidget()
        self.state_table.setColumnCount(len(self.STATE_HEADERS))
        self.state_table.setHorizontalHeaderLabels(self.STATE_HEADERS)
        self.state_table.verticalHeader().setVisible(False)
        self.state_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.state_table.setAlternatingRowColors(True)
        self.state_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.state_table.horizontalHeader().setStretchLastSection(True)
        return self.state_table

    def create_record_table(self) -> QtWidgets.QTableWidget:
        """创建触发记录表格。"""
        self.record_table = QtWidgets.QTableWidget()
        self.record_table.setColumnCount(len(self.RECORD_HEADERS))
        self.record_table.setHorizontalHeaderLabels(self.RECORD_HEADERS)
        self.record_table.verticalHeader().setVisible(False)
        self.record_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.record_table.setAlternatingRowColors(True)
        self.record_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.record_table.horizontalHeader().setStretchLastSection(True)
        return self.record_table

    def create_param_input(self) -> ParamInputWidgets:
        """创建一组“参数标签 + 输入框”控件。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label = QtWidgets.QLabel("未使用")
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        editor = QtWidgets.QDoubleSpinBox()
        editor.setRange(0.0, 100000.0)
        editor.setDecimals(0)
        editor.setSingleStep(1)

        layout.addWidget(label)
        layout.addWidget(editor)
        container.setLayout(layout)
        return ParamInputWidgets(container=container, label=label, editor=editor)

    def register_event(self) -> None:
        """注册引擎事件。"""
        self.signal_log.connect(self.process_log_event)
        self.signal_status.connect(self.process_status_event)
        self.signal_record.connect(self.process_record_event)
        self.signal_state.connect(self.process_state_event)
        self.signal_chart.connect(self.process_chart_event)

        self.event_engine.register(EVENT_ALERTCENTER_LOG, self.signal_log.emit)
        self.event_engine.register(EVENT_ALERTCENTER_STATUS, self.signal_status.emit)
        self.event_engine.register(EVENT_ALERTCENTER_RECORD, self.signal_record.emit)
        self.event_engine.register(EVENT_ALERTCENTER_STATE, self.signal_state.emit)
        self.event_engine.register(EVENT_ALERTCENTER_CHART, self.signal_chart.emit)

    def load_config_from_engine(self) -> None:
        """重新从磁盘读取配置并刷新界面。"""
        self.current_config = self.alert_engine.load_config()
        self.load_config_to_form(self.current_config)
        self.populate_state_placeholders(self.current_config)
        self.load_recent_records()
        self.refresh_runtime_info()
        self.append_log("INFO", "UI", "已从配置文件重新加载提醒设置。")
        self.set_mode_label("neutral", "空闲")
        self.refresh_chart_placeholder(self.current_config)

    def reset_row(self, row_widgets: SymbolRowWidgets) -> None:
        """把某一行恢复为默认展示。"""
        row_widgets.enabled.setChecked(False)
        row_widgets.vt_symbol.setText("")
        row_widgets.cached_params.clear()
        row_widgets.strategy_combo.blockSignals(True)
        self.set_combo_data(row_widgets.strategy_combo, BASIC_ALERT_STRATEGY)
        row_widgets.strategy_combo.blockSignals(False)
        row_widgets.current_strategy_name = BASIC_ALERT_STRATEGY
        self.apply_strategy_to_row(row_widgets, BASIC_ALERT_STRATEGY)

    def load_config_to_form(self, config: AppConfig) -> None:
        """把配置对象回填到表单。"""
        self.set_combo_data(self.interval_combo, config.interval)
        self.poll_spin.setValue(config.poll_seconds)
        self.cooldown_spin.setValue(config.cooldown_seconds)
        self.set_combo_data(self.adjust_combo, config.adjust)
        self.notification_checkbox.setChecked(config.notification_enabled)

        for row_widgets in self.row_widgets:
            self.reset_row(row_widgets)

        for index, symbol in enumerate(config.symbol_configs[:MAX_SYMBOL_COUNT]):
            row_widgets = self.row_widgets[index]
            row_widgets.enabled.setChecked(symbol.enabled)
            row_widgets.vt_symbol.setText(symbol.vt_symbol)
            row_widgets.cached_params[symbol.strategy_name] = dict(symbol.params)
            row_widgets.strategy_combo.blockSignals(True)
            self.set_combo_data(row_widgets.strategy_combo, symbol.strategy_name)
            row_widgets.strategy_combo.blockSignals(False)
            row_widgets.current_strategy_name = symbol.strategy_name
            self.apply_strategy_to_row(row_widgets, symbol.strategy_name, symbol.params)
            self.refresh_basic_price_defaults(row_widgets)
        self.refresh_chart_placeholder(config)

    def collect_strategy_params(self, row_widgets: SymbolRowWidgets, strategy_name: str) -> dict[str, float | int]:
        """按当前策略读取一行参数。"""
        params: dict[str, float | int] = {}
        for spec, param_input in zip(get_strategy_definition(strategy_name).param_specs, row_widgets.params):
            value = param_input.editor.value()
            if spec.kind == "int":
                params[spec.name] = int(round(value))
            else:
                params[spec.name] = round(float(value), spec.decimals)
        return params

    def cache_current_row_params(self, row_widgets: SymbolRowWidgets) -> None:
        """在切换策略前缓存当前策略的参数值。"""
        strategy_name = normalize_strategy_name(row_widgets.current_strategy_name)
        row_widgets.cached_params[strategy_name] = self.collect_strategy_params(row_widgets, strategy_name)

    def apply_strategy_to_row(
        self,
        row_widgets: SymbolRowWidgets,
        strategy_name: str,
        params: dict[str, float | int] | None = None,
    ) -> None:
        """按策略定义刷新某一行的参数标签和输入框。"""
        definition = get_strategy_definition(strategy_name)
        effective_params = get_default_strategy_params(strategy_name)
        if strategy_name in row_widgets.cached_params:
            effective_params.update(row_widgets.cached_params[strategy_name])
        if params:
            effective_params.update(params)
        row_widgets.cached_params[strategy_name] = dict(effective_params)

        for index, param_input in enumerate(row_widgets.params):
            if index < len(definition.param_specs):
                spec = definition.param_specs[index]
                value = effective_params.get(spec.name, spec.default)

                param_input.spec_name = spec.name
                param_input.label.setText(spec.label)
                param_input.editor.setEnabled(True)
                param_input.editor.setRange(spec.minimum, spec.maximum)
                param_input.editor.setDecimals(spec.decimals)
                param_input.editor.setSingleStep(spec.step)
                param_input.editor.setValue(float(value))
            else:
                param_input.spec_name = None
                param_input.label.setText("未使用")
                param_input.editor.setEnabled(False)
                param_input.editor.setRange(0.0, 0.0)
                param_input.editor.setDecimals(0)
                param_input.editor.setSingleStep(1)
                param_input.editor.setValue(0.0)

    def on_strategy_changed(self, row_index: int) -> None:
        """切换策略时动态刷新该行参数区。"""
        row_widgets = self.row_widgets[row_index]
        self.cache_current_row_params(row_widgets)
        strategy_name = normalize_strategy_name(str(row_widgets.strategy_combo.currentData()))
        row_widgets.current_strategy_name = strategy_name
        self.apply_strategy_to_row(row_widgets, strategy_name)
        self.refresh_basic_price_defaults(row_widgets)

    def on_symbol_edited(self, row_index: int) -> None:
        """用户修改股票代码后，按最近交易日开盘价刷新基础提醒默认值。"""
        row_widgets = self.row_widgets[row_index]
        self.refresh_basic_price_defaults(row_widgets)

    def refresh_basic_price_defaults(self, row_widgets: SymbolRowWidgets) -> None:
        """把基础提醒策略的突破价/止损价更新为最近交易日开盘价的正负 2%。"""
        strategy_name = normalize_strategy_name(str(row_widgets.strategy_combo.currentData()))
        vt_symbol = row_widgets.vt_symbol.text().strip().upper()
        if strategy_name != BASIC_ALERT_STRATEGY or not vt_symbol:
            return

        try:
            open_price, _source_name = fetch_reference_open_price(vt_symbol)
        except Exception:
            # 默认值获取失败时保留当前输入，避免影响用户继续操作。
            return

        params = row_widgets.cached_params.get(
            BASIC_ALERT_STRATEGY,
            get_default_strategy_params(BASIC_ALERT_STRATEGY),
        ).copy()
        params["breakout_price"] = round(open_price * 1.02, 3)
        params["stop_loss_price"] = round(open_price * 0.98, 3)
        row_widgets.cached_params[BASIC_ALERT_STRATEGY] = params
        self.apply_strategy_to_row(row_widgets, BASIC_ALERT_STRATEGY, params)

    def collect_config_from_form(self) -> AppConfig:
        """把当前表单值组装成配置对象。"""
        symbol_configs: list[SymbolConfig] = []
        seen_symbols: set[str] = set()
        for row_widgets in self.row_widgets:
            vt_symbol = row_widgets.vt_symbol.text().strip().upper()
            if not vt_symbol:
                continue

            strategy_name = normalize_strategy_name(str(row_widgets.strategy_combo.currentData()))
            params = self.collect_strategy_params(row_widgets, strategy_name)
            symbol_config = SymbolConfig(
                vt_symbol=vt_symbol,
                strategy_name=strategy_name,
                params=params,
                enabled=row_widgets.enabled.isChecked(),
            )
            try:
                symbol_config = ensure_valid_symbol_config(symbol_config)
            except ValueError as exc:
                raise ValueError(f"{vt_symbol} 的参数不合法：{exc}") from exc

            if symbol_config.vt_symbol in seen_symbols:
                raise ValueError(f"{symbol_config.vt_symbol} 重复出现，请删除重复股票。")

            seen_symbols.add(symbol_config.vt_symbol)
            symbol_configs.append(symbol_config)

        if not symbol_configs:
            raise ValueError("请至少填写一只股票代码。")
        if not any(symbol.enabled for symbol in symbol_configs):
            raise ValueError("请至少启用一只股票。")

        return AppConfig(
            interval=str(self.interval_combo.currentData()),
            poll_seconds=int(self.poll_spin.value()),
            adjust=str(self.adjust_combo.currentData()),
            cooldown_seconds=int(self.cooldown_spin.value()),
            alert_history_path=self.current_config.alert_history_path,
            notification_enabled=self.notification_checkbox.isChecked(),
            symbol_configs=tuple(symbol_configs),
        )

    def save_form_config(self) -> None:
        """保存当前表单配置。"""
        try:
            config = self.collect_config_from_form()
        except ValueError as exc:
            self.show_warning(str(exc))
            return

        self.alert_engine.save_config(config)
        self.current_config = config
        self.populate_state_placeholders(config)
        self.refresh_chart_placeholder(config)
        self.refresh_runtime_info()

    def run_preview_once(self) -> None:
        """用历史时间执行单次测试，便于非交易时段验证提醒逻辑。"""
        try:
            config = self.collect_config_from_form()
            preview_dt = self.preview_time_edit.dateTime().toPyDateTime()
            preview_dt = preview_dt.replace(tzinfo=CHINA_TZ)
        except ValueError as exc:
            self.show_warning(str(exc))
            return

        self.current_config = config
        self.populate_state_placeholders(config)
        self.refresh_chart_placeholder(config)
        self.current_log_mode = "preview"
        self.set_mode_label("preview", f"单次测试 / {preview_dt.strftime('%m-%d %H:%M')}")
        self.append_session_marker(f"单次测试开始：{preview_dt.strftime('%Y-%m-%d %H:%M')}", "preview")
        try:
            self.alert_engine.run_preview_once(config, preview_dt)
        except RuntimeError as exc:
            self.show_warning(str(exc))
            return
        self.refresh_runtime_info()

    def start_alerting(self) -> None:
        """用当前表单配置启动提醒线程。"""
        try:
            config = self.collect_config_from_form()
        except ValueError as exc:
            self.show_warning(str(exc))
            return

        self.current_config = config
        self.populate_state_placeholders(config)
        self.refresh_chart_placeholder(config)
        self.current_log_mode = "live"
        self.set_mode_label("live", "实时运行")
        self.append_session_marker("实时提醒启动", "live")
        self.alert_engine.start_alerting(config)
        self.refresh_runtime_info()

    def stop_alerting(self) -> None:
        """停止提醒线程。"""
        self.set_mode_label("neutral", "停止中")
        self.append_session_marker("实时提醒停止请求", "neutral")
        self.alert_engine.stop_alerting()
        self.refresh_runtime_info()

    def load_recent_records(self) -> None:
        """读取最近记录，刷新表格。"""
        self.record_table.setRowCount(0)
        for record in self.alert_engine.get_recent_records(limit=80):
            self.insert_record_row(record, to_top=False)

    def populate_state_placeholders(self, config: AppConfig) -> None:
        """按当前配置重建状态表格的股票行。"""
        self.state_table.setRowCount(0)
        self.state_rows.clear()

        for row, symbol in enumerate(config.symbol_configs[:MAX_SYMBOL_COUNT]):
            self.state_table.insertRow(row)
            self.state_rows[symbol.vt_symbol] = row
            self.update_state_row(build_default_state(symbol))

    def process_log_event(self, event: Event) -> None:
        """处理日志事件。"""
        data = event.data
        self.append_log(data.level, data.source, data.message, data.timestamp)

    def process_status_event(self, event: Event) -> None:
        """处理整体状态事件。"""
        data: RunnerStatusData = event.data
        self.status_label.setText(data.message)
        if not data.running and data.paused:
            self.current_log_mode = "preview"
            self.set_mode_label("preview", data.message)
            self.status_label.setStyleSheet("color: #2563eb; font-weight: 600;")
        elif data.running and data.paused:
            self.set_mode_label("neutral", data.message)
            self.status_label.setStyleSheet("color: #d97706; font-weight: 600;")
        elif data.running:
            self.current_log_mode = "live"
            self.set_mode_label("live", "实时运行")
            self.status_label.setStyleSheet("color: #15803d; font-weight: 600;")
        else:
            next_mode = "preview" if "测试完成" in data.message else "neutral"
            self.set_mode_label(next_mode, data.message if next_mode == "preview" else "空闲")
            self.status_label.setStyleSheet("color: #475569; font-weight: 600;")

        self.start_button.setEnabled(not data.running)
        self.stop_button.setEnabled(data.running)
        self.refresh_runtime_info()

    def process_record_event(self, event: Event) -> None:
        """处理触发记录事件。"""
        self.insert_record_row(event.data, to_top=True)

    def process_state_event(self, event: Event) -> None:
        """处理单只股票状态事件。"""
        self.update_state_row(event.data)

    def process_chart_event(self, event: Event) -> None:
        """处理右侧 K 线图快照事件。"""
        data: ChartSnapshotData = event.data
        primary_symbol = self.get_primary_enabled_symbol(self.current_config)
        if primary_symbol and data.vt_symbol != primary_symbol.vt_symbol:
            return
        self.chart_widget.set_snapshot(data)

    def update_state_row(self, data: SymbolStateData) -> None:
        """更新或新增一条状态行。"""
        row = self.state_rows.get(data.vt_symbol)
        if row is None:
            row = self.state_table.rowCount()
            self.state_table.insertRow(row)
            self.state_rows[data.vt_symbol] = row

        values = [
            data.vt_symbol,
            "是" if data.enabled else "否",
            get_strategy_display_name(data.strategy_name),
            data.data_source,
            data.latest_bar_dt,
            data.latest_close,
            data.signal_state,
            data.last_alert_at,
            data.last_error,
            data.status,
        ]

        for column, value in enumerate(values):
            item = self.state_table.item(row, column)
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.state_table.setItem(row, column, item)
            item.setText(str(value))

    def insert_record_row(self, data: RecordData, to_top: bool) -> None:
        """向记录表中插入一条记录。"""
        row = 0 if to_top else self.record_table.rowCount()
        self.record_table.insertRow(row)

        values = [
            data.occurred_at,
            data.vt_symbol,
            get_strategy_display_name(data.strategy_name),
            data.interval,
            data.rule_name,
            data.level,
            data.rule_value,
            data.triggered_bar_dt,
            data.message,
        ]
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(str(value))
            self.record_table.setItem(row, column, item)

    def append_log(self, level: str, source: str, message: str, timestamp: str | None = None) -> None:
        """向日志框追加一条格式化日志。"""
        log_time = timestamp or QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        mode = self.infer_log_mode(level, message)
        badge_text, color = self.get_log_badge(mode, level, message)
        line = (
            f'<span style="color:#94a3b8;">[{html.escape(log_time)}]</span> '
            f'<span style="color:{color}; font-weight:600;">[{html.escape(badge_text)}]</span> '
            f'<span style="color:#cbd5e1;">[{html.escape(level)}] [{html.escape(source)}]</span> '
            f'<span style="color:#e2e8f0;">{html.escape(message)}</span>'
        )
        self.log_edit.append(line)

    def append_session_marker(self, title: str, mode: str) -> None:
        """在日志区插入明显的会话分隔线，区分测试和真实运行。"""
        badge_text, color = self.get_log_badge(mode, "INFO", title)
        marker = (
            f'<span style="color:{color}; font-weight:700;">'
            f'========== {html.escape(badge_text)} · {html.escape(title)} =========='
            "</span>"
        )
        self.log_edit.append(marker)

    def infer_log_mode(self, level: str, message: str) -> str:
        """根据日志内容判断当前属于测试模式还是实时运行模式。"""
        if level == "ERROR":
            return "error"
        if "历史回放测试" in message or "测试中" in message or "测试完成" in message:
            return "preview"
        if "实时提醒已启动" in message or "提醒线程已停止" in message:
            return "live"
        return self.current_log_mode

    def get_log_badge(self, mode: str, level: str, message: str) -> tuple[str, str]:
        """为日志生成更醒目的模式标签和颜色。"""
        if level == "ERROR":
            return "错误", "#ef4444"
        if "风控型提醒" in message:
            return "风控", "#f59e0b"
        if "观察型提醒" in message:
            return "提醒", "#10b981"
        if mode == "preview":
            return "测试", "#2563eb"
        if mode == "live":
            return "实盘", "#16a34a"
        return "系统", "#64748b"

    def set_mode_label(self, mode: str, text: str) -> None:
        """在顶部控制栏显示当前模式，避免只看日志才能分辨状态。"""
        label_text = text.strip() or "空闲"
        if mode == "preview":
            color = "#2563eb"
            background = "rgba(37, 99, 235, 0.15)"
        elif mode == "live":
            color = "#16a34a"
            background = "rgba(22, 163, 74, 0.15)"
        else:
            color = "#64748b"
            background = "rgba(100, 116, 139, 0.15)"

        self.mode_label.setText(label_text)
        self.mode_label.setStyleSheet(
            "font-weight: 700;"
            f"color: {color};"
            f"background: {background};"
            "border: 1px solid rgba(148, 163, 184, 0.35);"
            "border-radius: 6px;"
            "padding: 4px 10px;"
        )

    def refresh_runtime_info(self) -> None:
        """刷新配置文件、记录文件和线程状态显示。"""
        runtime = self.alert_engine.get_runtime_status()
        self.config_path_label.setText(str(Path(runtime["config_path"])))
        self.history_path_label.setText(str(Path(runtime["history_path"])))
        running = bool(runtime["running"])
        self.thread_label.setText("运行中" if running else "未运行")
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

    def get_primary_enabled_symbol(self, config: AppConfig) -> SymbolConfig | None:
        """返回当前配置里首个启用的股票，供图表区复用。"""
        for symbol in config.symbol_configs:
            if symbol.enabled:
                return symbol
        return None

    def refresh_chart_placeholder(self, config: AppConfig) -> None:
        """在启动前根据当前配置刷新右侧图表的占位提示。"""
        primary_symbol = self.get_primary_enabled_symbol(config)
        if primary_symbol is None:
            self.chart_widget.clear_snapshot("暂无图表数据，请先启用至少一只股票")
            return
        self.chart_widget.clear_snapshot(
            f"等待 {primary_symbol.vt_symbol} 图表数据，请先执行单次测试或启动提醒"
        )

    def set_combo_data(self, combo: QtWidgets.QComboBox, value: str) -> None:
        """按 userData 选择下拉项。"""
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def build_default_preview_qdatetime(self) -> QtCore.QDateTime:
        """默认把单次测试时间放在最近一个交易日开盘时，方便直接验证历史数据。"""
        default_dt = datetime.now() - timedelta(days=1)
        while default_dt.weekday() >= 5:
            default_dt -= timedelta(days=1)
        default_dt = default_dt.replace(hour=9, minute=30, second=0, microsecond=0)
        return QtCore.QDateTime(default_dt)

    def show_warning(self, message: str) -> None:
        """统一显示输入或配置错误。"""
        QtWidgets.QMessageBox.warning(self, "提醒配置", message)
