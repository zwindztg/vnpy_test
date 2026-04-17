from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.akshare_realtime_alert import ConsoleAlertApp
from vnpy_alertcenter.core import AppConfig


class ConsoleAlertAppTest(unittest.TestCase):
    """验证独立提醒脚本能跟上共享提醒内核的接口变化。"""

    def test_console_alert_app_can_initialize_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = AppConfig(
                interval="1m",
                poll_seconds=20,
                adjust="qfq",
                cooldown_seconds=300,
                alert_history_path=Path(temp_dir) / "alerts.csv",
                notification_enabled=False,
                symbol_configs=(),
            )

            with patch("scripts.akshare_realtime_alert.load_app_config", return_value=config):
                app = ConsoleAlertApp()

        self.assertIs(app.config, config)
        self.assertIsNotNone(app.runner)
        self.assertFalse(app.stop_event.is_set())
        self.assertEqual("", app.last_status_message)
