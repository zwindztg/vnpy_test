#!/usr/bin/env python3
"""使用共享提醒内核运行独立版实时提醒。"""

from __future__ import annotations

from threading import Event as ThreadEvent

from vnpy_alertcenter.core import (
    AlertCenterRunner,
    DEFAULT_CONFIG_PATH,
    LogData,
    RecordData,
    RunnerStatusData,
    SymbolStateData,
    disable_process_proxy_env,
    install_requests_no_proxy,
    load_app_config,
)


class ConsoleAlertApp:
    """把 GUI 版提醒核心桥接成终端输出。"""

    def __init__(self) -> None:
        self.config = load_app_config(DEFAULT_CONFIG_PATH)
        self.runner = AlertCenterRunner(
            config=self.config,
            log_callback=self.on_log,
            status_callback=self.on_status,
            record_callback=self.on_record,
            state_callback=self.on_state,
        )
        self.stop_event = ThreadEvent()
        self.last_status_message: str = ""

    def on_log(self, data: LogData) -> None:
        """打印结构化日志。"""
        print(f"[{data.timestamp}] [{data.level}] [{data.source}] {data.message}", flush=True)

    def on_status(self, data: RunnerStatusData) -> None:
        """仅在整体状态变化时输出一条终端提示。"""
        if data.message == self.last_status_message:
            return
        self.last_status_message = data.message
        print(
            f"[{data.updated_at}] [STATUS] running={data.running} paused={data.paused} {data.message}",
            flush=True,
        )

    def on_record(self, data: RecordData) -> None:
        """规则触发已由日志输出覆盖，这里保留空实现供后续扩展。"""
        return

    def on_state(self, data: SymbolStateData) -> None:
        """独立脚本默认不持续刷状态，避免终端噪音。"""
        return

    def run(self) -> int:
        """以前台方式运行提醒，直到用户手动中断。"""
        try:
            self.runner.run_forever(self.stop_event)
        except KeyboardInterrupt:
            self.stop_event.set()
            print("收到手动中断，提醒脚本已停止。", flush=True)
        return 0


def main() -> int:
    cleared_proxy_keys = disable_process_proxy_env()
    install_requests_no_proxy()
    if cleared_proxy_keys:
        print("提醒脚本已自动绕过代理：", ", ".join(cleared_proxy_keys), flush=True)

    app = ConsoleAlertApp()
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main())
