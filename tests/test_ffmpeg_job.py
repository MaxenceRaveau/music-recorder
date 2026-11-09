import struct
import zlib
from pathlib import Path

import mutagen
import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from audiorec.convert.ffmpeg import JobCancelled, JobFailed, process_job
from audiorec.convert.probe import probe
from audiorec.convert.scan import Action, scan
from tests.audio_files import make_audio


def _png() -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d))
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("target,codec,container", [("mp3", "mp3", "mp3"), ("aac", "aac", "mp4"),
                                                    ("wav", "pcm", "wav"), ("aiff", "pcm", "aiff")])
def test_converts_flac_96k_with_tags(tmp_path, target, codec, container):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "x" / "t.flac", codec_args=["-c:a", "flac", "-sample_fmt", "s32"], rate=96000)
    [job] = scan(lib, out, target)
    process_job(job, target)
    got = probe(job.dst)
    assert (got.codec, got.container, got.rate) == (codec, container, 48000)
    if target in ("wav", "aiff"):
        assert got.bits == 24
    if target == "mp3":
        assert got.bitrate_kbps >= 300
    if target == "aac":  # ffmpeg's aac undershoots 320k on a pure sine (~232 measured)
        assert got.bitrate_kbps >= 200
    if target in ("wav", "aiff"):
        f = (WAVE if target == "wav" else AIFF)(job.dst)
        assert f.tags["TIT2"].text == ["T"] and f.tags["TPE1"].text == ["A"]
    else:
        tags = mutagen.File(job.dst, easy=True)
        assert tags["title"] == ["T"] and tags["artist"] == ["A"]
    assert not job.dst.with_name(job.dst.name + ".part").exists()


def _flac_with_cover(path: Path) -> Path:
    make_audio(path, codec_args=["-c:a", "flac"])
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", _png()
    f = FLAC(path)
    f.add_picture(pic)
    f.save()
    return path


def test_cover_is_carried_to_mp3_and_m4a(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    _flac_with_cover(lib / "c.flac")
    [job] = scan(lib, out, "mp3")
    process_job(job, "mp3")
    assert mutagen.File(job.dst).tags.getall("APIC")
    [job] = scan(lib, out, "aac")
    process_job(job, "aac")
    assert MP4(job.dst).tags["covr"]


@pytest.mark.parametrize("target", ["wav", "aiff"])
def test_cover_is_carried_to_wav_and_aiff(tmp_path, target):
    lib, out = tmp_path / "lib", tmp_path / "out"
    _flac_with_cover(lib / "c.flac")
    [job] = scan(lib, out, target)
    process_job(job, target)
    f = (WAVE if target == "wav" else AIFF)(job.dst)
    assert f.tags.getall("APIC")


def test_copy_is_byte_identical(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "a.mp3", codec_args=["-c:a", "libmp3lame", "-b:a", "192k"])
    [job] = scan(lib, out, "mp3")
    assert job.action == Action.COPY
    process_job(job, "mp3")
    assert job.dst.read_bytes() == job.src.read_bytes()
    assert not job.dst.with_name(job.dst.name + ".part").exists()


def test_undecodable_file_raises_jobfailed_and_cleans(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    (lib).mkdir()
    (lib / "bad.flac").write_bytes(b"this is not audio" * 100)
    [job] = scan(lib, out, "mp3")
    with pytest.raises(JobFailed) as e:
        process_job(job, "mp3")
    assert str(e.value)
    assert not job.dst.exists()
    assert not job.dst.with_name(job.dst.name + ".part").exists()


def test_non_ascii_path_converts(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "Été – Démo 🎧.flac", codec_args=["-c:a", "flac"])
    [job] = scan(lib, out, "mp3")
    process_job(job, "mp3")
    assert job.dst.exists()


def test_cancel_kills_ffmpeg_and_cleans(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "long.flac", codec_args=["-c:a", "flac"], seconds=60)
    [job] = scan(lib, out, "mp3")
    state = {"p": None}

    def register(p):
        if p is not None:
            state["p"] = p

    with pytest.raises(JobCancelled):
        process_job(job, "mp3", register=register, cancelled=lambda: state["p"] is not None)
    assert state["p"].poll() is not None
    assert not job.dst.exists()
    assert not job.dst.with_name(job.dst.name + ".part").exists()


def test_rejects_non_actionable_job(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "a.flac", codec_args=["-c:a", "flac"])
    [job] = scan(lib, out, "mp3")
    job.action = Action.DONE
    with pytest.raises(ValueError):
        process_job(job, "mp3")
