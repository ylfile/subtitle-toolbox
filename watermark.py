# watermark.py — 图片水印转 ASS 绘图命令 + 渐显/渐隐
from pathlib import Path


def _bgr_to_ass(b, g, r):
    """RGB → ASS 颜色 BGR(16进制)"""
    return f"&H00{b:02X}{g:02X}{r:02X}&"


def image_to_ass_drawing_grouped(image_path, scale=100):
    r"""
    将图片转为 ASS 绘图命令（\p1 模式），按颜色分组。
    返回 (drawing_str, width, height)
    """
    from PIL import Image

    img = Image.open(image_path).convert("RGBA")

    if scale != 100:
        w = int(img.width * scale / 100)
        h = int(img.height * scale / 100)
        img = img.resize((w, h), Image.LANCZOS)

    pixels = img.load()
    w, h = img.size

    # 按颜色+alpha分组
    color_groups = {}
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if a < 10:
                continue
            color = f"&H00{b:02X}{g:02X}{r:02X}&"
            alpha_hex = f"&H{255 - a:02X}&"
            key = (color, alpha_hex)
            if key not in color_groups:
                color_groups[key] = []
            color_groups[key].append((x, y))

    if not color_groups:
        return "", 0, 0

    # 为每个颜色组生成绘图命令
    commands = []
    for (color, alpha), points in color_groups.items():
        path_parts = []
        for i, (x, y) in enumerate(points):
            if i == 0:
                path_parts.append(f"m {x} {y}")
            else:
                path_parts.append(f"l {x} {y}")
        path_parts.append("x")
        path_str = " ".join(path_parts)
        commands.append(
            f"{{\\c{color}\\alpha{alpha}\\p1\\pos(0,0){path_str}\\p0}}"
        )

    return "".join(commands), w, h


def generate_watermark_dialogue(
    image_path, scale=100,
    x=0, y=0,
    fade_in_ms=1000, fade_out_ms=1000,
    start_time="0:00:38.00", end_time="1:00:00.00",
):
    r"""
    生成完整的水印 Dialogue 行（ASS 格式）。
    返回 (dialogue_line, img_width, img_height)
    """
    try:
        drawing_str, img_w, img_h = image_to_ass_drawing_grouped(image_path, scale)
    except Exception as e:
        raise RuntimeError(f"图片加载失败：{e}")

    if not drawing_str:
        raise RuntimeError("图片转 ASS 绘图命令失败（可能是全透明图片）")

    # 渐显渐隐效果
    fade_tag = f"\\fad({fade_in_ms},{fade_out_ms})"

    # ASS Dialogue 行
    # 使用 \an7（左上角定位），pos(x,y) 指定位置
    dialogue = (
        f"Dialogue: 0,{start_time},{end_time},Watermark,,0,0,0,,"
        f"{{{fade_tag}\\an7\\pos({x},{y})\\p1}}"
        f"{drawing_str}\n"
    )

    return dialogue, img_w, img_h
