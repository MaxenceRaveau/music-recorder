"""
FAT32 layout and writer. Windows refuses to format volumes over 32 GB as
FAT32, and CDJs need FAT32, so we lay the file system out ourselves. This
module is pure Python and writes to any seekable binary file (an image in
tests, the raw volume in the elevated helper), with offset 0 being the
volume's first sector.

Sizing follows Microsoft's fatgen103. Only a boot sector, FSInfo, two empty
FATs and an empty root directory are written; nothing else on the volume is
touched, so a "format" is a quick one.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable

SECTOR = 512
CHUNK = 1024 * 1024
_MAX_CLUSTERS = 0x0FFFFFF5
_MIN_CLUSTERS = 65525


def cluster_bytes_for(volume_bytes: int) -> int:
    gib = 1024**3
    if volume_bytes <= 8 * gib:
        return 4096
    if volume_bytes <= 16 * gib:
        return 8192
    if volume_bytes <= 32 * gib:
        return 16384
    return 32768


@dataclass(frozen=True)
class Fat32Layout:
    total_sectors: int
    hidden_sectors: int
    sectors_per_cluster: int
    reserved_sectors: int
    fat_sectors: int
    cluster_count: int
    volume_id: int
    label: str

    bytes_per_sector = 512
    num_fats = 2
    root_cluster = 2


def compute_layout(
    total_sectors: int,
    *,
    hidden_sectors: int,
    label: str,
    volume_id: int,
    sectors_per_cluster: int | None = None,
) -> Fat32Layout:
    if sectors_per_cluster is None:
        sectors_per_cluster = cluster_bytes_for(total_sectors * SECTOR) // SECTOR
    reserved = 32
    num_fats = Fat32Layout.num_fats
    tmp1 = total_sectors - reserved
    tmp2 = (256 * sectors_per_cluster + num_fats) // 2
    fat_sectors = (tmp1 + tmp2 - 1) // tmp2
    cluster_count = (total_sectors - reserved - num_fats * fat_sectors) // sectors_per_cluster
    if cluster_count < _MIN_CLUSTERS:
        raise ValueError("Volume too small for FAT32")
    if cluster_count > _MAX_CLUSTERS:
        raise ValueError("Volume too large for FAT32")
    return Fat32Layout(
        total_sectors=total_sectors,
        hidden_sectors=hidden_sectors,
        sectors_per_cluster=sectors_per_cluster,
        reserved_sectors=reserved,
        fat_sectors=fat_sectors,
        cluster_count=cluster_count,
        volume_id=volume_id,
        label=label,
    )


def _label11(label: str) -> bytes:
    return label.encode("cp437", errors="replace")[:11].ljust(11, b" ")


def boot_sector(layout: Fat32Layout) -> bytes:
    b = bytearray(SECTOR)
    b[0:3] = b"\xeb\x58\x90"
    b[3:11] = b"MSWIN4.1"
    struct.pack_into("<HBHBHHBHHHII", b, 11,
                     SECTOR, layout.sectors_per_cluster, layout.reserved_sectors,
                     layout.num_fats, 0, 0, 0xF8, 0, 63, 255,
                     layout.hidden_sectors, layout.total_sectors)
    struct.pack_into("<IHHIHH", b, 36,
                     layout.fat_sectors, 0, 0, layout.root_cluster, 1, 6)
    b[64] = 0x80
    b[66] = 0x29
    struct.pack_into("<I", b, 67, layout.volume_id)
    b[71:82] = _label11(layout.label)
    b[82:90] = b"FAT32   "
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def fsinfo_sector(layout: Fat32Layout) -> bytes:
    b = bytearray(SECTOR)
    struct.pack_into("<I", b, 0, 0x41615252)
    struct.pack_into("<I", b, 484, 0x61417272)
    struct.pack_into("<II", b, 488, layout.cluster_count - 1, 3)
    struct.pack_into("<I", b, 508, 0xAA550000)
    return bytes(b)


def write_volume(
    dev: BinaryIO,
    layout: Fat32Layout,
    progress: Callable[[float], None] = lambda f: None,
) -> None:
    cluster_bytes = layout.sectors_per_cluster * SECTOR
    fat_bytes = layout.fat_sectors * SECTOR
    total = layout.reserved_sectors * SECTOR + layout.num_fats * fat_bytes + cluster_bytes
    done = 0

    def write_at(offset: int, size: int, head: bytes = b"") -> None:
        """Write `head` then zeros up to `size` bytes, in 1 MiB chunks."""
        nonlocal done
        dev.seek(offset)
        written = 0
        while written < size:
            n = min(CHUNK, size - written)
            chunk = head[written:written + n].ljust(n, b"\x00") if written < len(head) else bytes(n)
            dev.write(chunk)
            written += n
            done += n
            progress(done / total)

    boot, info = boot_sector(layout), fsinfo_sector(layout)
    reserved = bytearray(layout.reserved_sectors * SECTOR)
    reserved[0:SECTOR] = boot
    reserved[SECTOR:2 * SECTOR] = info
    reserved[6 * SECTOR:7 * SECTOR] = boot
    reserved[7 * SECTOR:8 * SECTOR] = info
    write_at(0, len(reserved), bytes(reserved))

    fat_head = struct.pack("<III", 0x0FFFFFF8, 0x0FFFFFFF, 0x0FFFFFFF)
    for i in range(layout.num_fats):
        write_at((layout.reserved_sectors + i * layout.fat_sectors) * SECTOR, fat_bytes, fat_head)

    label_entry = _label11(layout.label) + b"\x08"
    root_offset = (layout.reserved_sectors + layout.num_fats * layout.fat_sectors) * SECTOR
    write_at(root_offset, cluster_bytes, label_entry)
    dev.flush()
