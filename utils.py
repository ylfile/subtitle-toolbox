import re
import subprocess
from pathlib import Path

# ================= 集数标识 =================
EPISODE_RE = re.compile(r"(?i)(S\d+E\d+)")

# ================= 文件名语言标识 =================
LANG_HINTS = {
    "eng": "eng",
    "english": "eng",
    "en": "eng",
    "tha": "tha",
    "thai": "tha",
    "kor": "kor",
    "korean": "kor",
    "ko": "kor",
    "jpn": "jpn",
    "japanese": "jpn",
    "ja": "jpn",
    "chi": "chi",
    "chinese": "chi",
    "zh": "chi",
}

SDH_KEYWORDS = (
    "sdh",
    "closed caption",
    "closed captions",
    "cc",
    "hearing impaired",
    "hearing-impaired",
    "deaf",
    "听障",
    "描述",
)

# ================= 字幕清洗（提取 SDH / ASS 共用） =================
DROP_TEXT_PATTERNS = re.compile(r"(字幕翻译|翻译|校对|时间轴)[:：]")
# 中文字幕署名行 → 固定链接（xxx 任意；冒号可有可无）
TRANSLATOR_CREDIT_REPL = "更多内容：https://ylfile.com"
TRANSLATOR_CREDIT_PATTERNS = (
    re.compile(r"字幕翻译\s*[:：]?\s*.+"),  # 字幕翻译：xxx / 字幕翻译xxx
    re.compile(r"(?<!字幕)翻译\s*[:：]\s*.+"),  # 翻译：xxx（不含「字幕翻译」已匹配部分）
)
HEARING_TAG_RE = re.compile(r"\[.*?\]")
CLEAN_START = re.compile(r"^(?:- )?\[.*?\](?:\\[nN])?$")
# 听障标签去掉后仅剩连接符，如 "-  -"、"- -"（原：- [kuş sesleri]\N- [motor sesi]）
BLANK_DIALOGUE_RE = re.compile(r"^[\s\-–—]+$")

SUFFIX_ORIG = "原版.srt"
SUFFIX_CHI = "中文版.srt"

# 常见繁体用字（用于判断中文字幕是否为繁体）
TRAD_CJK_RE = re.compile(
    r"[體臺國語學時這說對電腦裡開關無視頻聽讀寫經過還發現際網絡話東車長見鐘錢買賣頭髮畫裡邊應該繼續雖然準備認識讓語課幫過組織謝請問廣東門診藥醫療護檢驗報導採訪紀錄聯繫傳真郵遞區號為與萬無樂觀眾歡迎訂閱點贊轉發載內容簡介標題評論發布視頻頻道帳號登錄註冊綁定驗證碼設備雲盤軟硬體驅動下載安裝卸載壓縮解壓備份恢復復製粘貼剪切撤銷重做]"
)


def normalize_lang(lang):
    if not lang:
        return ""
    lang = lang.lower()
    if lang.startswith("[") and lang.endswith("]"):
        lang = lang[1:-1]
    return lang


def extract_episode_id(name):
    """从文件名提取 S01E01"""
    m = EPISODE_RE.search(name)
    return m.group(1).upper() if m else None


def extract_lang_from_filename(filename):
    """音轨 und 时，从视频文件名中的英文标识猜语言"""
    stem = Path(filename).stem.lower()
    tokens = re.split(r"[.\-_+\s]+", stem)
    for token in reversed(tokens):
        if token in LANG_HINTS:
            return LANG_HINTS[token]
    parts = re.findall(r"[a-zA-Z]+", stem)
    if parts:
        last = parts[-1].lower()
        if last in LANG_HINTS:
            return LANG_HINTS[last]
    return None


def extract_folder_lang(folder_name):
    """文件夹名兜底（次选）"""
    parts = re.findall(r"[a-zA-Z]+", folder_name)
    if parts:
        last = parts[-1].lower()
        if last in LANG_HINTS:
            return LANG_HINTS[last]
    return None


def subtitle_output_names(mkv_path):
    """返回 (原版.srt 路径名, 中文版.srt 路径名) 的文件名部分"""
    mkv_path = Path(mkv_path)
    ep = extract_episode_id(mkv_path.name)
    if not ep:
        ep = mkv_path.stem
    return f"{ep}原版.srt", f"{ep}中文版.srt"


def is_sdh_track(track_name):
    name = (track_name or "").lower()
    return any(k in name for k in SDH_KEYWORDS)


def pick_original_subtitle_track(candidates):
    """
    多轨时优先非 SDH；仅有 SDH 时用 SDH 并标记需清洗。
    返回 (track_dict, need_sdh_clean) 或 (None, False)
    """
    if not candidates:
        return None, False

    plain = [t for t in candidates if not is_sdh_track(t.get("name", ""))]
    if plain:
        return plain[0], False

    return candidates[0], True


def is_blank_dialogue(text):
    """清洗听障后无实际对白（如 "-  -"），应删除整条字幕"""
    if not text or not text.strip():
        return True
    t = text.strip()
    if BLANK_DIALOGUE_RE.match(t):
        return True
    return False


def should_drop(text):
    if DROP_TEXT_PATTERNS.search(text):
        return True
    if CLEAN_START.match(text.strip()):
        return True
    if is_blank_dialogue(text):
        return True
    return False


def clean_hearing_tags(text):
    text = text.replace("\\N", "\n").replace("\\n", "\n")
    return HEARING_TAG_RE.sub("", text).strip()


def flatten_subtitle_line(text):
    """删除所有换行，保证 ASS 每条 Dialogue 仅一行文本"""
    text = text.replace("\\N", " ").replace("\\n", " ")
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_srt_timestamp(ts):
    """SRT/ASS 时间 → 秒（支持 00:01:02,345 或 00:01:02.345）"""
    ts = (ts or "").strip()
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})", ts)
    if not m:
        return 0.0
    h, mnt, s, ms = m.groups()
    ms = ms.ljust(3, "0")[:3]
    return int(h) * 3600 + int(mnt) * 60 + int(s) + int(ms) / 1000.0


def format_srt_timestamp(seconds):
    """秒 → SRT 时间字符串"""
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_s //= 60
    mnt = total_s % 60
    h = total_s // 60
    return f"{h:02d}:{mnt:02d}:{s:02d},{ms:03d}"


def resolve_subtitle_overlaps(subs, min_gap_ms=50):
    """
    同一轨相邻字幕若时间重叠，截短前一条结束时间，避免硬字幕叠在一起。
    返回 (处理后的列表, 截短条数)
    """
    if not subs:
        return subs, 0
    out = [dict(s) for s in subs]
    out.sort(key=lambda s: parse_srt_timestamp(s["start"]))
    gap = min_gap_ms / 1000.0
    trimmed = 0
    for i in range(len(out) - 1):
        end_i = parse_srt_timestamp(out[i]["end"])
        start_next = parse_srt_timestamp(out[i + 1]["start"])
        if end_i > start_next - gap:
            new_end = max(
                parse_srt_timestamp(out[i]["start"]) + 0.08,
                start_next - gap,
            )
            out[i]["end"] = format_srt_timestamp(new_end)
            trimmed += 1
    return out, trimmed


def replace_translator_credit(text):
    """中文字幕：字幕翻译… / 翻译：… → 更多内容：https://ylfile.com"""
    if not text:
        return text
    for pat in TRANSLATOR_CREDIT_PATTERNS:
        text = pat.sub(TRANSLATOR_CREDIT_REPL, text)
    return text


def normalize_subtitle_text(text, is_chinese=False):
    text = clean_hearing_tags(text)
    if is_chinese:
        text = replace_translator_credit(text)
    return flatten_subtitle_line(text)


def is_traditional_chinese_text(text, min_hits=2):
    """抽样检测字幕正文是否主要为繁体"""
    if not text:
        return False
    return len(TRAD_CJK_RE.findall(text[:15000])) >= min_hits


def _opencc_exe():
    from config import TOOLS

    exe = Path(TOOLS["opencc"])
    if exe.is_file():
        return exe
    alt = Path(__file__).resolve().parent / "opencc" / "opencc.exe"
    return alt if alt.is_file() else None


def convert_srt_traditional_to_simplified(srt_path):
    """OpenCC 繁体 → 简体，原地覆盖 srt"""
    from config import TOOLS

    path = Path(srt_path)
    opencc = _opencc_exe()
    if not opencc:
        raise FileNotFoundError("未找到 opencc.exe，无法繁转简")
    t2s_cfg = Path(TOOLS["t2s"])
    if not t2s_cfg.is_file():
        t2s_cfg = Path(__file__).resolve().parent / "share" / "opencc" / "t2s.json"
    subprocess.run(
        [str(opencc), "-c", str(t2s_cfg), "-i", str(path), "-o", str(path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def ensure_chinese_simplified(srt_path, log=None, force=False):
    """
    若中文字幕为繁体（或 force），转为简体后再继续后续流程。
    返回是否执行了转换。
    """
    path = Path(srt_path)
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if not force and not is_traditional_chinese_text(text):
        return False
    try:
        convert_srt_traditional_to_simplified(path)
    except Exception as e:
        if log:
            log(f"⚠️ 繁转简失败：{path.name} | {e}")
        return False
    if log:
        log(f"🔄 繁体→简体：{path.name}")
    return True


REF_PLAYRES_Y = 1080
INFO_FONT_REF = 50
CN_FONT_REF = 60
EN_FONT_REF = 45
CN_FONT_4K = 110
EN_FONT_4K = 80
LAYOUT_GAP = 5
# 双语行叠放：略小于字号像素，避免中文底与原版顶之间空隙过大
BILINGUAL_GAP = 3
BILINGUAL_LINE_RATIO = 0.78
INFO_BELOW_BAR_GAP = 5  # 顶黑边装不下字号时，贴在黑边下沿外多少像素


def font_size_to_pixels(font_size, playres_y, ref_y=REF_PLAYRES_Y):
    """Aegisub 字号 → 当前 PlayRes 下的像素高度（与视频像素同坐标系）"""
    return max(1, int(round(font_size * playres_y / ref_y)))


def line_layout_height(font_size, playres_y, ref_y=REF_PLAYRES_Y):
    """双语叠放用的近似行高（\\an2 底对齐时用于算 \\pos，仍用样式里的完整 \\fs）"""
    px = font_size_to_pixels(font_size, playres_y, ref_y)
    return max(1, int(round(px * BILINGUAL_LINE_RATIO)))


def calc_symmetric_bar_heights(video_height, top_line_y, bottom_line_y):
    """
    多数片源上下黑边等高时：
    有效画面高 = 底黑边上沿 Y - 顶黑边下沿 Y
    单侧黑边高 = (画面总高 - 有效画面高) / 2
    返回 (top_bar, bottom_bar, content_height)
    """
    if bottom_line_y <= top_line_y:
        raise ValueError("第二次点击须在第一次下方（底黑边上沿）")
    content_h = bottom_line_y - top_line_y
    bar_h = (video_height - content_h) // 2
    return bar_h, bar_h, content_h


def scale_bars_to_playres(top, bottom, playres_y, measured_height):
    """将测量时的黑边高度换算到目标 PlayRes"""
    if not measured_height or measured_height <= 0:
        return int(top), int(bottom)
    scale = playres_y / measured_height
    return int(round(top * scale)), int(round(bottom * scale))


def scale_y_to_playres(y, playres_y, measured_height):
    """将测量时的 Y 坐标换算到目标 PlayRes"""
    if y is None:
        return None
    if not measured_height or measured_height <= 0:
        return int(y)
    return int(round(y * playres_y / measured_height))


def calc_info_position(top_bar, content_top_y, playres_y, info_font_ref=INFO_FONT_REF):
    """
    按每部剧测量的顶黑边高度布局字幕组信息（\\an8 顶中，y 为文字顶边）：
    - 无顶黑边：距画面上沿 INFO_BELOW_BAR_GAP
    - 顶黑边 < 信息字号：黑边装不下 → 贴在画面起点（top_line_y）下 5px
    - 顶黑边 >= 信息字号：在 [0, top_bar] 内垂直居中
    返回 (y, anchor, mode)  mode 供日志说明
    """
    info_font_px = font_size_to_pixels(info_font_ref, playres_y)
    top_bar = max(0, int(top_bar))
    content_top_y = max(0, int(content_top_y))

    if top_bar <= 0:
        return INFO_BELOW_BAR_GAP, 8, "无顶黑边"

    if top_bar < info_font_px:
        y = content_top_y + INFO_BELOW_BAR_GAP
        return y, 8, "黑边下(顶黑边<字号)"

    y_top = (top_bar - info_font_px) // 2
    return max(0, y_top), 8, "黑边内居中"


def calc_info_y(top_bar, content_top_y, playres_y, info_font_ref=INFO_FONT_REF):
    y, _, _ = calc_info_position(top_bar, content_top_y, playres_y, info_font_ref)
    return y


def calc_bilingual_y(playres_y, bottom_bar, cn_h, en_h, gap=BILINGUAL_GAP):
    """
    按每部剧测量的底黑边高度布局双语（\\an2 底对齐，y 为行底边）：
    - 底黑边 >= 两行叠放高：在底黑边内垂直居中
    - 底黑边较矮：中文上移，原版尽量贴在底黑边内或上方
    bottom_bar 优先来自 bottom_line_y 换算（见 resolve_subtitle_layout）
    """
    if bottom_bar >= cn_h + en_h + gap:
        block_h = cn_h + en_h + gap
        block_top = playres_y - bottom_bar + (bottom_bar - block_h) // 2
        y_cn = block_top + cn_h
        y_en = block_top + cn_h + gap + en_h
    elif bottom_bar > en_h:
        y_en = playres_y - gap
        y_cn = y_en - gap - cn_h
    else:
        y_en = playres_y - bottom_bar - gap
        y_cn = y_en - gap - cn_h
    return y_cn, y_en


def calc_chinese_only_y(playres_y, bottom_bar, cn_font_px, gap=LAYOUT_GAP):
    """
    仅中文字幕（\\an2 底对齐，y 为行底边）：
    - 底黑边高度 >= 中文字号像素：在底黑边内垂直居中
    - 底黑边高度 < 中文字号像素：贴在底黑边上沿上方（画面内）
    返回 (y, mode)
    """
    bottom_bar = max(0, int(bottom_bar))
    cn_font_px = max(1, int(cn_font_px))
    playres_y = int(playres_y)
    bar_top = playres_y - bottom_bar

    if bottom_bar <= 0:
        return playres_y - gap, "无底黑边"

    if bottom_bar >= cn_font_px:
        block_top = bar_top + (bottom_bar - cn_font_px) // 2
        y = block_top + cn_font_px
        return y, "底黑边内居中"

    y = bar_top - gap
    return y, "底黑边上方"


def layout_subtitles_from_crop(
    playres_x, playres_y, top_bar, bottom_bar, res="1080p", content_top_y=None
):
    """根据黑边与分辨率，计算字幕组信息及双语字幕的像素坐标"""
    cn_ref = CN_FONT_4K if res == "4K" else CN_FONT_REF
    en_ref = EN_FONT_4K if res == "4K" else EN_FONT_REF
    cn_h = line_layout_height(cn_ref, playres_y)
    en_h = line_layout_height(en_ref, playres_y)
    info_h = font_size_to_pixels(INFO_FONT_REF, playres_y)
    top_content = content_top_y if content_top_y is not None else top_bar
    info_y, info_an, info_mode = calc_info_position(top_bar, top_content, playres_y)
    y_cn, y_en = calc_bilingual_y(playres_y, bottom_bar, cn_h, en_h)
    return {
        "cx": playres_x // 2,
        "info_y": info_y,
        "info_an": info_an,
        "info_mode": info_mode,
        "top_bar": top_bar,
        "bottom_bar": bottom_bar,
        "content_top_y": top_content,
        "info_font_px": info_h,
        "cn_font_px": font_size_to_pixels(cn_ref, playres_y),
        "en_font_px": font_size_to_pixels(en_ref, playres_y),
        "cn_pos": y_cn,
        "en_pos": y_en,
    }


def calc_subtitle_positions(playres_y, top_crop, bottom_crop, res="1080p"):
    """兼容旧调用：返回 (cn_pos, en_pos)"""
    playres_x = 3840 if res == "4K" else 1920
    layout = layout_subtitles_from_crop(playres_x, playres_y, top_crop, bottom_crop, res)
    return layout["cn_pos"], layout["en_pos"]


def measure_base_resolution(height):
    """测量视频以高度判断基准分辨率"""
    return "4K" if height >= 1440 else "1080p"


def show_name_from_video(root_path, video_path):
    """根目录下第一层子文件夹名 = 剧集名"""
    root = Path(root_path).resolve()
    video = Path(video_path).resolve()
    try:
        rel = video.parent.relative_to(root)
        if rel.parts:
            return rel.parts[0]
    except ValueError:
        pass
    return video.parent.name


def clean_srt_file(path, drop_credits=False, is_chinese=False):
    """清洗已提取的 SRT（SDH 听障标签、中文字幕翻译署名替换等）"""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    blocks = raw.strip().split("\n\n")
    out_blocks = []

    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue

        idx, time_line = lines[0], lines[1]
        text = normalize_subtitle_text(" ".join(lines[2:]), is_chinese=is_chinese)
        if is_blank_dialogue(text):
            continue
        if drop_credits and should_drop(text):
            continue
        if not text:
            continue

        out_blocks.append(f"{idx}\n{time_line}\n{text}")

    path.write_text(
        "\n\n".join(out_blocks) + ("\n" if out_blocks else ""),
        encoding="utf-8",
    )


def find_mkv_for_episode(folder, episode_id):
    folder = Path(folder)
    ep = episode_id.upper()
    for mkv in sorted(folder.glob("*.mkv")):
        if extract_episode_id(mkv.name) == ep:
            return mkv
    for mkv in sorted(folder.glob("*.mkv")):
        if ep in mkv.name.upper():
            return mkv
    return None


def list_subtitle_pairs(folder):
    """
    配对字幕：S01E01原版.srt + S01E01中文版.srt
    兼容旧版 *.orig.srt / *.chi.srt
    仅中文版时 orig 为 None，仍返回一条记录
    返回 [(orig_filename|None, chi_filename, episode_id), ...]
    """
    folder = Path(folder)
    orig_map = {}
    chi_map = {}

    for f in folder.iterdir():
        if not f.is_file():
            continue
        name = f.name
        if name.endswith(SUFFIX_ORIG):
            ep = name[: -len(SUFFIX_ORIG)].upper()
            orig_map[ep] = name
        elif name.endswith(SUFFIX_CHI):
            ep = name[: -len(SUFFIX_CHI)].upper()
            chi_map[ep] = name
        elif name.endswith(".orig.srt"):
            ep = extract_episode_id(name) or Path(name).stem.replace(".orig", "")
            orig_map[ep.upper()] = name
        elif name.endswith(".chi.srt"):
            ep = extract_episode_id(name) or Path(name).stem.replace(".chi", "")
            chi_map[ep.upper()] = name

    pairs = []
    for ep in sorted(set(orig_map) | set(chi_map)):
        o, c = orig_map.get(ep), chi_map.get(ep)
        if c:
            pairs.append((o, c, ep))
    return pairs


def folder_has_subtitles(folder):
    """目录内是否已有可生成 ASS 的字幕（有中文版即可，原版可选）"""
    folder = Path(folder)
    if not folder.is_dir():
        return False
    names = [f.name for f in folder.iterdir() if f.is_file()]
    return any("中文版" in n or n.endswith(".chi.srt") for n in names)


def _is_show_folder(path):
    """子目录是否像一部剧（含 mkv 或双语字幕）"""
    path = Path(path)
    return path.is_dir() and (
        folder_has_subtitles(path) or bool(list(path.glob("*.mkv")))
    )


def iter_show_dirs_for_work(root):
    """
    列出要处理的剧集目录：
    - 根下存在含 mkv/字幕的子文件夹 → 每子文件夹一部剧（根目录零散 mkv 不抢批量）
    - 否则若所选目录本身含 mkv/字幕 → 当作一部剧（可直接选剧集文件夹）
    """
    root = Path(root).resolve()
    if not root.is_dir():
        return []

    children = sorted(d for d in root.iterdir() if _is_show_folder(d))
    if children:
        return children

    if _is_show_folder(root):
        return [root]

    return []


def iter_show_dirs_with_mkv(selected):
    """
    解析用户选择的目录：
    - 若该文件夹内直接有 .mkv → 视为「单部剧文件夹」，只处理这一部
    - 否则 → 视为「字幕根目录」，批量处理其下所有包含 .mkv 的子文件夹
  返回 Path 列表（每个元素为一个剧集目录）
    """
    selected = Path(selected)
    if not selected.is_dir():
        return []

    if list(selected.glob("*.mkv")):
        return [selected]

    shows = []
    for child in sorted(selected.iterdir()):
        if child.is_dir() and list(child.glob("*.mkv")):
            shows.append(child)
    return shows
