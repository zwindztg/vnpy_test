from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


class AlertCenterImportSafetyTest(unittest.TestCase):
    """验证包入口已从 GUI 依赖里解耦。"""

    def run_python_probe(self, code: str) -> subprocess.CompletedProcess:
        """在独立进程里执行一段短脚本。"""
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
        )

    def run_import_probe(self, module_name: str, banned_modules: list[str]) -> None:
        """在独立进程中导入模块，并确认不会顺带加载重依赖。"""
        result = self.run_python_probe(
            f"""
            import importlib
            import sys

            importlib.import_module({module_name!r})
            banned = {banned_modules!r}
            loaded = [name for name in banned if name in sys.modules]
            if loaded:
                raise SystemExit("unexpected imports: " + ", ".join(loaded))
            """
        )
        self.assertEqual(0, result.returncode, msg=result.stderr or result.stdout)

    def test_import_core_does_not_pull_ctabacktester_or_qt(self) -> None:
        self.run_import_probe(
            "vnpy_alertcenter.core",
            ["vnpy_ctabacktester", "vnpy.trader.ui"],
        )

    def test_import_core_does_not_monkey_patch_requests_session(self) -> None:
        result = self.run_python_probe(
            """
            import importlib
            import requests.sessions

            original = requests.sessions.Session.merge_environment_settings
            importlib.import_module("vnpy_alertcenter.core")
            current = requests.sessions.Session.merge_environment_settings

            if current is not original:
                raise SystemExit("requests session unexpectedly patched at import time")
            """
        )
        self.assertEqual(0, result.returncode, msg=result.stderr or result.stdout)

    def test_import_chart_view_does_not_pull_widget_or_qt(self) -> None:
        self.run_import_probe(
            "vnpy_alertcenter.ui.chart_view",
            ["vnpy_ctabacktester", "vnpy.trader.ui", "vnpy_alertcenter.ui.widget"],
        )

    def test_alertcenter_app_module_still_points_ui_import_to_package_root(self) -> None:
        result = self.run_python_probe(
            """
            import importlib
            from vnpy_alertcenter.app import AlertCenterApp

            if AlertCenterApp.app_module != "vnpy_alertcenter":
                raise SystemExit(f"unexpected app_module: {AlertCenterApp.app_module}")

            importlib.import_module(AlertCenterApp.app_module + ".ui")
            """
        )
        self.assertEqual(0, result.returncode, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
