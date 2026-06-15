# audio_trim.py — 批量去掉非原语言音轨，保留视频/字幕轨（mkvmerge 重封装，不转码）
import json
import re
import threading
from pathlib import Path

from config import TOOLS
from utils import (
    check_output_hidden,
    run_hidden,
    extract_folder_audio_lang,
    normalize_audio_lang,
)

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[a-zA-Z]")
_ORIG_AUDIO_SUFFIX = "_原音"


def _parse_tracks(info):
    if "tracks" in info and isinstance(info["tracks"], list):
        return info["tracks"]
    if "track_info" in info:
        return info["track_info"]
    if "tracks" in info and "track" in info["tracks"]:
        return info["tracks"]["track"]
    return []


def folder_has_cjk_and_latin(folder_name: str) -> bool:
    """文件夹名同时含中文与英文字母，如「赌命大翻身tur」"""
    if not folder_name:
        return False
    return bool(_CJK_RE.search(folder_name)) and bool(_LATIN_RE.search(folder_name))


def folder_eligible_for_audio_trim(folder_name: str) -> bool:
    if not folder_has_cjk_and_latin(folder_name):
        return False
    return extract_folder_audio_lang(folder_name) is not None


def is_trim_source_mkv(path: Path) -> bool:
    return path.suffix.lower() == ".mkv" and not path.stem.endswith(_ORIG_AUDIO_SUFFIX)


def trim_output_path(mkv: Path) -> Path:
    return mkv.with_name(f"{mkv.stem}{_ORIG_AUDIO_SUFFIX}.mkv")


def iter_audio_trim_dirs(selected, forced_lang=None):
    """
    列出待处理剧集目录：
    - 文件夹名含中文+英文且能解析末尾语言码（自动识别模式）
    - 用户指定语言时，只要文件夹内有 MKV 即可
    - 支持选根目录（批量）或单部剧文件夹
    """
    selected = Path(selected)
    if not selected.is_dir():
        return []

    # 用户指定了原版语言 → 放宽条件，只要有 MKV 就算
    if forced_lang:
        if list(selected.glob("*.mkv")):
            return [selected]
        shows = []
        for child in sorted(selected.iterdir()):
            if not child.is_dir():
                continue
            if any(is_trim_source_mkv(p) for p in child.glob("*.mkv")):
                shows.append(child)
        return shows

    # 自动识别模式（原逻辑）
    if list(selected.glob("*.mkv")):
        if folder_eligible_for_audio_trim(selected.name):
            return [selected]
        return []

    shows = []
    for child in sorted(selected.iterdir()):
        if not child.is_dir():
            continue
        if not folder_eligible_for_audio_trim(child.name):
            continue
        if any(is_trim_source_mkv(p) for p in child.glob("*.mkv")):
            shows.append(child)
    return shows


def list_trim_mkvs(show_dir: Path):
    return sorted(p for p in show_dir.glob("*.mkv") if is_trim_source_mkv(p))


def count_audio_trim_jobs(show_dirs):
    return sum(len(list_trim_mkvs(d)) for d in show_dirs)


def _read_mkv_tracks(mkv: Path):
    info = json.loads(
        check_output_hidden(
            [TOOLS["mkvmerge"], "-J", str(mkv)],
            encoding="utf-8",
        )
    )
    return _parse_tracks(info)


def _collect_audio_tracks(tracks):
    out = []
    for t in tracks:
        if (t.get("type") or "").lower() != "audio":
            continue
        props = t.get("properties", {})
        out.append(
            {
                "id": t.get("id"),
                "raw": props.get("language", ""),
                "name": props.get("track_name") or "",
            }
        )
    return out


def _track_lang_code(track) -> str:
    lang = normalize_audio_lang(track.get("raw", ""))
    return lang if lang and lang != "und" else ""


def plan_mkv_audio_trim(mkv: Path, folder_lang: str, skip_existing: bool = True):
    """
    分析单个 mkv 是否需处理。
    返回 (action, keep_ids, message)
    action: process | skip
    """
    folder_lang = normalize_audio_lang(folder_lang)
    out_path = trim_output_path(mkv)

    if skip_existing and out_path.is_file():
        return "skip", [], f"已存在 {out_path.name}"

    tracks = _read_mkv_tracks(mkv)
    audio_tracks = _collect_audio_tracks(tracks)
    if len(audio_tracks) < 2:
        return "skip", [], f"音轨不足 2 条（{len(audio_tracks)}）"

    matching = [t for t in audio_tracks if _track_lang_code(t) == folder_lang]
    if not matching:
        langs = ", ".join(_track_lang_code(t) or "und" for t in audio_tracks)
        return "skip", [], f"无 language={folder_lang} 的音轨（当前：{langs}）"

    non_matching = [t for t in audio_tracks if _track_lang_code(t) != folder_lang]
    if not non_matching:
        return "skip", [], "全部为原语言音轨，无需处理"

    keep_ids = [t["id"] for t in matching]
    removed = len(non_matching)
    names = ", ".join(
        f"id={t['id']}({_track_lang_code(t) or 'und'})" for t in non_matching
    )
    return (
        "process",
        keep_ids,
        f"保留 {len(keep_ids)} 条原语言轨，去掉 {removed} 条（{names}）",
    )


def remux_mkv_keep_audio(mkv: Path, out_path: Path, audio_ids, log=None):
    """重封装：保留指定音轨 + 全部视频/字幕轨"""
    id_str = ",".join(str(int(i)) for i in sorted(audio_ids, key=int))
    temp = out_path.with_suffix(".mkv.__trim__")
    if temp.exists():
        temp.unlink()

    cmd = [TOOLS["mkvmerge"], "-o", str(temp), "-a", id_str, str(mkv)]
    result = run_hidden(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        temp.unlink(missing_ok=True)
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err or f"mkvmerge 退出码 {result.returncode}")

    temp.replace(out_path)
    if log:
        log(f"✅ {mkv.name} → {out_path.name}")


def count_deletable_original_mkvs(show_dirs):
    """已有对应 _原音.mkv 的原始 mkv 数量"""
    n = 0
    for show_dir in show_dirs:
        for mkv in list_trim_mkvs(show_dir):
            out = trim_output_path(mkv)
            if out.is_file() and out.stat().st_size > 0 and mkv.is_file():
                n += 1
    return n


def safe_delete_original_mkv(mkv: Path, log=None) -> bool:
    """仅当对应 _原音.mkv 已存在且非空时删除原 mkv"""
    out = trim_output_path(mkv)
    if not mkv.is_file():
        return False
    if not out.is_file() or out.stat().st_size <= 0:
        if log:
            log(f"⚠️ 未删除 {mkv.name}：缺少有效的 {out.name}")
        return False
    try:
        mkv.unlink()
        if log:
            log(f"🗑️ 已删除原文件：{mkv.name}")
        return True
    except OSError as e:
        if log:
            log(f"⚠️ 删除失败：{mkv.name} | {e}")
        return False


def trim_mkv_audio(
    mkv: Path,
    folder_lang: str,
    log=None,
    skip_existing: bool = True,
    delete_original: bool = False,
):
    """
    处理单个 mkv。
    返回 processed | skipped | failed | deleted（仅删除原文件、未重封装）
    """
    try:
        out_path = trim_output_path(mkv)
        action, keep_ids, msg = plan_mkv_audio_trim(
            mkv, folder_lang, skip_existing=skip_existing
        )
        if action == "skip":
            if log:
                log(f"⏭️ {mkv.name}：{msg}")
            if delete_original and out_path.is_file():
                if safe_delete_original_mkv(mkv, log=log):
                    return "deleted"
            return "skipped"
        if log:
            log(f"🔧 {mkv.name}：{msg}")
        remux_mkv_keep_audio(mkv, out_path, keep_ids, log=log)
        if delete_original:
            safe_delete_original_mkv(mkv, log=log)
        return "processed"
    except Exception as e:
        if log:
            log(f"❌ {mkv.name}：{e}")
        return "failed"


def batch_delete_original_mkvs(show_dirs, log=None, on_progress=None, should_cancel=None):
    """批量删除已有 _原音.mkv 的原始 mkv。返回删除数量"""
    jobs = []
    for show in show_dirs:
        for mkv in list_trim_mkvs(show):
            if trim_output_path(mkv).is_file() and mkv.is_file():
                jobs.append((show, mkv))

    total = len(jobs)
    deleted = 0
    for i, (show, mkv) in enumerate(jobs):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(i, total, show.name, f"删除 {mkv.name}")
        if log:
            log(f"\n=== [{i + 1}/{total}] 删除原文件 {show.name} / {mkv.name} ===")
        if safe_delete_original_mkv(mkv, log=log):
            deleted += 1

    if on_progress:
        on_progress(total, total, "", "完成")
    return deleted


def batch_trim_audio(
    root_path,
    log=None,
    on_progress=None,
    skip_existing=True,
    show_dirs=None,
    delete_original=False,
    should_cancel=None,
    forced_lang=None,
):
    """
    批量去多余音轨。
    show_dirs 可显式传入剧集列表；否则从 root_path 扫描。
    forced_lang 用户指定原版语言：不为空时覆盖文件夹自动识别。
    返回 (成功, 跳过, 失败, 仅删除原文件数)
    """
    shows = list(show_dirs) if show_dirs is not None else iter_audio_trim_dirs(root_path, forced_lang=forced_lang)
    if not shows:
        return 0, 0, 0, 0

    jobs = []
    for show in shows:
        lang = forced_lang if forced_lang else extract_folder_audio_lang(show.name)
        for mkv in list_trim_mkvs(show):
            jobs.append((show, mkv, lang))

    total = len(jobs)
    ok = skip = fail = deleted = 0
    for i, (show, mkv, lang) in enumerate(jobs):
        if should_cancel and should_cancel():
            break
        if on_progress:
            on_progress(i, total, show.name, f"处理 {mkv.name}")
        if log:
            log(f"\n=== [{i + 1}/{total}] {show.name} / {mkv.name}（原语言 {lang}）===")

        result = trim_mkv_audio(
            mkv,
            lang,
            log=log,
            skip_existing=skip_existing,
            delete_original=delete_original,
        )
        if result == "processed":
            ok += 1
        elif result == "deleted":
            deleted += 1
        elif result == "skipped":
            skip += 1
        else:
            fail += 1

    if on_progress:
        on_progress(total, total, "", "完成")
    return ok, skip, fail, deleted


def resolve_audio_trim_scope(selected, forced_lang=None):
    """
    解析用户选择的路径。
    返回 (root, shows, mode_label) 或 (None, [], "") 表示无效。
    mode_label: single | batch
    """
    selected = Path(selected)
    shows = iter_audio_trim_dirs(selected, forced_lang=forced_lang)
    if not shows:
        return None, [], ""

    if len(shows) == 1 and shows[0].resolve() == selected.resolve():
        parent = shows[0].parent
        root = parent if parent.is_dir() else selected
        return root, shows, "single"

    return selected, shows, "batch"


def embed_audio_trim_panel(parent, app):
    """音轨处理面板：批量去多余音轨 + 手动批量删除原 mkv"""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from config import SUBTITLE_ROOT, set_subtitle_root

    dlg_parent = app if app else parent.winfo_toplevel()
    state = {"root_path": app.subtitle_root if app else SUBTITLE_ROOT or ""}

    # 变量声明
    stats_var = tk.StringVar(value="")
    from utils import COMMON_LANG_OPTIONS as _common_lang
    _lang_options = list(_common_lang)
    _lang_var = tk.StringVar(value="自动识别")

    def log(msg):
        if app:
            app.log_msg(msg)

    def refresh_stats():
        path = app.subtitle_root if app else ""
        forced_lang = _get_forced_lang()
        if not path or not Path(path).is_dir():
            stats_var.set("请先选择字幕根目录")
            return
        shows = iter_audio_trim_dirs(path, forced_lang=forced_lang)
        if not shows:
            base = "未找到符合条件的剧集文件夹"
            if not forced_lang:
                base += "（须含中文+末尾语言码，如 赌命大翻身tur）"
            stats_var.set(base)
            return
        jobs = count_audio_trim_jobs(shows)
        deletable = count_deletable_original_mkvs(shows)
        stats_var.set(
            f"共 {len(shows)} 部剧 · 待去音轨 {jobs} 个 · 可删原 mkv {deletable} 个"
        )

    def _get_forced_lang():
        label = _lang_var.get()
        label = _lang_var.get()
        for lbl, code in _lang_options:
            if lbl == label:
                return code
        return ""

    def _on_lang_selected(*_):
        label = _lang_var.get()
        if label == "自定义…":
            import tkinter.simpledialog as sd
            custom = sd.askstring("自定义语言", "输入 ISO 639-2 三字母语言码（如 vie）",
                                  parent=dlg_parent)
            if custom and custom.strip():
                code = custom.strip().lower()
                display = f"{code.upper()} ({code})"
                new_options = list(lang_menu["values"])
                idx = list(new_options).index("自定义…") + 1
                new_options = list(new_options[:idx]) + [display] + list(new_options[idx:])
                lang_menu["values"] = new_options
                _lang_var.set(display)
                _lang_options.append((display, code))
            else:
                _lang_var.set("自动识别")

        # 更新提示文字
        forced = _get_forced_lang()
        if forced:
            hint_label.config(
                text=f"用户指定原版语言 {forced}，文件夹名不再需要末尾语言码限制。\n"
                     "先去多余音轨生成 S01E01_原音.mkv，确认无误后再点「批量删除原 MKV」。"
            )
        else:
            hint_label.config(
                text="自动识别：文件夹名须同时含中文与英文字母，且末尾能识别语言码。\n"
                     "先去多余音轨生成 S01E01_原音.mkv，确认无误后再点「批量删除原 MKV」。"
            )
        refresh_stats()

    lang_row = tk.Frame(parent)
    lang_row.pack(fill=tk.X, padx=10, pady=(2, 2))
    tk.Label(lang_row, text="原版语言：", font=("微软雅黑", 10), fg="#7f8c8d").pack(side=tk.LEFT)
    lang_menu = ttk.Combobox(
        lang_row, textvariable=_lang_var, font=("微软雅黑", 9),
        state="readonly", width=20,
        values=[lbl for lbl, _ in _lang_options],
    )
    lang_menu.pack(side=tk.LEFT, padx=(8, 0))

    hint_label = tk.Label(
        parent,
        text=(
            "自动识别：文件夹名须同时含中文与英文字母，且末尾能识别语言码（如 赌命大翻身tur）。\n"
            "先去多余音轨生成 S01E01_原音.mkv，确认无误后再点「批量删除原 MKV」。"
        ),
        justify=tk.LEFT,
        fg="#7f8c8d", font=("微软雅黑", 9),
    )
    hint_label.pack(fill=tk.X, padx=10, pady=(0, 6))

    stats_var = tk.StringVar(value="")
    tk.Label(parent, textvariable=stats_var, anchor="w", fg="#2d6cc9", font=("微软雅黑", 10, "bold")).pack(
        fill=tk.X, padx=10, pady=(0, 8)
    )

    btn_row = tk.Frame(parent)
    btn_row.pack(fill=tk.X, padx=10, pady=4)

    # 最后再绑定 trace 和设默认值（确保所有变量已在作用域内）
    _lang_var.trace_add("write", _on_lang_selected)
    lang_menu.current(0)

    def require_root():
        path = app.subtitle_root if app else ""
        if not path:
            messagebox.showwarning("提示", "请先在顶部全局目录栏选择有效的字幕根目录", parent=dlg_parent)
            return None
        return Path(path)

    def run_trim():
        if app and app._warn_if_busy(dlg_parent):
            return
        selected = require_root()
        if not selected:
            return

        forced_lang = _get_forced_lang()

        root, shows, mode = resolve_audio_trim_scope(selected, forced_lang=forced_lang)
        if not shows:
            log(
                f"❌ 未找到符合条件的文件夹：{selected}"
            )
            return

        job_total = count_audio_trim_jobs(shows)
        if job_total == 0:
            log("❌ 没有待去音轨的 mkv（可能已全部生成 _原音，或音轨无需处理）")
            refresh_stats()
            return

        if mode == "single":
            log(f"📂 单部剧模式：{shows[0].name}")
            set_subtitle_root(shows[0].parent)
            if app:
                app.subtitle_root = str(shows[0].parent)
        else:
            log(f"📂 批量模式：{len(shows)} 部剧 / {job_total} 个 mkv")
            set_subtitle_root(selected)
            if app:
                app.subtitle_root = str(selected)

        if not messagebox.askyesno(
            "批量去多余音轨",
            f"将对 {len(shows)} 部剧中 {job_total} 个 mkv 去掉非原语言音轨。\n"
            "输出为 原名_原音.mkv，原文件保留。\n\n是否继续？",
            parent=dlg_parent,
        ):
            return

        if app:
            app.show_log_tab()
            app._trimming_audio = True
            app._begin_batch_task(f"准备去多余音轨：{job_total} 个 mkv…")
        log(f"\n━━━ 批量去多余音轨：{', '.join(s.name for s in shows)} ━━━")

        def worker():
            try:
                ok, skip, fail, _deleted = batch_trim_audio(
                    root,
                    log=app._log_threadsafe if app else log,
                    on_progress=app._progress_callback if app else None,
                    skip_existing=True,
                    show_dirs=shows,
                    delete_original=False,
                    should_cancel=app._task_cancel.is_set if app else None,
                    forced_lang=forced_lang,
                )
                parts = [f"成功 {ok}", f"跳过 {skip}", f"失败 {fail}"]
                if app and app._task_cancel.is_set():
                    summary = f"去音轨已停止：{', '.join(parts)}"
                else:
                    summary = f"去音轨完成：{', '.join(parts)}"
                if app:
                    app._log_threadsafe(f"\n🎉 {summary}")
                    app.after(0, lambda s=summary: app._finish_batch_task(100, s))
                    app.after(0, refresh_stats)
                else:
                    log(f"\n🎉 {summary}")
            except Exception as e:
                if app:
                    app._log_threadsafe(f"❌ 去音轨异常：{e}")
                    app.after(0, lambda err=str(e): app._finish_batch_task(0, f"出错：{err}"))
                else:
                    log(f"❌ 去音轨异常：{e}")
            finally:
                if app:
                    app._trimming_audio = False

        threading.Thread(target=worker, daemon=True).start()

    def run_delete():
        if app and app._warn_if_busy(dlg_parent):
            return
        selected = require_root()
        if not selected:
            return

        _root, shows, mode = resolve_audio_trim_scope(selected)
        if not shows:
            log("❌ 未找到符合条件的文件夹")
            return

        deletable = count_deletable_original_mkvs(shows)
        if deletable == 0:
            log("❌ 未找到可删除的原 mkv（须已有对应且有效的 *_原音.mkv）")
            refresh_stats()
            return

        if mode == "single":
            log(f"📂 单部剧模式：{shows[0].name}")
        else:
            log(f"📂 批量模式：{len(shows)} 部剧 / {deletable} 个可删")

        if not messagebox.askyesno(
            "批量删除原 MKV",
            f"将删除 {deletable} 个原始 mkv（须已有对应 _原音.mkv）。\n\n"
            "删除后不可恢复，是否继续？",
            parent=dlg_parent,
        ):
            return

        if app:
            app.show_log_tab()
            app._deleting_mkv = True
            app._begin_batch_task(f"准备删除 {deletable} 个原 mkv…")
        log(f"\n━━━ 批量删除原 mkv（{deletable} 个）━━━")

        def worker():
            try:
                n = batch_delete_original_mkvs(
                    shows,
                    log=app._log_threadsafe if app else log,
                    on_progress=app._progress_callback if app else None,
                    should_cancel=app._task_cancel.is_set if app else None,
                )
                if app and app._task_cancel.is_set():
                    summary = f"删除原 mkv 已停止：已完成 {n} 个"
                else:
                    summary = f"删除原 mkv 完成：共 {n} 个"
                if app:
                    app._log_threadsafe(f"\n🎉 {summary}")
                    app.after(0, lambda s=summary: app._finish_batch_task(100, s))
                    app.after(0, refresh_stats)
                else:
                    log(f"\n🎉 {summary}")
            except Exception as e:
                if app:
                    app._log_threadsafe(f"❌ 删除原 mkv 异常：{e}")
                    app.after(0, lambda err=str(e): app._finish_batch_task(0, f"出错：{err}"))
                else:
                    log(f"❌ 删除原 mkv 异常：{e}")
            finally:
                if app:
                    app._deleting_mkv = False

        threading.Thread(target=worker, daemon=True).start()

    tk.Button(btn_row, text="批量去多余音轨", command=run_trim, width=16, font=("微软雅黑",10,"bold"), fg="white", bg="#2d6cc9", activebackground="#1a4f8a", activeforeground="white", bd=0, padx=16, pady=4, cursor="hand2").pack(
        side=tk.LEFT, padx=(0, 8)
    )
    tk.Button(btn_row, text="批量删除原 MKV", command=run_delete, width=16, font=("微软雅黑",10,"bold"), fg="white", bg="#2d6cc9", activebackground="#1a4f8a", activeforeground="white", bd=0, padx=16, pady=4, cursor="hand2").pack(
        side=tk.LEFT
    )

    if app and app.subtitle_root:
        refresh_stats()


def show_audio_trim_tab(app):
    """切换到音轨处理标签页"""
    if app and hasattr(app, "_audio_trim_tab"):
        app.notebook.select(app._audio_trim_tab)

