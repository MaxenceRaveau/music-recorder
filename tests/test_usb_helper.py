import json
from contextlib import contextmanager
from pathlib import Path

import pytest

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
    def __init__(self, image: Path, disks, *, partition=(MIB, 40 * MIB, 1), letter="E",
                 repartition_error=None, open_error=None, write_error=None, remount_error=None):
        self.image = image
        self.disks = disks
        self.partition = partition
        self.letter = letter
        self.repartition_error = repartition_error
        self.open_error = open_error
        self.write_error = write_error
        self.remount_error = remount_error
        self.repartitioned = []
        self.opened = []
        self.remounted = []

    def list_disks(self):
        return list(self.disks)

    def repartition(self, disk_number, serial, size):
        self.repartitioned.append((disk_number, serial, size))
        if self.repartition_error:
            raise self.repartition_error
        return self.partition

    @contextmanager
    def open_volume(self, disk_number, partition_number, offset, size):
        self.opened.append((disk_number, partition_number, offset, size))
        if self.open_error:
            raise self.open_error
        with open(self.image, "r+b") as f:
            if self.write_error:
                yield _FailingDev(f, self.write_error)
            else:
                yield f

    def remount(self, disk_number, partition_number):
        self.remounted.append((disk_number, partition_number))
        if self.remount_error:
            raise self.remount_error
        return self.letter


class _FailingDev:
    """Accepts the first write, then fails, like a stick pulled mid-format."""

    def __init__(self, f, error):
        self.f, self.error, self.writes = f, error, 0

    def seek(self, pos):
        return self.f.seek(pos)

    def write(self, data):
        self.writes += 1
        if self.writes > 1:
            raise self.error
        return self.f.write(data)

    def flush(self):
        self.f.flush()


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
    assert ops.repartitioned == [(2, "A", DISK_SIZE)]
    assert ops.opened == [(2, 1, MIB, 40 * MIB)] and ops.remounted == [(2, 1)]

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


def _untouched(image):
    return image.read_bytes() == bytes(40 * MIB)


def test_identity_refused_at_repartition_writes_nothing(tmp_path):
    image = _image(tmp_path)
    ops = FakeOps(image, [_disk()], repartition_error=RuntimeError(CHANGED))
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 1
    assert progress.read(req.progress_path).error == CHANGED
    assert ops.opened == [] and ops.remounted == [] and _untouched(image)


def test_extents_mismatch_at_open_writes_nothing(tmp_path):
    image = _image(tmp_path)
    ops = FakeOps(image, [_disk()], open_error=OSError("Volume is not on the selected stick."))
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 1
    assert progress.read(req.progress_path).error == "Volume is not on the selected stick."
    assert ops.remounted == [] and _untouched(image)


@pytest.mark.parametrize("partition", [
    (0, 40 * MIB, 1),                      # offset 0 would overwrite the MBR
    (MIB, DISK_SIZE, 1),                   # ends beyond the disk
    (MIB, 0, 1),                           # empty
    (MIB + 1, 40 * MIB, 1),                # not sector-aligned
])
def test_invalid_partition_refused_before_open(tmp_path, partition):
    image = _image(tmp_path)
    ops = FakeOps(image, [_disk()], partition=partition)
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 1
    assert progress.read(req.progress_path).error
    assert ops.opened == [] and _untouched(image)


def test_error_while_writing_reported(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk()], write_error=OSError("The device is not ready."))
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 1
    state = progress.read(req.progress_path)
    assert state.error == "The device is not ready." and not state.done
    assert ops.remounted == []


def test_progress_write_failure_still_exits_nonzero(tmp_path, monkeypatch):
    def broken(path, **kw):
        raise PermissionError("progress file locked")

    monkeypatch.setattr(progress, "write_line", broken)
    ops = FakeOps(_image(tmp_path), [_disk()])
    assert helper.run(_request(tmp_path), ops, sectors_per_cluster=1) == 1
    assert ops.repartitioned == []


def test_extents_check():
    from audiorec.usb.winops import check_extents

    check_extents([(2, MIB, 40 * MIB)], disk_number=2, offset=MIB, size=40 * MIB)
    for extents in ([], [(3, MIB, 40 * MIB)], [(2, 0, 40 * MIB)], [(2, MIB, 39 * MIB)],
                    [(2, MIB, 20 * MIB), (2, 21 * MIB, 20 * MIB)]):
        with pytest.raises(OSError):
            check_extents(extents, disk_number=2, offset=MIB, size=40 * MIB)


def test_powershell_error_trimmed():
    from audiorec.usb.winops import first_error_line

    stderr = (
        "The selected stick changed. Nothing was erased.\r\n"
        "At line:4 char:5\r\n+     throw 'x'\r\n+     ~~~~~~\r\n"
        "    + CategoryInfo          : OperationStopped: (:) [], RuntimeException\r\n"
    )
    assert first_error_line(stderr) == "The selected stick changed. Nothing was erased."
    assert first_error_line("\n  \n") == ""


@pytest.mark.parametrize("size", [2 * MIB, 128 * MIB, 256 * MIB])
def test_too_small_stick_refused_before_erasing(tmp_path, size):
    image = _image(tmp_path)
    ops = FakeOps(image, [_disk(size=size)])
    req = _request(tmp_path, size=size)
    assert helper.run(req, ops) == 1
    assert progress.read(req.progress_path).error == \
        "This stick is too small for FAT32. Nothing was erased."
    assert ops.repartitioned == [] and ops.opened == [] and _untouched(image)


def test_remount_failure_after_writing_is_done_with_warning(tmp_path):
    ops = FakeOps(_image(tmp_path), [_disk()],
                  remount_error=RuntimeError("Windows did not answer in time"))
    req = _request(tmp_path)
    assert helper.run(req, ops, sectors_per_cluster=1) == 0
    state = progress.read(req.progress_path)
    assert state.done and state.error is None
    assert state.message == ("Formatted, but Windows did not assign a drive letter: "
                             "Windows did not answer in time")


def _fake_powershell(monkeypatch, *, timeout=False, stdout="ok\n"):
    import subprocess

    from audiorec.usb import winops

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if timeout:
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(winops.subprocess, "run", fake_run)
    return calls


def test_powershell_scripts_set_utf8_and_import_storage_from_pshome(monkeypatch):
    from audiorec.usb import winops

    calls = _fake_powershell(monkeypatch)
    assert winops._ps("Get-Disk") == "ok"
    script = calls[0][-1]
    lines = script.splitlines()
    assert lines[0] == "[Console]::OutputEncoding = [Text.Encoding]::UTF8"
    assert r'Import-Module "$PSHOME\Modules\Storage"' in lines[:3]
    assert script.rstrip().endswith("Get-Disk")


def test_powershell_timeout_is_not_retried(monkeypatch):
    from audiorec.usb import winops

    calls = _fake_powershell(monkeypatch, timeout=True)
    monkeypatch.setattr(winops.time, "sleep", lambda s: None)
    with pytest.raises(winops.PowerShellTimeout):
        winops._retry(lambda: winops._ps("Get-Partition"))
    assert len(calls) == 1


def test_other_powershell_errors_are_retried(monkeypatch):
    from audiorec.usb import winops

    monkeypatch.setattr(winops.time, "sleep", lambda s: None)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("not mounted yet")
        return "done"

    assert winops._retry(flaky) == "done" and len(attempts) == 3
