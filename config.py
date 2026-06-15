# config.py — 配置管理
import json
import sys
from pathlib import Path

APP_NAME = "YLFile字幕工具箱"
APP_TAGLINE = "微博：YLFile  |  官网：https://ylfile.com"


def _app_dir():
    """源码：脚本目录；打包 exe：exe 所在目录"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundle_dir():
    """PyInstaller 单文件运行时，依赖解压到此临时目录"""
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return None


_APP_DIR = _app_dir()
_BUNDLE_DIR = _bundle_dir()
CONFIG_FILE = _APP_DIR / "config.json"
META_ROOT_KEY = "_subtitle_root"


def _resource_roots():
    """查找工具顺序：exe 同目录（可覆盖）→ 打包内嵌目录"""
    roots = [_APP_DIR]
    if _BUNDLE_DIR and _BUNDLE_DIR != _APP_DIR:
        roots.append(_BUNDLE_DIR)
    return roots


def resolve_resource(rel):
    """将相对路径解析为实际存在的文件（mkv 工具、opencc 配置等）"""
    rel_path = Path(rel)
    for root in _resource_roots():
        candidate = root / rel_path
        if candidate.is_file():
            return candidate.resolve()
    return (_APP_DIR / rel_path).resolve()


TOOLS = {
    "mkvmerge": str(resolve_resource("mkvmerge.exe")),
    "mkvextract": str(resolve_resource("mkvextract.exe")),
    "opencc": str(resolve_resource("opencc/opencc.exe")),
    "t2s": str(resolve_resource("share/opencc/t2s.json")),
}

CROP_TABLE = {}
SUBTITLE_ROOT = ""


def _is_show_key(key):
    return isinstance(key, str) and not key.startswith("_")


def load_config():
    global SUBTITLE_ROOT
    CROP_TABLE.clear()
    SUBTITLE_ROOT = ""
    if not CONFIG_FILE.exists():
        return
    try:
        raw = CONFIG_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if META_ROOT_KEY in data:
            SUBTITLE_ROOT = data.pop(META_ROOT_KEY) or ""
        for k, v in data.items():
            if _is_show_key(k) and isinstance(v, dict):
                CROP_TABLE[k] = v
    except Exception:
        pass


def save_config():
    payload = {META_ROOT_KEY: SUBTITLE_ROOT}
    payload.update(CROP_TABLE)
    CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def set_subtitle_root(path):
    global SUBTITLE_ROOT
    SUBTITLE_ROOT = str(path) if path else ""
    save_config()


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
    """按剧集文件夹解析黑边配置"""
    show_dir = Path(show_dir)
    cfg = get_crop_for_show(show_dir.name)
    if cfg:
        return cfg
    for v in CROP_TABLE.values():
        sample = (v.get("sample_video") or "").strip()
        if not sample:
            continue
        if (show_dir / sample).is_file():
            return v
    return None


def is_measured(show_name):
    return get_crop_for_show(show_name) is not None


def is_measured_folder(show_dir):
    return get_crop_for_folder(show_dir) is not None


def get_crop(show_name):
    return get_crop_for_show(show_name)


def save_crop(show_name, top, bottom, src_w, src_h,
              sample_video="", cn_pos=None, en_pos=None,
              info_y=None, content_height=None,
              top_line_y=None, bottom_line_y=None):
    from utils import measure_base_resolution, layout_subtitles_from_crop

    crop_h = src_h - top - bottom
    base = measure_base_resolution(src_h)
    layout = layout_subtitles_from_crop(
        src_w, src_h, top, bottom,
        content_top_y=top_line_y if top_line_y else top,
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
        "info_font_px": layout.get("info_font_px"),
        "cn_font_px": layout.get("cn_font_px"),
        "en_font_px": layout.get("en_font_px"),
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
    return entry


load_config()
