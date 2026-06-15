# fix_ass.py — 用 pysubs2 模拟 Aegisub 重新解析+写入 ASS，修复字幕重叠
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

try:
    import pysubs2
    HAS_PYSUBS2 = True
except ImportError:
    HAS_PYSUBS2 = False


def find_ass_files(root_path):
    """查找根目录下所有 .ass 文件（含直接放在根目录的，和各子目录内的）"""
    root = Path(root_path)
    if not root.is_dir():
        return []

    files = set()

    # 根目录自身下的 .ass 文件
    for f in root.iterdir():
        if f.is_file() and f.suffix.lower() == ".ass":
            files.add(f)

    # 子目录下的 .ass 文件
    for subdir in root.iterdir():
        if subdir.is_dir() and not subdir.name.startswith("."):
            for f in subdir.iterdir():
                if f.is_file() and f.suffix.lower() == ".ass":
                    files.add(f)

    return sorted(files)


def fix_ass_file(ass_path, log=None):
    """
    用 pysubs2 打开 → 修改 → 保存，等同 Aegisub 重写效果。
    pysubs2 会完整解析 ASS 结构再序列化输出，重写格式与 Aegisub 一致。

    修改逻辑：
    1. 如果某行文本中有中文逗号（，）或中文句号（。），
       将其替换为空格
    2. 否则，在最后一条 Dialogue 行的文本末尾加一个空格
    """
    path = Path(ass_path)

    try:
        subs = pysubs2.load(str(path), encoding="utf-8")
    except Exception as e:
        if log:
            log(f"❌ 读取失败：{path.name} | {e}")
        return False

    if len(subs.events) == 0:
        if log:
            log(f"⏭️  无字幕事件：{path.name}")
        return False

    modified = False
    last_idx = -1

    for i, event in enumerate(subs.events):
        text = event.text or ""
        # 跳过纯样式覆盖标签（{}）的文本
        if not text.strip() or text == "":
            continue

        # 策略 1：中文逗号/句号 → 空格
        if "，" in text or "。" in text:
            event.text = text.replace("，", " ").replace("。", " ")
            modified = True
        last_idx = i

    # 策略 2：没有任何中文标点，则在最后一条 Dialogue 末尾加空格
    if not modified and last_idx >= 0:
        event = subs.events[last_idx]
        event.text = (event.text or "") + " "
        modified = True

    if not modified:
        if log:
            log(f"⏭️  无需修改：{path.name}")
        return False

    try:
        subs.save(str(path))
        if log:
            log(f"✅ {path.name}")
        return True
    except Exception as e:
        if log:
            log(f"❌ 写入失败：{path.name} | {e}")
        return False


def batch_fix_ass_files(root_path, log=None, on_progress=None, should_cancel=None):
    """批量处理所有 .ass 文件"""
    files = find_ass_files(root_path)

    if not files:
        if log:
            log("❌ 未找到任何 .ass 文件")
        return 0, 0

    total = len(files)
    success = 0
    fail = 0

    if log:
        log(f"\n━━━ 转换 ASS 格式：共 {total} 个文件 ━━━")

    for i, f in enumerate(files):
        if should_cancel and should_cancel():
            break

        ok = fix_ass_file(f, log)
        if ok:
            success += 1
        else:
            fail += 1

        if on_progress:
            on_progress(i + 1, total, f.parent.name, f.name)

    if log:
        log(f"\n🎉 完成：成功 {success} 个，失败 {fail} 个")

    return success, fail


def embed_fix_ass_panel(parent, app):
    """转换 ASS 格式：目录选择、统计、批量转换"""

    state = {"root_path": app.subtitle_root if app else ""}

    # 如果 pysubs2 未安装，弹出警告
    if not HAS_PYSUBS2:
        msg = (tk.Label if not app else tk.messagebox).showwarning(
            "缺少依赖",
            "需要 pysubs2 才能运行本功能。\n请在终端中执行：pip install pysubs2",
        )

    def log(msg):
        if app:
            app.log_msg(msg)

    def browse_root():
        path = filedialog.askdirectory(initialdir=state["root_path"] or None)
        if not path:
            return
        state["root_path"] = path
        root_var.set(path)
        count_ass()

    def require_root():
        path = root_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("提示", "请先选择有效的字幕根目录")
            return None
        state["root_path"] = path
        return Path(path)

    def count_ass():
        path = root_var.get().strip()
        if not path or not Path(path).is_dir():
            count_var.set("请先选择目录")
            return
        files = find_ass_files(path)
        count_var.set(f"找到 {len(files)} 个 .ass 文件")

    def run_fix_ass():
        if app and app._warn_if_busy():
            return

        if not HAS_PYSUBS2:
            messagebox.showerror("缺少依赖", "需要 pysubs2，请运行：pip install pysubs2")
            return

        selected = require_root()
        if not selected:
            return

        files = find_ass_files(str(selected))
        if not files:
            log("❌ 未找到任何 .ass 文件")
            count_var.set("未找到 .ass 文件")
            return

        if not messagebox.askyesno(
            "转换 ASS 格式",
            f"将使用 pysubs2 处理 {len(files)} 个 .ass 文件。\n"
            "效果等同 Aegisub 打开再保存。\n\n"
            "修改方式：\n"
            "  • 中文标点（，。）→ 替换为空格\n"
            "  • 无中文标点 → 文末添加空格\n\n是否继续？",
        ):
            return

        app._fixing_ass = True
        app._begin_batch_task(f"转换 {len(files)} 个 ASS 格式…")
        log(f"\n━━━ 批量转换 ASS 格式（pysubs2）：共 {len(files)} 个文件 ━━━")

        def worker():
            try:
                ok, fail = batch_fix_ass_files(
                    str(selected),
                    log=app._log_threadsafe,
                    on_progress=app._progress_callback,
                    should_cancel=app._task_cancel.is_set if app else None,
                )
                cancelled = getattr(app, "_task_cancel", None) and app._task_cancel.is_set()
                if cancelled:
                    summary = f"已停止：成功 {ok} 个，失败 {fail} 个"
                else:
                    summary = f"完成：成功 {ok} 个，失败 {fail} 个"
                app._log_threadsafe(f"\n🎉 转换 ASS 格式{summary}")
                app.after(0, lambda: app._finish_batch_task(100, summary))
            except Exception as e:
                err = f"❌ 转换异常：{e}"
                app._log_threadsafe(err)
                app.after(0, lambda: app._finish_batch_task(0, f"出错：{err}"))
            finally:
                app._fixing_ass = False
                app.after(0, count_ass)

        threading.Thread(target=worker, daemon=True).start()

    # ===== UI 控件 =====

    # 第一行：根目录选择
    root_row = tk.Frame(parent)
    root_row.pack(fill=tk.X, padx=10, pady=8)
    tk.Label(root_row, text="字幕根目录：", font=("微软雅黑", 10), fg="#7f8c8d").pack(side=tk.LEFT)
    root_var = tk.StringVar(value=state["root_path"])
    tk.Entry(
        root_row, textvariable=root_var, font=("微软雅黑", 10), fg="#2c3e50",
        relief="solid", bd=1, highlightcolor="#2d6cc9", highlightthickness=1,
        bg="#f9faff",
    ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8), ipady=3)
    tk.Button(
        root_row, text="浏览", command=browse_root,
        font=("微软雅黑", 9), fg="#2d6cc9", bg="#eaf1fd",
        activebackground="#2d6cc9", activeforeground="white",
        bd=0, padx=14, pady=2, cursor="hand2",
    ).pack(side=tk.LEFT)

    # 说明
    tk.Label(
        parent,
        text="扫描根目录（含子目录）下所有 .ass 文件，用 pysubs2 重写保存以修复字幕重叠。",
        fg="#7f8c8d", font=("微软雅黑", 9),
        justify=tk.LEFT,
    ).pack(anchor="w", padx=10, pady=(0, 4))

    # 依赖提示
    dep_var = tk.StringVar(value="")
    if not HAS_PYSUBS2:
        dep_var.set("⚠️ 缺少 pysubs2，请运行：pip install pysubs2")
    tk.Label(
        parent, textvariable=dep_var, anchor="w", fg="#e74c3c",
        font=("微软雅黑", 9),
    ).pack(fill=tk.X, padx=10, pady=(0, 2))

    # 统计信息
    count_var = tk.StringVar(value="")
    tk.Label(
        parent, textvariable=count_var, anchor="w", fg="#2d6cc9",
        font=("微软雅黑", 10, "bold"),
    ).pack(fill=tk.X, padx=10, pady=(0, 8))

    # 按钮行
    btn_row = tk.Frame(parent)
    btn_row.pack(fill=tk.X, padx=10, pady=6)

    _btn_style = dict(
        font=("微软雅黑", 10, "bold"), fg="white", bg="#2d6cc9",
        activebackground="#1a4f8a", activeforeground="white",
        bd=0, padx=16, pady=4, cursor="hand2",
    )

    tk.Button(
        btn_row, text="刷新统计", command=count_ass, width=12, **_btn_style,
    ).pack(side=tk.LEFT, padx=(0, 8))

    tk.Button(
        btn_row, text="转换为Aegisub格式", command=run_fix_ass, width=18, **_btn_style,
    ).pack(side=tk.LEFT)

    # 初始统计
    if state["root_path"]:
        parent.after(200, count_ass)

    return parent
