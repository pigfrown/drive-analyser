#!/usr/bin/env python3
"""Generate media/git inventory reports for a mounted drive path.

Creates a timestamped directory under ./reports in the current working directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif", ".svg", ".raw", ".cr2", ".nef", ".arw", ".dng"
}
VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg", ".3gp", ".ts", ".mts"
}
AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".alac", ".aiff", ".opus", ".mid", ".midi"
}


def run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        return result.stdout.strip() if result.stdout.strip() else result.stderr.strip()
    except FileNotFoundError:
        return f"Command not found: {' '.join(command)}"


def sha256sum(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def format_bytes(num_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    val = float(num_bytes)
    for unit in units:
        if val < 1024.0 or unit == units[-1]:
            return f"{val:.2f} {unit}"
        val /= 1024.0
    return f"{num_bytes} B"


@dataclass
class CategoryStats:
    paths: list[str]
    total_bytes: int = 0

    def add(self, path: str, size: int) -> None:
        self.paths.append(path)
        self.total_bytes += size


@dataclass
class ScanSummary:
    images: CategoryStats
    videos: CategoryStats
    audios: CategoryStats
    git_repos: list[str]
    ext_counts: Counter
    ext_bytes: defaultdict[str, int]
    duplicate_groups: list[dict]
    total_files: int
    skipped_files: int
    scanned_dirs: int


def classify_extension(path: str) -> str:
    return Path(path).suffix.lower()


def scan_tree(root: Path, duplicate_scope: set[str]) -> ScanSummary:
    images = CategoryStats(paths=[])
    videos = CategoryStats(paths=[])
    audios = CategoryStats(paths=[])
    git_repos: list[str] = []
    ext_counts: Counter = Counter()
    ext_bytes: defaultdict[str, int] = defaultdict(int)
    files_by_size: defaultdict[int, list[str]] = defaultdict(list)
    seen_git_repo_roots: set[str] = set()

    total_files = 0
    skipped_files = 0
    scanned_dirs = 0

    stack = [root]
    while stack:
        current = stack.pop()
        scanned_dirs += 1
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name == ".git":
                                repo_root = os.path.abspath(current)
                                if repo_root not in seen_git_repo_roots:
                                    seen_git_repo_roots.add(repo_root)
                                    git_repos.append(repo_root)
                                continue
                            stack.append(Path(entry.path))
                            continue

                        if not entry.is_file(follow_symlinks=False):
                            continue

                        total_files += 1
                        ext = classify_extension(entry.name)
                        size = entry.stat(follow_symlinks=False).st_size
                        ext_key = ext if ext else "[no_ext]"
                        ext_counts[ext_key] += 1
                        ext_bytes[ext_key] += size

                        path_abs = os.path.abspath(entry.path)
                        if ext in IMAGE_EXTS:
                            images.add(path_abs, size)
                            if "images" in duplicate_scope:
                                files_by_size[size].append(path_abs)
                        elif ext in VIDEO_EXTS:
                            videos.add(path_abs, size)
                            if "videos" in duplicate_scope:
                                files_by_size[size].append(path_abs)
                        elif ext in AUDIO_EXTS:
                            audios.add(path_abs, size)
                            if "audios" in duplicate_scope:
                                files_by_size[size].append(path_abs)

                        if entry.name == ".git" and entry.is_file(follow_symlinks=False):
                            repo_root = os.path.abspath(current)
                            if repo_root not in seen_git_repo_roots:
                                seen_git_repo_roots.add(repo_root)
                                git_repos.append(repo_root)
                    except (PermissionError, OSError):
                        skipped_files += 1
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            skipped_files += 1

    duplicate_groups = []
    for size, paths in files_by_size.items():
        if len(paths) < 2:
            continue
        hashes: defaultdict[str, list[str]] = defaultdict(list)
        for path in paths:
            try:
                hashes[sha256sum(path)].append(path)
            except (PermissionError, OSError):
                skipped_files += 1
        for digest, dup_paths in hashes.items():
            if len(dup_paths) > 1:
                duplicate_groups.append(
                    {
                        "sha256": digest,
                        "size": size,
                        "paths": sorted(dup_paths),
                    }
                )

    return ScanSummary(
        images=images,
        videos=videos,
        audios=audios,
        git_repos=sorted(git_repos),
        ext_counts=ext_counts,
        ext_bytes=ext_bytes,
        duplicate_groups=sorted(duplicate_groups, key=lambda x: (x["size"], x["sha256"]), reverse=True),
        total_files=total_files,
        skipped_files=skipped_files,
        scanned_dirs=scanned_dirs,
    )


def mount_context(target: Path) -> dict[str, str]:
    resolved = str(target.resolve())
    return {
        "findmnt": run_command(["findmnt", "-T", resolved, "-o", "TARGET,SOURCE,FSTYPE,OPTIONS", "-n"]),
        "lsblk": run_command(["lsblk", "-f"]),
        "blkid": run_command(["blkid"]),
    }


def infer_drive_type(mount_info: str, root: Path) -> str:
    lower = mount_info.lower()
    home = str(Path.home())
    if "ntfs" in lower or "windows" in lower:
        return "This looks like a Windows-style filesystem/archive drive based on NTFS-related mount details."
    if str(root).startswith(home):
        return "This appears to be a Linux home/work data area based on the mount path under the current user's home directory."
    if any(k in lower for k in ["ext4", "xfs", "btrfs", "zfs"]):
        return "This appears to be a Linux data or system-mounted filesystem."
    return "This drive appears to be a general-purpose mounted filesystem; inspect mount metadata below for exact provenance."


def top_extensions(ext_counts: Counter, ext_bytes: defaultdict[str, int], limit: int = 15) -> list[tuple[str, int, int]]:
    ranked = sorted(ext_counts.items(), key=lambda kv: kv[1], reverse=True)
    return [(ext, count, ext_bytes[ext]) for ext, count in ranked[:limit]]


def write_list(path: Path, values: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for val in values:
            f.write(f"{val}\n")


def build_report(target: Path, summary: ScanSummary, mount_meta: dict[str, str]) -> str:
    intro = infer_drive_type(mount_meta.get("findmnt", ""), target)
    duplicate_files = sum(len(group["paths"]) for group in summary.duplicate_groups)
    duplicate_waste = sum((len(group["paths"]) - 1) * group["size"] for group in summary.duplicate_groups)

    lines = [
        f"Scan target: {target.resolve()}",
        f"Scan time (UTC): {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Intro: {intro}",
        "",
        "Mount identification:",
        f"- findmnt: {mount_meta.get('findmnt', '')}",
        "- lsblk output:",
        mount_meta.get("lsblk", ""),
        "- blkid output:",
        mount_meta.get("blkid", ""),
        "",
        "Category totals:",
        f"- Images: {len(summary.images.paths)} files, {format_bytes(summary.images.total_bytes)}",
        f"- Videos: {len(summary.videos.paths)} files, {format_bytes(summary.videos.total_bytes)}",
        f"- Audio: {len(summary.audios.paths)} files, {format_bytes(summary.audios.total_bytes)}",
        f"- Git repos: {len(summary.git_repos)}",
        "",
        "Duplicates (within media categories, hash-verified):",
        f"- Duplicate groups: {len(summary.duplicate_groups)}",
        f"- Duplicate file instances: {duplicate_files}",
        f"- Potential reclaimable space (excluding one file per group): {format_bytes(duplicate_waste)}",
        "",
        "Overall contents overview:",
        f"- Total files scanned: {summary.total_files}",
        f"- Directories visited: {summary.scanned_dirs}",
        f"- Entries skipped due to permission/errors: {summary.skipped_files}",
        "",
        "Top file extensions by count:",
    ]

    for ext, count, size in top_extensions(summary.ext_counts, summary.ext_bytes):
        lines.append(f"- {ext}: {count} files, {format_bytes(size)}")

    if summary.duplicate_groups:
        lines.extend(["", "Duplicate details:"])
        for group in summary.duplicate_groups[:100]:
            lines.append(
                f"- sha256={group['sha256']} size={group['size']} ({format_bytes(group['size'])}) copies={len(group['paths'])}"
            )
            for path in group["paths"]:
                lines.append(f"  - {path}")
        if len(summary.duplicate_groups) > 100:
            lines.append(f"- ... truncated {len(summary.duplicate_groups) - 100} additional duplicate groups ...")

    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate drive inventory reports")
    parser.add_argument("target", help="Mount point or directory to scan")
    parser.add_argument("--name", help="Optional report subdirectory name")
    parser.add_argument(
        "--duplicate-scope",
        choices=["none", "media"],
        default="media",
        help="Whether to detect duplicates across media files (default: media)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = Path(args.target).expanduser()
    if not target.exists() or not target.is_dir():
        print(f"Target directory does not exist or is not a directory: {target}", file=sys.stderr)
        return 2

    reports_root = Path.cwd() / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)

    default_name = datetime.now().strftime("report_%Y%m%d_%H%M%S")
    out_dir = reports_root / (args.name or default_name)
    out_dir.mkdir(parents=True, exist_ok=False)

    dup_scope = set()
    if args.duplicate_scope == "media":
        dup_scope = {"images", "videos", "audios"}

    summary = scan_tree(target, dup_scope)
    mount_meta = mount_context(target)
    report_text = build_report(target, summary, mount_meta)

    write_list(out_dir / "all_image_paths", summary.images.paths)
    write_list(out_dir / "all_video_paths", summary.videos.paths)
    write_list(out_dir / "all_audio_paths", summary.audios.paths)
    write_list(out_dir / "all_git_repos", summary.git_repos)

    (out_dir / "report").write_text(report_text, encoding="utf-8")
    (out_dir / "meta.json").write_text(
        json.dumps(
            {
                "target": str(target.resolve()),
                "generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "report_dir": str(out_dir),
                "hostname": run_command(["hostname"]),
                "disk_usage": run_command(["df", "-h", str(target.resolve())]),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Report generated at: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
