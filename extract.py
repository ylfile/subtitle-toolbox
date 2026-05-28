# extract.py
import json
import subprocess
from pathlib import Path

from config import TOOLS
from utils import (
    normalize_lang,
    extract_lang_from_filename,
    extract_folder_lang,
    subtitle_output_names,
    pick_original_subtitle_track,
    clean_srt_file,
    ensure_chinese_simplified,
)

LANG_ALIAS = {
    "thai": "tha",
    "kor": "kor",
    "jpn": "jpn",
    "eng": "eng",
    "chi": "chi",
}


def normalize_alias(lang):
    return LANG_ALIAS.get(lang, lang)


def _parse_tracks(info):
    if "tracks" in info and isinstance(info["tracks"], list):
        return info["tracks"]
    if "track_info" in info:
        return info["track_info"]
    if "tracks" in info and "track" in info["tracks"]:
        return info["tracks"]["track"]
    return []


def _extract_track(mkv, track_id, out_path):
    result = subprocess.run(
        [TOOLS["mkvextract"], str(mkv), "tracks", f"{track_id}:{out_path}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mkvextract 失败：{out_path.name}")


def _select_chinese_track(chi_tracks):
    """简体优先；仅繁体时返回繁体轨（后续 OpenCC）"""
    chi_sim = None
    chi_tra = None

    for t in chi_tracks:
        name = (t.get("name") or "").lower()
        if "simplified" in name or "简体" in name:
            chi_sim = t
        elif "traditional" in name or "繁体" in name:
            chi_tra = t
        elif chi_sim is None and chi_tra is None:
            chi_sim = t

    if chi_sim:
        return chi_sim, False
    if chi_tra:
        return chi_tra, True
    return None, False


def extract_subtitles(mkv, out_dir, log):
    mkv = Path(mkv)
    out_dir = Path(out_dir)

    log(f"🔍 调用 mkvmerge: {TOOLS['mkvmerge']}")

    info = json.loads(
        subprocess.check_output(
            [TOOLS["mkvmerge"], "-J", str(mkv)], encoding="utf-8"
        )
    )
    tracks = _parse_tracks(info)

    # ========= 音轨 → 原版语言 =========
    audio_lang = None
    for t in tracks:
        if (t.get("type") or "").lower() != "audio":
            continue
        raw = t.get("properties", {}).get("language", "")
        audio_lang = normalize_lang(raw)
        log(f"🎧 音轨 language = {raw} → {audio_lang}")
        break

    if not audio_lang or audio_lang == "und":
        file_lang = extract_lang_from_filename(mkv.name)
        if file_lang:
            log(f"⚠️ 音轨 und，使用文件名语言标识：{file_lang}")
            audio_lang = file_lang
        else:
            folder_lang = extract_folder_lang(mkv.parent.name)
            if folder_lang:
                log(f"⚠️ 音轨 und，使用文件夹名兜底：{folder_lang}")
                audio_lang = folder_lang
            else:
                raise RuntimeError("未找到音轨 language，且无法从文件名识别")

    audio_lang = normalize_alias(audio_lang)

    # ========= 收集字幕轨 =========
    orig_candidates = []
    chi_tracks = []

    for t in tracks:
        if (t.get("type") or "").lower() not in ("subtitle", "subtitles"):
            continue

        props = t.get("properties", {})
        raw_lang = props.get("language", "")
        lang = normalize_alias(normalize_lang(raw_lang))
        tid = t.get("id")
        name = props.get("track_name") or ""

        log(f"📜 字幕轨 id={tid} raw={raw_lang} norm={lang} name={name}")

        entry = {"id": tid, "name": name, "lang": lang}

        if lang == audio_lang:
            orig_candidates.append(entry)

        if lang == "chi":
            chi_tracks.append(entry)

    orig_name, chi_name = subtitle_output_names(mkv)
    out_orig = out_dir / orig_name
    out_chi = out_dir / chi_name

    # ========= 原版字幕 =========
    orig_track, need_sdh_clean = pick_original_subtitle_track(orig_candidates)

    if orig_track:
        if need_sdh_clean:
            log(f"⚠️ 仅找到 SDH 轨，将清洗听障说明：id={orig_track['id']}")
        else:
            log(f"✅ 选定原版轨：id={orig_track['id']} name={orig_track['name']}")

        _extract_track(mkv, orig_track["id"], out_orig)
        if need_sdh_clean:
            clean_srt_file(out_orig, drop_credits=False)
        log(f"✅ 原版字幕：{out_orig.name}")
    else:
        log("⚠️ 未找到原版字幕，仅提取中文字幕")

    # ========= 中文字幕 =========
    chi_track, need_t2s = _select_chinese_track(chi_tracks)
    if not chi_track:
        if not orig_track:
            raise RuntimeError("未找到中文字幕")
        return

    log(f"✅ 选定中文轨：id={chi_track['id']} name={chi_track['name']}")

    _extract_track(mkv, chi_track["id"], out_chi)
    if need_t2s:
        log("📌 字幕轨标记为繁体，将转为简体")
    ensure_chinese_simplified(out_chi, log, force=need_t2s)

    clean_srt_file(out_chi, drop_credits=False, is_chinese=True)

    log(f"✅ 中文字幕：{out_chi.name}")

    if orig_track:
        log("✅ 原版 + 中文字幕提取完成")
    else:
        log("✅ 仅生成中文字幕")


