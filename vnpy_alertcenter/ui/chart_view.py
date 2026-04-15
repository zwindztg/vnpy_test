"""K 线图视图状态的纯逻辑辅助函数。"""

from __future__ import annotations

from ..core import ChartBarData, ChartMarkerData, ChartSnapshotData


def get_available_bars(
    snapshot: ChartSnapshotData | None,
    intraday_only: bool,
) -> list[ChartBarData]:
    """返回当前图表允许浏览的完整 bar 集合。"""
    if snapshot is None:
        return []

    bars = list(snapshot.bars)
    if not bars:
        return []

    if not intraday_only or snapshot.interval == "d":
        return bars

    latest_trade_date = bars[-1].dt.date()
    return [bar for bar in bars if bar.dt.date() == latest_trade_date]


def get_available_markers(
    snapshot: ChartSnapshotData | None,
    intraday_only: bool,
) -> list[ChartMarkerData]:
    """返回当前图表允许浏览的完整标记集合。"""
    if snapshot is None:
        return []

    allowed_dt = {bar.dt for bar in get_available_bars(snapshot, intraday_only)}
    if not allowed_dt:
        return []
    return [marker for marker in snapshot.markers if marker.dt in allowed_dt]


def get_view_key(
    snapshot: ChartSnapshotData | None,
    intraday_only: bool,
) -> tuple[str, str, object] | None:
    """生成判断是否需要重置视图的关键字。"""
    if snapshot is None or not snapshot.bars:
        return None

    if intraday_only and snapshot.interval != "d":
        view_date = snapshot.bars[-1].dt.date()
    else:
        view_date = snapshot.reference_time.date()
    return snapshot.vt_symbol, snapshot.interval, view_date


def can_zoom(
    interactive: bool,
    snapshot: ChartSnapshotData | None,
    total_bars: int,
) -> bool:
    """判断当前是否允许缩放。"""
    return (
        interactive
        and snapshot is not None
        and snapshot.interval in {"1m", "5m", "15m", "30m"}
        and total_bars > 1
    )


def get_right_offset(total: int, visible_start: int, visible_count: int) -> int:
    """记录当前窗口距离最右侧的偏移，方便快照刷新后尽量保持位置。"""
    if total <= 0 or visible_count <= 0:
        return 0
    return max(0, total - (visible_start + visible_count))


def sync_view_state(
    *,
    total: int,
    visible_start: int,
    visible_count: int,
    reset: bool,
    zoom_enabled: bool,
    min_visible_bars: int,
    right_offset: int | None = None,
) -> tuple[int, int]:
    """根据最新快照和当前交互状态归一化可见区间。"""
    if total <= 0:
        return 0, 0

    if reset or visible_count <= 0 or not zoom_enabled:
        return 0, total

    min_count = min(min_visible_bars, total)
    normalized_count = min(max(visible_count, min_count), total)
    max_start = max(0, total - normalized_count)
    if right_offset is None:
        normalized_start = min(max(visible_start, 0), max_start)
        return normalized_start, normalized_count

    normalized_start = min(max(total - normalized_count - right_offset, 0), max_start)
    return normalized_start, normalized_count


def can_zoom_in(zoom_enabled: bool, visible_count: int, total: int, min_visible_bars: int) -> bool:
    """判断当前是否还能继续放大。"""
    if not zoom_enabled:
        return False
    min_count = min(min_visible_bars, total)
    return visible_count > min_count


def can_zoom_out(zoom_enabled: bool, visible_count: int, total: int) -> bool:
    """判断当前是否还能继续缩小。"""
    return zoom_enabled and visible_count < total


def can_pan_left(zoom_enabled: bool, visible_start: int) -> bool:
    """判断当前是否还能向左平移。"""
    return zoom_enabled and visible_start > 0


def can_pan_right(zoom_enabled: bool, visible_start: int, visible_count: int, total: int) -> bool:
    """判断当前是否还能向右平移。"""
    return zoom_enabled and visible_start + visible_count < total


def apply_zoom_in(
    visible_start: int,
    visible_count: int,
    total: int,
    min_visible_bars: int,
) -> tuple[int, int]:
    """缩小可见 K 线数量，并保持右侧对齐。"""
    step = max(1, visible_count // 5)
    min_count = min(min_visible_bars, total)
    new_count = max(min_count, visible_count - step)
    if new_count >= visible_count:
        return visible_start, visible_count

    right_offset = get_right_offset(total, visible_start, visible_count)
    return sync_view_state(
        total=total,
        visible_start=visible_start,
        visible_count=new_count,
        reset=False,
        zoom_enabled=True,
        min_visible_bars=min_visible_bars,
        right_offset=right_offset,
    )


def apply_zoom_out(
    visible_start: int,
    visible_count: int,
    total: int,
    min_visible_bars: int,
) -> tuple[int, int]:
    """扩大可见 K 线数量，并尽量保留当前右侧偏移。"""
    step = max(1, visible_count // 4)
    new_count = min(total, visible_count + step)
    if new_count <= visible_count:
        return visible_start, visible_count

    right_offset = get_right_offset(total, visible_start, visible_count)
    return sync_view_state(
        total=total,
        visible_start=visible_start,
        visible_count=new_count,
        reset=False,
        zoom_enabled=True,
        min_visible_bars=min_visible_bars,
        right_offset=right_offset,
    )


def apply_pan_left(visible_start: int, visible_count: int) -> int:
    """把可视窗口向左平移。"""
    step = max(1, visible_count // 3)
    return max(0, visible_start - step)


def apply_pan_right(visible_start: int, visible_count: int, total: int) -> int:
    """把可视窗口向右平移。"""
    step = max(1, visible_count // 3)
    max_start = max(0, total - visible_count)
    return min(max_start, visible_start + step)


def apply_drag_pan(origin_start: int, total: int, visible_count: int, bar_shift: int) -> int:
    """按拖动跨过的 bar 数量平移视图，正数表示查看更早的数据。"""
    max_start = max(0, total - visible_count)
    return min(max(origin_start - bar_shift, 0), max_start)
