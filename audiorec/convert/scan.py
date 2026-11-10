"""
Walk the library and decide, per audio file, what to do: convert, copy as-is,
skip (output already exists) or flag a name collision. Scanning only reads the
library; it never writes anything, so it is safe to run repeatedly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from .formats import TARGETS, is_audio
from .probe import AudioInfo, probe
from .rules import can_copy, estimate_bytes


class Action(str, Enum):
    CONVERT = "convert"
    COPY = "copy"
    DONE = "done"
    COLLISION = "collision"


@dataclass
class Job:
    src: Path
    dst: Path
    group: str  # source parent folder relative to the library, POSIX; "" = root
    action: Action
    info: AudioInfo
    est_bytes: int
    note: str = ""


def _norm(p: Path) -> str:
    return os.path.normcase(str(p.resolve()))


def validate_paths(library: Path, dest: Path) -> str | None:
    if not library.is_dir():
        return "Library folder not found."
    if str(dest).strip() in ("", "."):
        return "Choose a destination folder."
    lib, dst = _norm(library), _norm(dest)
    if lib == dst:
        return "Destination can't be the library folder."
    if dst.startswith(lib.rstrip(os.sep) + os.sep):
        return "Destination can't be inside the library folder."
    if lib.startswith(dst.rstrip(os.sep) + os.sep):
        return "Destination can't contain the library folder."
    return None


def output_path(library: Path, dest: Path, src: Path, target: str) -> Path:
    return dest / src.relative_to(library).with_suffix(TARGETS[target].ext)


def _walk_audio(library: Path) -> list[Path]:
    files: list[Path] = []
    for root, dirs, names in os.walk(library):
        dirs.sort(key=str.lower)
        for n in sorted(names, key=str.lower):
            p = Path(root) / n
            if is_audio(p):
                files.append(p)
    return files


def scan(library: Path, dest: Path, target: str, *,
         progress: Callable[[int, int], None] | None = None,
         cancelled: Callable[[], bool] = lambda: False) -> list[Job] | None:
    files = _walk_audio(library)
    jobs: list[Job] = []
    for i, src in enumerate(files, 1):
        info = probe(src)
        copy = can_copy(info, target)
        jobs.append(Job(
            src=src, dst=output_path(library, dest, src, target),
            group="" if src.parent == library else src.parent.relative_to(library).as_posix(),
            action=Action.COPY if copy else Action.CONVERT, info=info,
            est_bytes=estimate_bytes(info, target, src.stat().st_size, copy)))
        if progress:
            progress(i, len(files))
        if cancelled():
            return None

    by_dst: dict[str, list[Job]] = {}
    for j in jobs:
        by_dst.setdefault(str(j.dst).lower(), []).append(j)
    for group in by_dst.values():
        winner = next((j for j in group if j.action == Action.COPY), group[0])
        for j in group:
            if j is not winner:
                j.action = Action.COLLISION
                j.note = f"same output name as {winner.src.name}"
        if winner.dst.exists():
            winner.action = Action.DONE
    return jobs


def folder_groups(jobs: list[Job]) -> list[str]:
    return sorted({j.group for j in jobs})


def pending_counts(jobs: list[Job]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for j in jobs:
        if j.action not in (Action.CONVERT, Action.COPY):
            continue
        counts[""] = counts.get("", 0) + 1
        parts = j.group.split("/") if j.group else []
        for n in range(1, len(parts) + 1):
            key = "/".join(parts[:n])
            counts[key] = counts.get(key, 0) + 1
    return counts
