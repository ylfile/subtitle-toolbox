# extract.py
import json
import subprocess
from pathlib import Path

from config import TOOLS
from utils import (
    check_output_hidden,
    run_hidden,
    normalize_lang,
    normalize_audio_lang,
    subtitle_output_names,
    pick_original_subtitle_track,
    pick_audio_language,
    clean_srt_file,
    ensure_chinese_simplified,
)


def _parse_tracks(info):
    if "tracks" in info and isinstance(info["tracks"], list):
        return info["tracks"]
    if "track_info" in info:
        return info["track_info"]
    if "tracks" in info and "track" in info["tracks"]:
        return info["tracks"]["track"]
    return []


def count_extract_jobs(show_dirs):
    """统计待提取的 mkv 数量"""
    return sum(len(list(d.glob("*.mkv"))) for d in show_dirs)


def _extract_track(mkv, track_id, out_path):
    result = run_hidden(
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
        lang = (t.get("lang") or "").lower()

        if lang in ("chi", "chs"):
            chi_sim = t
        elif lang == "cht":
            chi_tra = t
        elif "simplified" in name or "简体" in name:
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


def extract_subtitles(mkv, out_dir, log, forced_lang=None):
    mkv = Path(mkv)
    out_dir = Path(out_dir)

    log(f"🔍 调用 mkvmerge: {TOOLS['mkvmerge']}")

    info = json.loads(
        check_output_hidden(
            [TOOLS["mkvmerge"], "-J", str(mkv)], encoding="utf-8"
        )
    )
    tracks = _parse_tracks(info)

    # ========= 音轨收集 =========
    audio_tracks = []
    for t in tracks:
        if (t.get("type") or "").lower() != "audio":
            continue
        props = t.get("properties", {})
        audio_tracks.append(
            {
                "id": t.get("id"),
                "raw": props.get("language", ""),
                "name": props.get("track_name") or "",
            }
        )

    audio_lang = pick_audio_language(
        audio_tracks, mkv.parent.name, mkv.name, log=log, forced_lang=forced_lang
    )
    audio_lang = normalize_audio_lang(audio_lang)
    log(f"🎯 原版语言（与所选音轨一致）：{audio_lang}")

    # ========= 收集字幕轨 =========
    orig_candidates = []
    chi_tracks = []

    for t in tracks:
        if (t.get("type") or "").lower() not in ("subtitle", "subtitles"):
            continue

        props = t.get("properties", {})
        raw_lang = props.get("language", "")
        lang = normalize_audio_lang(raw_lang)
        tid = t.get("id")
        name = props.get("track_name") or ""

        log(f"📜 字幕轨 id={tid} raw={raw_lang} norm={lang} name={name}")

        forced_flag = bool(props.get("flag_forced"))

        entry = {
            "id": tid,
            "name": name,
            "lang": lang,
            "forced": forced_flag,
        }

        if lang == audio_lang:
            orig_candidates.append(entry)

        if lang == "chi":
            chi_tracks.append(entry)

    orig_name, chi_name = subtitle_output_names(mkv)
    out_orig = out_dir / orig_name
    out_chi = out_dir / chi_name

    # ========= 原版字幕 =========
    orig_track, need_sdh_clean, pick_status = pick_original_subtitle_track(
        orig_candidates
    )

    if pick_status == "forced_only":
        log(
            f"⚠️ {mkv.name}：与音轨同语言的字幕仅有 Forced 轨，"
            f"不提取原版，仅提取中文字幕"
        )
        orig_track = None
    elif orig_track:
        if need_sdh_clean:
            log(f"⚠️ 仅找到 SDH 轨，将清洗听障说明：id={orig_track['id']}")
        else:
            log(f"✅ 选定原版轨：id={orig_track['id']} name={orig_track['name']}")

        _extract_track(mkv, orig_track["id"], out_orig)
        if need_sdh_clean:
            clean_srt_file(out_orig, drop_credits=False)
        log(f"✅ 原版字幕：{out_orig.name}")
    elif pick_status != "forced_only":
        log("⚠️ 未找到原版字幕，仅提取中文字幕")

    # ========= 中文字幕 =========
    chi_track, need_t2s = _select_chinese_track(chi_tracks)
    if not chi_track:
        if not orig_track:
            raise RuntimeError("未找到中文字幕")
        return None

    log(f"✅ 选定中文轨：id={chi_track['id']} name={chi_track['name']}")

    _extract_track(mkv, chi_track["id"], out_chi)
    if need_t2s:
        log("📌 字幕轨标记为繁体，将转为简体")
    ensure_chinese_simplified(out_chi, log, force=need_t2s)

    clean_srt_file(out_chi, drop_credits=False, is_chinese=True)

    log(f"✅ 中文字幕：{out_chi.name}")

    if orig_track:
        log("✅ 原版 + 中文字幕提取完成")
        return None

    log("✅ 仅生成中文字幕")
