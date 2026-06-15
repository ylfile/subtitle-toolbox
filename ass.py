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
    for start, end, text in INFO_DIALOGUES:
        ass_lines.append(
            f"Dialogue: 0,{start},{end},{style_info},,0,0,0,,"
            f"{{\\an{info_an}\\pos({cx},{info_y})\\c&H077DF6&}}{text}\n"
        )


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
        if not is_measured(str(sub)):
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
    state = {"root_path": SUBTITLE_ROOT or (app.subtitle_root if app else "") or ""}

    def log(msg):
        if app:
            app.log_msg(msg)

    def refresh_stats():
        path = state["root_path"].strip()
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

    def browse_root():
        path = filedialog.askdirectory(
            initialdir=state["root_path"] or None,
            parent=dlg_parent,
        )
        if not path:
            return
        state["root_path"] = path
        root_var.set(path)
        set_subtitle_root(path)
        if app:
            app.subtitle_root = path
        refresh_stats()
        log(f"📁 字幕根目录：{path}")

    def require_root():
        path = root_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先选择有效的字幕根目录", parent=dlg_parent)
            return None
        state["root_path"] = path
        return Path(path)

    def run_generate_ass():
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
            "未测量黑边的剧集将被跳过。\n\n是否继续？",
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

    # ========== UI 控件 ==========

    # 第一行：根目录选择
    root_row = tk.Frame(parent)
    root_row.pack(fill=tk.X, padx=10, pady=8)
    tk.Label(root_row, text="字幕根目录：", font=("微软雅黑", 10), fg="#7f8c8d").pack(side=tk.LEFT)
    root_var = tk.StringVar(value=state["root_path"])
    tk.Entry(root_row, textvariable=root_var, font=("微软雅黑", 10), fg="#2c3e50",
             relief="solid", bd=1, highlightcolor="#2d6cc9", highlightthickness=1,
             bg="#f9faff").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=3)
    tk.Button(root_row, text="浏览", command=browse_root,
              font=("微软雅黑", 9), fg="#2d6cc9", bg="#eaf1fd",
              activebackground="#2d6cc9", activeforeground="white",
              bd=0, padx=14, pady=2, cursor="hand2").pack(side=tk.LEFT)

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
        side=tk.LEFT
    )

    # 初始统计刷新
    if state["root_path"]:
        refresh_stats()

    return parent


def show_ass_tab(app):
    if app:
        if hasattr(app, "_ass_tab"):
            app.notebook.select(app._ass_tab)
            return None
        return None
