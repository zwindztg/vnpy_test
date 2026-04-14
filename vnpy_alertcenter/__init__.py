"""vn.py 实时提醒功能模块。"""

from __future__ import annotations

from pathlib import Path

from vnpy.trader.app import BaseApp
from vnpy_ctabacktester import CtaBacktesterApp

from .engine import APP_NAME, AlertCenterEngine


class AlertCenterApp(BaseApp):
    """把 AKShare 提醒能力挂进 vn.py 的功能菜单。"""

    app_name: str = APP_NAME
    app_module: str = __module__
    app_path: Path = Path(__file__).parent
    display_name: str = "实时提醒"
    engine_class: type[AlertCenterEngine] = AlertCenterEngine
    widget_name: str = "AlertCenterWidget"
    icon_name: str = CtaBacktesterApp.icon_name

