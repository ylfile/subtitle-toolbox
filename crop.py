# crop.py — 黑边测量：仅选根目录，子文件夹=剧集，测一次算 ASS 坐标
from pathlib import Path

import cv2
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
)


def list_show_folders(root_path):
    """根目录下每个子文件夹视为一部剧"""
    root = Path(root_path)
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def crop_ui(app):
    load_config()

    win = tk.Toplevel(app)
    win.title(f"{APP_NAME} — 黑边测量")
    win.geometry("1000x760")

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

    root_row = tk.Frame(win)
    root_row.pack(fill=tk.X, padx=10, pady=5)
    tk.Label(root_row, text="字幕根目录（其下每个文件夹=一部剧）：").pack(side=tk.LEFT)
    root_var = tk.StringVar(value=state["root_path"])
    tk.Entry(root_row, textvariable=root_var, width=48).pack(side=tk.LEFT, padx=5)
    tk.Button(root_row, text="浏览", command=lambda: browse_root()).pack(side=tk.LEFT)

    res_row = tk.Frame(win)
    res_row.pack(fill=tk.X, padx=10, pady=2)
    pixel_label = tk.Label(res_row, text="画面像素：—", font=("Arial", 11))
    pixel_label.pack(side=tk.LEFT, padx=(0, 20))
    show_label = tk.Label(res_row, text="当前剧集：—", font=("Arial", 11, "bold"))
    show_label.pack(side=tk.LEFT)

    measure_row = tk.Frame(win)
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

    status_var = tk.StringVar(
        value="点两次：顶黑边下沿、底黑边上沿 → 黑边高=(画面高-两线间距)/2（上下一致）"
    )
    tk.Label(win, textvariable=status_var, fg="gray", wraplength=920).pack(
        anchor="w", padx=10, pady=4
    )

    btn_row = tk.Frame(win)
    btn_row.pack(pady=5)

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
                f"✅「{show_name}」已测量 | 黑边各 {cfg['top']}px（对称）| 有效画面 {ch}px | "
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
        log(f"📁 根目录：{path}（{len(shows)} 部剧）")
        status_var.set(
            f"已选根目录，共 {len(shows)} 个子文件夹。"
            f"请点「选择视频」进入某剧文件夹选一个 mkv 进行测量。"
        )

    def load_video():
        if not state["root_path"]:
            messagebox.showwarning("提示", "请先选择字幕根目录", parent=win)
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
                f"📹 {show} / {Path(path).name}\n"
                f"点击「读取视频并测量」加载画面后，点两次标记黑边。"
            )

    def read_video_pixels():
        """读取视频，记录宽高像素"""
        if not state["video_path"]:
            messagebox.showwarning("提示", "请先选择视频", parent=win)
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
            messagebox.showwarning("提示", "请先选择根目录", parent=win)
            return
        if not state["video_path"]:
            messagebox.showwarning("提示", "请先选择视频", parent=win)
            return

        show = show_name_from_video(state["root_path"], state["video_path"])
        if is_measured(show) and not state["allow_measure"]:
            if not messagebox.askyesno(
                "已测量", f"「{show}」已有记录，是否重新测量？", parent=win
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
                messagebox.showerror("错误", "无法读取视频帧", parent=win)
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

            log(f"📐 {show} 画面 {state['img_width']}×{state['img_height']}")
            messagebox.showinfo(
                "提示",
                f"画面：{state['img_width']}×{state['img_height']}\n"
                "1. 点击顶黑边下沿\n"
                "2. 点击底黑边上沿\n"
                "黑边高度 = (画面高 - 两线间距) ÷ 2（上下相同）",
                parent=win,
            )
        except Exception as e:
            messagebox.showerror("错误", str(e), parent=win)

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
                messagebox.showwarning("提示", str(e), parent=win)
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
            messagebox.showinfo("已记录", msg, parent=win)
            log(
                f"✅ {show} | {state['img_width']}×{state['img_height']} | "
                f"有效画面{content_h}px 黑边各{top_bar}px | "
                f"中文Y={layout['cn_pos']} 原版Y={layout['en_pos']}"
            )

    tk.Button(btn_row, text="选择视频", command=load_video).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_row, text="读取视频并测量", command=measure_crop).pack(side=tk.LEFT, padx=5)
    tk.Button(btn_row, text="重新测量", command=remeasure).pack(side=tk.LEFT, padx=5)

    canvas = tk.Canvas(win, bg="black")
    canvas.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
    canvas.bind("<Button-1>", on_click)

    if state["root_path"] and app:
        app.subtitle_root = state["root_path"]
