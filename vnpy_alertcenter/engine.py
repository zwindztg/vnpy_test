"""CTA 实时监控 BaseApp 的引擎层。"""

from __future__ import annotations

from threading import Event as ThreadEvent
from threading import Lock
from threading import Thread
from threading import current_thread

from vnpy.event import Event
from vnpy.trader.engine import BaseEngine, MainEngine

from .core import (
    AlertCenterRunner,
    AppConfig,
    ChartSnapshotData,
    DEFAULT_CONFIG_PATH,
    LogData,
    RecordData,
    RunnerStatusData,
    SOURCE_CTA_PUBLISHED,
    SymbolConfig,
    SymbolStateData,
    find_enabled_symbol_conflicts,
    load_app_config,
    make_log,
    make_runner_status,
    publish_symbol_config,
    read_recent_records,
    save_app_config,
    send_desktop_notification,
)


APP_NAME = "AlertCenter"
EVENT_ALERTCENTER_LOG = "eAlertCenterLog"
EVENT_ALERTCENTER_STATUS = "eAlertCenterStatus"
EVENT_ALERTCENTER_RECORD = "eAlertCenterRecord"
EVENT_ALERTCENTER_STATE = "eAlertCenterState"
EVENT_ALERTCENTER_CHART = "eAlertCenterChart"
EVENT_ALERTCENTER_CONFIG = "eAlertCenterConfig"


class AlertCenterEngine(BaseEngine):
    """负责配置加载、后台轮询和 GUI 事件分发。"""

    def __init__(self, main_engine: MainEngine, event_engine) -> None:
        super().__init__(main_engine, event_engine, APP_NAME)
        self.config_path = DEFAULT_CONFIG_PATH
        self.current_config: AppConfig = load_app_config(self.config_path)
        self._thread: Thread | None = None
        self._stop_event: ThreadEvent | None = None
        self._runner: AlertCenterRunner | None = None
        self._thread_lock = Lock()

    def load_config(self) -> AppConfig:
        """读取最新配置并缓存。"""
        self.current_config = load_app_config(self.config_path)
        return self.current_config

    def save_config(self, config: AppConfig, message: str = "配置已保存。") -> None:
        """把当前配置写回 JSON，并通知已打开的监控窗口刷新。"""
        save_app_config(config, self.config_path)
        self.current_config = config
        self.process_config(config)
        if message:
            self.write_log(message)

    def publish_from_backtest(
        self,
        *,
        vt_symbol: str,
        interval: str,
        strategy_name: str,
        params: dict,
        target_index: int,
        summary_text: str = "",
    ) -> AppConfig:
        """接收 CTA 回测发布的一条策略配置，并写入监控中心配置。"""
        if self.is_running():
            raise RuntimeError("当前 CTA 实时监控正在运行，请先停止监控后再接收 CTA 回测发布。")

        current = self.load_config()
        published_config = publish_symbol_config(
            current,
            SymbolConfig(
                vt_symbol=vt_symbol,
                strategy_name=strategy_name,
                params=params,
                enabled=True,
                source_state=SOURCE_CTA_PUBLISHED,
            ),
            interval=interval,
            target_index=target_index,
        )
        summary_suffix = f"；回测摘要：{summary_text}" if summary_text else ""
        self.save_config(
            published_config,
            message=(
                f"已接收 CTA 回测发布：{vt_symbol} / {strategy_name} / {interval}"
                f"{summary_suffix}"
            ),
        )
        return published_config

    def start_alerting(self, config: AppConfig) -> None:
        """启动后台提醒线程。"""
        self._raise_if_duplicate_enabled_symbols(config)
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                self.write_log("提醒已在运行中，本次启动请求已忽略。")
                return

            self.current_config = config
            stop_event = ThreadEvent()
            runner = AlertCenterRunner(
                config=config,
                log_callback=self.process_log,
                status_callback=self.process_status,
                record_callback=self.process_record,
                state_callback=self.process_state,
                chart_callback=self.process_chart,
            )
            thread = Thread(
                target=self._run_loop,
                args=(runner, stop_event),
                name="AlertCenterRunner",
                daemon=True,
            )

            self._stop_event = stop_event
            self._runner = runner
            self._thread = thread

        thread.start()

    def run_preview_once(self, config: AppConfig, reference_time) -> None:
        """按指定时间执行单次历史回放测试。"""
        if self.is_running():
            raise RuntimeError("请先停止当前实时监控，再执行单次测试。")
        self._raise_if_duplicate_enabled_symbols(config)

        self.current_config = config
        runner = AlertCenterRunner(
            config=config,
            log_callback=self.process_log,
            status_callback=self.process_status,
            record_callback=self.process_record,
            state_callback=self.process_state,
            chart_callback=self.process_chart,
        )
        runner.run_preview_once(reference_time)

    def stop_alerting(self) -> None:
        """停止后台提醒线程。"""
        with self._thread_lock:
            thread = self._thread
            stop_event = self._stop_event

        if not thread or not thread.is_alive():
            self._clear_worker(thread)
            self.process_status(make_runner_status(False, False, "未运行"))
            return

        if stop_event:
            stop_event.set()

        thread.join(timeout=5)
        if thread.is_alive():
            self.write_log("停止请求已发送，正在等待当前轮询结束。")
            self.process_status(make_runner_status(True, False, "停止中，等待当前请求结束"))
            return

        self._clear_worker(thread)
        self.write_log("监控线程已停止。")

    def is_running(self) -> bool:
        """判断后台线程是否仍在运行。"""
        with self._thread_lock:
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

    def _run_loop(self, runner: AlertCenterRunner, stop_event: ThreadEvent) -> None:
        """后台线程入口。"""
        try:
            runner.run_forever(stop_event)
        except Exception as exc:
            self.write_log(f"监控线程异常退出：{exc}", source="Runner", level="ERROR")
            self.process_status(make_runner_status(False, False, f"异常退出：{exc}"))
        finally:
            self._clear_worker(current_thread())

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
            error = send_desktop_notification("vn.py CTA 实时监控", data.message)
            if error and "当前系统不是 macOS" not in error:
                self.write_log(f"桌面通知失败：{error}", source="Notifier", level="ERROR")

    def process_chart(self, data: ChartSnapshotData) -> None:
        """把图表快照事件发给 GUI。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_CHART, data))

    def process_config(self, config: AppConfig) -> None:
        """把配置刷新事件发给 GUI，方便已打开窗口同步最新配置。"""
        self.event_engine.put(Event(EVENT_ALERTCENTER_CONFIG, config))

    def write_log(self, message: str, source: str = "Engine", level: str = "INFO") -> None:
        """对外提供简单日志接口。"""
        self.process_log(make_log(level, source, message))

    def _clear_worker(self, thread: Thread | None = None) -> None:
        """仅在句柄仍然匹配当前工作线程时清理后台状态。"""
        with self._thread_lock:
            if thread is not None and self._thread is not thread:
                return

            self._thread = None
            self._stop_event = None
            self._runner = None

    def _raise_if_duplicate_enabled_symbols(self, config: AppConfig) -> None:
        """引擎层也兜底拦截同股票多条启用，避免绕过 GUI 后直接跑出歧义状态。"""
        conflicts = find_enabled_symbol_conflicts(config)
        if not conflicts:
            return

        conflict_text = "；".join(
            f"{vt_symbol} 位于第 {','.join(str(row) for row in rows)} 行"
            for vt_symbol, rows in conflicts.items()
        )
        raise RuntimeError(
            "CTA 实时监控中心暂不支持同一股票同时运行多条策略，请先处理重复启用项："
            f"{conflict_text}"
        )
