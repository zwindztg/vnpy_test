"""K线图视图状态的纯逻辑辅助函数。"""

from __future__ import annotations

from math import ceil, floor

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


def merge_marker_bucket(markers: list[ChartMarkerData]) -> list[ChartMarkerData]:
    """合并同一时间桶里同方向的重复标记，减少 1m 图遮挡。"""
    merged: list[ChartMarkerData] = []
    seen_directions: set[str] = set()
    for marker in markers:
        if marker.direction in seen_directions:
            continue
        seen_directions.add(marker.direction)
        merged.append(marker)
    return merged


def build_visible_range_text(bars: list[ChartBarData]) -> str:
    """把当前可视区间格式化成稳定文本，供主图和详情窗口共用。"""
    if not bars:
        return "-"

    start_dt = bars[0].dt
    end_dt = bars[-1].dt
    if start_dt.date() == end_dt.date():
        return f"{start_dt:%Y-%m-%d %H:%M} ~ {end_dt:%H:%M}"
    return f"{start_dt:%Y-%m-%d %H:%M} ~ {end_dt:%Y-%m-%d %H:%M}"


def infer_wheel_input_kind(
    *,
    source_name: str,
    device_type_name: str,
    pointer_type_name: str,
    pixel_x: int,
    pixel_y: int,
    angle_x: int,
    angle_y: int,
    threshold: int = 2,
) -> str:
    """根据 Qt 暴露的来源字段推断滚轮事件来自触摸板还是鼠标。

    判断优先级：
    - 先看事件形态：典型离散滚轮（`angleDelta=±120` 等）优先视为鼠标。
    - 再看 `pixelDelta`：明显存在时通常是触摸板。
    - 再看 `TouchPad` / `Finger` / `Mouse` 这些设备元数据。
    - 最后才把 `MouseEventSynthesizedBySystem` 当作触摸板提示，避免它误伤物理鼠标。
    """
    has_pixel_delta = abs(pixel_x) >= threshold or abs(pixel_y) >= threshold
    has_angle_delta = abs(angle_x) >= threshold or abs(angle_y) >= threshold
    discrete_wheel_y = abs(angle_y) >= 120 and angle_y % 120 == 0
    discrete_wheel_x = abs(angle_x) >= 120 and angle_x % 120 == 0

    if discrete_wheel_x or discrete_wheel_y:
        return "mouse"
    if pointer_type_name == "Finger":
        return "trackpad"
    if has_pixel_delta:
        return "trackpad"
    if has_angle_delta:
        return "mouse"
    if device_type_name == "TouchPad" or pointer_type_name == "Finger":
        return "trackpad"
    if device_type_name == "Mouse":
        return "mouse"
    if source_name == "MouseEventSynthesizedBySystem":
        return "trackpad"
    return "mouse"


def build_wheel_device_signature(
    *,
    source_name: str,
    device_type_name: str,
    pointer_type_name: str,
    device_name: str,
    device_system_id: str,
) -> str:
    """把 wheel 相关的设备字段拼成稳定签名，供一轮输入意图锁复用。"""
    return "|".join(
        (
            source_name or "-",
            device_type_name or "-",
            pointer_type_name or "-",
            device_name or "-",
            device_system_id or "-",
        )
    )


def looks_like_smooth_mouse_wheel(
    *,
    pixel_x: int,
    pixel_y: int,
    angle_x: int,
    angle_y: int,
    phase_name: str,
    begin_event: bool,
    update_event: bool,
    end_event: bool,
    threshold: int = 2,
) -> bool:
    """识别一类被 Qt 包装成 smooth scroll 的“鼠标滚轮”画像。

    当前观察到的特征：
    - 纵向为主
    - 同时带有 pixelDelta 和 angleDelta
    - `angle` 近似 `pixel * 2`
    - 没有 begin/update/end 生命周期
    - `phase` 为 `NoScrollPhase`
    """
    if phase_name != "NoScrollPhase":
        return False
    if begin_event or update_event or end_event:
        return False

    vertical_intent = abs(pixel_y) >= threshold and abs(pixel_y) > abs(pixel_x) * 1.5
    if not vertical_intent:
        return False
    # 罗技滚轮在 macOS/Qt 下会被包装成平滑滚动，单次幅度不一定很大，
    # 这里降低阈值，并改用“纵向占优 + 无阶段生命周期”来区分触摸板。
    if abs(pixel_y) < 12:
        return False
    if abs(angle_y) < threshold or abs(angle_y) <= abs(angle_x) * 1.5:
        return False
    if pixel_y * angle_y <= 0:
        return False

    ratio_base = max(abs(pixel_y), 1)
    ratio = abs(angle_y) / ratio_base
    return 1.4 <= ratio <= 3.2


def looks_like_trackpad_pan(
    *,
    pixel_x: int,
    pixel_y: int,
    angle_x: int,
    angle_y: int,
    threshold: int = 2,
) -> bool:
    """只在横向意图非常明确时才认定为触摸板平移，避免滚轮噪声误伤。"""
    if abs(pixel_x) < max(threshold, 8):
        return False
    if abs(pixel_x) <= abs(pixel_y) * 1.8:
        return False
    if abs(angle_x) > 0 and abs(angle_x) <= abs(angle_y) * 1.2:
        return False
    return True


def classify_wheel_intent(
    *,
    input_kind: str,
    pixel_x: int,
    pixel_y: int,
    angle_x: int,
    angle_y: int,
    phase_name: str,
    begin_event: bool,
    update_event: bool,
    end_event: bool,
    threshold: int = 2,
) -> str | None:
    """把单个 wheel 事件归类成一轮输入应该坚持的意图。"""
    if looks_like_smooth_mouse_wheel(
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        angle_x=angle_x,
        angle_y=angle_y,
        phase_name=phase_name,
        begin_event=begin_event,
        update_event=update_event,
        end_event=end_event,
        threshold=threshold,
    ):
        return "smooth_wheel_zoom"

    if input_kind == "trackpad" and looks_like_trackpad_pan(
        pixel_x=pixel_x,
        pixel_y=pixel_y,
        angle_x=angle_x,
        angle_y=angle_y,
        threshold=threshold,
    ):
        return "trackpad_pan"

    if input_kind == "mouse" and abs(angle_y) >= threshold:
        return "mouse_wheel_zoom"

    if input_kind == "trackpad" and (
        abs(pixel_y) >= threshold or abs(angle_y) >= threshold or abs(angle_x) >= threshold
    ):
        return "trackpad_ignore"

    return None


def should_preserve_wheel_intent(
    *,
    active_intent: str | None,
    current_intent: str | None,
    active_signature: str,
    active_timestamp: float,
    current_signature: str,
    current_timestamp: float,
    timeout_seconds: float = 0.18,
) -> bool:
    """判断当前事件是否应沿用上一帧已经锁定的 wheel 输入意图。"""
    if not active_intent or not active_signature:
        return False
    if current_signature != active_signature:
        return False
    if (current_timestamp - active_timestamp) > timeout_seconds:
        return False
    if current_intent is None:
        return True
    if current_intent == active_intent:
        return True
    # ignore 只是一种“兜底无动作”意图，不应该压住后面已经明确识别出的缩放/平移。
    if current_intent == "trackpad_ignore" and active_intent in {
        "trackpad_pan",
        "smooth_wheel_zoom",
        "mouse_wheel_zoom",
    }:
        return True
    return False


def classify_wheel_navigation(
    *,
    pixel_x: int,
    pixel_y: int,
    angle_x: int,
    angle_y: int,
    threshold: int = 2,
) -> str | None:
    """把滚轮/触摸板手势分类成平移或缩放动作。

    处理顺序：
    - 触摸板路径只保留左右平移；上下滑不再承担缩放语义。
    - 鼠标路径继续允许滚轮缩放。
    """
    has_pixel_delta = abs(pixel_x) >= threshold or abs(pixel_y) >= threshold
    if has_pixel_delta:
        if abs(pixel_x) >= threshold and abs(pixel_x) > abs(pixel_y):
            # 正数表示视图向右滚动，内容向左移动，更接近“看更新的数据”。
            return "pan_right" if pixel_x > 0 else "pan_left"
        return None

    delta_x = angle_x
    delta_y = angle_y

    if abs(delta_x) >= threshold and abs(delta_x) > abs(delta_y):
        return "pan_right" if delta_x > 0 else "pan_left"

    if abs(delta_y) >= threshold and abs(delta_y) >= abs(delta_x):
        return "zoom_in" if delta_y > 0 else "zoom_out"

    return None


def classify_continuous_zoom_action(delta: float, threshold: float = 0.03) -> str | None:
    """把连续缩放增量映射成放大/缩小动作。

    mac 触摸板捏合事件的单次增量通常比较小，这里把阈值压低一档，
    让连续捏合更容易触发图表缩放，同时保留基础防抖。
    """
    if delta >= threshold:
        return "zoom_in"
    if delta <= -threshold:
        return "zoom_out"
    return None


def extract_pinch_zoom_factor(gesture) -> float | None:
    """尽量从 Qt pinch gesture 对象里提取相对手势起点的总缩放比例。"""
    total_getter = getattr(gesture, "totalScaleFactor", None)
    scale_getter = getattr(gesture, "scaleFactor", None)

    try:
        if callable(total_getter):
            total_factor = float(total_getter())
            if total_factor > 0:
                return total_factor
    except Exception:
        pass

    try:
        if callable(scale_getter):
            scale_factor = float(scale_getter())
            if scale_factor > 0:
                return scale_factor
    except Exception:
        pass

    return None


def extract_pinch_zoom_delta(gesture) -> float | None:
    """尽量从 Qt pinch gesture 对象里提取一次连续缩放增量。"""
    current_getter = getattr(gesture, "scaleFactor", None)
    previous_getter = getattr(gesture, "lastScaleFactor", None)
    total_getter = getattr(gesture, "totalScaleFactor", None)

    try:
        if callable(current_getter) and callable(previous_getter):
            current = float(current_getter())
            previous = float(previous_getter())
            if previous > 0:
                return current - previous
            return current - 1.0
    except Exception:
        pass

    try:
        if callable(total_getter):
            return float(total_getter()) - 1.0
    except Exception:
        pass

    return None


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


def get_right_offset(total: int, visible_start: float, visible_count: int) -> float:
    """记录当前窗口距离最右侧的偏移，方便快照刷新后尽量保持位置。"""
    if total <= 0 or visible_count <= 0:
        return 0.0
    return max(0.0, total - (visible_start + visible_count))


def get_default_visible_window(
    snapshot: ChartSnapshotData | None,
    *,
    total: int,
    min_visible_bars: int = 0,
) -> tuple[int, int] | None:
    """根据快照约定返回统一的默认可见窗口。

    这条规则同时服务主界面静态图和详情窗口 reset：
    - snapshot 负责声明默认想看到多少根；
    - 图层只消费这个窗口，不再各自追加 1m/详情窗口 special case。
    """
    if snapshot is None or total <= 0:
        return None

    if snapshot.default_visible_count > 0:
        visible_count = min(
            total,
            max(min_visible_bars, snapshot.default_visible_count),
        )
        return max(0, total - visible_count), visible_count

    return 0, total


def get_reset_visible_window(
    snapshot: ChartSnapshotData | None,
    *,
    interactive: bool,
    total: int,
    min_visible_bars: int,
) -> tuple[int, int] | None:
    """仅在可交互图表 reset 时读取默认视口。"""
    if not interactive:
        return None
    return get_default_visible_window(
        snapshot,
        total=total,
        min_visible_bars=min_visible_bars,
    )


def sync_view_state(
    *,
    total: int,
    visible_start: float,
    visible_count: int,
    reset: bool,
    zoom_enabled: bool,
    min_visible_bars: int,
    right_offset: float | None = None,
) -> tuple[float, int]:
    """根据最新快照和当前交互状态归一化可见区间。"""
    if total <= 0:
        return 0, 0

    if reset or visible_count <= 0 or not zoom_enabled:
        return 0, total

    min_count = min(min_visible_bars, total)
    normalized_count = min(max(visible_count, min_count), total)
    max_start = max(0.0, total - normalized_count)
    if right_offset is None:
        normalized_start = min(max(visible_start, 0.0), max_start)
        return normalized_start, normalized_count

    normalized_start = min(max(total - normalized_count - right_offset, 0.0), max_start)
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


def can_pan_left(zoom_enabled: bool, visible_start: float) -> bool:
    """判断当前是否还能向左平移。"""
    return zoom_enabled and visible_start > 1e-6


def can_pan_right(zoom_enabled: bool, visible_start: float, visible_count: int, total: int) -> bool:
    """判断当前是否还能向右平移。"""
    return zoom_enabled and visible_start < max(0.0, total - visible_count) - 1e-6


def apply_zoom_in(
    visible_start: float,
    visible_count: int,
    total: int,
    min_visible_bars: int,
) -> tuple[float, int]:
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
    visible_start: float,
    visible_count: int,
    total: int,
    min_visible_bars: int,
) -> tuple[float, int]:
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


def apply_zoom_scale(
    visible_start: float,
    visible_count: int,
    total: int,
    min_visible_bars: int,
    zoom_factor: float,
    *,
    anchor_ratio: float = 0.5,
) -> tuple[float, int]:
    """按连续缩放比例调整可见窗口。

    约定：
    - `zoom_factor > 1` 表示放大，看到更少的 K 线。
    - `zoom_factor < 1` 表示缩小，看到更多的 K 线。
    - `anchor_ratio` 取值在 0~1 之间，表示当前缩放锚点位于可见窗口的相对位置。
    """
    if total <= 0 or visible_count <= 0 or zoom_factor <= 0:
        return visible_start, visible_count

    clamped_anchor = min(max(anchor_ratio, 0.0), 1.0)
    min_count = min(min_visible_bars, total)
    new_count = round(visible_count / zoom_factor)
    new_count = min(max(new_count, min_count), total)
    if new_count == visible_count:
        return visible_start, visible_count

    anchor_index = visible_start + clamped_anchor * max(visible_count - 1, 0)
    new_start = anchor_index - clamped_anchor * max(new_count - 1, 0)
    max_start = max(0.0, total - new_count)
    return min(max(new_start, 0), max_start), new_count


def apply_pan_left(visible_start: float, visible_count: int) -> float:
    """把可视窗口向左平移。"""
    step = max(1, visible_count // 3)
    return max(0.0, visible_start - step)


def apply_pan_right(visible_start: float, visible_count: int, total: int) -> float:
    """把可视窗口向右平移。"""
    step = max(1, visible_count // 3)
    max_start = max(0.0, total - visible_count)
    return min(max_start, visible_start + step)


def apply_pan_delta(visible_start: float, total: int, visible_count: int, delta_bars: float) -> float:
    """按连续 bar 位移平移视图，正数表示查看更新的数据。"""
    max_start = max(0.0, total - visible_count)
    return min(max(visible_start + delta_bars, 0.0), max_start)


def apply_drag_pan(origin_start: float, total: int, visible_count: int, bar_shift: float) -> float:
    """按拖动跨过的 bar 数量平移视图，正数表示查看更早的数据。"""
    return apply_pan_delta(origin_start, total, visible_count, -bar_shift)


def get_render_window(total: int, visible_start: float, visible_count: int) -> tuple[int, int]:
    """根据浮点起点返回当前绘制需要覆盖的 bar 索引窗口。"""
    if total <= 0 or visible_count <= 0:
        return 0, 0

    start_index = max(0, floor(visible_start))
    end_index = min(total, ceil(visible_start + visible_count))
    if end_index <= start_index:
        end_index = min(total, start_index + 1)
    return start_index, end_index
