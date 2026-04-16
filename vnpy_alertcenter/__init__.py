"""CTA 实时监控包入口。"""

from __future__ import annotations

from typing import Any

__all__ = ["AlertCenterApp"]


def __getattr__(name: str) -> Any:
    """对外兼容 AlertCenterApp，但避免导入包时立即加载 GUI 依赖。"""
    if name == "AlertCenterApp":
        from .app import AlertCenterApp

        return AlertCenterApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
