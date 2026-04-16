"""CTA 实时监控应用注册入口。"""

from __future__ import annotations

from pathlib import Path

from vnpy.trader.app import BaseApp
from vnpy_ctabacktester import CtaBacktesterApp

from .engine import APP_NAME, AlertCenterEngine


class AlertCenterApp(BaseApp):
    """把 CTA 实时监控能力挂进 vn.py 的功能菜单。"""

    app_name: str = APP_NAME
    # vn.py 会按 app_module + ".ui" 动态导入界面层，这里必须指向包根。
    app_module: str = __package__ or "vnpy_alertcenter"
    app_path: Path = Path(__file__).parent
    display_name: str = "CTA 实时监控"
    engine_class: type[AlertCenterEngine] = AlertCenterEngine
    widget_name: str = "AlertCenterWidget"
    icon_name: str = CtaBacktesterApp.icon_name
