"""CTA 实时监控 UI 包入口。"""

from __future__ import annotations

from typing import Any

__all__ = ["AlertCenterWidget"]


def __getattr__(name: str) -> Any:
    """懒导出主窗口，避免导入 chart_view 时提前拉起 Qt 依赖。"""
    if name == "AlertCenterWidget":
        from .widget import AlertCenterWidget

        return AlertCenterWidget
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
