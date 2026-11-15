import pytest

from audiorec.usb.fat32 import (
    boot_sector,
    cluster_bytes_for,
    compute_layout,
    fsinfo_sector,
    write_volume,
)


@pytest.mark.parametrize("gib,expect", [(4, 4096), (8, 4096), (8.01, 8192), (16, 8192), (32, 16384), (64, 32768), (256, 32768)])
def test_cluster_bytes_for(gib, expect):
    assert cluster_bytes_for(int(gib * 1024**3)) == expect


def test_layout_fat_covers_all_clusters():
    lay = compute_layout(60_000_000, hidden_sectors=2048, label="REKORDBOX", volume_id=0x1234ABCD)
    assert lay.sectors_per_cluster == 32 and lay.reserved_sectors == 32  # 30.7 GB <= 32 GiB -> 16 KiB
    assert lay.fat_sectors * 512 // 4 >= lay.cluster_count + 2


def test_boot_sector_fields():
    lay = compute_layout(60_000_000, hidden_sectors=2048, label="REKORDBOX", volume_id=0x1234ABCD)
    b = boot_sector(lay)
    assert len(b) == 512 and b[510:512] == b"\x55\xaa" and b[82:90] == b"FAT32   "
    assert int.from_bytes(b[11:13], "little") == 512 and b[13] == 32 and b[16] == 2 and b[21] == 0xF8
    assert int.from_bytes(b[28:32], "little") == 2048 and int.from_bytes(b[32:36], "little") == 60_000_000
    assert int.from_bytes(b[44:48], "little") == 2 and b[71:82] == b"REKORDBOX  "
    assert b[:3] == b"\xeb\x58\x90" and b[64] == 0x80 and b[66] == 0x29
    assert int.from_bytes(b[48:50], "little") == 1 and int.from_bytes(b[50:52], "little") == 6
    assert int.from_bytes(b[67:71], "little") == 0x1234ABCD


def test_fsinfo_fields():
    lay = compute_layout(60_000_000, hidden_sectors=2048, label="X", volume_id=1)
    s = fsinfo_sector(lay)
    assert len(s) == 512
    assert int.from_bytes(s[0:4], "little") == 0x41615252
    assert int.from_bytes(s[484:488], "little") == 0x61417272
    assert int.from_bytes(s[488:492], "little") == lay.cluster_count - 1
    assert int.from_bytes(s[492:496], "little") == 3
    assert int.from_bytes(s[508:512], "little") == 0xAA550000


def test_too_small_and_too_large_rejected():
    with pytest.raises(ValueError, match="too small"):
        compute_layout(40_000, hidden_sectors=0, label="X", volume_id=1, sectors_per_cluster=1)
    with pytest.raises(ValueError, match="too large"):
        compute_layout(2**32 - 1, hidden_sectors=0, label="X", volume_id=1, sectors_per_cluster=1)


def test_write_volume_progress_and_backups(tmp_path):
    img = tmp_path / "s.img"
    lay = compute_layout(81_920, hidden_sectors=0, label="REKORDBOX", volume_id=7, sectors_per_cluster=1)
    seen = []
    with open(img, "wb") as f:
        f.truncate(81_920 * 512)
        write_volume(f, lay, seen.append)
    data = img.read_bytes()
    assert data[0:512] == data[6 * 512:7 * 512] == boot_sector(lay)
    assert data[512:1024] == data[7 * 512:8 * 512] == fsinfo_sector(lay)
    assert seen[-1] == 1.0 and seen == sorted(seen)


def test_image_is_readable_by_independent_reader(tmp_path):
    from pyfatfs.PyFat import PyFat
    from pyfatfs.PyFatFS import PyFatFS

    img = tmp_path / "stick.img"
    lay = compute_layout(81_920, hidden_sectors=0, label="REKORDBOX", volume_id=7, sectors_per_cluster=1)  # 40 MiB
    with open(img, "wb") as f:
        f.truncate(81_920 * 512)
        write_volume(f, lay)

    fs = PyFatFS(str(img))
    try:
        assert fs.fs.fat_type == PyFat.FAT_TYPE_FAT32
        assert fs.fs.bpb_header["BS_VolLab"] == b"REKORDBOX  "
        assert fs.listdir("/") == []
        fs.writetext("/TEST.TXT", "hello")
    finally:
        fs.close()

    fs = PyFatFS(str(img))
    try:
        assert fs.readbytes("/TEST.TXT") == b"hello"
    finally:
        fs.close()


class _ShortWriter:
    def __init__(self, f, limit):
        self.f, self.limit = f, limit

    def seek(self, *a):
        return self.f.seek(*a)

    def write(self, data):
        return self.f.write(bytes(data[:self.limit]))

    def flush(self):
        self.f.flush()


class _ZeroWriter(_ShortWriter):
    def write(self, data):
        return 0


def test_short_writes_still_produce_complete_image(tmp_path):
    lay = compute_layout(81_920, hidden_sectors=0, label="REKORDBOX", volume_id=7, sectors_per_cluster=1)
    full, short = tmp_path / "a.img", tmp_path / "b.img"
    with open(full, "wb") as f:
        f.truncate(81_920 * 512)
        write_volume(f, lay)
    with open(short, "wb") as f:
        f.truncate(81_920 * 512)
        write_volume(_ShortWriter(f, 1000), lay)
    assert full.read_bytes() == short.read_bytes()


def test_zero_write_raises(tmp_path):
    lay = compute_layout(81_920, hidden_sectors=0, label="X", volume_id=7, sectors_per_cluster=1)
    with open(tmp_path / "z.img", "wb") as f:
        with pytest.raises(OSError):
            write_volume(_ZeroWriter(f, 0), lay)


def test_empty_label_rejected():
    with pytest.raises(ValueError, match="label"):
        compute_layout(81_920, hidden_sectors=0, label="", volume_id=1, sectors_per_cluster=1)


def test_fats_and_root_entry_bytes(tmp_path):
    img = tmp_path / "s.img"
    lay = compute_layout(81_920, hidden_sectors=0, label="REKORDBOX", volume_id=7, sectors_per_cluster=1)
    with open(img, "wb") as f:
        f.truncate(81_920 * 512)
        write_volume(f, lay)
    data = img.read_bytes()
    entries = (0x0FFFFFF8).to_bytes(4, "little") + (0x0FFFFFFF).to_bytes(4, "little") * 2
    for i in range(2):
        off = (lay.reserved_sectors + i * lay.fat_sectors) * 512
        assert data[off:off + 12] == entries
        assert data[off + 12:off + 16] == bytes(4)
    root = (lay.reserved_sectors + 2 * lay.fat_sectors) * 512
    assert data[root:root + 12] == b"REKORDBOX  "
    assert int.from_bytes(data[root + 24:root + 26], "little") != 0  # write date stamped
    assert data[root + 32:root + 64] == bytes(32)
