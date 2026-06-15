# crop.py — 黑边测量：仅选根目录，子文件夹=剧集，测一次算 ASS 坐标
import re
import threading
from pathlib import Path

import cv2
import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox
from PIL import Image, ImageTk

from config import (
    APP_NAME,
    load_config,
    is_measured,
    get_crop,
    save_crop,
    set_subtitle_root,
    SUBTITLE_ROOT,
)
from utils import (
    show_name_from_video,
    layout_subtitles_from_crop,
    measure_base_resolution,
    calc_symmetric_bar_heights,
    iter_show_dirs_with_mkv,
    extract_episode_id,
)

_AUTO_ROW_FILL = 0.012
_AUTO_LUM_THRESH = 22
_AUTO_SAMPLE_FRACS = (0.15, 0.3, 0.5, 0.7, 0.85)


def list_show_folders(root_path):
    """根目录下每个子文件夹视为一部剧"""
    root = Path(root_path)
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def pick_sample_mkv(show_dir):
    """每部剧选一个样例 mkv（优先 S01E01）"""
    show_dir = Path(show_dir)
    mkvs = sorted(show_dir.glob("*.mkv"))
    if not mkvs:
        return None
    for mkv in mkvs:
        if extract_episode_id(mkv.name) == "S01E01":
            return mkv
    for mkv in mkvs:
        if re.search("S01E01", mkv.name, re.I):
            return mkv
    return mkvs[0]


def detect_content_bounds(max_gray, lum_thresh=_AUTO_LUM_THRESH, row_fill=_AUTO_ROW_FILL):
    """在多帧最大亮度投影上检测画面上、下沿（像素行号）"""
    h, w = max_gray.shape
    min_bright = max(1, int(w * row_fill))
    top_line = 0
    for y in range(h):
        if np.count_nonzero(max_gray[y] > lum_thresh) >= min_bright:
            top_line = y
            break
    bottom_line = h - 1
    for y in range(h - 1, -1, -1):
        if np.count_nonzero(max_gray[y] > lum_thresh) >= min_bright:
            bottom_line = y
            break
    if bottom_line <= top_line:
        raise ValueError("未检测到有效画面（可能全黑或无明显黑边）")
    return top_line, bottom_line


def read_video_max_projection(video_path, sample_fracs=_AUTO_SAMPLE_FRACS):
    """读取多帧并取逐像素最大亮度，便于在暗场中仍识别黑边"""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频：{video_path}")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        max_gray = np.zeros((height, width), dtype=np.float32)
        for frac in sample_fracs:
            pos = int(total * frac)
            if pos >= total:
                pos = total - 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if not ret:
                continue
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
            max_gray = np.maximum(max_gray, gray)
        return max_gray, width, height
    finally:
        cap.release()


def apply_crop_measurement(show_name, video_path, img_width, img_height, top_line_y, bottom_line_y, log=None):
    """根据上下沿保存黑边配置"""
    top_bar, bottom_bar, content_h = calc_symmetric_bar_heights(
        img_height, top_line_y, bottom_line_y
    )
    layout = layout_subtitles_from_crop(img_width, img_height, top_bar, bottom_bar)
    save_crop(
        show_name,
        top_bar,
        bottom_bar,
        img_width,
        img_height,
        sample_video=Path(video_path).name,
        cn_pos=layout["cn_pos"],
        en_pos=layout["en_pos"],
        info_y=layout["info_y"],
        content_height=content_h,
        top_line_y=top_line_y,
        bottom_line_y=bottom_line_y,
    )
    if log:
        log(
            f"✅ {show_name} | {Path(video_path).name} | "
            f"{img_width}×{img_height} | "
            f"黑边各{top_bar}px | 有效画面{content_h}px | "
            f"中文Y={layout['cn_pos']} 原版Y={layout['en_pos']}"
        )
    return top_bar, bottom_bar, content_h, layout


def auto_measure_video(video_path, show_name, log=None):
    """自动检测单个视频黑边并写入 config"""
    max_gray, width, height = read_video_max_projection(video_path)
    top_line, bottom_line = detect_content_bounds(max_gray)
    return apply_crop_measurement(show_name, video_path, width, height, top_line, bottom_line, log=log)


def batch_auto_measure(root_path, log, skip_measured, on_progress, should_cancel=None):
    """
    批量自动测量根目录下各部剧（每剧一个样例 mkv）。
    返回 (成功, 失败, 跳过)
    """
    root_path = Path(root_path)
    shows = iter_show_dirs_with_mkv(root_path)
    if not shows:
        return 0, 0, 0
    ok = 0
    fail = 0
    skip = 0
    total = len(shows)
    for i, show_dir in enumerate(shows):
        if should_cancel and should_cancel():
            break
        name = show_dir.name
        if on_progress:
            on_progress(i, total, name, "准备…")
        if skip_measured and is_measured(name):
            skip += 1
            if log:
                log(f"⏭️ 跳过已测量：{name}")
            continue
        mkv = pick_sample_mkv(show_dir)
        if not mkv:
            fail += 1
            if log:
                log(f"❌ {name}：未找到 mkv")
            continue
        if on_progress:
            on_progress(i, total, name, f"分析 {mkv.name}")
        try:
            auto_measure_video(mkv, name, log=log)
            ok += 1
        except Exception as e:
            fail += 1
            if log:
                log(f"❌ {name} | {mkv.name} | {e}")
    if on_progress:
        on_progress(total, total, "", "完成")
    return ok, fail, skip


def embed_crop_panel(parent, app):
    """在主窗口内构建黑边测量面板（不再弹出独立窗口）"""
    frame = tk.Frame(parent)

    state = {
        "root_path": SUBTITLE_ROOT or "",
        "video_path": "",
        "img_width": 0,
        "img_height": 0,
        "click_count": 0,
        "photo": None,
        "allow_measure": True,
        "current_show": "",
    }

    # ---------- 内部函数（定义在 UI 前） ----------

    def log(msg):
        if app and hasattr(app, "log_msg"):
            app.log_msg(msg)

    def update_pixel_label():
        w, h = state["img_width"], state["img_height"]
        if w and h:
            pixel_label.config(text=f"画面像素：{w} × {h}")
        else:
            pixel_label.config(text="画面像素：—")

    def update_pos_preview(top_px, bottom_px):
        w, h = state["img_width"], state["img_height"]
        if not w or not h:
            cn_pos_label.config(text="—")
            en_pos_label.config(text="—")
            return None
        res = measure_base_resolution(h)
        layout = layout_subtitles_from_crop(w, h, top_px, bottom_px, res)
        cn_pos_label.config(text=f"{layout['cn_pos']} px")
        en_pos_label.config(text=f"{layout['en_pos']} px")
        return layout

    def apply_measured_state(show_name):
        state["current_show"] = show_name
        show_label.config(text=f"当前剧集：{show_name}")
        if is_measured(show_name):
            cfg = get_crop(show_name)
            bar_label.config(text=f"{cfg['top']} px")
            ch = cfg.get("content_height")
            content_label.config(text=f"{ch} px" if ch else "—")
            w = cfg.get("source_width", 0)
            h = cfg.get("source_height", 0)
            if w and h:
                state["img_width"], state["img_height"] = w, h
                update_pixel_label()
            cn_pos_label.config(text=f"{cfg.get('cn_pos', '—')} px")
            en_pos_label.config(text=f"{cfg.get('en_pos', '—')} px")
            state["allow_measure"] = False
            status_var.set(
                f"✅「{show_name}」已测量 | 黑边各 {cfg['top']}px（对称）| "
                f"有效画面 {ch}px | "
                f"信息Y={cfg.get('info_y')} 中文Y={cfg.get('cn_pos')} 原版Y={cfg.get('en_pos')}"
            )
        else:
            bar_label.config(text="—")
            content_label.config(text="—")
            cn_pos_label.config(text="—")
            en_pos_label.config(text="—")
            state["allow_measure"] = True

    def browse_root():
        path = filedialog.askdirectory(initialdir=state["root_path"] or None)
        if not path:
            return
        state["root_path"] = path
        root_var.set(path)
        set_subtitle_root(path)
        if app:
            app.subtitle_root = path
        shows = list_show_folders(path)
        log(f"\U0001f4c1 根目录：{path}（{len(shows)} 部剧）")
        status_var.set(
            f"已选根目录，共 {len(shows)} 个子文件夹。"
            f"请点「选择视频」进入某剧文件夹选一个 mkv 进行测量。"
        )

    def load_video():
        if not state["root_path"]:
            messagebox.showwarning("提示", "请先选择字幕根目录", parent=frame)
            return
        path = filedialog.askopenfilename(
            initialdir=state["root_path"],
            filetypes=[("视频文件", "*.mp4 *.mkv *.avi *.mov *.ts")],
        )
        if not path:
            return
        show = show_name_from_video(state["root_path"], path)
        state["video_path"] = path
        apply_measured_state(show)
        if not is_measured(show):
            status_var.set(
                f"\U0001f4f9 {show} / {Path(path).name}\n"
                f"点击「读取视频并测量」加载画面后，点两次标记黑边。"
            )

    def read_video_pixels():
        if not state["video_path"]:
            messagebox.showwarning("提示", "请先选择视频", parent=frame)
            return False
        cap = cv2.VideoCapture(state["video_path"])
        state["img_width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        state["img_height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        update_pixel_label()
        return state["img_width"] > 0 and state["img_height"] > 0

    def remeasure():
        state["allow_measure"] = True
        state["click_count"] = 0
        bar_label.config(text="—")
        content_label.config(text="—")
        cn_pos_label.config(text="—")
        en_pos_label.config(text="—")

    def measure_crop():
        if not state["root_path"]:
            messagebox.showwarning("提示", "请先选择根目录", parent=frame)
            return
        if not state["video_path"]:
            messagebox.showwarning("提示", "请先选择视频", parent=frame)
            return

        show = show_name_from_video(state["root_path"], state["video_path"])
        if is_measured(show) and not state["allow_measure"]:
            if not messagebox.askyesno(
                "已测量", f"「{show}」已有记录，是否重新测量？", parent=frame
            ):
                return
            remeasure()

        try:
            cap = cv2.VideoCapture(state["video_path"])
            state["img_width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            state["img_height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
            ret, frame = cap.read()
            cap.release()

            if not ret:
                messagebox.showerror("错误", "无法读取视频帧", parent=frame)
                return

            update_pixel_label()
            state["current_show"] = show
            show_label.config(text=f"当前剧集：{show}")

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame)
            img.thumbnail((950, 500))
            state["photo"] = ImageTk.PhotoImage(img)

            canvas.delete("all")
            canvas.update_idletasks()
            cw = max(canvas.winfo_width(), 950)
            ch = max(canvas.winfo_height(), 500)
            canvas.create_image(cw // 2, ch // 2, image=state["photo"])

            state["click_count"] = 0
            bar_label.config(text="—")
            content_label.config(text="—")
            cn_pos_label.config(text="—")
            en_pos_label.config(text="—")

            log(f"\U0001f4d0 {show} 画面 {state['img_width']}×{state['img_height']}")
            messagebox.showinfo(
                "提示",
                f"画面：{state['img_width']}×{state['img_height']}\n"
                "1. 点击顶黑边下沿\n"
                "2. 点击底黑边上沿\n"
                "黑边高度 = (画面高 - 两线间距) ÷ 2（上下相同）",
                parent=frame,
            )
        except Exception as e:
            messagebox.showerror("错误", str(e), parent=frame)

    def on_click(event):
        if not state.get("photo") or not state["allow_measure"]:
            return

        photo = state["photo"]
        canvas_w = canvas.winfo_width()
        canvas_h = canvas.winfo_height()
        img_w, img_h = photo.width(), photo.height()
        img_x = (canvas_w - img_w) // 2
        img_y = (canvas_h - img_h) // 2

        if not (img_x <= event.x <= img_x + img_w and img_y <= event.y <= img_y + img_h):
            return

        scale = state["img_height"] / img_h
        video_y = int((event.y - img_y) * scale)
        state["click_count"] += 1

        if state["click_count"] == 1:
            state["top_line_y"] = video_y
            bar_label.config(text="…")
            content_label.config(text="…")
            canvas.create_line(img_x, event.y, img_x + img_w, event.y, fill="red", width=2)
        elif state["click_count"] == 2:
            canvas.create_line(img_x, event.y, img_x + img_w, event.y, fill="blue", width=2)
            try:
                top_bar, bottom_bar, content_h = calc_symmetric_bar_heights(
                    state["img_height"], state["top_line_y"], video_y
                )
            except ValueError as e:
                messagebox.showwarning("提示", str(e), parent=frame)
                state["click_count"] = 0
                return

            bar_label.config(text=f"{top_bar} px")
            content_label.config(text=f"{content_h} px")

            show = show_name_from_video(state["root_path"], state["video_path"])
            layout = update_pos_preview(top_bar, bottom_bar)

            save_crop(
                show,
                top_bar,
                bottom_bar,
                state["img_width"],
                state["img_height"],
                sample_video=Path(state["video_path"]).name,
                cn_pos=layout["cn_pos"],
                en_pos=layout["en_pos"],
                info_y=layout["info_y"],
                content_height=content_h,
                top_line_y=state["top_line_y"],
                bottom_line_y=video_y,
            )

            state["allow_measure"] = False
            apply_measured_state(show)

            msg = (
                f"已保存：{show}\n"
                f"画面：{state['img_width']}×{state['img_height']}\n"
                f"两线间距（有效画面）：{content_h} px\n"
                f"上下黑边（对称）：各 {top_bar} px\n"
                f"信息Y={layout['info_y']} 中文Y={layout['cn_pos']} 原版Y={layout['en_pos']}"
            )
            messagebox.showinfo("已记录", msg, parent=frame)
            log(
                f"✅ {show} | {state['img_width']}×{state['img_height']} | "
                f"有效画面{content_h}px 黑边各{top_bar}px | "
                f"中文Y={layout['cn_pos']} 原版Y={layout['en_pos']}"
            )

    def batch_measure():
        if not state["root_path"]:
            messagebox.showwarning("提示", "请先选择字幕根目录", parent=frame)
            return
        root_path = Path(state["root_path"])
        shows = iter_show_dirs_with_mkv(root_path)
        if not shows:
            messagebox.showinfo("提示", "未找到含 mkv 的剧集文件夹", parent=frame)
            return

        msg = (
            f"将对 {len(shows)} 部自动检测黑边"
            f"（每剧 1 个样例 mkv）。\n"
            f"结果写入 config.json，仍可在下方手动微调。\n\n是否继续？"
        )
        if not messagebox.askyesno("批量自动测量", msg, parent=frame):
            return

        if app and hasattr(app, "_warn_if_busy"):
            if app._warn_if_busy():
                return
        if app and hasattr(app, "_begin_batch_task"):
            app._crop_batching = True
            app._begin_batch_task("批量自动测量黑边…")

        skip_measured = skip_var.get()

        def on_progress(i, total, show_name, status):
            if app and hasattr(app, "_progress_callback"):
                app._progress_callback(i, total, f"{show_name} | {status}")
            status_var.set(
                f"批量测量黑边：{i + 1} / {total} 部剧中 — {show_name} | {status}"
            )

        def should_cancel():
            return (app and hasattr(app, "_task_cancel")
                    and app._task_cancel is not None
                    and app._task_cancel.is_set())

        def worker():
            try:
                ok, fail, skip = batch_auto_measure(
                    state["root_path"],
                    log=log,
                    skip_measured=skip_measured,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )
                total_str = f"黑边测量完成：成功 {ok}，失败 {fail}，跳过 {skip}"
                if app and hasattr(app, "_log_threadsafe"):
                    app._log_threadsafe(f"\n✅ 批量黑边测量完成（根目录：{state['root_path']}）")
                    app._log_threadsafe(f"✅ 成功 {ok}，失败 {fail}，跳过 {skip}")
                status_var.set(total_str)
                # 刷新面板状态
                load_config()
                if state["video_path"]:
                    s = show_name_from_video(state["root_path"], state["video_path"])
                    apply_measured_state(s)
            except Exception as e:
                if app and hasattr(app, "_log_threadsafe"):
                    app._log_threadsafe(f"❌ 批量测量异常：{e}")
                status_var.set(f"出错：{e}")
            finally:
                if app and hasattr(app, "_finish_batch_task"):
                    app.after(0, lambda: app._finish_batch_task(100, "批量黑边测量完成"))
                if app:
                    app._crop_batching = False

        threading.Thread(target=worker, daemon=True).start()

    # ========== UI 控件 ==========

    # 第一行：根目录
    root_row = tk.Frame(frame)
    root_row.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(root_row, text="字幕根目录（其下每个文件夹=一部剧）：").pack(side=tk.LEFT)
    root_var = tk.StringVar(value=state["root_path"])
    tk.Entry(root_row, textvariable=root_var, width=48).pack(side=tk.LEFT, padx=5)
    tk.Button(root_row, text="浏览", command=lambda: browse_root(),
              font=("微软雅黑", 9), fg="#2d6cc9", bg="#eaf1fd",
              activebackground="#2d6cc9", activeforeground="white",
              bd=0, padx=14, pady=2, cursor="hand2").pack(side=tk.LEFT)

    # 第二行：画面像素 + 当前剧集
    res_row = tk.Frame(frame)
    res_row.pack(fill=tk.X, padx=10, pady=2)
    pixel_label = tk.Label(res_row, text="画面像素：—", font=("Arial", 11))
    pixel_label.pack(side=tk.LEFT, padx=(0, 20))
    show_label = tk.Label(res_row, text="当前剧集：—", font=("Arial", 11, "bold"))
    show_label.pack(side=tk.LEFT)

    # 第三行：黑边高度 + 有效画面 + 字幕位置
    measure_row = tk.Frame(frame)
    measure_row.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(measure_row, text="上下黑边高度（对称）：").grid(row=0, column=0, sticky="w")
    bar_label = tk.Label(measure_row, text="—", font=("Arial", 12, "bold"))
    bar_label.grid(row=0, column=1, padx=(0, 16))
    tk.Label(measure_row, text="有效画面高度：").grid(row=0, column=2, sticky="w")
    content_label = tk.Label(measure_row, text="—", font=("Arial", 11))
    content_label.grid(row=0, column=3, padx=(0, 16))
    tk.Label(measure_row, text="中文 Y：").grid(row=1, column=0, sticky="w", pady=4)
    cn_pos_label = tk.Label(measure_row, text="—", font=("Arial", 11))
    cn_pos_label.grid(row=1, column=1, sticky="w")
    tk.Label(measure_row, text="原版 Y：").grid(row=1, column=2, sticky="w")
    en_pos_label = tk.Label(measure_row, text="—", font=("Arial", 11))
    en_pos_label.grid(row=1, column=3, sticky="w")

    # 提示栏
    status_var = tk.StringVar(
        value="点两次：顶黑边下沿、底黑边上沿 → 黑边高=(画面高-两线间距)/2（上下一致）"
    )
    tk.Label(frame, textvariable=status_var, fg="gray", wraplength=920).pack(
        anchor="w", padx=10, pady=4
    )

    # 按钮行
    btn_row = tk.Frame(frame)
    btn_row.pack(pady=5)

    skip_var = tk.BooleanVar(value=True)
    tk.Checkbutton(btn_row, text="跳过已测量", variable=skip_var,
                   font=("微软雅黑", 9), fg="#2c3e50",
                   selectcolor="white").pack(side=tk.LEFT, padx=5)
    _crop_btn_style = dict(font=("微软雅黑", 10, "bold"), fg="white", bg="#2d6cc9",
                           activebackground="#1a4f8a", activeforeground="white",
                           bd=0, padx=16, pady=4, cursor="hand2")
    tk.Button(btn_row, text="选择视频", command=load_video, **_crop_btn_style).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_row, text="读取视频并测量", command=measure_crop, **_crop_btn_style).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_row, text="重新测量", command=remeasure, **_crop_btn_style).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_row, text="批量自动测量", command=batch_measure, **_crop_btn_style).pack(side=tk.LEFT, padx=5)

    # Canvas 显示区域
    canvas = tk.Canvas(frame, bg="black")
    canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    canvas.bind("<Button-1>", on_click)

    if state["root_path"] and app:
        app.subtitle_root = state["root_path"]

    frame.pack(fill=tk.BOTH, expand=True)
    return frame


def crop_ui(app):
    """兼容旧调用：切换到主窗口内的黑边测量标签页"""
    if app and hasattr(app, "show_crop_tab"):
        app.show_crop_tab()
        return None
    embed_crop_panel(app, app)
