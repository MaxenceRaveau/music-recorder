import threading

from audiorec.convert import ffmpeg
from audiorec.convert.runner import Runner
from audiorec.convert.scan import Action, scan
from tests.audio_files import make_audio

FLAC = ["-c:a", "flac"]


def _jobs(lib, out, target="mp3"):
    return [j for j in scan(lib, out, target) if j.action in (Action.CONVERT, Action.COPY)]


def _run(jobs, target="mp3", workers=2):
    results = []
    ok = Runner(jobs, target, workers=workers,
                on_result=lambda j, s, m: results.append((j, s, m))).run()
    return ok, results


def test_runs_all_and_reports_each(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    for i in range(3):
        make_audio(lib / f"t{i}.flac", codec_args=FLAC, seconds=0.5)
    make_audio(lib / "a.mp3", codec_args=["-c:a", "libmp3lame", "-b:a", "192k"], seconds=0.5)
    ok, results = _run(_jobs(lib, out))
    assert ok is True
    statuses = sorted(s for _, s, _ in results)
    assert statuses == ["converted"] * 3 + ["copied"]
    assert all(j.dst.exists() for j, _, _ in results)


def test_failure_does_not_stop_others(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "a.flac", codec_args=FLAC, seconds=0.5)
    make_audio(lib / "c.flac", codec_args=FLAC, seconds=0.5)
    (lib / "bad.flac").write_bytes(b"this is not audio" * 100)
    ok, results = _run(_jobs(lib, out))
    assert ok is True
    failed = [(j, m) for j, s, m in results if s == "failed"]
    assert len(failed) == 1 and failed[0][1]
    assert sum(s == "converted" for _, s, _ in results) == 2


def test_source_deleted_after_scan_fails_cleanly(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    for n in "abc":
        make_audio(lib / f"{n}.flac", codec_args=FLAC, seconds=0.5)
    jobs = _jobs(lib, out)
    jobs[0].src.unlink()
    ok, results = _run(jobs)
    assert ok is True
    by = {j.src.name: s for j, s, _ in results}
    assert by[jobs[0].src.name] == "failed"
    assert sorted(by.values()) == ["converted", "converted", "failed"]


def test_unwritable_destination_fails_each_file(tmp_path, monkeypatch):
    lib, out = tmp_path / "lib", tmp_path / "out"
    for n in "abc":
        make_audio(lib / f"{n}.flac", codec_args=FLAC, seconds=0.5)

    def boom(*a, **k):
        raise OSError("No space left on device")

    monkeypatch.setattr(ffmpeg.os, "replace", boom)
    ok, results = _run(_jobs(lib, out))
    assert len(results) == 3
    assert all(s == "failed" and "No space left on device" in m for _, s, m in results)
    assert not list(out.rglob("*.part"))


def test_stop_keeps_finished_and_leaves_no_part(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    for i in range(6):
        make_audio(lib / f"t{i}.flac", codec_args=FLAC, seconds=20)
    results = []
    holder = {}

    def on_result(job, status, msg):
        results.append((job, status, msg))
        holder["r"].stop()

    holder["r"] = Runner(_jobs(lib, out), "mp3", workers=1, on_result=on_result)
    assert holder["r"].run() is False
    assert len(results) == 1
    assert len([p for p in out.rglob("*") if p.is_file()]) == 1
    assert not list(out.rglob("*.part"))
