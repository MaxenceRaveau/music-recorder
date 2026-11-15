"""
The elevated process that erases and formats one USB stick. The app starts it
through a UAC prompt with a FormatRequest on the command line and follows it
through the progress file; nothing else is shared between the two.

Before touching anything the helper lists the safe USB disks afresh and
requires the requested disk number to still carry the same serial and size.
A stick swapped, unplugged, or no longer qualifying as safe since the user
picked it is refused with nothing written. Disk access goes through DiskOps
so that every step here is tested on image files; the Windows implementation
lives in winops.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, ContextManager, Protocol

from . import progress
from .disks import UsbDisk, clean_label
from .fat32 import SECTOR, compute_layout, write_volume

CHANGED = "The selected stick changed. Nothing was erased."
_WRITE_START, _WRITE_END = 0.1, 0.95


@dataclass(frozen=True)
class FormatRequest:
    disk_number: int
    serial: str
    size: int
    label: str
    progress_path: Path

    def to_args(self) -> list[str]:
        # `--opt=value` keeps values that start with "-" (or are empty) intact.
        return [
            f"--disk={self.disk_number}",
            f"--serial={self.serial}",
            f"--size={self.size}",
            f"--label={self.label}",
            f"--progress={self.progress_path}",
        ]

    @classmethod
    def from_args(cls, argv: list[str]) -> FormatRequest:
        parser = argparse.ArgumentParser(prog="audiorec.usb.helper")
        parser.add_argument("--disk", type=int, required=True)
        parser.add_argument("--serial", required=True)
        parser.add_argument("--size", type=int, required=True)
        parser.add_argument("--label", required=True)
        parser.add_argument("--progress", type=Path, required=True)
        ns = parser.parse_args(argv)
        return cls(disk_number=ns.disk, serial=ns.serial, size=ns.size,
                   label=ns.label, progress_path=ns.progress)


class DiskOps(Protocol):
    def list_disks(self) -> list[UsbDisk]: ...

    def repartition(self, disk_number: int) -> tuple[int, int]: ...

    def open_volume(self, disk_number: int) -> ContextManager[BinaryIO]: ...

    def remount(self, disk_number: int) -> str: ...


def _still_same_disk(req: FormatRequest, disks: list[UsbDisk]) -> bool:
    return any(
        d.number == req.disk_number and d.serial == req.serial and d.size == req.size
        for d in disks
    )


def run(req: FormatRequest, ops: DiskOps, *, sectors_per_cluster: int | None = None) -> int:
    """`sectors_per_cluster` exists for tests on tiny images only."""
    def report(**kw) -> None:
        progress.write_line(req.progress_path, **kw)

    try:
        report(message="Checking the stick…")
        if not _still_same_disk(req, ops.list_disks()):
            report(error=CHANGED)
            return 1

        report(fraction=0.05, message="Partitioning…")
        offset, size = ops.repartition(req.disk_number)
        if offset <= 0 or size <= 0 or offset % SECTOR or size % SECTOR:
            raise RuntimeError(f"Unexpected partition (offset {offset}, size {size})")
        label = clean_label(req.label)
        layout = compute_layout(
            size // SECTOR,
            hidden_sectors=offset // SECTOR,
            label=label,
            volume_id=time.time_ns() & 0xFFFFFFFF,
            sectors_per_cluster=sectors_per_cluster,
        )

        report(fraction=_WRITE_START, message="Writing FAT32…")
        last = _WRITE_START

        def on_write(f: float) -> None:
            nonlocal last
            fraction = _WRITE_START + (_WRITE_END - _WRITE_START) * min(max(f, 0.0), 1.0)
            # One line per percent is plenty; write_volume calls back per MiB.
            if fraction - last >= 0.01 or (f >= 1.0 and fraction > last):
                last = fraction
                report(fraction=fraction, message="Writing FAT32…")

        with ops.open_volume(req.disk_number) as dev:
            write_volume(dev, layout, progress=on_write)

        report(fraction=_WRITE_END, message="Finishing…")
        letter = ops.remount(req.disk_number)
        report(
            fraction=1.0,
            done=True,
            message=f"{letter}: is ready — FAT32, {req.size / 1e9:.1f} GB, label {label}",
        )
        return 0
    except Exception as e:
        try:
            report(error=str(e) or type(e).__name__)
        except OSError:
            pass
        return 1


def main(argv: list[str] | None = None) -> int:
    from .winops import WinDiskOps

    req = FormatRequest.from_args(sys.argv[1:] if argv is None else argv)
    return run(req, WinDiskOps())


if __name__ == "__main__":
    sys.exit(main())
