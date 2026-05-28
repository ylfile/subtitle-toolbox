# ass.py — ASS 生成（逻辑对齐 release_final黑边测量.py / process_show）
import os
import re
from pathlib import Path

from utils import (
    should_drop,
    normalize_subtitle_text,
    flatten_subtitle_line,
    list_subtitle_pairs,
    extract_episode_id,
    layout_subtitles_from_crop,
    scale_bars_to_playres,
    scale_y_to_playres,
    ensure_chinese_simplified,
    INFO_FONT_REF,
    resolve_subtitle_overlaps,
    calc_chinese_only_y,
    font_size_to_pixels,
    CN_FONT_REF,
    CN_FONT_4K,
)

# 字幕组信息：每条独立时间段，蓝色 + \\an5 顶对齐定位
INFO_DIALOGUES = [
    ("0:00:38.16", "0:00:40.16", "字幕来源：官方"),
    ("0:00:42.16", "0:00:45.16", "压制&校对：有料视界"),
    ("0:00:49.16", "0:00:54.16", "微博：YLFile"),
    ("0:00:58.16", "0:01:02.16", "更多内容：https://ylfile.com"),
    ("0:01:05.16", "0:01:08.16", "视频仅供学习 禁止商用"),
]


def parse_srt(path, is_chinese=False):
    with open(path, "r", encoding="utf-8") as f:
        blocks = f.read().strip().split("\n\n")

    subs = []
    for b in blocks:
        lines = b.split("\n")
        if len(lines) < 3:
            continue
        start, end = lines[1].split(" --> ")
        text = normalize_subtitle_text(" ".join(lines[2:]), is_chinese=is_chinese)
        if should_drop(text):
            continue
        if not text:
            continue
        subs.append({"start": start, "end": end, "text": text})
    return subs


def detect_resolution(filename):
    name = filename.lower()
    return "4K" if re.search(r"2160p|4k", name) else "1080p"


def scale_crop(value, from_res, to_res):
    if from_res == to_res:
        return value
    if from_res == "1080p" and to_res == "4K":
        return value * 2
    if from_res == "4K" and to_res == "1080p":
        return value // 2
    return value


def ass_time(t):
    return t.replace(",", ".")


def build_ass_header(playres_x, playres_y):
    return f"""[Script Info]
Title: Bilingual Subtitle
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {playres_x}
PlayResY: {playres_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,5,1
Style: 中文SUB,微软雅黑,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,5,1
Style: 英文SUB,Calibri,45,&H0000A7E5,&H000000FF,&H00000000,&H00000000,-1,-1,0,0,100,100,0,0,1,0.5,0,8,10,10,10,1
Style: 中文SUB - 4K,微软雅黑,110,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,80,1
Style: 英文SUB - 4K,Calibri,80,&H0000A7E5,&H000000FF,&H00000000,&H00000000,-1,-1,0,0,100,100,0,0,1,0.5,0,8,10,10,5,1
Style: InfoSUB,Arial,50,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,10,10,0,1
Style: InfoSUB - 4K,Arial,100,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,10,10,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def self_check(lines):
    errors = []
    for i, line in enumerate(lines, 1):
        if not line.startswith("Dialogue"):
            continue
        if "\\N" in line or "\\n" in line:
            errors.append(f"第 {i} 行：含换行符")
    return errors


def resolve_subtitle_layout(crop_cfg, playres_x, playres_y, res):
    """按黑边高度 + 字号像素换算，计算字幕组信息与双语字幕位置"""
    mh = crop_cfg.get("source_height") or 1080
    top, bottom = scale_bars_to_playres(
        crop_cfg["top"], crop_cfg["bottom"], playres_y, mh
    )
    content_top = scale_y_to_playres(crop_cfg.get("top_line_y"), playres_y, mh)
    if content_top is None:
        content_top = top
    bottom_line = scale_y_to_playres(crop_cfg.get("bottom_line_y"), playres_y, mh)
    bottom_eff = playres_y - bottom_line if bottom_line is not None else bottom
    return layout_subtitles_from_crop(
        playres_x,
        playres_y,
        top,
        bottom_eff,
        res,
        content_top_y=content_top,
    )


def ass_output_name(ep, res, orig_srt_name=None, chi_srt_name=None):
    """生成 ASS 文件名，双语/仅中文均输出为 {集数}双语-{分辨率}.ass"""
    ref = orig_srt_name or chi_srt_name or ""
    if "原版" in ref:
        return ref.replace("原版", f"双语-{res}").replace(".srt", ".ass")
    if "中文版" in ref:
        return ref.replace("中文版", f"双语-{res}").replace(".srt", ".ass")
    stem = Path(ref).stem if ref else str(ep)
    if stem.endswith(".orig"):
        ep_id = extract_episode_id(ref) or stem.replace(".orig", "")
    elif stem.endswith(".chi"):
        ep_id = extract_episode_id(ref) or stem.replace(".chi", "")
    else:
        ep_id = extract_episode_id(ref) or ep or stem
    return f"{ep_id}双语-{res}.ass"


def _append_info_dialogues(ass_lines, style_info, cx, info_y, info_an):
    for start, end, text in INFO_DIALOGUES:
        ass_lines.append(
            f"Dialogue: 0,{start},{end},{style_info},,0,0,0,,"
            f"{{\\an{info_an}\\pos({cx},{info_y})\\c&H077DF6&}}{text}\n"
        )


def _append_bilingual_dialogues(ass_lines, zh, tr, cx, cn_pos, en_pos, style_cn, style_en):
    pair_count = max(len(zh), len(tr))
    for i in range(pair_count):
        z = zh[i] if i < len(zh) else None
        t = tr[i] if i < len(tr) else None
        if z and t:
            start, end = z["start"], z["end"]
        elif z:
            start, end = z["start"], z["end"]
        elif t:
            start, end = t["start"], t["end"]
        else:
            continue

        if z:
            line_text = flatten_subtitle_line(z["text"])
            ass_lines.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
                f"Default,,0,0,0,,"
                f"{{\\an2\\r{style_cn}\\pos({cx},{cn_pos})}}{line_text}\n"
            )
        if t:
            line_text = flatten_subtitle_line(t["text"])
            t_start = ass_time(start if z else t["start"])
            t_end = ass_time(end if z else t["end"])
            ass_lines.append(
                f"Dialogue: 0,{t_start},{t_end},"
                f"Default,,0,0,0,,"
                f"{{\\an2\\r{style_en}\\pos({cx},{en_pos})}}{line_text}\n"
            )


def _append_chinese_only_dialogues(ass_lines, zh, cx, cn_pos, style_cn):
    """仅中文版：位置与双语模式下的中文行一致（cn_pos）"""
    for z in zh:
        line_text = flatten_subtitle_line(z["text"])
        ass_lines.append(
            f"Dialogue: 0,{ass_time(z['start'])},{ass_time(z['end'])},"
            f"Default,,0,0,0,,"
            f"{{\\an2\\r{style_cn}\\pos({cx},{cn_pos})}}{line_text}\n"
        )


def find_ass_for_mkv(show_dir, mkv_path):
    """压制时查找对应 ASS（双语命名或旧版 mkv 同名）"""
    show_dir = Path(show_dir)
    mkv_path = Path(mkv_path)
    same_stem = show_dir / f"{mkv_path.stem}.ass"
    if same_stem.exists():
        return same_stem
    ep = extract_episode_id(mkv_path.name)
    if ep:
        matches = sorted(show_dir.glob(f"{ep}双语*.ass"))
        if matches:
            return matches[0]
    return None


def generate_ass(folder, crop_cfg, log):
    if not crop_cfg or "top" not in crop_cfg or "bottom" not in crop_cfg:
        log("❌ 缺少黑边数据，请先在「黑边测量」中测量")
        return 0

    folder = Path(folder)
    top_crop = crop_cfg["top"]
    bottom_crop = crop_cfg["bottom"]
    sample = crop_cfg.get("sample_video", "")
    extra = f"，样例={sample}" if sample else ""
    log(
        f"🚀 生成 ASS：{folder.name}（测量顶{top_crop} 底{bottom_crop}px"
        f"{extra}）"
    )

    pairs = list_subtitle_pairs(folder)
    if not pairs:
        originals = sorted(f for f in os.listdir(folder) if "原版" in f)
        chineses = sorted(f for f in os.listdir(folder) if "中文版" in f)
        pairs = [
            (o, c, extract_episode_id(o) or Path(o).stem.replace("原版", ""))
            for o, c in zip(originals, chineses)
        ]

    if not pairs:
        chineses = sorted(
            f.name
            for f in folder.iterdir()
            if f.is_file() and ("中文版" in f.name or f.name.endswith(".chi.srt"))
        )
        for c in chineses:
            ep = extract_episode_id(c) or Path(c).stem.replace("中文版", "").replace(".chi", "")
            pairs.append((None, c, ep))

    if not pairs:
        files = [f.name for f in folder.iterdir() if f.is_file()][:20]
        log(
            f"❌ 未找到字幕（需要 *中文版*.srt，"
            f"或 *原版*.srt + *中文版*.srt）：{folder.name}"
        )
        if files:
            log(f"   目录内文件示例：{', '.join(files)}")
        return 0

    ok = 0
    for o, c, ep in pairs:
        if not c:
            log(f"⚠️ 跳过（缺少中文版）：{ep}")
            continue

        chi_only = not o
        ref_name = o or c
        res = detect_resolution(ref_name)
        if res == "4K":
            playres_x, playres_y = 3840, 2160
            style_cn, style_en = "中文SUB - 4K", "英文SUB - 4K"
            style_info = "InfoSUB - 4K"
        else:
            playres_x, playres_y = 1920, 1080
            style_cn, style_en = "中文SUB", "英文SUB"
            style_info = "InfoSUB"

        layout = resolve_subtitle_layout(crop_cfg, playres_x, playres_y, res)
        cx = layout["cx"]
        cn_pos = layout["cn_pos"]
        en_pos = layout["en_pos"]
        info_y = layout["info_y"]
        info_an = layout["info_an"]
        info_mode = layout.get("info_mode", "")
        top_bar = layout.get("top_bar", 0)
        bottom_bar = layout.get("bottom_bar", 0)
        content_top = layout.get("content_top_y", top_bar)

        ensure_chinese_simplified(folder / c, log)
        zh, zh_trim = resolve_subtitle_overlaps(
            parse_srt(folder / c, is_chinese=True)
        )
        tr = []
        tr_trim = 0
        if not chi_only:
            tr, tr_trim = resolve_subtitle_overlaps(parse_srt(folder / o))

        if tr_trim or zh_trim:
            if chi_only:
                log(f"   {ep} 修正时间重叠：中文 {zh_trim} 条")
            else:
                log(
                    f"   {ep} 修正时间重叠：原版 {tr_trim} 条，中文 {zh_trim} 条"
                )
        if not chi_only and len(zh) != len(tr):
            log(
                f"⚠️ {ep} 中英条数不一致（{len(zh)} / {len(tr)}），"
                "按序号配对，以中文版时间为准"
            )
        if chi_only:
            cn_ref = CN_FONT_4K if res == "4K" else CN_FONT_REF
            cn_font_px = font_size_to_pixels(cn_ref, playres_y)
            cn_pos, cn_only_mode = calc_chinese_only_y(
                playres_y, bottom_bar, cn_font_px
            )
            log(
                f"ℹ️ {ep} 仅中文版：底黑边{bottom_bar}px，字号{cn_font_px}px"
                f" → [{cn_only_mode}] Y={cn_pos}"
            )

        ass_lines = [build_ass_header(playres_x, playres_y)]
        _append_info_dialogues(ass_lines, style_info, cx, info_y, info_an)

        if chi_only:
            _append_chinese_only_dialogues(ass_lines, zh, cx, cn_pos, style_cn)
        else:
            _append_bilingual_dialogues(
                ass_lines, zh, tr, cx, cn_pos, en_pos, style_cn, style_en
            )

        errors = self_check(ass_lines)
        if errors:
            log(f"❌ {c} 自检失败：")
            for err in errors:
                log(f"   {err}")
            continue

        out_name = ass_output_name(ep, res, orig_srt_name=o, chi_srt_name=c)
        out_path = folder / out_name
        with open(out_path, "w", encoding="utf-8") as f:
            f.writelines(ass_lines)

        if chi_only:
            log(
                f"✅ {out_name}（仅中文 {len(zh)} 条 | "
                f"信息[{info_mode}] Y={info_y} | "
                f"中文[{cn_only_mode}] Y={cn_pos}）"
            )
        else:
            log(
                f"✅ {out_name}（{len(zh)} 中 + {len(tr)} 原 | "
                f"信息[{info_mode}] Y={info_y} | "
                f"双语按底黑边{bottom_bar}px → 中文Y={cn_pos} 原版Y={en_pos}）"
            )
        ok += 1

    log(f"🎉 本剧完成，成功 {ok}/{len(pairs)} 个 ASS")
    return ok
