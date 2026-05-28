# config.py — 黑边配置读写（每部剧一条记录，与具体集数视频无关）
import json  # 导入 JSON 读写库
from pathlib import Path  # 导入路径处理库

APP_NAME = "YLFile字幕工具箱"
APP_TAGLINE = "微博：YLFile  |  官网：https://ylfile.com"

_APP_DIR = Path(__file__).resolve().parent
CONFIG_FILE = _APP_DIR / "config.json"  # 始终与程序同目录，避免工作目录不同读不到配置
META_ROOT_KEY = "_subtitle_root"
META_ENCODE_KEY = "_encode_settings"

TOOLS = {  # 外部工具可执行文件路径表
    "mkvmerge": "mkvmerge.exe",  # MKV 信息读取
    "mkvextract": "mkvextract.exe",  # 字幕轨提取
    "ffmpeg": "ffmpeg.exe",  # 视频压制
    "opencc": "opencc/opencc.exe",  # 繁简转换
    "t2s": "share/opencc/t2s.json",  # 繁转简配置
}

CROP_TABLE = {}
SUBTITLE_ROOT = ""
ENCODE_SETTINGS = {}


def _is_show_key(key):  # 判断 JSON 键是否为剧集名（非元数据）
    return isinstance(key, str) and not key.startswith("_")  # 字符串且不以 _ 开头


def load_config():
    global SUBTITLE_ROOT, ENCODE_SETTINGS
    from encode_settings import normalize_encode_settings, DEFAULT_ENCODE_SETTINGS

    CROP_TABLE.clear()
    SUBTITLE_ROOT = ""
    ENCODE_SETTINGS = normalize_encode_settings(DEFAULT_ENCODE_SETTINGS)

    if not CONFIG_FILE.exists():
        return

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return

    if not isinstance(data, dict):
        return

    SUBTITLE_ROOT = data.get(META_ROOT_KEY, "") or ""
    if isinstance(data.get(META_ENCODE_KEY), dict):
        ENCODE_SETTINGS = normalize_encode_settings(data[META_ENCODE_KEY])
    for key, val in data.items():
        if _is_show_key(key) and isinstance(val, dict) and "top" in val and "bottom" in val:
            CROP_TABLE[key] = val


def save_config():
    payload = {
        META_ROOT_KEY: SUBTITLE_ROOT,
        META_ENCODE_KEY: ENCODE_SETTINGS,
    }
    payload.update(CROP_TABLE)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_encode_settings(settings):
    global ENCODE_SETTINGS
    from encode_settings import normalize_encode_settings

    ENCODE_SETTINGS = normalize_encode_settings(settings)
    save_config()


def set_subtitle_root(path):  # 设置并保存字幕根目录
    global SUBTITLE_ROOT  # 声明修改全局根目录
    SUBTITLE_ROOT = str(path) if path else ""  # 转为字符串或置空
    save_config()  # 立即持久化


def get_crop_for_show(show_name):
    """按文件夹名取黑边配置（大小写不敏感）"""
    if not show_name:
        return None
    if show_name in CROP_TABLE:
        return CROP_TABLE[show_name]
    key_lower = show_name.lower()
    for k, v in CROP_TABLE.items():
        if k.lower() == key_lower:
            return v
    return None


def get_crop_for_folder(show_dir):
    """
    按剧集文件夹解析黑边配置：
    1. 文件夹名与 config 键一致（忽略大小写）
    2. 该文件夹内存在 config 里记录的 sample_video
    """
    show_dir = Path(show_dir)
    cfg = get_crop_for_show(show_dir.name)
    if cfg:
        return cfg
    for v in CROP_TABLE.values():
        sample = (v.get("sample_video") or "").strip()
        if sample and (show_dir / sample).is_file():
            return v
    return None


def is_measured(show_name):
    return get_crop_for_show(show_name) is not None


def is_measured_folder(show_dir):
    return get_crop_for_folder(show_dir) is not None


def get_crop(show_name):
    return get_crop_for_show(show_name)


def save_crop(
    show_name,
    top,
    bottom,
    src_w,
    src_h,
    sample_video="",
    cn_pos=0,
    en_pos=0,
    info_y=0,
    content_height=None,
    top_line_y=None,
    bottom_line_y=None,
):
    from utils import layout_subtitles_from_crop, measure_base_resolution

    crop_h = src_h - top - bottom
    base = measure_base_resolution(src_h)
    layout = layout_subtitles_from_crop(
        src_w, src_h, top, bottom, base, content_top_y=top_line_y if top_line_y else top
    )

    entry = {
        "top": int(top),
        "bottom": int(bottom),
        "crop": f"{src_w}:{crop_h}:0:{top}",
        "source_width": int(src_w),
        "source_height": int(src_h),
        "sample_video": sample_video or "",
        "cn_pos": int(cn_pos or layout["cn_pos"]),
        "en_pos": int(en_pos or layout["en_pos"]),
        "info_y": int(info_y or layout["info_y"]),
        "info_font_px": layout["info_font_px"],
        "cn_font_px": layout["cn_font_px"],
        "en_font_px": layout["en_font_px"],
        "measured_base": base,
        "symmetric_bars": True,
    }
    if content_height is not None:
        entry["content_height"] = int(content_height)
    if top_line_y is not None:
        entry["top_line_y"] = int(top_line_y)
    if bottom_line_y is not None:
        entry["bottom_line_y"] = int(bottom_line_y)

    CROP_TABLE[show_name] = entry
    save_config()
