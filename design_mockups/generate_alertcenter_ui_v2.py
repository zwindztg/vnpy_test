from __future__ import annotations

from math import cos, pi, sin
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parent
PREVIEW_VERSION = "V3"
OUTPUT = ROOT / "alertcenter_ui_v3.png"

WIDTH = 1728
HEIGHT = 1117

COLOR_BG_TOP = "#081221"
COLOR_BG_BOTTOM = "#071520"
COLOR_PANEL = "#0F1C2B"
COLOR_PANEL_2 = "#122133"
COLOR_PANEL_3 = "#101B28"
COLOR_BORDER = "#223447"
COLOR_TEXT = "#E5EDF7"
COLOR_MUTED = "#8EA2B8"
COLOR_BLUE = "#2F6BFF"
COLOR_BLUE_SOFT = "#183C78"
COLOR_GREEN = "#17B15C"
COLOR_GREEN_SOFT = "#113B28"
COLOR_AMBER = "#F4A71D"
COLOR_AMBER_SOFT = "#463314"
COLOR_RED = "#FF4D4F"
COLOR_GRID = "#1A2A3A"


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """优先使用系统中文字体，保证中文标题和说明能正常显示。"""
    candidates = [
        ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        ("/System/Library/Fonts/SFNS.ttf", 0),
    ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except OSError:
            continue
    return ImageFont.load_default()


FONT_11 = load_font(11)
FONT_12 = load_font(12)
FONT_13 = load_font(13)
FONT_14 = load_font(14)
FONT_15 = load_font(15)
FONT_16 = load_font(16)
FONT_18 = load_font(18, bold=True)
FONT_20 = load_font(20, bold=True)
FONT_22 = load_font(22, bold=True)
FONT_24 = load_font(24, bold=True)
FONT_28 = load_font(28, bold=True)


def hex_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def rounded_panel(
    base: Image.Image,
    box: tuple[int, int, int, int],
    *,
    radius: int,
    fill: str,
    outline: str | None = None,
    shadow: bool = True,
) -> None:
    """统一绘制带阴影的卡片，方便整张图视觉语言一致。"""
    x1, y1, x2, y2 = box
    if shadow:
        shadow_img = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_img)
        shadow_draw.rounded_rectangle(
            (x1, y1 + 10, x2, y2 + 10),
            radius=radius,
            fill=(0, 0, 0, 135),
        )
        shadow_img = shadow_img.filter(ImageFilter.GaussianBlur(18))
        base.alpha_composite(shadow_img)

    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.FreeTypeFont,
    fill: str,
    anchor: str = "la",
) -> None:
    draw.text(xy, text, font=font, fill=fill, anchor=anchor)


def pill(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: str,
    text: str,
    text_fill: str = "#FFFFFF",
    font: ImageFont.FreeTypeFont = FONT_12,
    outline: str | None = None,
) -> None:
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill, outline=outline, width=1 if outline else 0)
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2 + 1
    draw_text(draw, (cx, cy), text, font=font, fill=text_fill, anchor="mm")


def button(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    text: str,
    variant: str = "secondary",
) -> None:
    if variant == "primary":
        fill = COLOR_BLUE
        outline = None
        text_fill = "#FFFFFF"
    elif variant == "success":
        fill = COLOR_GREEN
        outline = None
        text_fill = "#FFFFFF"
    else:
        fill = "#142233"
        outline = "#355069"
        text_fill = "#D7E2EE"
    draw.rounded_rectangle(box, radius=14, fill=fill, outline=outline, width=1 if outline else 0)
    draw_text(draw, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2 + 1), text, font=FONT_14, fill=text_fill, anchor="mm")


def input_row(draw: ImageDraw.ImageDraw, y: int, label: str, value: str) -> None:
    draw_text(draw, (160, y), label, font=FONT_13, fill="#A5B8CC")
    draw.rounded_rectangle((266, y - 18, 484, y + 14), radius=10, fill="#0C1520", outline="#334A60")
    draw_text(draw, (288, y - 1), value, font=FONT_13, fill=COLOR_TEXT, anchor="lm")


def draw_checkbox(draw: ImageDraw.ImageDraw, x: int, y: int, label: str) -> None:
    draw.rounded_rectangle((x, y, x + 16, y + 16), radius=4, fill=COLOR_BLUE)
    draw.line((x + 4, y + 8, x + 7, y + 11), fill="white", width=2)
    draw.line((x + 7, y + 11, x + 12, y + 5), fill="white", width=2)
    draw_text(draw, (x + 24, y + 10), label, font=FONT_13, fill="#D7E2EE", anchor="lm")


def symbol_card(
    draw: ImageDraw.ImageDraw,
    y: int,
    *,
    symbol: str,
    detail: str,
    enabled: bool = True,
    status: str = "启用",
) -> None:
    draw.rounded_rectangle((158, y, 502, y + 66), radius=16, fill="#0D1723", outline="#2A4157")
    dot_fill = COLOR_BLUE if enabled else "#3A4B5D"
    draw.ellipse((175, y + 22, 193, y + 40), fill=dot_fill)
    if enabled:
        draw.line((179, y + 30, 182, y + 33), fill="white", width=2)
        draw.line((182, y + 33, 188, y + 27), fill="white", width=2)
    draw_text(draw, (212, y + 21), symbol, font=FONT_16, fill=COLOR_TEXT)
    draw_text(draw, (212, y + 45), detail, font=FONT_12, fill="#7FA6FF")
    status_box = (421, y + 18, 486, y + 42)
    if enabled:
        pill(draw, status_box, fill=COLOR_GREEN, text=status, font=FONT_11)
    else:
        pill(draw, status_box, fill="#243547", text="待命", text_fill="#9DB1C6", font=FONT_11)


def metric_row(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    title: str,
    value: str,
    value_fill: str,
) -> None:
    draw.rounded_rectangle(box, radius=16, fill="#121F2D", outline="#233548")
    draw_text(draw, (box[0] + 18, box[1] + 16), title, font=FONT_12, fill="#92A9BF")
    draw_text(draw, (box[0] + 18, box[1] + 42), value, font=FONT_20, fill=value_fill)


def state_item(draw: ImageDraw.ImageDraw, y: int, symbol: str, strategy: str, status_text: str, status_fill: str, recent: str) -> None:
    draw.rounded_rectangle((623, y, 364 + 623, y + 60), radius=14, fill="#101C29", outline="#213345")
    draw_text(draw, (642, y + 17), symbol, font=FONT_15, fill=COLOR_TEXT)
    draw_text(draw, (642, y + 38), strategy, font=FONT_11, fill="#7FA6FF")
    pill(draw, (808, y + 15, 865, y + 37), fill=status_fill, text=status_text, font=FONT_11)
    draw_text(draw, (885, y + 30), recent, font=FONT_11, fill="#B8CADB", anchor="lm")


def log_item(draw: ImageDraw.ImageDraw, y: int, badge_text: str, badge_fill: str, time_text: str, message: str) -> None:
    """把运行日志压缩成更紧凑的单行结构，方便同屏看到更多上下文。"""
    draw.rounded_rectangle((1022, y, 1536, y + 24), radius=10, fill="#0C1520", outline="#213345")
    pill(draw, (1034, y + 4, 1070, y + 20), fill=badge_fill, text=badge_text, font=FONT_11)
    draw_text(draw, (1086, y + 8), time_text, font=FONT_11, fill=COLOR_MUTED)
    draw_text(draw, (1184, y + 8), message, font=FONT_11, fill="#DCE7F3")


def draw_line_chart(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=18, fill="#09131E")

    for i in range(5):
        y = y1 + 40 + i * 54
        draw.line((x1 + 18, y, x2 - 18, y), fill=COLOR_GRID, width=1)

    for i in range(8):
        x = x1 + 52 + i * 58
        draw.line((x, y1 + 18, x, y2 - 16), fill="#152434", width=1)

    closes = [0.34, 0.42, 0.27, 0.55, 0.48, 0.63, 0.52, 0.71, 0.66, 0.57, 0.79, 0.74, 0.88, 0.81]
    ma = [0.39, 0.37, 0.34, 0.43, 0.48, 0.53, 0.57, 0.61, 0.63, 0.65, 0.69, 0.72, 0.75, 0.77]
    points_close: list[tuple[float, float]] = []
    points_ma: list[tuple[float, float]] = []
    bar_w = 18

    for i, value in enumerate(closes):
        x = x1 + 46 + i * 34
        low = y2 - 62 - (value - 0.12) * 205
        high = low - 42
        open_v = value - (0.06 if i % 3 == 0 else -0.04)
        close_v = value
        open_y = y2 - 62 - (open_v - 0.12) * 205
        close_y = y2 - 62 - (close_v - 0.12) * 205
        wick_top = min(open_y, close_y) - 16
        wick_bottom = max(open_y, close_y) + 14
        color = COLOR_GREEN if close_y < open_y else COLOR_RED
        draw.line((x + 9, wick_top, x + 9, wick_bottom), fill="#90A1B5", width=2)
        draw.rectangle((x, min(open_y, close_y), x + bar_w, max(open_y, close_y)), fill=color)
        points_close.append((x + 9, close_y))

    for i, value in enumerate(ma):
        x = x1 + 55 + i * 34
        y = y2 - 62 - (value - 0.12) * 205
        points_ma.append((x, y))

    draw.line(points_close, fill="#66A8FF", width=3, joint="curve")
    draw.line(points_ma, fill=COLOR_AMBER, width=3, joint="curve")

    # 关键提醒点，突出“学习型可视化反馈”
    buy_x, buy_y = points_close[12]
    sell_x, sell_y = points_close[10]
    draw.ellipse((buy_x - 8, buy_y - 8, buy_x + 8, buy_y + 8), fill=COLOR_GREEN)
    draw.polygon([(buy_x, buy_y - 18), (buy_x + 9, buy_y), (buy_x - 9, buy_y)], fill=COLOR_GREEN)
    draw.ellipse((sell_x - 8, sell_y - 8, sell_x + 8, sell_y + 8), fill=COLOR_AMBER)
    draw.polygon([(sell_x, sell_y + 18), (sell_x + 9, sell_y), (sell_x - 9, sell_y)], fill=COLOR_AMBER)

    draw_text(draw, (x1 + 22, y1 + 58), "26.10", font=FONT_11, fill="#9AAFC5")
    draw_text(draw, (x1 + 22, y1 + 112), "25.80", font=FONT_11, fill="#9AAFC5")
    draw_text(draw, (x1 + 22, y1 + 166), "25.50", font=FONT_11, fill="#9AAFC5")
    draw_text(draw, (x1 + 22, y1 + 220), "25.20", font=FONT_11, fill="#9AAFC5")
    draw_text(draw, (x1 + 42, y2 - 28), "13:50", font=FONT_11, fill="#748AA3")
    draw_text(draw, (x1 + 178, y2 - 28), "14:10", font=FONT_11, fill="#748AA3")
    draw_text(draw, (x1 + 316, y2 - 28), "14:30", font=FONT_11, fill="#748AA3")
    draw_text(draw, (x2 - 72, y2 - 28), "14:55", font=FONT_11, fill="#748AA3")


def add_radial_glow(base: Image.Image, center: tuple[int, int], radius: int, color: str, alpha: int) -> None:
    glow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for step in range(radius, 0, -18):
        current_alpha = int(alpha * (step / radius) ** 2)
        box = (center[0] - step, center[1] - step, center[0] + step, center[1] + step)
        glow_draw.ellipse(box, fill=hex_rgba(color, current_alpha))
    glow = glow.filter(ImageFilter.GaussianBlur(32))
    base.alpha_composite(glow)


def draw_background(base: Image.Image) -> None:
    """先把大背景氛围做好，让成图更像完整产品稿。"""
    draw = ImageDraw.Draw(base)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        r1, g1, b1 = hex_rgba(COLOR_BG_TOP)[:3]
        r2, g2, b2 = hex_rgba(COLOR_BG_BOTTOM)[:3]
        color = (
            int(r1 + (r2 - r1) * ratio),
            int(g1 + (g2 - g1) * ratio),
            int(b1 + (b2 - b1) * ratio),
            255,
        )
        draw.line((0, y, WIDTH, y), fill=color)

    add_radial_glow(base, (280, 210), 340, "#245BFF", 60)
    add_radial_glow(base, (1360, 820), 420, "#10B981", 38)

    grid = Image.new("RGBA", base.size, (0, 0, 0, 0))
    grid_draw = ImageDraw.Draw(grid)
    for y in range(120, HEIGHT, 62):
        grid_draw.line((72, y, WIDTH - 72, y), fill=(120, 145, 170, 16), width=1)
    grid = grid.filter(ImageFilter.GaussianBlur(0))
    base.alpha_composite(grid)


def draw_scene(base: Image.Image) -> None:
    draw = ImageDraw.Draw(base)

    # 主窗口外壳
    rounded_panel(base, (74, 54, 1636, 1036), radius=30, fill="#0A1522", outline="#203243", shadow=True)
    draw.rounded_rectangle((74, 54, 1636, 128), radius=30, fill="#0D1928", outline="#203243")
    draw.line((74, 108, 1636, 108), fill="#203243", width=1)
    for i, color in enumerate(("#FF5F57", "#FEBB2E", "#28C840")):
        draw.ellipse((104 + i * 24, 84, 116 + i * 24, 96), fill=color)

    draw_text(draw, (150, 86), "实时提醒中心", font=FONT_24, fill=COLOR_TEXT)
    draw_text(draw, (150, 112), f"vn.py A-Share Alert Center · High Fidelity Preview {PREVIEW_VERSION}", font=FONT_11, fill="#6F88A2")

    pill(draw, (1370, 74, 1492, 106), fill="#11243A", text="实时运行", text_fill="#9CC2FF")
    pill(draw, (1500, 74, 1608, 106), fill="#10281C", text="2 / 3 已启用", text_fill="#82E1A8")

    # 左右主体
    rounded_panel(base, (110, 146, 546, 1006), radius=28, fill="#0D1825", outline="#213446", shadow=False)
    rounded_panel(base, (570, 146, 1598, 1006), radius=28, fill="#0B1724", outline="#213446", shadow=False)

    # 左侧导语
    rounded_panel(base, (130, 168, 522, 258), radius=22, fill=COLOR_PANEL_2, outline="#263A4E", shadow=False)
    draw_text(draw, (154, 190), "CONTROL BAR", font=FONT_11, fill="#7DA4FF")
    draw_text(draw, (154, 218), "一屏完成配置、测试与监控", font=FONT_28, fill=COLOR_TEXT)
    draw_text(draw, (154, 246), "把学习策略、图表验证、提醒记录和运行日志放在同一个工作面板里。", font=FONT_13, fill="#8095AB")

    # 全局设置
    rounded_panel(base, (130, 282, 522, 504), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (154, 312), "全局设置", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (154, 336), "Global Configuration", font=FONT_11, fill="#72869F")
    input_row(draw, 374, "提醒周期", "5m")
    input_row(draw, 416, "轮询间隔", "20 秒")
    input_row(draw, 458, "冷却时间", "300 秒")
    input_row(draw, 500, "复权方式", "前复权 qfq")
    draw_checkbox(draw, 160, 520, "启用桌面通知")

    # 股票配置
    rounded_panel(base, (130, 528, 522, 814), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (154, 558), "股票配置", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (154, 582), "最多 3 只股票，支持不同策略与参数", font=FONT_11, fill="#72869F")
    symbol_card(draw, 606, symbol="601869.SSE", detail="基础提醒策略 · 突破 25.38 / 止损 24.12", enabled=True, status="启用")
    symbol_card(draw, 684, symbol="600000.SSE", detail="A股长仓学习策略 · 快 5 / 慢 20 / 100 股", enabled=True, status="启用")
    symbol_card(draw, 762, symbol="000001.SZSE", detail="A股短线放量突破策略 · 暂未启用", enabled=False)

    # 运行信息
    rounded_panel(base, (130, 836, 522, 982), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (154, 866), "运行信息", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (154, 906), "配置文件", font=FONT_13, fill="#A7BBCE")
    draw_text(draw, (154, 929), "config/akshare_realtime_alert.json", font=FONT_12, fill="#788EA6")
    draw_text(draw, (154, 960), "记录文件", font=FONT_13, fill="#A7BBCE")
    draw_text(draw, (154, 983), "logs/akshare_realtime_alerts.csv", font=FONT_12, fill="#788EA6")
    pill(draw, (382, 884, 490, 920), fill="#123B28", text="线程运行中", text_fill="#8BE0AB", font=FONT_13)

    # 顶部工具条
    rounded_panel(base, (594, 168, 1574, 258), radius=22, fill=COLOR_PANEL_2, outline="#263A4E", shadow=False)
    button(draw, (620, 196, 716, 236), text="加载配置")
    button(draw, (728, 196, 824, 236), text="保存配置")
    button(draw, (836, 196, 932, 236), text="单次测试", variant="primary")
    button(draw, (944, 196, 1040, 236), text="启动提醒", variant="success")
    button(draw, (1052, 196, 1148, 236), text="停止提醒")
    draw_text(draw, (1228, 191), "当前模式", font=FONT_11, fill="#6E849C")
    pill(draw, (1224, 206, 1342, 236), fill="#11243A", text="实时运行", text_fill="#9CC2FF")
    draw_text(draw, (1380, 191), "整体状态", font=FONT_11, fill="#6E849C")
    pill(draw, (1376, 206, 1538, 236), fill="#113420", text="监控中 · 轮询每 20 秒", text_fill="#82E1A8")

    # 指标摘要
    metric_row(draw, (620, 284, 792, 358), title="活动标的", value="2", value_fill="#9EC2FF")
    metric_row(draw, (810, 284, 982, 358), title="今日提醒", value="3", value_fill="#90E2B3")
    metric_row(draw, (1000, 284, 1172, 358), title="风控信号", value="1", value_fill="#F6C066")
    metric_row(draw, (1190, 284, 1362, 358), title="最近测试", value="14:48", value_fill="#E6EDF7")
    metric_row(draw, (1380, 284, 1552, 358), title="主数据源", value="PyTDX", value_fill="#9EC2FF")

    # 状态区
    rounded_panel(base, (594, 382, 978, 642), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (620, 412), "策略状态", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (620, 436), "State Board", font=FONT_11, fill="#72869F")
    draw.rounded_rectangle((620, 454, 950, 490), radius=12, fill="#0C1520")
    draw_text(draw, (638, 476), "股票", font=FONT_11, fill="#8EA4BB")
    draw_text(draw, (740, 476), "策略", font=FONT_11, fill="#8EA4BB")
    draw_text(draw, (850, 476), "状态", font=FONT_11, fill="#8EA4BB")
    draw_text(draw, (900, 476), "最近提醒", font=FONT_11, fill="#8EA4BB")
    state_item(draw, 502, "601869.SSE", "基础提醒", "观察中", COLOR_GREEN, "14:55 观察型")
    state_item(draw, 570, "600000.SSE", "长仓学习策略", "风控中", COLOR_AMBER, "14:52 止盈提示")

    # 记录区
    rounded_panel(base, (594, 662, 978, 982), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (620, 692), "提醒记录", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (620, 716), "Recent Alerts", font=FONT_11, fill="#72869F")
    draw.rounded_rectangle((620, 738, 950, 798), radius=14, fill="#101C29", outline="#213345")
    pill(draw, (636, 755, 694, 777), fill=COLOR_GREEN, text="观察型", font=FONT_11)
    draw_text(draw, (712, 758), "601869.SSE 突破提醒", font=FONT_14, fill=COLOR_TEXT)
    draw_text(draw, (712, 780), "价格上穿 25.38 · 2026-04-15 14:55", font=FONT_11, fill="#8EA4BB")
    draw.rounded_rectangle((620, 810, 950, 870), radius=14, fill="#101C29", outline="#213345")
    pill(draw, (636, 827, 694, 849), fill=COLOR_AMBER, text="风控型", font=FONT_11)
    draw_text(draw, (712, 830), "600000.SSE 收益回撤提醒", font=FONT_14, fill=COLOR_TEXT)
    draw_text(draw, (712, 852), "均线转弱，建议观察减仓 · 14:52", font=FONT_11, fill="#8EA4BB")
    draw.rounded_rectangle((620, 882, 950, 942), radius=14, fill="#101C29", outline="#213345")
    pill(draw, (636, 899, 694, 921), fill="#25384A", text="系统", text_fill="#C7D3E0", font=FONT_11)
    draw_text(draw, (712, 902), "单次测试完成", font=FONT_14, fill=COLOR_TEXT)
    draw_text(draw, (712, 924), "历史回放 48 根 K 线，2 条有效提醒", font=FONT_11, fill="#8EA4BB")

    # 图表区
    rounded_panel(base, (998, 382, 1558, 694), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (1024, 412), "K 线图", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (1024, 436), "601869.SSE · 基础提醒策略 · 5m · 实时运行", font=FONT_11, fill="#72869F")
    draw.rounded_rectangle((1024, 454, 1532, 490), radius=12, fill="#0C1520", outline="#22384B")
    draw_text(draw, (1046, 476), "数据源 PyTDX", font=FONT_12, fill="#84ABFF")
    draw_text(draw, (1162, 476), "观察型提醒 1", font=FONT_12, fill="#90E2B3")
    draw_text(draw, (1286, 476), "风控型提醒 1", font=FONT_12, fill="#F7C06E")
    draw_text(draw, (1412, 476), "最新 K 线 14:55", font=FONT_12, fill="#94A6B9")
    draw_line_chart(draw, (1024, 506, 1532, 670))

    # 日志区
    rounded_panel(base, (998, 714, 1558, 982), radius=22, fill=COLOR_PANEL, outline="#263A4E", shadow=False)
    draw_text(draw, (1024, 744), "运行日志", font=FONT_20, fill=COLOR_TEXT)
    draw_text(draw, (1024, 768), "Execution Log", font=FONT_11, fill="#72869F")
    pill(draw, (1410, 742, 1530, 766), fill="#132131", text="最近 6 条", text_fill="#9DB4CB", font=FONT_11)
    log_item(draw, 792, "测试", "#11243A", "14:48:02", "历史回放完成，处理 48 根 K 线，命中 2 条提醒。")
    log_item(draw, 822, "系统", "#25384A", "14:49:11", "提醒线程就绪，2 只启用标的已装载。")
    log_item(draw, 852, "系统", "#25384A", "14:50:00", "本轮扫描完成，PyTDX 行情获取成功。")
    log_item(draw, 882, "风控", "#463314", "14:52:18", "600000.SSE 均线转弱，触发风险提示。")
    log_item(draw, 912, "提醒", "#123B28", "14:54:41", "601869.SSE 新 K 线确认，突破条件持续成立。")
    log_item(draw, 942, "提醒", "#123B28", "14:55:01", "601869.SSE 上穿 25.38，生成观察型提醒。")

    draw_text(
        draw,
        (116, 1068),
        "Preview Intent: desktop vn.py learning workflow / A-share alerting / dark professional terminal / clarity and confidence first",
        font=FONT_11,
        fill="#62778E",
    )


def main() -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), hex_rgba("#081221"))
    draw_background(image)
    draw_scene(image)
    image.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
