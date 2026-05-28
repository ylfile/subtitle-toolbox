# encode_settings.py — 压制参数 UI 与 ffmpeg 编码参数
import re
import subprocess
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from config import TOOLS, ENCODE_SETTINGS, save_encode_settings

DEFAULT_ENCODE_SETTINGS = {
    "mode": "crf",
    "container": "mp4",
    "codec": "h264",
    "bitrate_mode": "source",
    "bitrate_kbps": 4000,
    "crf": 18,
    "gpu_vendor": "auto",
}

GPU_ENCODERS = {
    "auto": {"h264": "h264_nvenc", "h265": "hevc_nvenc"},
    "nvidia": {"h264": "h264_nvenc", "h265": "hevc_nvenc"},
    "amd": {"h264": "h264_amf", "h265": "hevc_amf"},
    "intel": {"h264": "h264_qsv", "h265": "hevc_qsv"},
}

CPU_ENCODERS = {"h264": "libx264", "h265": "libx265"}

_ENCODER_CACHE = None
_FFMPEG_PROBE_CACHE = {}

FFMPEG_DURATION_RE = re.compile(
    r"Duration:\s*(\d{2}):(\d{2}):(\d{2})[.](\d+)", re.IGNORECASE
)
FFMPEG_BITRATE_RE = re.compile(
    r"bitrate:\s*(\d+(?:\.\d+)?)\s*kb/s", re.IGNORECASE
)
FFMPEG_FPS_RE = re.compile(
    r"Video:.*?,\s*(\d+(?:\.\d+)?)\s*fps", re.IGNORECASE
)
FFMPEG_AUDIO_HZ_RE = re.compile(
    r"Audio:.*?,\s*(\d+)\s*Hz", re.IGNORECASE
)
FFMPEG_AUDIO_STEREO_RE = re.compile(r"Audio:.*?(stereo|mono)", re.IGNORECASE)


def reset_encoder_cache():
    """测试或更换 ffmpeg 后刷新缓存"""
    global _ENCODER_CACHE, _FFMPEG_PROBE_CACHE
    _ENCODER_CACHE = None
    _FFMPEG_PROBE_CACHE = {}


def get_ffmpeg_encoders():
    """ffmpeg -encoders 可用编码器名集合（缓存）"""
    global _ENCODER_CACHE
    if _ENCODER_CACHE is not None:
        return _ENCODER_CACHE
    encs = set()
    try:
        r = subprocess.run(
            [_ffmpeg_exe(), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("V"):
                encs.add(parts[1])
        if not encs:
            for line in (r.stderr or "").splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0].startswith("V"):
                    encs.add(parts[1])
    except Exception:
        pass
    _ENCODER_CACHE = encs
    return encs


def is_encoder_available(name):
    return name in get_ffmpeg_encoders()


def resolve_gpu_encoder(settings, skip_vendors=None):
    """返回 (encoder_name, vendor) 或 (None, None)"""
    s = normalize_encode_settings(settings)
    skip = set(skip_vendors or [])
    vendors = (
        [s["gpu_vendor"]]
        if s["gpu_vendor"] != "auto"
        else ["nvidia", "amd", "intel"]
    )
    for vendor in vendors:
        if vendor in skip:
            continue
        enc = GPU_ENCODERS[vendor][s["codec"]]
        if is_encoder_available(enc):
            return enc, vendor
    return None, None


def probe_gpu_encoder_works(enc):
    """
    试编码 1 帧，确认编码器在运行时可用。
    AMF 要求分辨率不能过小（64x64 会 Init 失败），故用 320x180。
    返回 (成功与否, 失败时 stderr 摘要)
    """
    if not enc:
        return False, ""
    try:
        cmd = [
            _ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:d=0.04",
            "-frames:v",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            enc,
        ]
        if enc.endswith("_nvenc"):
            cmd.extend(["-preset", "p4"])
        elif enc.endswith("_amf"):
            cmd.extend(["-usage", "transcoding", "-quality", "balanced"])
        cmd.extend(["-f", "null", "-"])
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
        )
        err = (r.stderr or "").strip()
        if r.returncode == 0:
            return True, ""
        first = err.splitlines()[0] if err else f"退出码 {r.returncode}"
        return False, first
    except Exception as e:
        return False, str(e)


def find_working_gpu_encoder(settings):
    """
    按厂商顺序找第一个「列表里有且实测能跑」的 GPU 编码器。
    返回 (settings, encoder, vendor, last_error)
    """
    s = normalize_encode_settings(settings)
    skip = set()
    last_err = ""
    while True:
        enc, vendor = resolve_gpu_encoder(s, skip_vendors=skip)
        if not enc:
            return None, None, None, last_err
        ok, err = probe_gpu_encoder_works(enc)
        if ok:
            picked = dict(s)
            picked["gpu_vendor"] = vendor
            return picked, enc, vendor, ""
        last_err = err or f"{enc} 自检失败"
        skip.add(vendor)
        if s["gpu_vendor"] != "auto":
            return None, None, None, last_err


def cpu_fallback_settings(settings):
    """GPU 失败时回退到 CPU CRF"""
    s = normalize_encode_settings(settings)
    fb = dict(s)
    fb["mode"] = "crf"
    return fb


def ensure_gpu_settings(settings, log=None):
    """
    若选 GPU：找第一个可用的 GPU 编码器（auto 会跳过不可用的 NVENC 试 AMF 等）。
    全部不可用则改为 CPU 并提示。
    """
    s = normalize_encode_settings(settings)
    if s["mode"] != "gpu":
        return s

    picked, enc, vendor, probe_err = find_working_gpu_encoder(s)
    if picked:
        if log:
            if s["gpu_vendor"] == "auto" and vendor != "nvidia":
                log(f"ℹ️ 未检测到可用 NVIDIA 编码，已选用 {enc} ({vendor})")
            elif s["gpu_vendor"] == vendor:
                log(f"🎮 GPU 硬编就绪：{enc} ({vendor})")
        return picked

    if log:
        detail = f"（{probe_err}）" if probe_err else ""
        if s["gpu_vendor"] == "amd":
            log(
                f"⚠️ AMD 硬编 h264_amf 不可用{detail}，"
                "已自动改用 CPU CRF"
            )
        else:
            log(
                f"⚠️ 当前 ffmpeg 未检测到可用的 GPU 硬编{detail}，"
                "将使用 CPU CRF 模式"
            )
    return cpu_fallback_settings(s)


def normalize_encode_settings(data=None):
    s = dict(DEFAULT_ENCODE_SETTINGS)
    if data:
        for k in DEFAULT_ENCODE_SETTINGS:
            if k in data and data[k] is not None:
                s[k] = data[k]
    s["mode"] = str(s["mode"]).lower()
    if s["mode"] not in ("gpu", "crf", "abr"):
        s["mode"] = "crf"
    s["container"] = "mkv" if str(s["container"]).lower() == "mkv" else "mp4"
    s["codec"] = "h265" if str(s["codec"]).lower() in ("h265", "hevc", "x265") else "h264"
    s["bitrate_mode"] = (
        "custom" if str(s["bitrate_mode"]).lower() == "custom" else "source"
    )
    try:
        s["bitrate_kbps"] = max(500, int(s["bitrate_kbps"]))
    except (TypeError, ValueError):
        s["bitrate_kbps"] = 4000
    try:
        s["crf"] = max(0, min(51, int(s["crf"])))
    except (TypeError, ValueError):
        s["crf"] = 18
    gv = str(s.get("gpu_vendor", "auto")).lower()
    s["gpu_vendor"] = gv if gv in GPU_ENCODERS else "auto"
    return s


def output_extension(settings):
    return ".mkv" if settings["container"] == "mkv" else ".mp4"


def settings_summary(settings):
    s = normalize_encode_settings(settings)
    mode = {"gpu": "GPU", "crf": "CRF", "abr": "ABR"}[s["mode"]]
    codec = "H.265" if s["codec"] == "h265" else "H.264"
    br = (
        "源码率"
        if s["bitrate_mode"] == "source" and s["mode"] != "crf"
        else f"{s['bitrate_kbps']}kbps"
    )
    extra = f" CRF{s['crf']}" if s["mode"] == "crf" else f" {br}"
    if s["mode"] == "gpu":
        extra += f" ({s['gpu_vendor']})"
    return f"{mode} | {codec} | {s['container'].upper()}{extra}"


def _ffmpeg_exe():
    """优先使用程序目录下的 ffmpeg.exe，避免工作目录不同找到错误版本。"""
    app = Path(__file__).resolve().parent / "ffmpeg.exe"
    if app.is_file():
        return str(app)
    exe = Path(TOOLS["ffmpeg"])
    if exe.is_file():
        return str(exe)
    return str(TOOLS["ffmpeg"])


def _ffprobe_exe():
    p = Path(_ffmpeg_exe()).parent / "ffprobe.exe"
    return str(p) if p.is_file() else None


def ffprobe_available():
    return _ffprobe_exe() is not None


def _run_capture(cmd, timeout=60):
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception:
        return None


def _duration_text_to_seconds(h, m, s, frac):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 100.0


def _run_ffmpeg_probe(video_path):
    """ffmpeg -i 解析 stderr（无 ffprobe 时的兜底）"""
    path = str(Path(video_path).resolve())
    if path in _FFMPEG_PROBE_CACHE:
        return _FFMPEG_PROBE_CACHE[path]
    try:
        r = _run_capture([_ffmpeg_exe(), "-hide_banner", "-i", path], timeout=120)
        text = (r.stderr or "") if r else ""
    except Exception:
        text = ""
    _FFMPEG_PROBE_CACHE[path] = text
    return text


def probe_duration_seconds(video_path):
    video_path = Path(video_path)
    ffprobe = _ffprobe_exe()
    if ffprobe:
        try:
            out = _run_capture(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                timeout=60,
            )
            if out and out.returncode == 0 and out.stdout.strip():
                return float(out.stdout.strip())
        except Exception:
            pass
    m = FFMPEG_DURATION_RE.search(_run_ffmpeg_probe(video_path))
    if m:
        return _duration_text_to_seconds(*m.groups())
    return None


def probe_bitrate_kbps(video_path):
    """探测视频码率（kbps）：ffprobe → ffmpeg bitrate 行 → 文件大小/时长"""
    video_path = Path(video_path)
    ffprobe = _ffprobe_exe()
    if ffprobe:
        try:
            for entries in (
                ("stream=bit_rate", ["-select_streams", "v:0"]),
                ("format=bit_rate", []),
            ):
                cmd = [
                    ffprobe,
                    "-v",
                    "error",
                    *entries[1],
                    "-show_entries",
                    entries[0],
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ]
                out = _run_capture(cmd, timeout=60)
                val = (out.stdout or "").strip() if out else ""
                if out and out.returncode == 0 and val.isdigit() and int(val) > 0:
                    return max(500, int(val) // 1000)
        except Exception:
            pass

    text = _run_ffmpeg_probe(video_path)
    m = FFMPEG_BITRATE_RE.search(text)
    if m:
        return max(500, int(float(m.group(1))))

    dur = probe_duration_seconds(video_path)
    try:
        size = video_path.stat().st_size
    except OSError:
        size = 0
    if dur and dur > 0 and size > 0:
        return max(500, int(size * 8 / dur / 1000))
    return None


def probe_video_fps_text(video_path):
    """返回帧率字符串如 30 或 24000/1001（ffprobe → 帧数/时长 → ffmpeg 解析）"""
    video_path = Path(video_path)
    ffprobe = _ffprobe_exe()
    if ffprobe:
        for key in ("avg_frame_rate", "r_frame_rate"):
            try:
                out = _run_capture(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        f"stream={key}",
                        "-of",
                        "default=noprint_wrappers=1:nokey=1",
                        str(video_path),
                    ],
                    timeout=60,
                )
                val = (out.stdout or "").strip() if out else ""
                if out and val and val not in ("0/0", "N/A"):
                    num, den = val.split("/")
                    if int(den) > 0 and int(num) > 0:
                        return val
            except Exception:
                pass
        try:
            out = _run_capture(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=nb_frames:format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                timeout=60,
            )
            if out and out.stdout:
                lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
                nb, dur = None, None
                for ln in lines:
                    if ln.isdigit():
                        nb = int(ln)
                    else:
                        try:
                            dur = float(ln)
                        except ValueError:
                            pass
                if nb and dur and dur > 0:
                    fps = nb / dur
                    if abs(fps - round(fps)) < 0.05:
                        return str(int(round(fps)))
                    return f"{fps:.3f}"
        except Exception:
            pass

    text = _run_ffmpeg_probe(video_path)
    m = FFMPEG_FPS_RE.search(text)
    if m:
        fps = float(m.group(1))
        if abs(fps - round(fps)) < 0.02:
            return str(int(round(fps)))
        return str(fps)
    return None


def probe_audio_stream_info(video_path):
    """探测第一条音轨，供 AAC 重编码"""
    video_path = Path(video_path)
    info = {}
    ffprobe = _ffprobe_exe()
    if ffprobe:
        try:
            out = _run_capture(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=codec_name,sample_rate,channels",
                    "-of",
                    "default=noprint_wrappers=1",
                    str(video_path),
                ],
                timeout=60,
            )
            if out and out.returncode == 0:
                for line in (out.stdout or "").splitlines():
                    if "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k == "sample_rate" and v.isdigit():
                        info["sample_rate"] = v
                    elif k == "channels" and v.isdigit():
                        info["channels"] = v
                    elif k == "codec_name":
                        info["codec_name"] = v
        except Exception:
            pass
    if info.get("sample_rate"):
        return info
    text = _run_ffmpeg_probe(video_path)
    m = FFMPEG_AUDIO_HZ_RE.search(text)
    if m:
        info["sample_rate"] = m.group(1)
    st = FFMPEG_AUDIO_STEREO_RE.search(text)
    if st:
        info["channels"] = "2" if st.group(1) == "stereo" else "1"
    return info


def normalize_fps_rate(fps_text):
    """将 ffprobe 帧率转为 ffmpeg fps 滤镜 / -r 可用格式"""
    if not fps_text:
        return None
    fps_s = str(fps_text).strip()
    if "/" in fps_s:
        num, den = fps_s.split("/", 1)
        n, d = int(num), int(den)
        if d > 0 and n % d == 0:
            return str(n // d)
        return fps_s
    return fps_s


def build_subtitle_video_filter_parts(subtitle_vf_expr, video_fps):
    """
    压制前先把视频帧率对齐，再烧字幕（避免 MKV 毫秒时间基导致字幕延后/超前）。
    顺序：fps → ass/subtitles → format=yuv420p
    """
    rate = normalize_fps_rate(video_fps)
    parts = []
    if rate:
        parts.append(f"fps=fps={rate}")
    parts.append(subtitle_vf_expr)
    parts.append("format=yuv420p")
    return ",".join(parts), rate


def build_fps_encode_args(fps_text, container="mp4"):
    """编码器输出与源帧率一致的 CFR 参数（须配合滤镜链里的 fps）"""
    rate = normalize_fps_rate(fps_text)
    if not rate:
        return [], None
    args = ["-fps_mode", "cfr", "-r", rate]
    if container == "mp4":
        args.extend(["-video_track_timescale", "90000"])
    return args, rate


def resolve_bitrate_kbps(settings, video_path, log=None):
    s = normalize_encode_settings(settings)
    if s["mode"] == "crf":
        return None
    if s["bitrate_mode"] == "custom":
        return s["bitrate_kbps"]
    kbps = probe_bitrate_kbps(video_path)
    if kbps:
        if log:
            src = "ffprobe" if ffprobe_available() else "ffmpeg"
            log(f"📊 源码率：{kbps} kbps（{src} 探测）")
        return kbps
    if log:
        if not ffprobe_available():
            log(
                "⚠️ 未找到 ffprobe.exe，已尝试 ffmpeg 探测仍失败，"
                f"使用 {s['bitrate_kbps']} kbps"
            )
        else:
            log(f"⚠️ 无法读取源码率，使用 {s['bitrate_kbps']} kbps")
    return s["bitrate_kbps"]


def pick_encoder_name(settings):
    """返回 ffmpeg -c:v 实际使用的编码器名"""
    return _pick_encoder(normalize_encode_settings(settings))


def encoder_usage_hint(settings):
    s = normalize_encode_settings(settings)
    enc = _pick_encoder(s)
    if s["mode"] == "gpu":
        return (
            f"🎮 实际编码器：{enc}（{s['gpu_vendor']} GPU）\n"
            "   硬字幕烧录与视频解码仍占用 CPU；"
            "任务管理器 → 性能 → GPU → 选「视频编码」查看 GPU 占用"
        )
    return f"💻 实际编码器：{enc}（CPU 软编）"


def _pick_encoder(settings):
    s = normalize_encode_settings(settings)
    if s["mode"] == "gpu":
        enc, _ = resolve_gpu_encoder(s)
        if enc:
            return enc
        return CPU_ENCODERS[s["codec"]]
    return CPU_ENCODERS[s["codec"]]


def build_video_encode_args(settings, bitrate_kbps=None):
    """返回 ffmpeg 视频编码相关参数列表"""
    s = normalize_encode_settings(settings)
    enc = _pick_encoder(s)
    args = ["-c:v", enc, "-pix_fmt", "yuv420p"]

    if s["mode"] == "crf":
        args.extend(["-preset", "medium", "-crf", str(s["crf"])])
        if enc == "libx265":
            args.extend(["-tag:v", "hvc1"])
    elif s["mode"] == "abr":
        kbps = bitrate_kbps or s["bitrate_kbps"]
        args.extend(
            [
                "-preset",
                "medium",
                "-b:v",
                f"{kbps}k",
                "-maxrate",
                f"{kbps}k",
                "-bufsize",
                f"{kbps * 2}k",
            ]
        )
        if enc == "libx265":
            args.extend(["-tag:v", "hvc1"])
    else:
        kbps = bitrate_kbps or s["bitrate_kbps"]
        if enc.endswith("_nvenc"):
            args.extend(
                [
                    "-preset",
                    "p4",
                    "-bf",
                    "0",
                    "-rc",
                    "vbr",
                    "-b:v",
                    f"{kbps}k",
                    "-maxrate",
                    f"{kbps}k",
                    "-bufsize",
                    f"{kbps * 2}k",
                ]
            )
        elif enc.endswith("_amf"):
            args.extend(
                [
                    "-usage",
                    "transcoding",
                    "-quality",
                    "balanced",
                    "-bf",
                    "0",
                    "-rc",
                    "vbr_peak",
                    "-b:v",
                    f"{kbps}k",
                ]
            )
        elif enc.endswith("_qsv"):
            args.extend(["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k"])
        else:
            args.extend(["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k"])

    return args


def build_container_args(settings):
    if settings.get("container") == "mp4":
        return ["-movflags", "+faststart"]
    return []


class EncodeSettingsPanel(tk.LabelFrame):
    """压制参数可视化面板"""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, text="压制参数", padx=8, pady=6, **kwargs)
        self._building = True

        row = 0
        tk.Label(self, text="编码模式").grid(row=row, column=0, sticky="w", padx=(0, 8))
        mode_f = tk.Frame(self)
        mode_f.grid(row=row, column=1, columnspan=5, sticky="w")
        self.mode_var = tk.StringVar(value="crf")
        for text, val in [("GPU 硬编", "gpu"), ("CRF 质量", "crf"), ("ABR 平均码率", "abr")]:
            tk.Radiobutton(
                mode_f,
                text=text,
                variable=self.mode_var,
                value=val,
                command=self._refresh_state,
            ).pack(side=tk.LEFT, padx=(0, 12))

        row += 1
        tk.Label(self, text="封装格式").grid(row=row, column=0, sticky="w")
        cf = tk.Frame(self)
        cf.grid(row=row, column=1, columnspan=2, sticky="w")
        self.container_var = tk.StringVar(value="mp4")
        for text, val in [("MP4", "mp4"), ("MKV", "mkv")]:
            tk.Radiobutton(
                cf, text=text, variable=self.container_var, value=val
            ).pack(side=tk.LEFT, padx=(0, 12))

        tk.Label(self, text="视频编码").grid(row=row, column=3, sticky="w", padx=(16, 4))
        vf = tk.Frame(self)
        vf.grid(row=row, column=4, columnspan=2, sticky="w")
        self.codec_var = tk.StringVar(value="h264")
        tk.Radiobutton(
            vf, text="H.264", variable=self.codec_var, value="h264"
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Radiobutton(
            vf, text="H.265", variable=self.codec_var, value="h265"
        ).pack(side=tk.LEFT)

        row += 1
        tk.Label(self, text="码率").grid(row=row, column=0, sticky="w")
        bf = tk.Frame(self)
        bf.grid(row=row, column=1, columnspan=5, sticky="w")
        self.bitrate_mode_var = tk.StringVar(value="source")
        self._rb_br_source = tk.Radiobutton(
            bf,
            text="与源视频相同",
            variable=self.bitrate_mode_var,
            value="source",
            command=self._refresh_state,
        )
        self._rb_br_source.pack(side=tk.LEFT, padx=(0, 10))
        self._rb_br_custom = tk.Radiobutton(
            bf,
            text="自定义",
            variable=self.bitrate_mode_var,
            value="custom",
            command=self._refresh_state,
        )
        self._rb_br_custom.pack(side=tk.LEFT)
        self.bitrate_entry = tk.Entry(bf, width=8)
        self.bitrate_entry.pack(side=tk.LEFT, padx=4)
        self.bitrate_entry.insert(0, "4000")
        tk.Label(bf, text="kbps").pack(side=tk.LEFT)

        row += 1
        tk.Label(self, text="CRF 值").grid(row=row, column=0, sticky="w")
        crf_f = tk.Frame(self)
        crf_f.grid(row=row, column=1, sticky="w")
        self.crf_var = tk.StringVar(value="18")
        self.crf_spin = ttk.Spinbox(
            crf_f, from_=0, to=51, width=6, textvariable=self.crf_var
        )
        self.crf_spin.pack(side=tk.LEFT)
        tk.Label(crf_f, text="（越小画质越高，仅 CRF 模式）").pack(side=tk.LEFT, padx=6)

        tk.Label(self, text="GPU").grid(row=row, column=3, sticky="w", padx=(16, 4))
        self.gpu_combo = ttk.Combobox(
            self,
            width=12,
            state="readonly",
            values=["auto", "nvidia", "amd", "intel"],
        )
        self.gpu_combo.grid(row=row, column=4, sticky="w")
        self.gpu_combo.set("auto")

        row += 1
        btn_f = tk.Frame(self)
        btn_f.grid(row=row, column=0, columnspan=6, sticky="w", pady=(6, 0))
        tk.Button(btn_f, text="保存为默认", command=self.save_defaults).pack(
            side=tk.LEFT
        )
        self.hint_var = tk.StringVar(value="")
        tk.Label(btn_f, textvariable=self.hint_var, fg="gray").pack(
            side=tk.LEFT, padx=12
        )

        self._building = False
        self.load_settings(ENCODE_SETTINGS)
        self._refresh_state()

    def _refresh_state(self):
        if self._building:
            return
        mode = self.mode_var.get()
        is_crf = mode == "crf"
        need_br = mode in ("abr", "gpu")
        self.crf_spin.config(state=tk.NORMAL if is_crf else "disabled")
        br_rb = tk.NORMAL if need_br else tk.DISABLED
        self._rb_br_source.config(state=br_rb)
        self._rb_br_custom.config(state=br_rb)
        if need_br and self.bitrate_mode_var.get() == "custom":
            self.bitrate_entry.config(state=tk.NORMAL)
        else:
            self.bitrate_entry.config(state=tk.DISABLED)
        self.gpu_combo.config(state="readonly" if mode == "gpu" else "disabled")
        self.hint_var.set(settings_summary(self.get_settings()))

    def get_settings(self):
        try:
            crf = int(self.crf_var.get())
        except ValueError:
            crf = 18
        try:
            br = int(self.bitrate_entry.get())
        except ValueError:
            br = 4000
        return normalize_encode_settings(
            {
                "mode": self.mode_var.get(),
                "container": self.container_var.get(),
                "codec": self.codec_var.get(),
                "bitrate_mode": self.bitrate_mode_var.get(),
                "bitrate_kbps": br,
                "crf": crf,
                "gpu_vendor": self.gpu_combo.get() or "auto",
            }
        )

    def load_settings(self, data):
        self._building = True
        s = normalize_encode_settings(data)
        self.mode_var.set(s["mode"])
        self.container_var.set(s["container"])
        self.codec_var.set(s["codec"])
        self.bitrate_mode_var.set(s["bitrate_mode"])
        self.crf_var.set(str(s["crf"]))
        self.bitrate_entry.delete(0, tk.END)
        self.bitrate_entry.insert(0, str(s["bitrate_kbps"]))
        self.gpu_combo.set(s["gpu_vendor"])
        self._building = False
        self._refresh_state()

    def save_defaults(self):
        save_encode_settings(self.get_settings())
        self.hint_var.set("已保存默认 · " + settings_summary(self.get_settings()))
