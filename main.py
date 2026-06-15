# main.py — YLFile 字幕工具箱主界面（美化版）
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from pathlib import Path

from config import (
    APP_NAME,
    APP_TAGLINE,
    load_config,
    CROP_TABLE,
    SUBTITLE_ROOT,
    set_subtitle_root,
)
from utils import iter_show_dirs_with_mkv
from extract import extract_subtitles, count_extract_jobs
from crop import embed_crop_panel
from ass import embed_ass_panel
from rename_mkv_episodes import collect_rename_plans, execute_renames_for_shows
from rename_mp4_episodes import collect_mp4_rename_plans, execute_mp4_renames_for_shows
from audio_trim import embed_audio_trim_panel

# ---- 配色方案 ----
BG = "#f5f6fa"
CARD = "#ffffff"
ACCENT = "#2d6cc9"
ACCENT_LIGHT = "#eaf1fd"
TEXT = "#2c3e50"
TEXT_SEC = "#7f8c8d"
BORDER = "#e0e3eb"
STOP_RED = "#e74c3c"
STOP_RED_LIGHT = "#fde8e8"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1200x920")
        self.configure(bg=BG)

        self.subtitle_root = ""

        self._extracting = False
        self._crop_batching = False
        self._renaming = False
        self._renaming_mp4 = False
        self._generating_ass = False
        self._deleting_srt = False
        self._trimming_audio = False
        self._deleting_mkv = False
        self._crop_loading = False
        self._task_cancel = threading.Event()

        load_config()
        self.subtitle_root = SUBTITLE_ROOT

        # ---- Notebook 标签页（选中标签凸起样式） ----
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("微软雅黑", 9), padding=(16, 8),
                         borderwidth=1, relief="raised")
        style.map("TNotebook.Tab",
                   background=[("selected", CARD), ("active", ACCENT_LIGHT)],
                   foreground=[("selected", ACCENT), ("!selected", TEXT_SEC)],
                   font=[("selected", ("微软雅黑", 11, "bold"))],
                   relief=[("selected", "raised"), ("!selected", "raised")],
        )

        # ---- 顶部全局目录栏 + 进度条 ----
        top_bar = tk.Frame(self, bg=BG)
        top_bar.pack(fill=tk.X, padx=16, pady=(10, 2))
        tk.Label(top_bar, text="📁 根目录：", font=("微软雅黑", 10), fg=TEXT, bg=BG).pack(side=tk.LEFT)
        self._global_root_var = tk.StringVar(value=self.subtitle_root)
        self._global_root_entry = tk.Entry(
            top_bar, textvariable=self._global_root_var, font=("微软雅黑", 10),
            fg=TEXT, bg="#f9faff", relief="solid", bd=1,
            highlightcolor=ACCENT, highlightthickness=1,
        )
        self._global_root_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=3)
        self._browse_btn = tk.Button(top_bar, text="浏览", command=self._browse_global_root,
                  font=("微软雅黑", 9), fg=ACCENT, bg=ACCENT_LIGHT,
                  activebackground=ACCENT, activeforeground="white",
                  bd=0, padx=14, pady=2, cursor="hand2")
        self._browse_btn.pack(side=tk.LEFT)

        # 进度行（与目录栏完全对齐）
        prog_frame = tk.Frame(self, bg=BG)
        prog_frame.pack(fill=tk.X, padx=16, pady=(2, 0))

        self.task_status = tk.StringVar(value="就  绪")
        tk.Label(
            prog_frame, textvariable=self.task_status, font=("微软雅黑", 10),
            fg=TEXT, bg=BG, anchor="w", width=9,
        ).pack(side=tk.LEFT)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("color.Horizontal.TProgressbar", background="#2ecc71", troughcolor="#e0e3eb", bordercolor="#ffffff", lightcolor="#2ecc71", darkcolor="#27ae60")
        self.task_progress = ttk.Progressbar(
            prog_frame, style="color.Horizontal.TProgressbar", mode="determinate", maximum=100
        )
        self.task_progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=3)

        self.btn_stop = tk.Button(
            prog_frame, text="停止", command=self.stop_task,
            state=tk.DISABLED, fg="white", bg=STOP_RED,
            activebackground=STOP_RED, activeforeground="white",
            font=("微软雅黑", 9), bd=0, padx=14, pady=2,
            cursor="hand2",
        )
        self.btn_stop.pack(side=tk.LEFT)

        self._global_refresh_callbacks = []

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=16, pady=(4, 4))

        # ---- 各标签页 ----
        self._build_rename_mkv_panel()
        self._build_extract_panel()
        crop_frame = tk.Frame(self.notebook, bg=CARD)
        embed_crop_panel(crop_frame, self)
        self.notebook.add(crop_frame, text="黑边测量")
        self._crop_tab = crop_frame

        ass_frame = tk.Frame(self.notebook, bg=CARD)
        embed_ass_panel(ass_frame, self)
        self.notebook.add(ass_frame, text="生成ASS")
        self._ass_tab = ass_frame

        audio_frame = tk.Frame(self.notebook, bg=CARD)
        embed_audio_trim_panel(audio_frame, self)
        self.notebook.add(audio_frame, text="音频处理")
        self._audio_trim_tab = audio_frame

        log_frame = tk.Frame(self.notebook, bg=CARD)
        self.log = scrolledtext.ScrolledText(
            log_frame, font=("Consolas", 10), bg=CARD, fg=TEXT,
            relief="flat", bd=0, padx=8, pady=8,
        )
        self.log.pack(fill=tk.BOTH, expand=True)
        self.notebook.add(log_frame, text="工作日志")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- 底部脚标 ----
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill=tk.X)
        tk.Label(
            footer, text=APP_TAGLINE, font=("微软雅黑", 9),
            anchor="e", fg=TEXT_SEC, bg=BG,
        ).pack(fill=tk.X, padx=16, pady=(0, 8))

        if self.subtitle_root:
            self.log_msg(f"已加载字幕根目录：{self.subtitle_root}")
        if CROP_TABLE:
            self.log_msg(f"已加载 {len(CROP_TABLE)} 部剧黑边")

    # ---- 构建面板（带美化样式） ----

    def _panel_card(self, parent):
        """创建卡片容器（白色背景 + 圆角阴影效果）"""
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=16, pady=14)
        card.pack(fill=tk.BOTH, expand=False, padx=12, pady=12)
        return card

    def _section_title(self, parent, text):
        tk.Label(
            parent, text=text, font=("微软雅黑", 12, "bold"),
            fg=TEXT, bg=CARD, anchor="w",
        ).pack(fill=tk.X, pady=(0, 10))

    def _dir_row(self, parent, var, browse_cmd):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill=tk.X)
        tk.Label(row, text="字幕根目录", font=("微软雅黑", 10), fg=TEXT_SEC, bg=CARD).pack(side=tk.LEFT)
        tk.Entry(
            row, textvariable=var, font=("微软雅黑", 10),
            fg=TEXT, bg="#f9faff", relief="solid", bd=1,
            highlightcolor=ACCENT, highlightthickness=1,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=3)
        tk.Button(
            row, text="浏览", command=browse_cmd,
            font=("微软雅黑", 9), fg=ACCENT, bg=ACCENT_LIGHT,
            activebackground=ACCENT, activeforeground="white",
            bd=0, padx=14, pady=2, cursor="hand2",
        ).pack(side=tk.LEFT)
        return row

    def _start_btn(self, parent, text, cmd):
        tk.Button(
            parent, text=text, command=cmd,
            font=("微软雅黑", 11, "bold"), fg="white", bg=ACCENT,
            activebackground="#1a4f8a", activeforeground="white",
            bd=0, padx=24, pady=6, cursor="hand2",
        ).pack(anchor="w", pady=(8, 0))

    def _hint(self, parent, text):
        tk.Label(
            parent, text=text, font=("微软雅黑", 9),
            fg=TEXT_SEC, bg=CARD, anchor="w",
        ).pack(fill=tk.X, pady=(2, 0))

    def _build_rename_mkv_panel(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="规范命名")
        card = self._panel_card(frame)
        self._section_title(card, "规范命名")

        # MKV 重命名
        mkv_frame = tk.Frame(card, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10)
        mkv_frame.pack(fill=tk.X, pady=(0, 12))
        tk.Label(mkv_frame, text="MKV 重命名", font=("微软雅黑", 10, "bold"), fg=ACCENT, bg=CARD).pack(anchor="w")
        self._hint(mkv_frame, "将各剧集文件夹内含有 SxxExx 标识的 .mkv 重命名为 SxxExx.mkv")
        self._start_btn(mkv_frame, "开始重命名", self.rename_mkv_all)

        # MP4 重命名
        mp4_frame = tk.Frame(card, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=12, pady=10)
        mp4_frame.pack(fill=tk.X)
        tk.Label(mp4_frame, text="MP4 重命名", font=("微软雅黑", 10, "bold"), fg=ACCENT, bg=CARD).pack(anchor="w")
        self._hint(mp4_frame, "将 SxxExx_压制版.mp4 / SxxExx_原音_压制版.mp4 重命名为 SxxExx.mp4")
        self._start_btn(mp4_frame, "开始重命名", self.rename_mp4_all)

    def _build_extract_panel(self):
        frame = tk.Frame(self.notebook, bg=BG)
        self.notebook.add(frame, text="提取字幕")
        card = self._panel_card(frame)
        self._section_title(card, "提取字幕")
        self._hint(card, "从 .mkv 中提取原版 + 中文字幕（SRT），繁体中文自动转为简体")

        # 原版语言选择
        lang_row = tk.Frame(card, bg=CARD)
        lang_row.pack(fill=tk.X, pady=(6, 2))
        tk.Label(lang_row, text="原版语言：", font=("微软雅黑", 10), fg=TEXT_SEC, bg=CARD).pack(side=tk.LEFT)

        from utils import COMMON_LANG_OPTIONS

        self._ext_lang_var = tk.StringVar(value="自动识别")
        lang_menu = ttk.Combobox(
            lang_row, textvariable=self._ext_lang_var, font=("微软雅黑", 9),
            state="readonly", width=20,
            values=[label for label, _ in COMMON_LANG_OPTIONS],
        )
        lang_menu.pack(side=tk.LEFT, padx=(8, 0))
        lang_menu.current(0)
        self._lang_menu = lang_menu
        self._lang_options = COMMON_LANG_OPTIONS

        def _on_lang_selected(*_):
            label = self._ext_lang_var.get()
            # 选"自定义…"时弹出输入框
            if label == "自定义…":
                import tkinter.simpledialog as sd
                custom = sd.askstring("自定义语言", "输入 ISO 639-2 三字母语言码（如 vie）",
                                      parent=self)
                if custom and custom.strip():
                    code = custom.strip().lower()
                    # 插入自定义选项到"自定义…"后面
                    display = f"{code.upper()} ({code})"
                    new_options = list(lang_menu["values"])
                    idx = list(new_options).index("自定义…") + 1
                    new_options = list(new_options[:idx]) + [display] + list(new_options[idx:])
                    lang_menu["values"] = new_options
                    self._ext_lang_var.set(display)
                    self._lang_options = self._lang_options + [(display, code)]
                else:
                    self._ext_lang_var.set("自动识别")
            # 刷新 lang_options 引用
            for lbl, code in self._lang_options:
                if lbl == label:
                    break
        self._ext_lang_var.trace_add("write", _on_lang_selected)

        self._start_btn(card, "开始提取", self.extract_all)

    # ---- 规范命名合并到 _build_rename_mkv_panel ----

    def _browse_global_root(self):
        p = filedialog.askdirectory(initialdir=self._global_root_var.get() or None)
        if not p:
            return
        self._global_root_var.set(p)
        self.subtitle_root = p
        set_subtitle_root(p)
        for cb in self._global_refresh_callbacks:
            try:
                cb()
            except Exception:
                pass

    # ---- 回调 ----

    def _on_tab_changed(self, event=None):
        try:
            idx = self.notebook.index(self.notebook.select())
            t = self.notebook.tab(idx, "text")
            if t in ("黑边测量", "生成ASS"):
                load_config()
        except Exception:
            pass

    # ---- 任务管理 ----

    def _task_busy(self):
        return any((
            self._extracting, self._crop_batching, self._renaming,
            self._renaming_mp4, self._generating_ass, self._deleting_srt,
            self._trimming_audio, self._deleting_mkv, self._crop_loading,
        ))

    def _warn_if_busy(self, parent=None):
        if self._task_busy():
            messagebox.showinfo("提示", "有任务正在运行，请稍候…", parent=parent)
            return True
        return False

    def _progress_callback(self, current, total=100, show_name="", status=""):
        """进度回调，支持 (pct, status) 或 (current, total, ...) 两种格式"""
        # 两个参数 → (pct, status) 短格式
        if isinstance(total, str):
            pct = int(current)
            s = total
        elif isinstance(current, (int, float)) and total and total > 0:
            pct = int(current * 100 / max(total, 1))
            s = status or str(show_name) if show_name else ""
        else:
            pct = int(current)
            s = status or str(show_name) if show_name else ""
        self.after(0, lambda p=pct, s=s: self._set_task_ui(p, s))

    def _begin_batch_task(self, status_text):
        self._task_cancel.clear()
        self.btn_stop.config(state=tk.NORMAL, bg=STOP_RED)
        self.task_progress["value"] = 0
        self.task_status.set("处理中…")

    def _finish_batch_task(self, pct, status):
        self.task_progress["value"] = pct
        self.task_status.set(status)
        self.btn_stop.config(state=tk.DISABLED, bg="#ccc")

    def stop_task(self):
        if not self._task_busy():
            return
        self._task_cancel.set()
        self.task_status.set("正在停止…")
        self.log_msg("用户请求停止")

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _log_threadsafe(self, msg):
        self.after(0, lambda m=msg: self.log_msg(m))

    def _set_task_ui(self, pct, status):
        self.task_progress["value"] = pct
        self.task_status.set(status)

    # ========== 功能入口 ==========

    def rename_mkv_all(self):
        if self._warn_if_busy(): return
        path = self._global_root_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先选择有效的字幕根目录"); return
        selected = Path(path)
        shows, all_plans, all_errors = collect_rename_plans(selected)
        if all_errors:
            self.log_msg("以下问题需先处理：")
            for e in all_errors: self.log_msg(f"  {e}"); return
        if not shows: self.log_msg(f"未找到含 .mkv 的文件夹：{selected}"); return
        if not all_plans: self.log_msg("所有 mkv 已是 SxxExx.mkv"); return
        self.subtitle_root = str(selected); set_subtitle_root(selected)
        self.log_msg(f"将重命名 {len(all_plans)} 个 mkv：")
        for s, d in all_plans: self.log_msg(f"  {s.parent.name}\\{s.name}  ->  {d.name}")
        if not messagebox.askyesno("确认", f"{len(all_plans)} 个 mkv，是否继续？"): return
        self._renaming = True; self._begin_batch_task(f"重命名 {len(all_plans)} 个 mkv…")
        def w():
            try:
                c, e = execute_renames_for_shows(shows, on_progress=self._progress_callback, should_cancel=self._task_cancel.is_set)
                s = f"MKV 重命名{'已停止' if self._task_cancel.is_set() else '完成'}：共 {c} 个"
                self._log_threadsafe(f"\n{s}"); self.after(0, lambda: self._finish_batch_task(100, s))
            except Exception as e: self._log_threadsafe(f"重命名失败：{e}"); self.after(0, lambda: self._finish_batch_task(0, "出错"))
            finally: self._renaming = False
        threading.Thread(target=w, daemon=True).start()

    def rename_mp4_all(self):
        if self._warn_if_busy(): return
        path = self._global_root_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先选择有效的字幕根目录"); return
        selected = Path(path)
        shows, all_plans, all_errors = collect_mp4_rename_plans(selected)
        if all_errors: self.log_msg("以下问题需先处理：")
        for e in all_errors: self.log_msg(f"  {e}"); return
        if not shows: self.log_msg(f"未找到含 .mp4 的文件夹：{selected}"); return
        if not all_plans: self.log_msg("没有符合规则的 mp4 需重命名"); return
        self.subtitle_root = str(selected); set_subtitle_root(selected)
        self.log_msg(f"将重命名 {len(all_plans)} 个 mp4")
        if not messagebox.askyesno("确认", f"{len(all_plans)} 个 mp4，是否继续？"): return
        self._renaming_mp4 = True; self._begin_batch_task(f"重命名 {len(all_plans)} 个 mp4…")
        def w():
            try:
                c, e = execute_mp4_renames_for_shows(shows, on_progress=self._progress_callback, should_cancel=self._task_cancel.is_set)
                s = f"MP4 重命名{'已停止' if self._task_cancel.is_set() else '完成'}：共 {c} 个"
                self._log_threadsafe(f"\n{s}"); self.after(0, lambda: self._finish_batch_task(100, s))
            except Exception as e: self._log_threadsafe(f"MP4 重命名异常：{e}"); self.after(0, lambda: self._finish_batch_task(0, "出错"))
            finally: self._renaming_mp4 = False
        threading.Thread(target=w, daemon=True).start()

    def extract_all(self):
        if self._warn_if_busy(): return
        path = self._global_root_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先选择有效的字幕根目录"); return
        selected = Path(path)
        shows = iter_show_dirs_with_mkv(selected)
        if not shows: self.log_msg(f"未找到含 .mkv 的文件夹：{selected}"); return
        job_total = count_extract_jobs(shows)
        if job_total == 0: self.log_msg("没有可提取的 .mkv 文件"); return
        self.subtitle_root = str(selected); set_subtitle_root(selected)
        self.log_msg(f"待提取：{', '.join(s.name for s in shows)}（{job_total} 个视频）")

        # 解析用户指定的语言
        selected_label = self._ext_lang_var.get()
        forced_lang = None
        for label, code in self._lang_options:
            if label == selected_label:
                forced_lang = code
                break
        if forced_lang:
            self.log_msg(f"🎯 用户指定原版语言：{forced_lang}")
        else:
            self.log_msg(f"🔍 原版语言：自动识别")

        self._extracting = True; self._begin_batch_task(f"提取 {job_total} 个视频…")
        def w():
            ok=fail=0
            try:
                for show in shows:
                    if self._task_cancel.is_set(): break
                    for mkv in sorted(show.glob("*.mkv")):
                        if self._task_cancel.is_set(): break
                        try: self._log_threadsafe(f"=== {mkv.name}"); extract_subtitles(mkv, show, self._log_threadsafe, forced_lang=forced_lang); ok+=1
                        except Exception as e: self._log_threadsafe(f"失败：{mkv.name}|{e}"); fail+=1
                        self._progress_callback(int((ok+fail)*100/max(job_total,1)), f"{ok+fail}/{job_total}")
                s = f"提取{'已停止' if self._task_cancel.is_set() else '完成'}：成功 {ok}，失败 {fail}"
                self._log_threadsafe(f"\n{s}"); self.after(0, lambda: self._finish_batch_task(100, s))
            except Exception as e: self._log_threadsafe(f"提取异常：{e}"); self.after(0, lambda: self._finish_batch_task(0, "出错"))
            finally: self._extracting = False
        threading.Thread(target=w, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
