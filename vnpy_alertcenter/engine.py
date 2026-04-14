"""实时提醒 BaseApp 的引擎层。"""

from __future__ import annotations

from threading import Event as ThreadEvent
from threading import Thread

from vnpy.event import Event
from vnpy.trader.engine import BaseEngine, MainEngine

from .core import (
    AlertCenterRunner,
    AppConfig,
    DEFAULT_CONFIG_PATH,
    LogData,
    RecordData,
    RunnerStatusData,
    SymbolStateData,
    load_app_config,
    make_log,
    read_recent_records,
    save_app_config,
    send_desktop_notification,
)


APP_NAME = "AlertCenter"
EVENT_ALERTCENTER_LOG = "eAlertCenterLog"
EVENT_ALERTCENTER_STATUS = "eAlertCenterStatus"
EVENT_ALERTCENTER_RECORD = "eAlertCenterRecord"
EVENT_ALERTCENTER_STATE = "eAlertCenterState"


class AlertCenterEngine(BaseEngine):
    """负责配置加载、后台轮询和 GUI 事件分发。"""

    def __init__(self, main_engine: MainEngine, event_engine) -> None:
        super().__init__(main_engine, event_engine, APP_NAME)
        self.config_path = DEFAULT_CONFIG_PATH
        self.current_config: AppConfig = load_app_config(self.config_path)
        self._thread: Thread | None = None
        self._stop_event: ThreadEvent | None = None
        self._runner: AlertCenterRunner | None = None

    def load_config(self) -> AppConfig:
        """读取最新配置并缓存。"""
        self.current_config = load_app_config(self.config_path)
        return self.current_config

    def save_config(self, config: AppConfig) -> None:
        """把当前配置写回 JSON。"""
        save_app_config(config, self.config_path)
        self.current_config = config
        self.write_log("配置已保存。")

    def start_alerting(self, config: AppConfig) -> None:
        """启动后台提醒线程。"""
        if self.is_running():
            self.write_log("提醒已在运行中，本次启动请求已忽略。")
            return

        self.current_config = config
        self._stop_event = ThreadEvent()
        self._runner = AlertCenterRunner(
            config=config,
            log_callback=self.process_log,
            status_callback=self.process_status,
            record_callback=self.process_record,
            state_callback=self.process_state,
        )
        self._thread = Thread(target=self._run_loop, name="AlertCenterRunner", daemon=True)
        self._thread.start()

    def stop_alerting(self) -> None:
        """停止后台提醒线程。"""
        if not self.is_running():
            self.process_status(RunnerStatusData(False, False, "未运行", ""))
            return

        if self._stop_event:
            self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=5)

        self._thread = None
        self._stop_event = None
        self._runner = None
        self.write_log("已请求停止提醒线程。")

    def is_running(self) -> bool:
        """判断后台线程是否仍在运行。"""
        return bool(self._thread and self._thread.is_alive())

    def get_recent_records(self, limit: int = 100) -> list[RecordData]:
        """读取最近触发记录，供 GUI 初始展示。"""
        return read_recent_records(self.current_config.alert_history_path, limit)

    def get_runtime_status(self) -> dict:
        """为 GUI 提供简单的运行状态读取接口。"""
        return {
            "running": self.is_running(),
            "config_path": str(self.config_path),
            "history_path": str(self.current_config.alert_history_path),
        }

    def close(self) -> None:
        """主程序退出时安全关闭后台线程。"""
        self.stop_alerting()

    def _run_loop(self) -> None:
        """后台线程入口。"""
        if not self._runner or not self._stop_event:
            return

        try:
            self._runner.run_forever(self._stop_event)
        except Exception as exc:
            self.write_log(f"提醒线程异常退出：{exc}", source="Runner", level="ERROR")
            self.process_status(RunnerStatusData(False, False, f"异常退出：{exc}", ""))

    def process_log(self, data: LogData) -> None:
        """把日志事件发给 GUI。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_LOG, data))

    def process_status(self, data: RunnerStatusData) -> None:
        """把整体状态事件发给 GUI。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_STATUS, data))

    def process_state(self, data: SymbolStateData) -> None:
        """把单只股票状态事件发给 GUI。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_STATE, data))

    def process_record(self, data: RecordData) -> None:
        """把触发记录事件发给 GUI，并按配置发桌面通知。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_RECORD, data))

        if self.current_config.notification_enabled:
            error = send_desktop_notification("vn.py 实时提醒", data.message)
            if error and "当前系统不是 macOS" not in error:
                self.write_log(f"桌面通知失败：{error}", source="Notifier", level="ERROR")

    def write_log(self, message: str, source: str = "Engine", level: str = "INFO") -> None:
        """对外提供简单日志接口。"""
        self.process_log(make_log(level, source, message))
