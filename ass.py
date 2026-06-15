# ass.py — ASS 生成（基于 pycdc 反编译复原）
import os
import re
import threading
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
    prepare_original_srt_for_ass,
    INFO_FONT_REF,
    calc_chinese_only_y,
    font_size_to_pixels,
    CN_FONT_REF,
    EN_FONT_REF,
    REF_PLAYRES_Y,
    playres_from_crop_cfg,
)


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
    """样式固定按 1080p 定义，字号/边距/描边随 PlayRes 同比缩放"""
    return (
        "[Script Info]\n"
        "Title: Bilingual Subtitle\n"
        "ScriptType: v4.00+\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n"
        f"PlayResX: {playres_x}\n"
        f"PlayResY: {playres_y}\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Default,Arial,50,"
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,2,8,10,10,5,1\n"
        "Style: 中文SUB,微软雅黑,60,"
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,2,2,8,10,10,5,1\n"
        "Style: 英文SUB,Calibri,45,"
        "&H0000A7E5,&H000000FF,&H00000000,&H00000000,"
        "-1,-1,0,0,100,100,0,0,1,0.5,0,8,10,10,10,1\n"
        "Style: InfoSUB,Arial,50,"
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,8,10,10,0,1\n"
        "Style: Watermark,Arial,1,"
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def self_check(lines):
    errors = []
    for i, line in enumerate(lines, 1):
        if not line.startswith("Dialogue"):
            continue
        if "\\N" in line or "\\n" in line:
            errors.append(f"第 {i} 行：含换行符")
    return errors


def resolve_subtitle_layout(crop_cfg, playres_x, playres_y):
    """按黑边高度 + 1080p 字号规则（随 PlayRes 同比缩放）计算字幕位置"""
    mh = 1080
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
    from config import INFO_DIALOGUES as _info
    for start, end, text in _info:
        ass_lines.append(
            f"Dialogue: 0,{start},{end},{style_info},,0,0,0,,"
            f"{{\\an{info_an}\\pos({cx},{info_y})\\c&H077DF6&}}{text}\n"
        )


def _append_watermark_dialogue(ass_lines, playres_x, playres_y):
    """如果水印配置启用且图片存在，追加水印 Dialogue 行"""
    from config import WATERMARK_CONFIG as wm
    if not wm.get("enabled"):
        return
    img_path = wm.get("image_path", "")
    if not img_path or not Path(img_path).is_file():
        return
    try:
        from watermark import generate_watermark_dialogue
        # 计算位置（负数表示从右下角偏移）
        x = wm.get("x", -20)
        y = wm.get("y", -20)
        if x < 0:
            x = playres_x + x  # 负数 = 从右往左偏
        if y < 0:
            y = playres_y + y  # 负数 = 从下往上偏
        dialogue, img_w, img_h = generate_watermark_dialogue(
            img_path,
            scale=wm.get("scale", 100),
            x=x, y=y,
            fade_in_ms=wm.get("fade_in_ms", 1000),
            fade_out_ms=wm.get("fade_out_ms", 1000),
            start_time=wm.get("start_time", "0:00:38.00"),
            end_time=wm.get("end_time", "1:00:00.00"),
        )
        ass_lines.append(dialogue)
    except Exception:
        pass  # 水印生成失败不影响字幕


def _append_bilingual_dialogues(ass_lines, zh, tr, cx, cn_pos, en_pos, style_cn, style_en):
    """中英字幕各自保留原时间轴，分别写入对应位置（不按序号配对）"""
    for z in zh:
        line_text = flatten_subtitle_line(z["text"])
        ass_lines.append(
            f"Dialogue: 0,{ass_time(z['start'])},{ass_time(z['end'])},"
            f"Default,,0,0,0,,"
            f"{{\\an2\\r{style_cn}\\pos({cx},{cn_pos})}}{line_text}\n"
        )
    for t in tr:
        line_text = flatten_subtitle_line(t["text"])
        ass_lines.append(
            f"Dialogue: 0,{ass_time(t['start'])},{ass_time(t['end'])},"
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


def collect_subtitle_pairs(folder):
    """收集文件夹内可生成 ASS 的字幕对（与 generate_ass 相同规则）"""
    folder = Path(folder)
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
    return pairs


def count_ass_jobs(show_dirs, is_measured):
    """统计可生成 ASS 的字幕文件数"""
    total = 0
    for sub in show_dirs:
        if not is_measured(sub.name):
            continue
        pairs = collect_subtitle_pairs(sub)
        total += sum(1 for _ in pairs)
    return total


def _srt_kind_suffixes(kind):
    if kind == "orig":
        return ("原版.srt", ".orig.srt")
    return ("中文版.srt", ".chi.srt")


def list_srt_files_for_delete(show_dir, kind):
    """列出某剧集文件夹内可删除的 srt（kind: orig | chi）"""
    show_dir = Path(show_dir)
    files = []
    for f in show_dir.iterdir():
        if not f.is_file() or not f.name.lower().endswith(".srt"):
            continue
        name = f.name
        if kind == "orig":
            if name.endswith("原版.srt") or name.endswith(".orig.srt"):
                files.append(f)
                continue
        if kind == "chi":
            if name.endswith("中文版.srt") or name.endswith(".chi.srt"):
                files.append(f)
                continue
    return sorted(files)


def collect_srt_delete_jobs(show_dirs, kind):
    jobs = []
    for show in show_dirs:
        for path in list_srt_files_for_delete(show, kind):
            jobs.append((show, path))
    return jobs


def batch_delete_srts(show_dirs, kind, log, on_progress=None, should_cancel=None):
    """批量删除指定类型 srt。返回删除数量"""
    label = "原版" if kind == "orig" else "中文版"
    jobs = collect_srt_delete_jobs(show_dirs, kind)
    total = len(jobs)
    deleted = 0
    for i, (show, path) in enumerate(jobs):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(i, total, show.name, f"删除 {path.name}")
        if log:
            log(f"[{i + 1}/{total}] 删除 {show.name}/{path.name}")
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            if log:
                log(f"❌ 删除失败：{path.name} | {e}")
    if on_progress:
        on_progress(total, total, "", f"{label} srt 完成")
    return deleted


def generate_ass(folder, crop_cfg, log, on_progress=None, progress_state=None, should_cancel=None):
    if not crop_cfg or "top" not in crop_cfg or "bottom" not in crop_cfg:
        log("❌ 缺少黑边数据，请先在「黑边测量」中测量")
        return 0

    folder = Path(folder)
    top_crop = crop_cfg["top"]
    bottom_crop = crop_cfg["bottom"]
    sample = crop_cfg.get("sample_video", "")
    extra = f"，样例={sample}" if sample else ""

    playres_x, playres_y, show_res = playres_from_crop_cfg(crop_cfg)
    style_cn, style_en, style_info = "中文SUB", "英文SUB", "InfoSUB"

    log(
        f"🚀 生成 ASS：{folder.name}（测量顶{top_crop} 底{bottom_crop}px"
        f"{extra} | PlayRes {playres_x}×{playres_y}"
        f"{'，1080p样式同比放大' if show_res == '4K' else ''}）"
    )

    pairs = collect_subtitle_pairs(folder)

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
        if should_cancel and should_cancel():
            log("⏹ 已停止生成 ASS")
            break
        if not c:
            log(f"⚠️ 跳过（缺少中文版）：{ep}")
            continue

        chi_only = not o
        ref_name = o or c

        layout = resolve_subtitle_layout(crop_cfg, playres_x, playres_y)
        cx = layout["cx"]
        cn_pos = layout["cn_pos"]
        en_pos = layout["en_pos"]
        info_y = layout["info_y"]
        info_an = layout["info_an"]
        info_mode = layout.get("info_mode", "")
        top_bar = layout.get("top_bar", 0)
        bottom_bar = layout.get("bottom_bar", 0)
        content_top = layout.get("content_top_y", top_bar)

        # 中文版：繁体转简体
        ensure_chinese_simplified(folder / c, log)

        # 解析中文 srt
        zh = parse_srt(folder / c, is_chinese=True)
        zh_trim = 0

        # 解析原版 srt（双语时）
        tr = []
        tr_trim = 0
        if not chi_only:
            tr = parse_srt(folder / o)
            # 原版跳过纯标点/空行
            tr = [t for t in tr if should_drop(t["text"]) is False or t["text"].strip()]

        if not chi_only and len(zh) != len(tr):
            log(
                f"⚠️ {ep} 中英条数不一致（{len(zh)} / {len(tr)}），"
                "已改用独立时间轴分别写入（不配对）"
            )
        if chi_only:
            cn_font_px = font_size_to_pixels(CN_FONT_REF, playres_y)
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

        # 水印
        _append_watermark_dialogue(ass_lines, playres_x, playres_y)

        errors = self_check(ass_lines)
        if errors:
            log(f"❌ {c} 自检失败：")
            for err in errors:
                log(f"   {err}")
            continue

        out_name = ass_output_name(ep, show_res, orig_srt_name=o, chi_srt_name=c)
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
                f"双语[{layout.get('bilingual_mode', '')}]"
                f"底黑边{bottom_bar}px → 中文Y={cn_pos} 原版Y={en_pos}）"
            )
        if on_progress:
            on_progress(ok, len(pairs), folder.name, f"{out_name}")
        ok += 1

    log(f"🎉 本剧完成，成功 {ok}/{len(pairs)} 个 ASS")
    return ok


def embed_ass_panel(parent, app):
    """生成 ASS 面板：目录选择、统计、生成 ASS、批量删除 srt、进度显示"""
    import tkinter as tk
    from tkinter import filedialog, messagebox

    from config import SUBTITLE_ROOT, is_measured, set_subtitle_root

    dlg_parent = app if app else parent.winfo_toplevel()
    state = {"root_path": app.subtitle_root if app else SUBTITLE_ROOT or ""}

    def log(msg):
        if app:
            app.log_msg(msg)

    def refresh_stats():
        path = app.subtitle_root if app else ""
        if not path or not Path(path).is_dir():
            stats_var.set("请先选择字幕根目录")
            return
        from utils import iter_show_dirs_with_mkv as _iter_shows

        shows = _iter_shows(Path(path))
        if not shows:
            stats_var.set("根目录下未找到包含 .mkv 的剧集文件夹")
            return
        measured = [s for s in shows if is_measured(s.name)]
        ass_total = count_ass_jobs(shows, is_measured)
        stats_var.set(
            f"{len(shows)} 部剧 · "
            f"已测量 {len(measured)} 部 · "
            f"可生成 {ass_total} 个 ASS"
        )

    # browse_root 已废弃（全局目录栏接管），保留桩函数避免 AttributeError
    def browse_root():
        pass

    def require_root():
        path = app.subtitle_root if app else ""
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先在顶部全局目录栏选择有效的字幕根目录", parent=dlg_parent)
            return None
        return Path(path)

    def run_generate_ass():
        if app and app._warn_if_busy(dlg_parent):
            return
        selected = require_root()
        if not selected:
            return

        do_fix = fix_var.get()  # 是否生成后自动转换格式

        from utils import iter_show_dirs_with_mkv as _iter_shows

        shows = _iter_shows(selected)
        if not shows:
            log(f"❌ 未找到包含 .mkv 的文件夹：{selected}")
            return

        job_total = count_ass_jobs(shows, is_measured)
        if job_total == 0:
            log("❌ 没有可生成 ASS 的字幕文件")
            refresh_stats()
            return

        unmeasured = [s.name for s in shows if not is_measured(s.name)]
        if unmeasured:
            log("⚠️ 以下剧集未测量黑边，将跳过：")
            for name in unmeasured:
                log(f"   {name}")
            measured_shows = [s for s in shows if is_measured(s.name)]
            if not measured_shows:
                log("❌ 没有已测量黑边的剧集，请在「黑边测量」中先测量")
                return
            shows = measured_shows

        if not messagebox.askyesno(
            "批量生成 ASS",
            f"将对 {len(shows)} 部剧中的 {job_total} 个字幕生成 ASS。\n"
            "未测量黑边的剧集将被跳过。\n"
            + ("生成后自动转换 Aegisub 格式（修复重叠）。\n" if do_fix else "")
            + "\n是否继续？",
            parent=dlg_parent,
        ):
            return

        set_subtitle_root(selected)
        if app:
            app.subtitle_root = str(selected)
            try:
                app.notebook.select(app.notebook.index("end") - 1)
            except Exception:
                pass
            app._generating_ass = True
            app._begin_batch_task(f"准备生成 {job_total} 个 ASS…")
        log(f"\n━━━ 批量生成 ASS：{len(shows)} 部剧 {job_total} 个字幕 ━━━")

        def worker():
            from config import get_crop_for_folder

            ok = 0
            fail = 0
            try:
                for show in shows:
                    if app and app._task_cancel.is_set():
                        break
                    cfg = get_crop_for_folder(show)
                    if not cfg:
                        if app:
                            app._log_threadsafe(f"⏭️ 跳过（无黑边配置）：{show.name}")
                        continue
                    r = generate_ass(
                        show,
                        cfg,
                        log=app._log_threadsafe if app else log,
                        on_progress=app._progress_callback if app else None,
                        progress_state=None,
                        should_cancel=app._task_cancel.is_set if app else None,
                    )
                    if r > 0:
                        ok += r
                    else:
                        fail += 1
                if app and app._task_cancel.is_set():
                    summary = f"生成 ASS 已停止：成功 {ok} 个，失败 {fail}"
                else:
                    summary = f"生成 ASS 完成：成功 {ok} 个，失败 {fail}"
                if app:
                    app._log_threadsafe(f"\n🎉 {summary}")

                # ---- 生成完毕后自动执行格式转换（修复字幕重叠） ----
                if do_fix and not (app and app._task_cancel.is_set()):
                    try:
                        from fix_ass import fix_ass_file, find_ass_files
                        ass_files = find_ass_files(str(selected))
                        if ass_files:
                            fixed = 0
                            for f in ass_files:
                                if app._task_cancel.is_set():
                                    break
                                if fix_ass_file(f):
                                    fixed += 1
                            app._log_threadsafe(
                                f"🔄 格式转换完成：{fixed}/{len(ass_files)} 个 ASS 已转 Aegisub 格式"
                            )
                    except Exception as fix_e:
                        app._log_threadsafe(f"⚠️ 格式转换步骤异常：{fix_e}")

                if app:
                    app.after(0, lambda s=summary: app._finish_batch_task(100, s))
                    app.after(0, refresh_stats)
                else:
                    log(f"\n🎉 {summary}")
            except Exception as e:
                err_msg = f"❌ 生成 ASS 异常：{e}"
                if app:
                    app._log_threadsafe(err_msg)
                    app.after(0, lambda err=err_msg: app._finish_batch_task(0, f"出错：{err}"))
                else:
                    log(err_msg)
            finally:
                if app:
                    app._generating_ass = False

        threading.Thread(target=worker, daemon=True).start()

    def _run_delete_srt(kind):
        if app and app._warn_if_busy(dlg_parent):
            return
        selected = require_root()
        if not selected:
            return

        from utils import iter_show_dirs_with_mkv as _iter_shows

        shows = _iter_shows(selected)
        if not shows:
            log(f"❌ 未找到包含 .mkv 的文件夹：{selected}")
            return

        jobs = collect_srt_delete_jobs(shows, kind)
        if not jobs:
            label = "原版" if kind == "orig" else "中文版"
            log(f"❌ 未找到可删除的 {label} srt 文件")
            refresh_stats()
            return

        label = "原版" if kind == "orig" else "中文版"
        if not messagebox.askyesno(
            f"批量删除{label} SRT",
            f"将删除 {len(jobs)} 个 {label} srt 文件。\n\n删除后不可恢复，是否继续？",
            parent=dlg_parent,
        ):
            return

        set_subtitle_root(selected)
        if app:
            app.subtitle_root = str(selected)
            try:
                app.notebook.select(app.notebook.index("end") - 1)
            except Exception:
                pass
            app._deleting_srt = True
            app._begin_batch_task(f"准备删除 {len(jobs)} 个 {label} srt…")
        log(f"\n━━━ 批量删除{label} srt ({len(jobs)} 个)━━━")

        def worker():
            try:
                n = batch_delete_srts(
                    shows,
                    kind,
                    log=app._log_threadsafe if app else log,
                    on_progress=app._progress_callback if app else None,
                    should_cancel=app._task_cancel.is_set if app else None,
                )
                if app and app._task_cancel.is_set():
                    summary = f"删除{label} srt 已停止：已完成 {n} 个"
                else:
                    summary = f"删除{label} srt 完成：共删除 {n} 个"
                if app:
                    app._log_threadsafe(f"\n🎉 {summary}")
                    app.after(0, lambda s=summary: app._finish_batch_task(100, s))
                    app.after(0, refresh_stats)
                else:
                    log(f"\n🎉 {summary}")
            except Exception as e:
                err_msg = f"❌ 删除{label} srt 异常：{e}"
                if app:
                    app._log_threadsafe(err_msg)
                    app.after(0, lambda err=err_msg: app._finish_batch_task(0, f"出错：{err}"))
                else:
                    log(err_msg)
            finally:
                if app:
                    app._deleting_srt = False

        threading.Thread(target=worker, daemon=True).start()

    def run_delete_orig():
        _run_delete_srt("orig")

    def run_delete_chi():
        _run_delete_srt("chi")

    def run_fix_ass_solo():
        """单独执行 ASS 格式转换"""
        if app and app._warn_if_busy(dlg_parent):
            return
        selected = require_root()
        if not selected:
            return

        from fix_ass import find_ass_files, batch_fix_ass_files

        files = find_ass_files(str(selected))
        if not files:
            log("❌ 未找到任何 .ass 文件")
            return

        if not messagebox.askyesno(
            "转换 ASS 格式",
            f"将用 pysubs2 处理 {len(files)} 个 .ass 文件，\n"
            "效果等同 Aegisub 打开再保存。\n\n是否继续？",
            parent=dlg_parent,
        ):
            return

        app._generating_ass = True
        app._begin_batch_task(f"转换 {len(files)} 个 ASS 格式…")

        def worker():
            try:
                ok, fail = batch_fix_ass_files(
                    str(selected),
                    log=app._log_threadsafe,
                    on_progress=app._progress_callback,
                    should_cancel=app._task_cancel.is_set if app else None,
                )
                cancelled = app._task_cancel.is_set()
                summary = f"已停止：成功 {ok} 个，失败 {fail}" if cancelled else f"完成：成功 {ok} 个，失败 {fail}"
                app._log_threadsafe(f"\n🎉 转换 ASS 格式{summary}")
                app.after(0, lambda: app._finish_batch_task(100, summary))
            except Exception as e:
                err = f"❌ 转换异常：{e}"
                app._log_threadsafe(err)
                app.after(0, lambda: app._finish_batch_task(0, f"出错：{err}"))
            finally:
                app._generating_ass = False

        threading.Thread(target=worker, daemon=True).start()

    # ========== UI 控件 ==========

    # 说明
    tk.Label(
        parent,
        text="对各剧集内已提取的字幕（原版+中文版 SRT）生成双语 ASS。需先在「黑边测量」中完成测量。",
        fg="#7f8c8d", font=("微软雅黑", 9),
        justify=tk.LEFT,
    ).pack(anchor="w", padx=10, pady=(0, 4))

    # 统计信息
    stats_var = tk.StringVar(value="")
    tk.Label(parent, textvariable=stats_var, anchor="w", fg="#2d6cc9",
             font=("微软雅黑", 10, "bold")).pack(
        fill=tk.X, padx=10, pady=(0, 8)
    )

    # 生成后自动转换 Aegisub 格式（复选框）
    fix_var = tk.BooleanVar(value=True)
    fix_cb = tk.Frame(parent)
    fix_cb.pack(fill=tk.X, padx=10, pady=(0, 4))
    tk.Checkbutton(
        fix_cb, text="生成后自动转换为 Aegisub 格式（修复字幕重叠）",
        variable=fix_var, font=("微软雅黑", 9),
        fg="#27ae60", selectcolor="#ffffff",
        activebackground="#f5f6fa",
    ).pack(side=tk.LEFT)

    # 字幕组信息（内联编辑区）
    info_label_frame = tk.LabelFrame(parent, text="字幕组信息（时间 → 结束 → 文字）",
                                     font=("微软雅黑", 9, "bold"),
                                     fg="#2c3e50", bg="#ffffff",
                                     relief="solid", bd=1, padx=8, pady=4)
    info_label_frame.pack(fill=tk.X, padx=10, pady=(4, 2))

    info_text = tk.Text(info_label_frame, font=("Consolas", 10), bg="#f9faff", fg="#2c3e50",
                        relief="flat", bd=0, padx=6, pady=4, height=5)
    info_text.pack(fill=tk.X)

    # 加载当前字幕组信息到文本框
    from config import INFO_DIALOGUES as cur_info, DEFAULT_INFO_DIALOGUES, save_config, load_config
    def _reload_info_text():
        info_text.delete("1.0", tk.END)
        load_config()
        from config import INFO_DIALOGUES as reloaded
        for start, end, text in reloaded:
            info_text.insert(tk.END, f"{start} → {end} → {text}\n")
    _reload_info_text()

    info_btn_row = tk.Frame(info_label_frame, bg="#ffffff")
    info_btn_row.pack(fill=tk.X, pady=(2, 0))

    def _save_info_inline():
        raw = info_text.get("1.0", tk.END).strip()
        new_list = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(" → ", 2)
            if len(parts) != 3:
                log(f"⚠️ 字幕组信息格式跳过：{line}")
                continue
            new_list.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
        if not new_list:
            log("⚠️ 字幕组信息为空，使用默认")
            new_list = list(DEFAULT_INFO_DIALOGUES)
        from config import INFO_DIALOGUES as info_var
        info_var.clear()
        info_var.extend(new_list)
        save_config()
        log(f"✅ 字幕组信息已保存（{len(new_list)} 条）")

    def _reset_info_inline():
        info_text.delete("1.0", tk.END)
        for start, end, text in DEFAULT_INFO_DIALOGUES:
            info_text.insert(tk.END, f"{start} → {end} → {text}\n")
        _save_info_inline()

    tk.Button(info_btn_row, text="恢复默认", command=_reset_info_inline,
              font=("微软雅黑", 8), fg="#7f8c8d", bg="#e0e3eb",
              activebackground="#bdc3c7", bd=0, padx=8, pady=1, cursor="hand2").pack(side=tk.LEFT)
    tk.Label(info_btn_row, text="  ", bg="#ffffff").pack(side=tk.LEFT)
    tk.Button(info_btn_row, text="立即保存", command=_save_info_inline,
              font=("微软雅黑", 8, "bold"), fg="white", bg="#27ae60",
              activebackground="#1e8449", bd=0, padx=10, pady=1, cursor="hand2").pack(side=tk.LEFT)
    tk.Label(info_btn_row, text="  修改后需点保存，下次生成ASS时生效", bg="#ffffff",
             font=("微软雅黑", 8), fg="#bdc3c7").pack(side=tk.LEFT)

    # ---- 图片水印配置 ----
    from config import WATERMARK_CONFIG as wm_cfg, DEFAULT_WATERMARK as wm_default
    wm_enabled_var = tk.BooleanVar(value=wm_cfg.get("enabled", False))
    wm_image_var = tk.StringVar(value=wm_cfg.get("image_path", ""))
    wm_scale_var = tk.StringVar(value=str(wm_cfg.get("scale", 100)))
    wm_x_var = tk.StringVar(value=str(wm_cfg.get("x", -20)))
    wm_y_var = tk.StringVar(value=str(wm_cfg.get("y", -20)))
    wm_fadein_var = tk.StringVar(value=str(wm_cfg.get("fade_in_ms", 1000)))
    wm_fadeout_var = tk.StringVar(value=str(wm_cfg.get("fade_out_ms", 1000)))
    wm_start_var = tk.StringVar(value=wm_cfg.get("start_time", "0:00:38.00"))
    wm_end_var = tk.StringVar(value=wm_cfg.get("end_time", "1:00:00.00"))

    wm_frame = tk.LabelFrame(parent, text="图片水印（可选）",
                             font=("微软雅黑", 9, "bold"),
                             fg="#2c3e50", bg="#ffffff",
                             relief="solid", bd=1, padx=8, pady=4)
    wm_frame.pack(fill=tk.X, padx=10, pady=(4, 2))

    # 第一行：启用 + 图片路径
    wm_row1 = tk.Frame(wm_frame, bg="#ffffff")
    wm_row1.pack(fill=tk.X, pady=(0, 2))
    tk.Checkbutton(wm_row1, text="启用水印", variable=wm_enabled_var,
                   font=("微软雅黑", 9), fg="#27ae60", selectcolor="#ffffff",
                   activebackground="#ffffff").pack(side=tk.LEFT)
    tk.Label(wm_row1, text="  图片：", font=("微软雅黑", 9), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row1, textvariable=wm_image_var, font=("Consolas", 9),
             fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))

    def _browse_wm_image():
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif")])
        if p:
            wm_image_var.set(p)
    tk.Button(wm_row1, text="浏览", command=_browse_wm_image,
              font=("微软雅黑", 8), fg="#2d6cc9", bg="#eaf1fd",
              activebackground="#2d6cc9", activeforeground="white",
              bd=0, padx=6, cursor="hand2").pack(side=tk.LEFT)

    # 第二行：缩放 + 位置
    wm_row2 = tk.Frame(wm_frame, bg="#ffffff")
    wm_row2.pack(fill=tk.X, pady=(0, 2))
    tk.Label(wm_row2, text="缩放%", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row2, textvariable=wm_scale_var, width=5,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(wm_row2, text="X", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row2, textvariable=wm_x_var, width=6,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(wm_row2, text="Y", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row2, textvariable=wm_y_var, width=6,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(wm_row2, text="渐显ms", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row2, textvariable=wm_fadein_var, width=5,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(wm_row2, text="渐隐ms", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row2, textvariable=wm_fadeout_var, width=5,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 0))

    # 第三行：时间范围 + 保存按钮
    wm_row3 = tk.Frame(wm_frame, bg="#ffffff")
    wm_row3.pack(fill=tk.X)
    tk.Label(wm_row3, text="起始", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row3, textvariable=wm_start_var, width=12,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))
    tk.Label(wm_row3, text="结束", font=("微软雅黑", 8), fg="#7f8c8d", bg="#ffffff").pack(side=tk.LEFT)
    tk.Entry(wm_row3, textvariable=wm_end_var, width=12,
             font=("Consolas", 8), fg="#2c3e50", bg="#f9faff", relief="solid", bd=1).pack(side=tk.LEFT, padx=(2, 8))

    def _save_wm_inline():
        from config import WATERMARK_CONFIG as wm_var, save_config
        wm_var["enabled"] = wm_enabled_var.get()
        wm_var["image_path"] = wm_image_var.get()
        try: wm_var["scale"] = int(wm_scale_var.get())
        except: wm_var["scale"] = 100
        try: wm_var["x"] = int(wm_x_var.get())
        except: wm_var["x"] = -20
        try: wm_var["y"] = int(wm_y_var.get())
        except: wm_var["y"] = -20
        try: wm_var["fade_in_ms"] = int(wm_fadein_var.get())
        except: wm_var["fade_in_ms"] = 1000
        try: wm_var["fade_out_ms"] = int(wm_fadeout_var.get())
        except: wm_var["fade_out_ms"] = 1000
        wm_var["start_time"] = wm_start_var.get()
        wm_var["end_time"] = wm_end_var.get()
        save_config()
        log(f"✅ 水印配置已保存")

    def _reset_wm_inline():
        wm_enabled_var.set(wm_default["enabled"])
        wm_image_var.set(wm_default["image_path"])
        wm_scale_var.set(str(wm_default["scale"]))
        wm_x_var.set(str(wm_default["x"]))
        wm_y_var.set(str(wm_default["y"]))
        wm_fadein_var.set(str(wm_default["fade_in_ms"]))
        wm_fadeout_var.set(str(wm_default["fade_out_ms"]))
        wm_start_var.set(wm_default["start_time"])
        wm_end_var.set(wm_default["end_time"])
        _save_wm_inline()

    tk.Button(wm_row3, text="恢复默认", command=_reset_wm_inline,
              font=("微软雅黑", 8), fg="#7f8c8d", bg="#e0e3eb",
              activebackground="#bdc3c7", bd=0, padx=6, cursor="hand2").pack(side=tk.RIGHT)
    tk.Button(wm_row3, text="保存", command=_save_wm_inline,
              font=("微软雅黑", 8, "bold"), fg="white", bg="#27ae60",
              activebackground="#1e8449", bd=0, padx=8, cursor="hand2").pack(side=tk.RIGHT, padx=(0, 4))

    # 提示栏（进度/状态）
    status_var = tk.StringVar(value="")
    tk.Label(parent, textvariable=status_var, anchor="w", fg="#7f8c8d",
             font=("微软雅黑", 9)).pack(
        fill=tk.X, padx=10, pady=(0, 4)
    )

    # 按钮行
    btn_row = tk.Frame(parent)
    btn_row.pack(fill=tk.X, padx=10, pady=6)

    _ass_btn_style = dict(font=("微软雅黑", 10, "bold"), fg="white", bg="#2d6cc9",
                          activebackground="#1a4f8a", activeforeground="white",
                          bd=0, padx=16, pady=4, cursor="hand2")

    tk.Button(btn_row, text="生成 ASS", command=run_generate_ass, width=14, **_ass_btn_style).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(btn_row, text="删除原版 srt", command=run_delete_orig, width=14, **_ass_btn_style).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(btn_row, text="删除中文版 srt", command=run_delete_chi, width=14, **_ass_btn_style).pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(btn_row, text="转换ASS格式", command=run_fix_ass_solo, width=14, **_ass_btn_style).pack(
        side=tk.LEFT
    )

    # 初始统计刷新
    if app and app.subtitle_root:
        parent.after(200, refresh_stats)

    return parent


def show_ass_tab(app):
    if app:
        if hasattr(app, "_ass_tab"):
            app.notebook.select(app._ass_tab)
            return None
        return None
