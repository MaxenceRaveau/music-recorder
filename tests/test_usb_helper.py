import json
from contextlib import contextmanager
from pathlib import Path

from audiorec.usb import helper, progress
from audiorec.usb.disks import UsbDisk
from audiorec.usb.helper import FormatRequest

MIB = 1024 * 1024
CHANGED = "The selected stick changed. Nothing was erased."
DISK_SIZE = 41 * MIB


def _disk(number=2, serial="A", size=DISK_SIZE):
    return UsbDisk(number=number, model="Stick", serial=serial, size=size,
                   letters=("E",), label="OLD", filesystem="exFAT")


def _request(tmp_path, *, serial="A", size=DISK_SIZE, label="rekord*box"):
    return FormatRequest(disk_number=2, serial=serial, size=size, label=label,
                         progress_path=tmp_path / "progress.jsonl")


class FakeOps:
    def __init__(self, image: Path, disks, *, partition=(MIB, 40 * MIB), letter="E", open_error=None):
        self.image = image
        self.disks = disks
        self.partition = partition
        self.letter = letter
        self.open_error = open_error
        self.repartitioned = []
        self.remounted = []

    def list_disks(self):
        return list(self.disks)

    def repartition(self, disk_number):
        self.repartitioned.append(disk_number)
        return self.partition

    @contextmanager
    def open_volume(self, disk_number):
        if self.open_error:
            raise self.open_error
        with open(self.image, "r+b") as f:
            yield f

    def remount(self, disk_number):
        self.remounted.append(disk_number)
        return self.letter


def _image(tmp_path, size=40 * MIB):
    path = tmp_path / "volume.img"
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_progress_last_line_wins_and_partial_line_ignored(tmp_path):
    path = tmp_path / "p.jsonl"
    assert progress.read(path) == progress.Progress()
    progress.write_line(path, fraction=0.2, message="Partitioning…")
    progress.write_line(path, fraction=0.5, message="Writing FAT32…")
    assert progress.read(path) == progress.Progress(fraction=0.5, message="Writing FAT32…")
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"fraction": 0.9, "mess')
    assert progress.read(path) == progress.Progress(fraction=0.5, message="Writing FAT32…")
    with open(path, "a", encoding="utf-8") as f:
        f.write('age": "x", "done": true, "error": null}\n')
    assert progress.read(path) == progress.Progress(fraction=0.9, message="x", done=True)
    progress.write_line(path, error="boom")
    assert progress.read(path).error == "boom"


def test_request_args_round_trip(tmp_path):
    req = FormatRequest(disk_number=3, serial="-AB 12", size=64_000_000_000, label="",
                        progress_path=tmp_path / "my progress.jsonl")
    args = req.to_args()
    assert all(isinstance(a, str) for a in args)
    assert FormatRequest.from_args(args) == req


def test_changed_stick_aborts_before_writing(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk(serial="B")])
    req = _request(tmp_path, serial="A")
    assert helper.run(req, ops) == 1
    assert progress.read(req.progress_path).error == CHANGED
    assert ops.repartitioned == []


def test_changed_size_aborts_before_writing(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk(size=DISK_SIZE + 512)])
    req = _request(tmp_path)
    assert helper.run(req, ops) == 1
    assert progress.read(req.progress_path).error == CHANGED
    assert ops.repartitioned == []


def test_unplugged_stick_aborts(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk(number=3)])
    req = _request(tmp_path)
    assert helper.run(req, ops) == 1
    assert progress.read(req.progress_path).error == CHANGED
    assert ops.repartitioned == []


def test_success_writes_fat32_and_reports_done(tmp_path):
    image = _image(tmp_path)
    ops = FakeOps(image, [_disk()])
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 0
    state = progress.read(req.progress_path)
    assert state.done and state.error is None and state.fraction == 1.0
    assert state.message == "E: is ready — FAT32, 0.0 GB, label REKORDBOX"
    assert ops.repartitioned == [2] and ops.remounted == [2]

    lines = req.progress_path.read_text(encoding="utf-8").splitlines()
    messages = [json.loads(line)["message"] for line in lines]
    assert messages[0] == "Checking the stick…"
    for step in ("Partitioning…", "Writing FAT32…", "Finishing…"):
        assert step in messages

    data = image.read_bytes()
    assert data[510:512] == b"\x55\xaa" and data[82:90] == b"FAT32   "
    assert int.from_bytes(data[28:32], "little") == 2048
    assert int.from_bytes(data[32:36], "little") == 40 * MIB // 512
    assert data[71:82] == b"REKORDBOX  "


def test_writing_progress_stays_between_bounds(tmp_path, monkeypatch):
    seen = []
    real = progress.write_line

    def spy(path, **kw):
        seen.append(kw)
        real(path, **kw)

    monkeypatch.setattr(progress, "write_line", spy)
    ops = FakeOps(_image(tmp_path), [_disk()])
    assert helper.run(_request(tmp_path), ops, sectors_per_cluster=1) == 0
    fractions = [kw.get("fraction", 0.0) for kw in seen]
    assert fractions == sorted(fractions)
    writing = [kw["fraction"] for kw in seen if kw.get("message") == "Writing FAT32…"]
    assert writing and min(writing) >= 0.1 and max(writing) <= 0.95


def test_exception_reported_as_error(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk()], open_error=OSError("Access is denied."))
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 1
    state = progress.read(req.progress_path)
    assert state.error == "Access is denied." and not state.done
    assert ops.remounted == []
