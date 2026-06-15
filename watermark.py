# watermark.py — 图片水印转 ASS 绘图命令（放射渐显/渐隐）
import math
import random
import hashlib
from pathlib import Path

# 缓存：{图片路径+缩放 → (drawing_str, width, height)}
_CACHE = {}


def _cache_key(image_path, scale):
    try:
        data = Path(image_path).read_bytes()
        h = hashlib.md5(data).hexdigest()[:12]
    except Exception:
        h = str(image_path)
    return f"{h}_{scale}"


def image_to_ass_drawing_grouped(image_path, scale=100):
    r"""
    将图片转为 ASS 绘图命令（\p1 模式），按颜色分组。
    带缓存：相同图片+缩放只转一次。
    返回 (drawing_str, width, height)
    """
    key = _cache_key(image_path, scale)
    if key in _CACHE:
        return _CACHE[key]

    from PIL import Image

    img = Image.open(image_path).convert("RGBA")

    if scale != 100:
        w = int(img.width * scale / 100)
        h = int(img.height * scale / 100)
        img = img.resize((w, h), Image.LANCZOS)

    pixels = img.load()
    w, h = img.size

    color_groups = {}
    for py in range(h):
        for px in range(w):
            r, g, b, a = pixels[px, py]
            if a < 10:
                continue
            color = f"&H00{b:02X}{g:02X}{r:02X}&"
            alpha_hex = f"&H{255 - a:02X}&"
            key_c = (color, alpha_hex)
            if key_c not in color_groups:
                color_groups[key_c] = []
            color_groups[key_c].append((px, py))

    if not color_groups:
        _CACHE[key] = ("", 0, 0)
        return "", 0, 0

    commands = []
    for (color, alpha), points in color_groups.items():
        path_parts = []
        for i, (px, py) in enumerate(points):
            if i == 0:
                path_parts.append(f"m {px} {py}")
            else:
                path_parts.append(f"l {px} {py}")
        path_parts.append("x")
        path_str = " ".join(path_parts)
        commands.append(
            f"{{\\c{color}\\alpha{alpha}\\p1\\pos(0,0){path_str}\\p0}}"
        )

    result = "".join(commands), w, h
    _CACHE[key] = result
    return result


def _calc_position(alignment, margin, img_w, img_h, playres_x, playres_y):
    """
    根据对齐方式和边距计算水印左上角坐标。
    alignment: top-left, top-right, bottom-left, bottom-right
    返回 (x, y)
    """
    if alignment == "top-left":
        return margin, margin
    elif alignment == "top-right":
        return playres_x - img_w - margin, margin
    elif alignment == "bottom-left":
        return margin, playres_y - img_h - margin
    elif alignment == "bottom-right":
        return playres_x - img_w - margin, playres_y - img_h - margin
    return margin, margin


def _time_fmt(seconds):
    """秒数 → ASS 时间格式 0:MM:SS.cc"""
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"0:{m:02d}:{s:05.2f}"


def _parse_time(t):
    """ASS 时间格式 → 秒数"""
    parts = t.replace(",", ".").split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def generate_watermark_dialogues(
    image_path, scale=100,
    alignment="top-left", margin=10,
    appearances=3, duration_sec=30,
    playres_x=1920, playres_y=1080,
    video_duration_sec=3600,
):
    """
    生成多个水印 Dialogue 行。
    每次出现 30 秒，渐显 5 秒，渐隐 5 秒，完整显示 20 秒。
    位置按 alignment 自动循环变化。
    返回 (dialogue_lines, img_width, img_height)
    """
    drawing_str, img_w, img_h = image_to_ass_drawing_grouped(image_path, scale)
    if not drawing_str:
        return [], 0, 0

    FADE_IN = 5.0   # 渐显 5 秒
    FADE_OUT = 5.0  # 渐隐 5 秒

    # 位置循环顺序
    position_order = ["top-left", "top-right", "bottom-right", "bottom-left"]

    # 均匀分配出现时间
    if appearances <= 1:
        start_times = [10.0]  # 第 10 秒开始
    else:
        gap = (video_duration_sec - duration_sec) / (appearances - 1)
        start_times = [i * gap for i in range(appearances)]

    lines = []
    for i, start_sec in enumerate(start_times):
        end_sec = start_sec + duration_sec
        align = position_order[i % len(position_order)]
        x, y = _calc_position(align, margin, img_w, img_h, playres_x, playres_y)

        start_str = _time_fmt(start_sec)
        end_str = _time_fmt(end_sec)

        # 放射渐显：分 3 层，每层延迟 1.5 秒
        for layer in range(3):
            layer_delay = layer * 1.5
            # 渐显阶段 alpha 动画
            t_in_start = layer_delay
            t_in_end = layer_delay + FADE_IN
            # 渐隐阶段 alpha 动画
            t_out_start = duration_sec - FADE_OUT - layer_delay
            t_out_end = duration_sec - layer_delay

            # alpha 从 255(全透明) → 0(不透明) 做渐显
            # alpha 从 0(不透明) → 255(全透明) 做渐隐
            alpha_tag = (
                f"\\t({t_in_start:.1f},{t_in_end:.1f},\\alpha,255,0)"
                f"\\t({t_out_start:.1f},{t_out_end:.1f},\\alpha,0,255)"
            )

            dialogue = (
                f"Dialogue: 0,{start_str},{end_str},Watermark,,0,0,0,,"
                f"{{\\an7\\pos({x},{y})\\p1{alpha_tag}}}"
                f"{drawing_str}\\p0\n"
            )
            lines.append(dialogue)

    return lines, img_w, img_h
