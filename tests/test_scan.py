from pathlib import Path

from audiorec.convert.scan import (
    Action, folder_groups, output_path, pending_counts, scan, validate_paths,
)
from tests.audio_files import make_audio

FLAC = ["-c:a", "flac"]
MP3 = ["-c:a", "libmp3lame", "-b:a", "192k"]


def test_mirrors_tree_and_ignores_non_audio(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "House" / "A" / "t1.flac", codec_args=FLAC)
    make_audio(lib / "House" / "t2.mp3", codec_args=MP3)
    (lib / "House" / "cover.jpg").write_bytes(b"x")
    jobs = scan(lib, dst, "mp3")
    assert {(j.dst.relative_to(dst).as_posix(), j.action) for j in jobs} == {
        ("House/A/t1.mp3", Action.CONVERT), ("House/t2.mp3", Action.COPY)}
    assert folder_groups(jobs) == ["House", "House/A"]
    assert pending_counts(jobs) == {"": 2, "House": 2, "House/A": 1}
    assert not dst.exists()


def test_existing_output_is_done(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "t.flac", codec_args=FLAC)
    make_audio(lib / "u.flac", codec_args=FLAC)
    dst.mkdir()
    (dst / "t.mp3").write_bytes(b"x")
    jobs = {j.src.name: j for j in scan(lib, dst, "mp3")}
    assert jobs["t.flac"].action == Action.DONE
    assert jobs["u.flac"].action == Action.CONVERT
    assert pending_counts(list(jobs.values())) == {"": 1}


def test_case_only_collision_prefers_copy(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "Song.FLAC", codec_args=FLAC)
    make_audio(lib / "song.mp3", codec_args=["-c:a", "libmp3lame", "-b:a", "192k"])
    jobs = {j.src.name: j for j in scan(lib, dst, "mp3")}
    assert jobs["song.mp3"].action == Action.COPY
    assert jobs["Song.FLAC"].action == Action.COLLISION
    assert jobs["Song.FLAC"].note == "same output name as song.mp3"


def test_non_ascii_paths(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "Café" / "Été – Démo 🎧.flac", codec_args=FLAC)
    (job,) = scan(lib, dst, "mp3")
    assert job.dst == dst / "Café" / "Été – Démo 🎧.mp3"
    assert job.action == Action.CONVERT
    assert job.group == "Café"


def test_cancel_returns_none(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    for n in "abc":
        make_audio(lib / f"{n}.flac", codec_args=FLAC)
    calls = []
    assert scan(lib, dst, "mp3", progress=lambda d, t: calls.append((d, t)),
                cancelled=lambda: bool(calls)) is None
    assert calls == [(1, 3)]


def test_output_path(tmp_path):
    lib, dst = tmp_path / "lib", tmp_path / "out"
    assert output_path(lib, dst, lib / "a" / "x.flac", "aac") == dst / "a" / "x.m4a"


def test_validate_paths(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    assert validate_paths(tmp_path / "nope", tmp_path / "o") == "Library folder not found."
    assert validate_paths(lib, lib) == "Destination can't be the library folder."
    assert validate_paths(lib, lib / "sub") == "Destination can't be inside the library folder."
    assert validate_paths(lib, Path("")) == "Choose a destination folder."
    assert validate_paths(lib, tmp_path / "out") is None
