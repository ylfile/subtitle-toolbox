#!/usr/bin/env python3
"""
将字幕根目录下各剧集文件夹内的压制版 mp4 重命名为 SxxExx.mp4。

支持：
  S01E01_压制版.mp4       → S01E01.mp4
  S01E01_原音_压制版.mp4  → S01E01.mp4
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

from rename_mkv_episodes import apply_renames, log_rename_plans
from utils import extract_episode_id

MP4_YAZHI_RE = re.compile(r"^(S\d+E\d+)(?:_原音)?_压制版$", re.I)
LogFn = Callable[[str], None]


def iter_show_dirs_with_mp4(selected: Path) -> list[Path]:
    """根目录下含 mp4 的子文件夹；或直接选单部剧文件夹"""
    selected = Path(selected)
    if not selected.is_dir():
        return []
    if list(selected.glob("*.mp4")):
        return [selected]
    shows = []
    for child in sorted(selected.iterdir()):
        if child.is_dir() and list(child.glob("*.mp4")):
            shows.append(child)
    return shows


def plan_mp4_renames(folder: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    """返回 [(源路径, 目标路径), ...] 与错误信息列表。"""
    folder = Path(folder)
    plans: list[tuple[Path, Path]] = []
    errors: list[str] = []
    by_episode: dict[str, str] = {}

    for mp4 in sorted(folder.glob("*.mp4")):
        m = MP4_YAZHI_RE.match(mp4.stem)
        if not m:
            continue

        ep = m.group(1).upper()
        if extract_episode_id(mp4.name) != ep:
            ep = extract_episode_id(mp4.name) or ep

        target = folder / f"{ep}.mp4"
        if mp4.resolve() == target.resolve():
            continue

        if ep in by_episode:
            errors.append(
                f"[{folder.name}] 重复集数 {ep}: "
                f"{by_episode[ep]} 与 {mp4.name}"
            )
            continue

        by_episode[ep] = mp4.name
        plans.append((mp4, target))

    planned_sources = {src.resolve() for src, _ in plans}
    for _, target in plans:
        if target.exists() and target.resolve() not in planned_sources:
            errors.append(f"[{folder.name}] 目标已存在: {target.name}")

    if errors:
        return [], errors

    return plans, []


def collect_mp4_rename_plans(
    root: Path,
) -> tuple[list[Path], list[tuple[Path, Path]], list[str]]:
    root = Path(root)
    shows = iter_show_dirs_with_mp4(root)
    all_plans: list[tuple[Path, Path]] = []
    all_errors: list[str] = []
    for show_dir in shows:
        plans, errors = plan_mp4_renames(show_dir)
        all_errors.extend(errors)
        all_plans.extend(plans)
    return shows, all_plans, all_errors


def execute_mp4_renames_for_shows(
    shows: list[Path],
    on_progress=None,
    should_cancel=None,
) -> tuple[int, list[str]]:
    all_plans: list[tuple[Path, Path]] = []
    for show_dir in shows:
        plans, errors = plan_mp4_renames(show_dir)
        if errors:
            return 0, ["执行前检测到新冲突，已中止。"] + errors
        all_plans.extend(plans)
    if not all_plans:
        return 0, []
    count = apply_renames(
        all_plans,
        dry_run=False,
        on_progress=on_progress,
        should_cancel=should_cancel,
    )
    return count, []


def rename_mp4_at_root(
    root: Path,
    *,
    dry_run: bool = False,
    log: LogFn | None = None,
    list_plans: bool = True,
) -> tuple[int, list[str]]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    shows, all_plans, all_errors = collect_mp4_rename_plans(root)
    if not shows:
        return 0, [f"未找到包含 .mp4 的剧集目录: {root}"]
    if all_errors:
        return 0, all_errors
    if not all_plans:
        _log("没有符合 _压制版 / _原音_压制版 规则的 mp4 需要重命名。")
        return 0, []

    if list_plans and log:
        log_rename_plans(all_plans, log, preview=dry_run)

    if dry_run:
        return len(all_plans), []

    count, errors = execute_mp4_renames_for_shows(shows)
    return count, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将各剧集目录下压制版 mp4 重命名为 SxxExx.mp4"
    )
    parser.add_argument("root", nargs="?", default="", help="字幕根目录")
    parser.add_argument("--dry-run", action="store_true", help="仅预览")
    args = parser.parse_args()

    root_s = args.root.strip() or input("请输入剧集根目录路径: ").strip()
    if not root_s:
        print("未指定根目录。", file=sys.stderr)
        return 1

    root = Path(root_s)
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 1

    if args.dry_run:
        count, errors = rename_mp4_at_root(root, dry_run=True, log=print)
    else:
        shows, all_plans, all_errors = collect_mp4_rename_plans(root)
        if all_errors:
            errors = all_errors
            count = 0
        elif not all_plans:
            print("没有符合规则的 mp4 需要重命名。")
            return 0
        else:
            log_rename_plans(all_plans, print)
            if input("\n确认执行？(y/N): ").strip().lower() != "y":
                print("已取消。")
                return 0
            count, errors = execute_mp4_renames_for_shows(shows)

    if errors:
        print("以下问题需先处理，未执行任何重命名：")
        for msg in errors:
            print(f"  {msg}")
        return 1

    if not args.dry_run and count:
        print(f"完成，已重命名 {count} 个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
