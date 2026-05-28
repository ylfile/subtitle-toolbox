# main.py — YLFile 字幕工具箱主界面
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
    is_measured_folder,
    get_crop_for_folder,
    set_subtitle_root,
)
from utils import iter_show_dirs_for_work
from extract import extract_subtitles  # 字幕提取
from utils import iter_show_dirs_with_mkv  # 解析批量/单剧目录
from crop import crop_ui  # 黑边测量（每剧一个视频）
from ass import generate_ass  # ASS 生成（读取已存黑边）
from encode import encode_show, count_encode_jobs, stop_encode_process
from encode_settings import EncodeSettingsPanel, settings_summary
from config import save_encode_settings


class App(tk.Tk):  # 主窗口类
    def __init__(self):  # 初始化
        super().__init__()  # 调用父类构造

        self.title(APP_NAME)
        self.geometry("1200x880")
        self.subtitle_root = ""
        self._encoding = False
        self._encode_cancel = threading.Event()

        load_config()
        self.subtitle_root = SUBTITLE_ROOT  # 恢复上次根目录

        bar = tk.Frame(self)  # 工具栏容器
        bar.pack(fill=tk.X)  # 横向铺满

        tk.Button(bar, text="批量提取字幕",  # 提取按钮
                  command=self.extract_all).pack(side=tk.LEFT)  # 靠左
        tk.Button(bar, text="黑边测量",  # 黑边按钮
                  command=lambda: crop_ui(self)).pack(side=tk.LEFT)  # 每剧只测一个视频
        tk.Button(bar, text="生成 ASS",  # ASS 按钮
                  command=self.gen_ass).pack(side=tk.LEFT)  # 自动读黑边
        self.btn_encode = tk.Button(
            bar, text="批量压制", command=self.encode_all
        )
        self.btn_encode.pack(side=tk.LEFT)
        self.btn_encode_stop = tk.Button(
            bar,
            text="停止压制",
            command=self.stop_encode,
            state=tk.DISABLED,
        )
        self.btn_encode_stop.pack(side=tk.LEFT, padx=(4, 0))

        self.encode_panel = EncodeSettingsPanel(self)
        self.encode_panel.pack(fill=tk.X, padx=8, pady=(6, 0))

        prog = tk.Frame(self)
        prog.pack(fill=tk.X, padx=8, pady=(4, 0))
        self.encode_status = tk.StringVar(value="就绪")
        tk.Label(prog, textvariable=self.encode_status, anchor="w").pack(
            fill=tk.X
        )
        self.encode_progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.encode_progress.pack(fill=tk.X, pady=(2, 4))

        self.log = scrolledtext.ScrolledText(self)
        self.log.pack(fill=tk.BOTH, expand=True, padx=0, pady=4)

        footer = tk.Frame(self)
        footer.pack(fill=tk.X)
        tk.Label(footer, text=APP_TAGLINE, anchor="e", fg="#555").pack(
            fill=tk.X, padx=8, pady=(0, 6)
        )

        if self.subtitle_root:  # 有保存的根目录
            self.log_msg(f"📂 已加载字幕根目录：{self.subtitle_root}")  # 提示
        if CROP_TABLE:  # 有已测黑边
            self.log_msg(  # 说明 ASS 会直接用
                f"📐 已加载 {len(CROP_TABLE)} 部剧黑边（每剧仅测过一个视频，全剧共用）"
            )

    def log_msg(self, msg):
        self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)

    def _log_threadsafe(self, msg):
        self.after(0, lambda m=msg: self.log_msg(m))

    def _set_encode_ui(self, percent, status, done=False):
        self.encode_progress["value"] = percent
        self.encode_status.set(status)
        if done:
            self.btn_encode.config(state=tk.NORMAL)
            self.btn_encode_stop.config(state=tk.DISABLED)
            self._encoding = False

    def stop_encode(self):
        if not self._encoding:
            return
        self._encode_cancel.set()
        stop_encode_process()
        self.encode_status.set("正在停止当前任务…")
        self.log_msg("⏹ 用户请求停止压制")

    def _ask_subtitle_root(self):  # 获取字幕根目录
        if self.subtitle_root and Path(self.subtitle_root).is_dir():  # 已有有效路径
            if messagebox.askyesno(  # 询问是否沿用
                "字幕根目录",
                f"使用已保存的字幕根目录？\n{self.subtitle_root}\n\n选「否」可重新选择",
            ):
                return Path(self.subtitle_root)  # 直接返回
        path = filedialog.askdirectory(initialdir=self.subtitle_root or None)  # 选文件夹
        if path:  # 用户选了
            self.subtitle_root = path  # 更新内存
            set_subtitle_root(path)  # 写入 config.json
        return Path(path) if path else None  # 返回 Path 或 None

    def extract_all(self):  # 批量提取字幕
        path = filedialog.askdirectory(  # 选择目录（根目录或单部剧文件夹）
            title="选择字幕根目录（批量）或某一部剧文件夹（单独）",
            initialdir=self.subtitle_root or None,
        )
        if not path:  # 用户取消
            return  # 结束

        selected = Path(path)  # 用户选中的路径
        shows = iter_show_dirs_with_mkv(selected)  # 解析要处理的剧集文件夹列表
        if not shows:  # 没有找到含 mkv 的文件夹
            self.log_msg(f"❌ 未找到包含 .mkv 的文件夹：{selected}")  # 提示
            return  # 结束

        if len(shows) == 1 and shows[0].resolve() == selected.resolve():  # 选中的是单部剧文件夹
            self.log_msg(f"📂 单部剧模式：{shows[0].name}")  # 说明模式
            parent = shows[0].parent  # 上级目录作为字幕根目录
            if parent.is_dir():  # 上级有效
                self.subtitle_root = str(parent)  # 记住根目录
                set_subtitle_root(parent)  # 写入配置
        else:  # 选中的是字幕根目录，批量多部剧
            self.log_msg(f"📂 批量模式：共 {len(shows)} 部剧（仅处理含 .mkv 的子文件夹）")  # 说明
            self.subtitle_root = str(selected)  # 记住根目录
            set_subtitle_root(selected)  # 写入配置

        ok, fail = 0, 0  # 成功/失败计数
        for show in shows:  # 逐部剧
            mkvs = sorted(show.glob("*.mkv"))  # 该剧所有 mkv
            self.log_msg(f"\n━━━ 剧集：{show.name}（{len(mkvs)} 个视频）━━━")  # 剧集头
            for mkv in mkvs:  # 逐集提取
                try:  # 尝试提取
                    self.log_msg(f"=== 处理：{mkv.name}")  # 开始
                    extract_subtitles(mkv, show, self.log_msg)  # 提取字幕
                    self.log_msg(f"✅ 成功：{mkv.name}")  # 成功
                    ok += 1  # 计数
                except Exception as e:  # 失败
                    self.log_msg(f"❌ 失败：{mkv.name} | {e}")  # 错误
                    fail += 1  # 计数

        self.log_msg(f"\n🎉 提取完成：成功 {ok}，失败 {fail}")  # 汇总

    def gen_ass(self):  # 批量生成 ASS
        load_config()  # 刷新黑边表（含其他窗口新测的数据）

        root = self._ask_subtitle_root()  # 根目录
        if not root:  # 未选
            self.log_msg("❌ 未选择字幕根目录")  # 提示
            return  # 结束

        self.log_msg(f"📁 字幕根目录：{root}")  # 日志

        shows = iter_show_dirs_for_work(root)
        if not shows:
            self.log_msg(
                "❌ 未找到可处理的剧集文件夹。\n"
                "   · 选根目录：其下每个子文件夹是一部剧\n"
                "   · 或直接进入某部剧文件夹（内含 mkv / 原版+中文版 srt）\n"
                f"   · 已登记黑边的剧集：{', '.join(sorted(CROP_TABLE.keys())) or '（无）'}"
            )
            return

        self.log_msg(f"📋 待处理 {len(shows)} 部：{', '.join(s.name for s in shows)}")
        if CROP_TABLE:
            self.log_msg(f"📐 config 已登记黑边：{', '.join(CROP_TABLE.keys())}")

        unmatched = [s for s in shows if not is_measured_folder(s)]
        if unmatched and len(unmatched) == len(shows):
            self.log_msg(
                "⚠️ 上述文件夹均未匹配到黑边记录。\n"
                f"   文件夹名：{', '.join(s.name for s in shows)}\n"
                f"   config 键名：{', '.join(CROP_TABLE.keys()) or '（空）'}\n"
                "   文件夹名须与测量时一致；或在该剧文件夹内保留测量用的样例 mkv 文件名。"
            )

        show_ok = 0
        ass_total = 0
        for sub in shows:
            if not is_measured_folder(sub):
                self.log_msg(
                    f"⚠️ 跳过「{sub.name}」：未匹配黑边（config 键："
                    f"{', '.join(CROP_TABLE.keys()) or '无'}）"
                )
                continue

            cfg = get_crop_for_folder(sub)
            sample = cfg.get("sample_video", "")
            try:
                self.log_msg(
                    f"\n🎬 {sub.name} | top={cfg['top']} bottom={cfg['bottom']}"
                    + (f"（样例：{sample}）" if sample else "")
                )
                n = generate_ass(str(sub), cfg, self.log_msg)
                if n > 0:
                    show_ok += 1
                    ass_total += n
                else:
                    self.log_msg(f"⚠️「{sub.name}」未生成任何 ASS，请查看上方日志")
            except Exception as e:
                self.log_msg(f"❌ ASS 生成失败：{sub.name} | {e}")

        self.log_msg(
            f"\n🎉 完成：{show_ok} 部剧、共 {ass_total} 个 ASS 文件"
        )

    def encode_all(self):
        if self._encoding:
            messagebox.showinfo("提示", "正在压制中，请稍候…")
            return

        load_config()
        root = self._ask_subtitle_root()
        if not root:
            self.log_msg("❌ 未选择字幕根目录")
            return

        shows = iter_show_dirs_for_work(root)
        if not shows:
            self.log_msg(
                "❌ 未找到可处理的剧集文件夹（需含 .mkv 的子文件夹，或直接进入剧集目录）"
            )
            return

        job_total = count_encode_jobs(shows)
        if job_total == 0:
            self.log_msg("❌ 没有可压制的 .mkv 文件")
            return

        self._encoding = True
        self._encode_cancel.clear()
        self.btn_encode.config(state=tk.DISABLED)
        self.btn_encode_stop.config(state=tk.NORMAL)
        self._set_encode_ui(0, f"准备压制 {len(shows)} 部剧，共 {job_total} 个视频…")

        enc_cfg = self.encode_panel.get_settings()
        save_encode_settings(enc_cfg)

        self.log_msg(f"📁 字幕根目录：{root}")
        self.log_msg(f"⚙️ 压制参数：{settings_summary(enc_cfg)}")
        self.log_msg(f"📋 待压制 {len(shows)} 部 / {job_total} 个视频：{', '.join(s.name for s in shows)}")

        def worker():
            total_ok = total_fail = total_skip = 0
            offset = 0
            try:
                for sub in shows:
                    if self._encode_cancel.is_set():
                        break

                    def on_progress(pct, status):
                        self.after(
                            0,
                            lambda p=pct, s=status: self._set_encode_ui(p, s),
                        )

                    ok, fail, skip = encode_show(
                        sub,
                        self._log_threadsafe,
                        skip_existing=False,
                        on_progress=on_progress,
                        cancel_event=self._encode_cancel,
                        file_index_offset=offset,
                        file_total=job_total,
                        encode_settings=enc_cfg,
                    )
                    total_ok += ok
                    total_fail += fail
                    total_skip += skip
                    offset += len(list(sub.glob("*.mkv")))

                summary = (
                    f"全部完成：成功 {total_ok}，失败 {total_fail}，跳过 {total_skip}"
                )
                if self._encode_cancel.is_set():
                    summary = f"已取消（已完成 {total_ok} 个）"
                self._log_threadsafe(f"\n🎉 {summary}")
                self.after(
                    0,
                    lambda: self._set_encode_ui(100, summary, done=True),
                )
            except Exception as e:
                self._log_threadsafe(f"❌ 压制异常：{e}")
                self.after(
                    0,
                    lambda: self._set_encode_ui(0, f"出错：{e}", done=True),
                )

        threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":  # 脚本直接运行
    App().mainloop()  # 启动主循环
