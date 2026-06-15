#!/usr/bin/env python3
"""
将字幕根目录下各剧集文件夹内的 .mkv 重命名为 SxxExx.mkv。

用法:
  python rename_mkv_episodes.py "D:\\剧集根目录"
  python rename_mkv_episodes.py "D:\\剧集根目录" --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from utils import extract_episode_id, iter_show_dirs_with_mkv

_TEMP_PREFIX = ".__renaming__"
LogFn = Callable[[str], None]


def plan_mkv_renames(folder: Path) -> tuple[list[tuple[Path, Path]], list[str]]:
    """返回 [(源路径, 目标路径), ...] 与错误信息列表。"""
    folder = Path(folder)
    plans: list[tuple[Path, Path]] = []
    errors: list[str] = []
    by_episode: dict[str, str] = {}

    for mkv in sorted(folder.glob("*.mkv")):
        ep = extract_episode_id(mkv.name)
        if not ep:
            errors.append(f"[{folder.name}] 无法识别集数，已跳过: {mkv.name}")
            continue

        target = folder / f"{ep}.mkv"
        if mkv.resolve() == target.resolve():
            continue

        if ep in by_episode:
            errors.append(
                f"[{folder.name}] 重复集数 {ep}: "
                f"{by_episode[ep]} 与 {mkv.name}"
            )
            continue

        by_episode[ep] = mkv.name
        plans.append((mkv, target))

    planned_sources = {src.resolve() for src, _ in plans}
    for _, target in plans:
        if target.exists() and target.resolve() not in planned_sources:
            errors.append(f"[{folder.name}] 目标已存在: {target.name}")

    if errors:
        return [], errors

    return plans, []


def collect_rename_plans(
    root: Path,
) -> tuple[list[Path], list[tuple[Path, Path]], list[str]]:
    """解析根目录，汇总所有待重命名的 mkv。"""
    root = Path(root)
    shows = iter_show_dirs_with_mkv(root)
    all_plans: list[tuple[Path, Path]] = []
    all_errors: list[str] = []
    for show_dir in shows:
        plans, errors = plan_mkv_renames(show_dir)
        all_errors.extend(errors)
        all_plans.extend(plans)
    return shows, all_plans, all_errors


def apply_renames(
    plans: list[tuple[Path, Path]],
    dry_run: bool = False,
    on_progress=None,
    should_cancel=None,
) -> int:
    if not plans:
        return 0
    if dry_run:
        return len(plans)

    total = len(plans)
    count = 0
    for src, dst in plans:
        if should_cancel and should_cancel():
            break
        temp = src.parent / f"{_TEMP_PREFIX}{dst.name}"
        if temp.exists():
            raise RuntimeError(f"临时文件已存在，请手动删除后重试: {temp}")
        src.rename(temp)
        temp.rename(dst)
        count += 1
        if on_progress:
            on_progress(count, total, dst.parent.name, dst.name)
    return count


def log_rename_plans(
    plans: list[tuple[Path, Path]],
    log: LogFn,
    *,
    preview: bool = False,
) -> None:
    prefix = "[预览] " if preview else ""
    log(f"{prefix}共 {len(plans)} 个文件将重命名：")
    for src, dst in plans:
        log(f"  {src.parent.name}\\{src.name}  ->  {dst.name}")


def execute_renames_for_shows(
    shows: list[Path],
    on_progress=None,
    should_cancel=None,
) -> tuple[int, list[str]]:
    """按剧集目录执行已规划的重命名。"""
    all_plans: list[tuple[Path, Path]] = []
    for show_dir in shows:
        plans, errors = plan_mkv_renames(show_dir)
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


def rename_mkv_at_root(
    root: Path,
    *,
    dry_run: bool = False,
    log: LogFn | None = None,
    list_plans: bool = True,
) -> tuple[int, list[str]]:
    """
    对根目录下所有剧集文件夹执行 mkv 重命名。
    返回 (成功数量, 错误列表)；有错误时不执行任何改名。
    """
    def _log(msg: str) -> None:
        if log:
            log(msg)

    shows, all_plans, all_errors = collect_rename_plans(root)
    if not shows:
        return 0, [f"未找到包含 .mkv 的剧集目录: {root}"]
    if all_errors:
        return 0, all_errors
    if not all_plans:
        _log("所有 mkv 文件名已是 SxxExx.mkv，无需修改。")
        return 0, []

    if list_plans and log:
        log_rename_plans(all_plans, log, preview=dry_run)

    if dry_run:
        return len(all_plans), []

    count, errors = execute_renames_for_shows(shows)
    return count, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="将各剧集目录下含 SxxExx 的 mkv 重命名为 SxxExx.mkv"
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="",
        help="字幕根目录（其下每个子文件夹为一部剧）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不实际改名",
    )
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
        count, errors = rename_mkv_at_root(root, dry_run=True, log=print)
    else:
        shows, all_plans, all_errors = collect_rename_plans(root)
        if all_errors:
            errors = all_errors
            count = 0
        elif not all_plans:
            print("所有 mkv 文件名已是 SxxExx.mkv，无需修改。")
            return 0
        else:
            log_rename_plans(all_plans, print)
            if input("\n确认执行？(y/N): ").strip().lower() != "y":
                print("已取消。")
                return 0
            count, errors = execute_renames_for_shows(shows)

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
