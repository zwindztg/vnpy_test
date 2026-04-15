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
from .chart_widget import AlertChartPopupWindow, AlertChartWidget


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
        self.latest_chart_snapshot: ChartSnapshotData | None = None
        self.chart_popup: AlertChartPopupWindow | None = None
        self.latest_runner_status: RunnerStatusData | None = None
        self.state_cache: dict[str, SymbolStateData] = {}
        self.recent_records_cache: list[RecordData] = []
        self.latest_preview_time: datetime | None = None
        self.metric_value_labels: dict[str, QtWidgets.QLabel] = {}

        self.init_ui()
        self.register_event()
        self.load_config_to_form(self.current_config)
        self.load_recent_records()
        self.populate_state_placeholders(self.current_config)
        self.refresh_runtime_info()
        # 启动时先写一条界面日志，方便确认日志区已经恢复显示。
        self.append_log("INFO", "UI", "日志窗口已就绪，单次测试或启动提醒后会在这里显示运行过程。")

    def init_ui(self) -> None:
        """初始化整个窗口布局。"""
        self.setWindowTitle("实时提醒中心")
        self.setObjectName("alertCenterWidget")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(1660, 960)
        self.close_shortcut = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Close, self)
        # 让 macOS 下的 Command+W 直接关闭当前提醒窗口，不影响主窗口退出逻辑。
        self.close_shortcut.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.close_shortcut.activated.connect(self.close)
        self.apply_scoped_stylesheet()

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addWidget(self.create_main_splitter(), stretch=1)
        self.setLayout(layout)

    def apply_scoped_stylesheet(self) -> None:
        """只给提醒中心窗口注入局部深色样式，避免污染其他 vn.py 页面。"""
        self.setStyleSheet(
            """
            #alertCenterWidget {
                background-color: #081321;
                color: #e5edf7;
            }
            #alertCenterWidget QFrame[cardRole="hero"],
            #alertCenterWidget QFrame[cardRole="panel"],
            #alertCenterWidget QFrame[cardRole="metric"],
            #alertCenterWidget QFrame[cardRole="statusBlock"] {
                background-color: #0f1c2b;
                border: 1px solid #223447;
                border-radius: 18px;
            }
            #alertCenterWidget QLabel[textRole="eyebrow"] {
                color: #5b7aa0;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #alertCenterWidget QLabel[textRole="heroTitle"] {
                color: #f4f8fc;
                font-size: 24px;
                font-weight: 700;
            }
            #alertCenterWidget QLabel[textRole="heroSubtitle"],
            #alertCenterWidget QLabel[textRole="cardSubtitle"] {
                color: #8ea2b8;
                font-size: 12px;
            }
            #alertCenterWidget QLabel[textRole="cardTitle"] {
                color: #e5edf7;
                font-size: 18px;
                font-weight: 700;
            }
            #alertCenterWidget QLabel[textRole="metricTitle"],
            #alertCenterWidget QLabel[textRole="gridHeader"] {
                color: #8ea2b8;
                font-size: 12px;
                font-weight: 600;
            }
            #alertCenterWidget QLabel[textRole="metricValue"] {
                color: #f4f8fc;
                font-size: 22px;
                font-weight: 700;
            }
            #alertCenterWidget QLabel[textRole="metricValue"][metricTone="success"] {
                color: #35d07f;
            }
            #alertCenterWidget QLabel[textRole="metricValue"][metricTone="primary"] {
                color: #85abff;
            }
            #alertCenterWidget QLabel[textRole="metricValue"][metricTone="warning"] {
                color: #ffcc6f;
            }
            #alertCenterWidget QLabel[textRole="metricValue"][metricTone="neutral"] {
                color: #dce7f3;
            }
            #alertCenterWidget QLabel[badgeRole="pill"] {
                color: #dce7f3;
                background-color: #12253a;
                border: 1px solid #30475f;
                border-radius: 12px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            #alertCenterWidget QLabel[badgeRole="pill"][badgeTone="neutral"] {
                color: #aebfd0;
                background-color: #12253a;
                border-color: #30475f;
            }
            #alertCenterWidget QLabel[badgeRole="pill"][badgeTone="preview"] {
                color: #9bc0ff;
                background-color: #13315b;
                border-color: #2f6bff;
            }
            #alertCenterWidget QLabel[badgeRole="pill"][badgeTone="live"] {
                color: #dff8eb;
                background-color: #123926;
                border-color: #17b15c;
            }
            #alertCenterWidget QLabel[badgeRole="pill"][badgeTone="warning"] {
                color: #ffe2a2;
                background-color: #463314;
                border-color: #f4a71d;
            }
            #alertCenterWidget QLabel[badgeRole="pill"][badgeTone="error"] {
                color: #ffd3d3;
                background-color: #4a1f22;
                border-color: #ff6b6b;
            }
            #alertCenterWidget QPushButton {
                min-height: 36px;
                padding: 0 14px;
                border-radius: 12px;
                border: 1px solid #355069;
                background-color: #142233;
                color: #dce7f3;
                font-weight: 600;
            }
            #alertCenterWidget QPushButton[buttonTone="primary"] {
                background-color: #2f6bff;
                border-color: #2f6bff;
                color: #ffffff;
            }
            #alertCenterWidget QPushButton[buttonTone="success"] {
                background-color: #17b15c;
                border-color: #17b15c;
                color: #ffffff;
            }
            #alertCenterWidget QPushButton[buttonTone="ghost"] {
                background-color: #102132;
                border-color: #2a4157;
                color: #d7e2ee;
            }
            #alertCenterWidget QPushButton:hover {
                border-color: #4d6b86;
            }
            #alertCenterWidget QPushButton:disabled {
                background-color: #101a25;
                border-color: #223447;
                color: #63778d;
            }
            #alertCenterWidget QLineEdit,
            #alertCenterWidget QComboBox,
            #alertCenterWidget QSpinBox,
            #alertCenterWidget QDoubleSpinBox,
            #alertCenterWidget QDateTimeEdit {
                min-height: 36px;
                padding: 0 12px;
                border: 1px solid #334a60;
                border-radius: 10px;
                background-color: #0c1520;
                color: #e5edf7;
                selection-background-color: #2f6bff;
            }
            #alertCenterWidget QComboBox::drop-down,
            #alertCenterWidget QDateTimeEdit::drop-down {
                border: none;
                width: 24px;
            }
            #alertCenterWidget QComboBox QAbstractItemView {
                background-color: #0f1c2b;
                border: 1px solid #334a60;
                color: #e5edf7;
                selection-background-color: #2f6bff;
            }
            #alertCenterWidget QCheckBox {
                color: #dce7f3;
                spacing: 8px;
            }
            #alertCenterWidget QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #355069;
                background-color: #0c1520;
            }
            #alertCenterWidget QCheckBox::indicator:checked {
                background-color: #2f6bff;
                border-color: #2f6bff;
            }
            #alertCenterWidget QTextEdit,
            #alertCenterWidget QTableWidget {
                background-color: #101b28;
                alternate-background-color: #0c1520;
                border: 1px solid #223447;
                border-radius: 14px;
                color: #e5edf7;
                gridline-color: #233548;
                selection-background-color: rgba(47, 107, 255, 0.22);
                selection-color: #ffffff;
            }
            #alertCenterWidget QHeaderView::section {
                background-color: #122133;
                color: #dce7f3;
                padding: 10px 8px;
                border: none;
                border-bottom: 1px solid #2a4157;
                font-weight: 700;
            }
            #alertCenterWidget QScrollArea {
                border: none;
                background: transparent;
            }
            #alertCenterWidget QScrollBar:vertical,
            #alertCenterWidget QScrollBar:horizontal {
                background: #0c1520;
                border-radius: 6px;
                margin: 4px;
            }
            #alertCenterWidget QScrollBar:vertical {
                width: 12px;
            }
            #alertCenterWidget QScrollBar:horizontal {
                height: 12px;
            }
            #alertCenterWidget QScrollBar::handle:vertical,
            #alertCenterWidget QScrollBar::handle:horizontal {
                background: #47637d;
                border-radius: 6px;
                min-height: 28px;
                min-width: 28px;
            }
            #alertCenterWidget QScrollBar::add-line,
            #alertCenterWidget QScrollBar::sub-line,
            #alertCenterWidget QScrollBar::add-page,
            #alertCenterWidget QScrollBar::sub-page {
                background: transparent;
                border: none;
            }
            #alertCenterWidget QSplitter::handle {
                background-color: #243648;
                border-radius: 5px;
                margin: 2px;
            }
            #alertCenterWidget QSplitter::handle:horizontal {
                width: 12px;
            }
            #alertCenterWidget QSplitter::handle:vertical {
                height: 12px;
            }
            """
        )

    def create_control_summary_card(self) -> QtWidgets.QWidget:
        """创建左侧顶部控制摘要卡，集中承载配置、测试与运行状态。"""
        self.load_button = QtWidgets.QPushButton("加载配置")
        self.save_button = QtWidgets.QPushButton("保存配置")
        self.test_button = QtWidgets.QPushButton("单次测试")
        self.start_button = QtWidgets.QPushButton("启动提醒")
        self.stop_button = QtWidgets.QPushButton("停止提醒")
        self.mode_label = QtWidgets.QLabel("空闲")
        self.mode_label.setMinimumWidth(180)
        self.status_label = QtWidgets.QLabel("未启动")
        self.status_label.setMinimumWidth(220)

        for button, tone in (
            (self.load_button, "secondary"),
            (self.save_button, "secondary"),
            (self.test_button, "primary"),
            (self.start_button, "success"),
            (self.stop_button, "ghost"),
        ):
            button.setProperty("buttonTone", tone)
            button.setMinimumHeight(38)

        for label in (self.mode_label, self.status_label):
            label.setProperty("badgeRole", "pill")
            label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        self.set_badge_text(self.mode_label, "空闲", "neutral")
        self.set_badge_text(self.status_label, "未启动", "neutral")

        self.load_button.clicked.connect(self.load_config_from_engine)
        self.save_button.clicked.connect(self.save_form_config)
        self.test_button.clicked.connect(self.run_preview_once)
        self.start_button.clicked.connect(self.start_alerting)
        self.stop_button.clicked.connect(self.stop_alerting)

        card = QtWidgets.QFrame()
        card.setProperty("cardRole", "hero")
        card.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        eyebrow = QtWidgets.QLabel("CONTROL BAR")
        eyebrow.setProperty("textRole", "eyebrow")
        title = QtWidgets.QLabel("一屏完成配置、测试与监控")
        title.setProperty("textRole", "heroTitle")
        subtitle = QtWidgets.QLabel("保留完整配置、状态表和提醒记录，把实时监控工作流集中在当前页面。")
        subtitle.setProperty("textRole", "heroSubtitle")
        subtitle.setWordWrap(True)

        button_row = QtWidgets.QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)
        button_row.addWidget(self.load_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.test_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(12)
        status_row.addWidget(self.create_status_block("当前模式", self.mode_label))
        status_row.addWidget(self.create_status_block("整体状态", self.status_label))

        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addLayout(button_row)
        layout.addLayout(status_row)
        card.setLayout(layout)
        return card

    def create_status_block(self, title: str, badge_label: QtWidgets.QLabel) -> QtWidgets.QWidget:
        """创建控制卡里的状态说明块。"""
        block = QtWidgets.QFrame()
        block.setProperty("cardRole", "statusBlock")
        block.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("textRole", "metricTitle")
        layout.addWidget(title_label)
        layout.addWidget(badge_label)
        block.setLayout(layout)
        return block

    def create_summary_metrics_row(self) -> QtWidgets.QWidget:
        """创建摘要指标卡，帮助快速浏览当前监控概览。"""
        container = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        metric_specs = (
            ("active", "活动标的", "--", "success"),
            ("today", "今日提醒", "--", "primary"),
            ("risk", "风险信号", "--", "warning"),
            ("preview", "最近测试", "--:--", "primary"),
            ("source", "主数据源", "待获取", "neutral"),
        )
        for key, title, default_value, tone in metric_specs:
            layout.addWidget(self.create_metric_card(key, title, default_value, tone), 1)

        container.setLayout(layout)
        return container

    def create_metric_card(
        self,
        key: str,
        title: str,
        default_value: str,
        tone: str,
    ) -> QtWidgets.QWidget:
        """创建单个指标卡，并缓存值标签用于后续刷新。"""
        card = QtWidgets.QFrame()
        card.setProperty("cardRole", "metric")
        card.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("textRole", "metricTitle")
        value_label = QtWidgets.QLabel(default_value)
        value_label.setProperty("textRole", "metricValue")
        value_label.setProperty("metricTone", tone)
        value_label.setWordWrap(True)

        layout.addWidget(title_label)
        layout.addWidget(value_label)
        card.setLayout(layout)

        self.metric_value_labels[key] = value_label
        return card

    def create_global_group(self) -> QtWidgets.QWidget:
        """创建全局参数卡片。"""
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)

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

        body.setLayout(form)
        return self.create_card_panel("全局设置", "Global configuration", body)

    def create_symbol_group(self) -> QtWidgets.QWidget:
        """创建股票配置卡片。"""
        body = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(2, 1)

        headers = ("启用", "股票代码", "提醒策略", "参数1", "参数2", "参数3", "参数4")
        for column, title in enumerate(headers):
            label = QtWidgets.QLabel(title)
            label.setProperty("textRole", "gridHeader")
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
            row_widgets.enabled.toggled.connect(lambda _checked: self.refresh_summary_metrics())
            row_widgets.vt_symbol.textChanged.connect(lambda _text: self.refresh_summary_metrics())
            self.apply_strategy_to_row(row_widgets, BASIC_ALERT_STRATEGY)

        body.setLayout(grid)
        return self.create_card_panel("股票配置（最多 3 只）", "最多 3 只股票，支持不同策略与参数", body)

    def create_runtime_group(self) -> QtWidgets.QWidget:
        """创建运行信息卡片。"""
        body = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)

        self.config_path_label = QtWidgets.QLabel("")
        self.history_path_label = QtWidgets.QLabel("")
        self.thread_label = QtWidgets.QLabel("")
        self.thread_label.setProperty("badgeRole", "pill")

        for label in (self.config_path_label, self.history_path_label):
            label.setWordWrap(True)
            label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        form.addRow("配置文件", self.config_path_label)
        form.addRow("记录文件", self.history_path_label)
        form.addRow("线程状态", self.thread_label)
        body.setLayout(form)
        return self.create_card_panel("运行信息", "Runtime information", body)

    def create_main_splitter(self) -> QtWidgets.QSplitter:
        """创建只保留两列的主布局。"""
        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(self.create_control_summary_card())
        left_layout.addWidget(self.create_summary_metrics_row())
        left_layout.addWidget(self.create_global_group())
        left_layout.addWidget(self.create_symbol_group())
        left_layout.addWidget(self.create_runtime_group())
        left_layout.addWidget(
            self.create_card_panel("策略状态", "State board", self.create_state_table())
        )
        left_layout.addWidget(
            self.create_card_panel("提醒记录", "Recent alerts", self.create_record_table())
        )
        left_layout.addStretch(1)
        left_panel.setLayout(left_layout)

        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_panel)
        left_scroll.setMinimumWidth(560)

        right_panel = self.create_chart_log_splitter()
        right_panel.setMinimumWidth(440)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_panel)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)
        splitter.setStretchFactor(0, 57)
        splitter.setStretchFactor(1, 43)
        splitter.setSizes([960, 760])
        return splitter

    def create_chart_log_splitter(self) -> QtWidgets.QSplitter:
        """创建右侧仅包含“K线图 + 运行日志”的工作区。"""

        self.chart_widget = AlertChartWidget()
        self.expand_chart_button = QtWidgets.QPushButton("放大查看")
        self.expand_chart_button.setProperty("buttonTone", "ghost")
        self.expand_chart_button.setMinimumHeight(34)
        self.expand_chart_button.clicked.connect(self.open_chart_popup)
        chart_group = self.create_card_panel(
            "K 线图",
            "Theoretical markers and time axis",
            self.chart_widget,
            toolbar_widget=self.expand_chart_button,
        )

        self.log_edit = QtWidgets.QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMinimumHeight(180)
        self.log_edit.document().setMaximumBlockCount(800)
        self.log_edit.document().setDocumentMargin(8)
        log_group = self.create_card_panel(
            "运行日志",
            "Execution log",
            self.log_edit,
        )

        chart_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        chart_splitter.addWidget(chart_group)
        chart_splitter.addWidget(log_group)
        chart_splitter.setChildrenCollapsible(False)
        chart_splitter.setStretchFactor(0, 7)
        chart_splitter.setStretchFactor(1, 3)
        chart_splitter.setSizes([540, 220])
        return chart_splitter

    def create_card_panel(
        self,
        title: str,
        subtitle: str,
        widget: QtWidgets.QWidget,
        toolbar_widget: QtWidgets.QWidget | None = None,
    ) -> QtWidgets.QWidget:
        """创建统一的卡片容器，避免只剩原生控件堆叠。"""
        card = QtWidgets.QFrame()
        card.setProperty("cardRole", "panel")
        card.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header_layout = QtWidgets.QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        header_text_layout = QtWidgets.QVBoxLayout()
        header_text_layout.setContentsMargins(0, 0, 0, 0)
        header_text_layout.setSpacing(2)
        title_label = QtWidgets.QLabel(title)
        title_label.setProperty("textRole", "cardTitle")
        subtitle_label = QtWidgets.QLabel(subtitle)
        subtitle_label.setProperty("textRole", "cardSubtitle")
        subtitle_label.setWordWrap(True)
        header_text_layout.addWidget(title_label)
        header_text_layout.addWidget(subtitle_label)

        header_layout.addLayout(header_text_layout)
        header_layout.addStretch(1)
        if toolbar_widget is not None:
            header_layout.addWidget(toolbar_widget, alignment=QtCore.Qt.AlignmentFlag.AlignTop)

        layout.addLayout(header_layout)
        layout.addWidget(widget)
        card.setLayout(layout)
        return card

    def create_state_table(self) -> QtWidgets.QTableWidget:
        """创建状态面板表格。"""
        self.state_table = QtWidgets.QTableWidget()
        self.state_table.setColumnCount(len(self.STATE_HEADERS))
        self.state_table.setHorizontalHeaderLabels(self.STATE_HEADERS)
        self.state_table.verticalHeader().setVisible(False)
        self.state_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.state_table.setAlternatingRowColors(True)
        self.state_table.setMinimumHeight(230)
        self.state_table.verticalHeader().setDefaultSectionSize(38)
        self.state_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.state_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.state_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.state_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        self.record_table.setMinimumHeight(280)
        self.record_table.verticalHeader().setDefaultSectionSize(38)
        self.record_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.record_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.record_table.setHorizontalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.record_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
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
        self.refresh_summary_metrics()

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
        self.refresh_summary_metrics()

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
        self.refresh_summary_metrics()

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
        self.refresh_summary_metrics()

    def stop_alerting(self) -> None:
        """停止提醒线程。"""
        self.set_mode_label("neutral", "停止中")
        self.set_badge_text(self.status_label, "停止中", "warning")
        self.append_session_marker("实时提醒停止请求", "neutral")
        self.alert_engine.stop_alerting()
        self.refresh_runtime_info()
        self.refresh_summary_metrics()

    def load_recent_records(self) -> None:
        """读取最近记录，刷新表格。"""
        self.record_table.setRowCount(0)
        self.recent_records_cache = self.alert_engine.get_recent_records(limit=120)
        for record in self.recent_records_cache[:80]:
            self.insert_record_row(record, to_top=False)
        self.refresh_summary_metrics()

    def populate_state_placeholders(self, config: AppConfig) -> None:
        """按当前配置重建状态表格的股票行。"""
        self.state_table.setRowCount(0)
        self.state_rows.clear()
        self.state_cache.clear()

        for row, symbol in enumerate(config.symbol_configs[:MAX_SYMBOL_COUNT]):
            self.state_table.insertRow(row)
            self.state_rows[symbol.vt_symbol] = row
            default_state = build_default_state(symbol)
            self.state_cache[symbol.vt_symbol] = default_state
            self.update_state_row(default_state)
        self.refresh_summary_metrics()

    def process_log_event(self, event: Event) -> None:
        """处理日志事件。"""
        data = event.data
        self.append_log(data.level, data.source, data.message, data.timestamp)

    def process_status_event(self, event: Event) -> None:
        """处理整体状态事件。"""
        data: RunnerStatusData = event.data
        self.latest_runner_status = data
        if not data.running and data.paused:
            self.current_log_mode = "preview"
            self.set_mode_label("preview", data.message)
            self.set_badge_text(self.status_label, data.message, "preview")
        elif data.running and data.paused:
            self.set_mode_label("neutral", data.message)
            self.set_badge_text(self.status_label, data.message, "warning")
        elif data.running:
            self.current_log_mode = "live"
            self.set_mode_label("live", "实时运行")
            self.set_badge_text(self.status_label, data.message, "live")
        else:
            next_mode = "preview" if "测试完成" in data.message else "neutral"
            self.set_mode_label(next_mode, data.message if next_mode == "preview" else "空闲")
            tone = "preview" if next_mode == "preview" else "neutral"
            status_text = data.message if next_mode == "preview" else "未启动"
            self.set_badge_text(self.status_label, status_text, tone)

        self.start_button.setEnabled(not data.running)
        self.stop_button.setEnabled(data.running)
        self.refresh_runtime_info()
        self.refresh_summary_metrics()

    def process_record_event(self, event: Event) -> None:
        """处理触发记录事件。"""
        data: RecordData = event.data
        self.recent_records_cache.insert(0, data)
        self.recent_records_cache = self.recent_records_cache[:120]
        self.insert_record_row(data, to_top=True)
        self.refresh_summary_metrics()

    def process_state_event(self, event: Event) -> None:
        """处理单只股票状态事件。"""
        data: SymbolStateData = event.data
        self.state_cache[data.vt_symbol] = data
        self.update_state_row(data)
        self.refresh_summary_metrics()

    def process_chart_event(self, event: Event) -> None:
        """处理右侧 K 线图快照事件。"""
        data: ChartSnapshotData = event.data
        primary_symbol = self.get_primary_enabled_symbol(self.current_config)
        if primary_symbol and data.vt_symbol != primary_symbol.vt_symbol:
            return
        self.latest_chart_snapshot = data
        if data.mode == "preview":
            self.latest_preview_time = data.reference_time.astimezone(CHINA_TZ)
        self.chart_widget.set_snapshot(data)
        if self.chart_popup is not None:
            self.chart_popup.set_snapshot(data)
        self.refresh_summary_metrics()

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
            '<div style="margin:0 0 6px 0; padding:5px 8px; '
            'background:#0c1520; border:1px solid #213345; border-radius:10px;">'
            f'<span style="display:inline-block; margin-right:8px; padding:1px 8px; '
            f'border-radius:9px; border:1px solid {color}; color:{color}; font-weight:700;">'
            f'{html.escape(badge_text)}</span>'
            f'<span style="color:#8ea2b8; margin-right:8px;">{html.escape(log_time[-8:])}</span>'
            f'<span style="color:#9fb3c8; margin-right:8px;">{html.escape(source)}</span>'
            f'<span style="color:#e5edf7;">{html.escape(message)}</span>'
            "</div>"
        )
        self.log_edit.append(line)

    def append_session_marker(self, title: str, mode: str) -> None:
        """在日志区插入明显的会话分隔线，区分测试和真实运行。"""
        badge_text, color = self.get_log_badge(mode, "INFO", title)
        marker = (
            '<div style="margin:10px 0 8px 0; padding:6px 10px; '
            'background:#101b28; border:1px solid #233548; border-radius:11px;">'
            f'<span style="color:{color}; font-weight:700;">{html.escape(badge_text)}</span>'
            f'<span style="color:#dce7f3; font-weight:700;"> · {html.escape(title)}</span>'
            "</div>"
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
        """刷新控制卡里的当前模式徽标。"""
        label_text = text.strip() or "空闲"
        if mode == "preview":
            tone = "preview"
        elif mode == "live":
            tone = "live"
        else:
            tone = "neutral"
        self.set_badge_text(self.mode_label, label_text, tone)

    def refresh_runtime_info(self) -> None:
        """刷新配置文件、记录文件和线程状态显示。"""
        runtime = self.alert_engine.get_runtime_status()
        self.config_path_label.setText(str(Path(runtime["config_path"])))
        self.history_path_label.setText(str(Path(runtime["history_path"])))
        running = bool(runtime["running"])
        self.set_badge_text(self.thread_label, "线程运行中" if running else "未运行", "live" if running else "neutral")
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
        self.latest_chart_snapshot = None
        primary_symbol = self.get_primary_enabled_symbol(config)
        if primary_symbol is None:
            message = "暂无图表数据，请先启用至少一只股票"
            self.chart_widget.clear_snapshot(message)
            if self.chart_popup is not None:
                self.chart_popup.clear_snapshot(message)
            self.refresh_summary_metrics()
            return
        message = f"等待 {primary_symbol.vt_symbol} 图表数据，请先执行单次测试或启动提醒"
        self.chart_widget.clear_snapshot(message)
        if self.chart_popup is not None:
            self.chart_popup.clear_snapshot(message)
        self.refresh_summary_metrics()

    def open_chart_popup(self) -> None:
        """打开或复用放大查看弹窗，并同步最新图表快照。"""
        if self.chart_popup is None:
            self.chart_popup = AlertChartPopupWindow(self)

        if self.latest_chart_snapshot is not None:
            self.chart_popup.set_snapshot(self.latest_chart_snapshot)
        else:
            primary_symbol = self.get_primary_enabled_symbol(self.current_config)
            if primary_symbol is None:
                message = "暂无图表数据，请先启用至少一只股票"
            else:
                message = f"等待 {primary_symbol.vt_symbol} 图表数据，请先执行单次测试或启动提醒"
            self.chart_popup.clear_snapshot(message)

        self.chart_popup.show_and_activate()

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

    def set_badge_text(self, label: QtWidgets.QLabel, text: str, tone: str) -> None:
        """统一刷新胶囊标签的文本和颜色语义。"""
        label.setText(text.strip() or "空闲")
        label.setProperty("badgeTone", tone)
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def refresh_summary_metrics(self) -> None:
        """根据当前缓存和表单状态刷新左侧摘要指标。"""
        if not self.metric_value_labels:
            return

        active_count = sum(
            1
            for row_widgets in self.row_widgets
            if row_widgets.enabled.isChecked() and row_widgets.vt_symbol.text().strip()
        )
        today = datetime.now(CHINA_TZ).date()
        today_records = [
            record
            for record in self.recent_records_cache
            if (parsed := self.parse_record_datetime(record.occurred_at)) and parsed.date() == today
        ]
        risk_count = sum(1 for record in today_records if record.level == "风控型")
        preview_text = self.latest_preview_time.strftime("%H:%M") if self.latest_preview_time else "--:--"

        primary_symbol = self.get_primary_enabled_symbol(self.current_config)
        data_source = "待获取"
        if primary_symbol:
            state = self.state_cache.get(primary_symbol.vt_symbol)
            if state and state.data_source not in {"", "-", "待获取"}:
                data_source = state.data_source
        if data_source == "待获取" and self.latest_chart_snapshot and self.latest_chart_snapshot.data_source:
            data_source = self.latest_chart_snapshot.data_source

        self.metric_value_labels["active"].setText(str(active_count))
        self.metric_value_labels["today"].setText(str(len(today_records)))
        self.metric_value_labels["risk"].setText(str(risk_count))
        self.metric_value_labels["preview"].setText(preview_text)
        self.metric_value_labels["source"].setText(data_source)

    def parse_record_datetime(self, value: str) -> datetime | None:
        """尽量解析提醒记录里的时间字符串，便于统计当日提醒数量。"""
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(value, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=CHINA_TZ)
        return parsed.astimezone(CHINA_TZ)

    def show_warning(self, message: str) -> None:
        """统一显示输入或配置错误。"""
        QtWidgets.QMessageBox.warning(self, "提醒配置", message)
