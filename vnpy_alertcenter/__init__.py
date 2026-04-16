"""vn.py CTA 实时监控功能模块。"""

from __future__ import annotations

from pathlib import Path

from vnpy.trader.app import BaseApp
from vnpy_ctabacktester import CtaBacktesterApp

from .engine import APP_NAME, AlertCenterEngine


class AlertCenterApp(BaseApp):
    """把 CTA 实时监控能力挂进 vn.py 的功能菜单。"""

    app_name: str = APP_NAME
    app_module: str = __module__
    app_path: Path = Path(__file__).parent
    display_name: str = "CTA 实时监控"
    engine_class: type[AlertCenterEngine] = AlertCenterEngine
    widget_name: str = "AlertCenterWidget"
    icon_name: str = CtaBacktesterApp.icon_name
