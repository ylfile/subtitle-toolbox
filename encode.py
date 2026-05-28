# encode.py — 批量压制：MKV + ASS 硬字幕（进度 / 停止 / GPU 回退 CPU）
import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from ass import find_ass_for_mkv
from encode_settings import (
    build_video_encode_args,
    build_container_args,
    output_extension,
    resolve_bitrate_kbps,
    settings_summary,
    normalize_encode_settings,
    cpu_fallback_settings,
    ensure_gpu_settings,
    pick_encoder_name,
    probe_duration_seconds,
    probe_video_fps_text,
    ffprobe_available,
    normalize_fps_rate,
    build_subtitle_video_filter_parts,
    build_fps_encode_args,
    probe_audio_stream_info,
    _ffmpeg_exe,
)

_active_proc = None
_proc_lock = threading.Lock()

DURATION_RE = re.compile(
    r"Duration:\s*(\d{2}):(\d{2}):(\d{2})[.](\d+)", re.IGNORECASE
)
TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})[.](\d+)")


def register_encode_process(proc):
    global _active_proc
    with _proc_lock:
        _active_proc = proc


def clear_encode_process(proc):
    global _active_proc
    with _proc_lock:
        if _active_proc is proc:
            _active_proc = None


def stop_encode_process():
    global _active_proc
    with _proc_lock:
        proc = _active_proc
    if not proc:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    with _proc_lock:
        if _active_proc is proc:
            _active_proc = None


def _time_to_seconds(h, m, s, frac):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 100.0


def probe_duration(video_path):
    return probe_duration_seconds(video_path)


def build_video_filter(ass_path, vf_kind="ass", video_fps=None):
    vf_expr = build_subtitle_vf(ass_path, vf_kind)
    return build_subtitle_video_filter_parts(vf_expr, video_fps)


def build_audio_encode_args(video_path):
    info = probe_audio_stream_info(video_path)
    args = ["-c:a", "aac", "-b:a", "192k"]
    if info.get("sample_rate"):
        args.extend(["-ar", str(info["sample_rate"])])
    if info.get("channels"):
        args.extend(["-ac", str(info["channels"])])
    return args, info


def path_for_ffmpeg_filter(path):
    s = str(Path(path).resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        s = s[0] + "\\:" + s[2:]
    s = s.replace("'", "'\\''")
    return s


def build_subtitle_vf(ass_path, vf_kind="ass"):
    ass_esc = path_for_ffmpeg_filter(ass_path)
    if vf_kind == "subtitles":
        return f"subtitles=filename='{ass_esc}':charenc=UTF-8"
    return f"ass=filename='{ass_esc}'"


def prepare_ass_for_filter(ass_path, near_dir=None):
    """
    复制 ASS 到纯 ASCII 路径，避免中文路径导致 libass 滤镜失败。
    优先放在视频同目录，减少临时目录权限问题。
    返回 (临时路径, 是否临时文件)
    """
    ass_path = Path(ass_path).resolve()
    try:
        ass_path.read_text(encoding="utf-8")
    except Exception:
        return ass_path, False

    key = hashlib.md5(
        f"{ass_path}:{ass_path.stat().st_mtime_ns}".encode("utf-8")
    ).hexdigest()[:12]
    # 优先放在视频目录（通常为 ASCII）；失败则退回系统临时目录
    candidates = []
    if near_dir:
        candidates.append(Path(near_dir))
    candidates.append(Path(tempfile.gettempdir()) / "ylfile_ass")
    tmp = None
    for tmp_dir in candidates:
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
            dest = tmp_dir / f"sub_{key}.ass"
            shutil.copy2(ass_path, dest)
            tmp = dest
            break
        except OSError:
            continue
    if tmp is None:
        return ass_path, False
    return tmp, True


def cleanup_staged_ass(staged_path, is_temp):
    if is_temp and staged_path and Path(staged_path).is_file():
        try:
            Path(staged_path).unlink(missing_ok=True)
        except Exception:
            pass


def cleanup_failed_output(out_path):
    out_path = Path(out_path)
    if out_path.is_file():
        try:
            if out_path.stat().st_size == 0:
                out_path.unlink()
        except Exception:
            pass


def build_encode_cmd(
    mkv_path,
    ass_path,
    out_path,
    encode_settings=None,
    bitrate_kbps=None,
    vf_kind="ass",
    video_fps=None,
):
    settings = normalize_encode_settings(encode_settings)
    audio_args, _ = build_audio_encode_args(mkv_path)
    vf_chain, vf_rate = build_video_filter(ass_path, vf_kind, video_fps)
    enc_args, enc_rate = build_fps_encode_args(
        video_fps, settings.get("container", "mp4")
    )
    sync_rate = enc_rate or vf_rate
    cmd = [
        _ffmpeg_exe(),
        "-hide_banner",
        "-y",
        "-i",
        str(mkv_path),
        "-sn",
        "-dn",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vf_chain,
    ]
    cmd.extend(build_video_encode_args(settings, bitrate_kbps))
    cmd.extend(enc_args)
    cmd.extend(
        [
            "-max_muxing_queue_size",
            "4096",
            "-muxpreload",
            "0",
            "-muxdelay",
            "0",
        ]
    )
    cmd.extend(audio_args)
    cmd.extend(build_container_args(settings))
    cmd.append(str(out_path))
    return cmd, sync_rate


def _stderr_tail(text, max_lines=8):
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return "\n   ".join(lines[-max_lines:])


def _should_retry(attempt_idx, total_attempts, settings, returncode, err_text):
    if returncode == 0 or attempt_idx >= total_attempts - 1:
        return False
    err = (err_text or "").lower()
    if "contradictory" in err or (
        "invalid argument" in err and "opening output" in err
    ):
        return False
    markers = (
        "nvenc", "amf", "qsv", "could not open encoder",
        "operation not permitted", "error code: -1",
        "ass", "subtitles", "libass", "font", "filter",
    )
    return any(m in err for m in markers)


def _build_encode_attempts(settings):
    s = normalize_encode_settings(settings)
    attempts = []
    if s["mode"] == "gpu":
        attempts.append((s, "ass"))
        attempts.append((s, "subtitles"))
        cpu = cpu_fallback_settings(s)
        attempts.append((cpu, "ass"))
        attempts.append((cpu, "subtitles"))
    else:
        attempts.append((s, "ass"))
        attempts.append((s, "subtitles"))
    return attempts


def _run_ffmpeg(cmd, duration, on_progress, cancel_event, log):
    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            stderr=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except FileNotFoundError:
        log("❌ 未找到 ffmpeg，请将 ffmpeg.exe 放在程序目录")
        return -1, "", False
    except Exception as e:
        log(f"❌ 启动 ffmpeg 失败：{e}")
        return -1, str(e), False

    register_encode_process(proc)
    err_lines = []
    last_pct = -1
    cancelled = False

    try:
        for line in proc.stderr:
            if cancel_event and cancel_event.is_set():
                cancelled = True
                stop_encode_process()
                return proc.returncode or -1, "\n".join(err_lines), True

            err_lines.append(line.rstrip())
            if len(err_lines) > 200:
                err_lines = err_lines[-100:]

            if not on_progress:
                continue

            if duration is None:
                m = DURATION_RE.search(line)
                if m:
                    duration = _time_to_seconds(*m.groups())

            m = TIME_RE.search(line)
            if m and duration and duration > 0:
                cur = _time_to_seconds(*m.groups())
                pct = min(99, int(cur * 100 / duration))
                if pct != last_pct:
                    last_pct = pct
                    on_progress(
                        pct,
                        f"编码中 {pct}%（{m.group(1)}:{m.group(2)}:{m.group(3)}）",
                    )
            elif "frame=" in line:
                on_progress(last_pct if last_pct >= 0 else 0, "编码中…")

        proc.wait()
    except Exception as e:
        log(f"❌ 读取进度失败：{e}")
        stop_encode_process()
        return -1, str(e), cancelled
    finally:
        clear_encode_process(proc)

    return proc.returncode, "\n".join(err_lines), cancelled


def encode_one(
    mkv_path,
    ass_path,
    out_path,
    log,
    skip_existing=False,
    on_progress=None,
    cancel_event=None,
    encode_settings=None,
):
    mkv_path = Path(mkv_path)
    ass_path = Path(ass_path)
    out_path = Path(out_path)

    if skip_existing and out_path.is_file():
        if out_path.stat().st_mtime >= mkv_path.stat().st_mtime and (
            out_path.stat().st_mtime >= ass_path.stat().st_mtime
        ):
            log(f"⏭️ 已存在且较新，跳过：{out_path.name}")
            if on_progress:
                on_progress(100, "已跳过")
            return "skip"

    settings = ensure_gpu_settings(encode_settings, log)
    duration = probe_duration(mkv_path)
    video_fps = probe_video_fps_text(mkv_path)
    staged_ass, is_temp = prepare_ass_for_filter(ass_path, mkv_path.parent)

    try:
        attempts = _build_encode_attempts(settings)

        last_err = ""
        for idx, (attempt, vf_kind) in enumerate(attempts):
            if cancel_event and cancel_event.is_set():
                return "fail"

            if idx > 0:
                cleanup_failed_output(out_path)
                prev = attempts[idx - 1]
                if prev[1] != vf_kind:
                    log("⚠️ ass 滤镜失败，改用 subtitles 滤镜重试")
                elif normalize_encode_settings(prev[0]).get("mode") == "gpu":
                    log(
                        f"⚠️ GPU 硬编失败，自动改用 CPU："
                        f"{settings_summary(attempt)}"
                    )
                if on_progress:
                    on_progress(0, "重试…")

            br = resolve_bitrate_kbps(attempt, mkv_path, log if idx == 0 else None)
            enc_name = pick_encoder_name(attempt)
            if idx == 0:
                log(
                    f"🎬 压制：{mkv_path.name} + {ass_path.name} "
                    f"[{settings_summary(attempt)} · {enc_name}]"
                )
            elif idx > 0:
                log(f"   重试：{enc_name}（{vf_kind}）")
            if on_progress and idx == 0:
                on_progress(0, "准备中…")

            cmd, sync_rate = build_encode_cmd(
                mkv_path,
                staged_ass,
                out_path,
                attempt,
                br,
                vf_kind,
                video_fps,
            )
            rc, err_text, cancelled = _run_ffmpeg(
                cmd, duration, on_progress, cancel_event, log
            )
            last_err = err_text

            if cancelled or (cancel_event and cancel_event.is_set()):
                cleanup_failed_output(out_path)
                log(f"⚠️ 已取消：{mkv_path.name}")
                return "fail"

            if rc == 0 and out_path.is_file() and out_path.stat().st_size > 0:
                if on_progress:
                    on_progress(100, "本集完成")
                size_mb = out_path.stat().st_size / (1024 * 1024)
                log(f"✅ 完成：{out_path.name}（约 {size_mb:.1f} MB）")
                return "ok"

            cleanup_failed_output(out_path)
            if _should_retry(idx, len(attempts), attempt, rc, err_text):
                continue
            break

        log(f"❌ 压制失败：{mkv_path.name}（退出码 {rc}）")
        tail = _stderr_tail(last_err)
        if tail:
            log(f"   {tail}")
        log("   建议：压制参数改为「CRF 质量 + H.264」，或检查 ffmpeg/显卡驱动")
        return "fail"
    finally:
        cleanup_staged_ass(staged_ass, is_temp)


def encode_show(
    show_dir,
    log,
    skip_existing=False,
    on_progress=None,
    cancel_event=None,
    file_index_offset=0,
    file_total=None,
    encode_settings=None,
):
    show_dir = Path(show_dir)
    mkvs = sorted(show_dir.glob("*.mkv"))
    if not mkvs:
        log(f"⚠️「{show_dir.name}」无 .mkv，跳过")
        return 0, 0, 0

    effective = ensure_gpu_settings(encode_settings, log)
    if not ffprobe_available():
        log(
            "ℹ️ 未找到 ffprobe.exe，码率探测将改用 ffmpeg；"
            "建议将 ffprobe.exe 与 ffmpeg.exe 放在同目录"
        )

    log(f"\n━━━ 压制：{show_dir.name}（{len(mkvs)} 个视频）━━━")

    ok = fail = skip = 0
    total = file_total if file_total else len(mkvs)

    for i, mkv in enumerate(mkvs):
        if cancel_event and cancel_event.is_set():
            log("⚠️ 用户已取消批量压制")
            break

        ass = find_ass_for_mkv(show_dir, mkv)
        if not ass or not ass.exists():
            log(
                f"❌ 未找到 ASS：{mkv.name}（需要 {mkv.stem}双语*.ass 或 {mkv.stem}.ass）"
            )
            fail += 1
            continue

        global_idx = file_index_offset + i

        def file_progress(pct, status, _gi=global_idx, _mkv=mkv):
            if on_progress:
                overall = int((_gi + pct / 100.0) * 100 / total)
                on_progress(
                    min(100, overall),
                    f"「{show_dir.name}」{_mkv.name} — {status}",
                )

        ext = output_extension(normalize_encode_settings(effective))
        out = show_dir / f"{mkv.stem}{ext}"
        result = encode_one(
            mkv,
            ass,
            out,
            log,
            skip_existing=skip_existing,
            on_progress=file_progress if on_progress else None,
            cancel_event=cancel_event,
            encode_settings=effective,
        )
        if result == "ok":
            ok += 1
        elif result == "skip":
            skip += 1
        else:
            fail += 1

        if cancel_event and cancel_event.is_set():
            break

    log(f"🎉「{show_dir.name}」压制结束：成功 {ok}，失败 {fail}，跳过 {skip}")
    return ok, fail, skip


def encode_all(show_dir, log, skip_existing=False, **kwargs):
    return encode_show(show_dir, log, skip_existing=skip_existing, **kwargs)


def count_encode_jobs(show_dirs):
    total = 0
    for d in show_dirs:
        total += len(list(Path(d).glob("*.mkv")))
    return total
