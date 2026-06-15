# remux_ass.py - remux ASS into MKV
import subprocess, sys
from pathlib import Path
from utils import extract_episode_id
from config import TOOLS

def _ass_label(filename):
    name = Path(filename).name
    return "双语[ylfile.com]" if "双语" in name else "中文[ylfile.com]"

def collect_remux_jobs(shows):
    results = []
    for show_dir in shows:
        show_dir = Path(show_dir)
        jobs = []
        for mkv in sorted(show_dir.glob("*.mkv")):
            ep = extract_episode_id(mkv.name)
            if not ep: continue
            b = show_dir / f"{ep}双语版-1080p.ass"
            if not b.exists(): b = show_dir / f"{ep}双语版-2160p.ass"
            if b.exists():
                jobs.append((mkv, [(str(b), _ass_label(b))])); continue
            c = show_dir / f"{ep}中文版-1080p.ass"
            if not c.exists(): c = show_dir / f"{ep}中文版-2160p.ass"
            if c.exists():
                jobs.append((mkv, [(str(c), _ass_label(c))]))
        if jobs: results.append((show_dir, jobs))
    return results

def count_remux_jobs(shows):
    return sum(len(items) for _, items in collect_remux_jobs(shows))

def remux_one_mkv(mkv_path, ass_list, log):
    mkv_path = Path(mkv_path)
    temp_path = mkv_path.with_suffix(".temp.mkv")
    cmd = [TOOLS["mkvmerge"], "-o", str(temp_path), "--no-subtitles", str(mkv_path)]
    for ass_path, label in ass_list:
        cmd.extend(["--track-name", f"0:{label}", "--language", "0:chi", ass_path])
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode >= 2:
        if temp_path.exists(): temp_path.unlink()
        raise RuntimeError(f"mkvmerge failed (code={proc.returncode})\nstderr: {proc.stderr.strip()}")
    mkv_path.unlink(); temp_path.rename(mkv_path)

def batch_remux(shows, log, on_progress=None, progress_state=None, should_cancel=None):
    jobs = collect_remux_jobs(shows); total = sum(len(items) for _, items in jobs)
    done = ok = fail = 0
    if total == 0: log("no jobs"); return ok,fail,0
    log(f"{len(jobs)} shows / {total} videos")
    for show_dir, items in jobs:
        log(f"\n[{show_dir.name}]")
        for mkv, ass_info in items:
            if should_cancel and should_cancel(): return ok,fail,0
            done += 1
            if on_progress and progress_state:
                on_progress(done, progress_state["total"] or total, show_dir.name, mkv.name)
            a = [Path(x).name for x,_ in ass_info]; l = [y for _,y in ass_info]
            log(f"  [{done}/{total}] {mkv.name}\n    ASS: {', '.join(a)}\n    labels: {', '.join(l)}")
            try: remux_one_mkv(mkv, ass_info, log); ok += 1
            except Exception as e: log(f"  FAIL: {e}"); fail += 1
    return ok,fail,0

def embed_remux_panel(parent, app):
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from config import SUBTITLE_ROOT, set_subtitle_root
    from utils import iter_show_dirs_with_mkv
    dlg = app if app else parent.winfo_toplevel()
    state = {"root_path": SUBTITLE_ROOT or (app.subtitle_root if app else "") or ""}
    def log(m):
        if app: app.log_msg(m)
    def refresh():
        p = state["root_path"].strip()
        if not p or not Path(p).is_dir(): stats_var.set("select dir"); return
        s = iter_show_dirs_with_mkv(p); j = count_remux_jobs(s)
        stats_var.set(f"{len(s)} shows / {j} videos")
    def browse():
        p = filedialog.askdirectory(initialdir=state["root_path"] or None, parent=dlg)
        if not p: return
        state["root_path"] = p; root_var.set(p); set_subtitle_root(p)
        if app: app.subtitle_root = p
        refresh(); log(f"dir: {p}")
    root_row = tk.Frame(parent); root_row.pack(fill=tk.X, padx=10, pady=8)
    tk.Label(root_row, text="root:").pack(side=tk.LEFT)
    root_var = tk.StringVar(value=state["root_path"])
    tk.Entry(root_row, textvariable=root_var, width=52).pack(side=tk.LEFT, padx=5)
    tk.Button(root_row, text="browse", command=browse).pack(side=tk.LEFT)
    tk.Label(parent, text="remux ASS into MKV").pack(fill=tk.X, padx=10)
    stats_var = tk.StringVar(value="")
    tk.Label(parent, textvariable=stats_var).pack(fill=tk.X, padx=10)
    btn_row = tk.Frame(parent); btn_row.pack(fill=tk.X, padx=10, pady=4)
    def run():
        if app and app._warn_if_busy(dlg): return
        root = state["root_path"]
        if not root or not Path(root).is_dir(): messagebox.showwarning("", "select dir",parent=dlg); return
        shows = iter_show_dirs_with_mkv(root)
        if not shows: log("no mkv"); return
        jt = count_remux_jobs(shows)
        if jt == 0: log("no ass"); return
        if app: app.show_log_tab(); app._remuxing_ass = True; app._begin_batch_task(f"{jt} videos...")
        def worker():
            try:
                ok,fail,skip = batch_remux(shows,log=app._log_threadsafe if app else log,
                    on_progress=app._progress_callback if app else None,
                    progress_state={"done":0,"total":jt} if app else None,
                    should_cancel=app._task_cancel.is_set if app else None)
                s = "done" if not (app and app._task_cancel.is_set()) else "stopped"
                if app: app._log_threadsafe(f"\n{s}"); app.after(0,lambda:app._finish_batch_task(100,s)); app.after(0,refresh)
            except Exception as e:
                if app: app._log_threadsafe(f"err:{e}"); app.after(0,lambda err=str(e):app._finish_batch_task(0,f"err:{err}"))
            finally:
                if app: app._remuxing_ass = False
        import threading; threading.Thread(target=worker, daemon=True).start()
    tk.Button(btn_row, text="start", command=run).pack(side=tk.LEFT)
    if state["root_path"]: refresh()

def show_remux_tab(app):
    if app and hasattr(app, "_remux_tab"): app.notebook.select(app._remux_tab)
