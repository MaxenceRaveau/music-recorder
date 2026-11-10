import struct
import zlib
from pathlib import Path

import mutagen
import pytest
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

import subprocess

import imageio_ffmpeg
from mutagen import id3

from audiorec.convert.ffmpeg import JobCancelled, JobFailed, build_command, process_job
from audiorec.convert.rules import EncodeParams
from audiorec.convert.probe import probe
from audiorec.convert.scan import Action, scan
from tests.audio_files import make_audio, make_music_video, wav_fmt_tag


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
    assert state["p"].poll() not in (None, 0)
    assert not job.dst.exists()
    assert not job.dst.with_name(job.dst.name + ".part").exists()


def test_rejects_non_actionable_job(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "a.flac", codec_args=["-c:a", "flac"])
    [job] = scan(lib, out, "mp3")
    job.action = Action.DONE
    with pytest.raises(ValueError):
        process_job(job, "mp3")


def _streams(path: Path) -> list[str]:
    err = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    return [ln.strip() for ln in err.splitlines() if "Stream #" in ln]


def test_24bit_wav_output_is_plain_pcm(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "t.flac", codec_args=["-c:a", "flac", "-sample_fmt", "s32"], rate=96000)
    [job] = scan(lib, out, "wav")
    process_job(job, "wav")
    assert wav_fmt_tag(job.dst) == 1
    got = probe(job.dst)
    assert (got.container, got.bits, got.rate) == ("wav", 24, 48000)
    assert WAVE(job.dst).tags["TIT2"].text == ["T"]
    assert not list(out.rglob("*.part"))


def test_wavex_24bit_source_is_converted_to_plain_wav(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "x.wav", codec_args=["-c:a", "pcm_s24le"], rate=48000)
    assert wav_fmt_tag(lib / "x.wav") == 0xFFFE
    [job] = scan(lib, out, "wav")
    assert job.action == Action.CONVERT
    process_job(job, "wav")
    assert wav_fmt_tag(job.dst) == 1


def test_aifc_source_is_converted_for_aiff_target(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "c.aif", codec_args=["-c:a", "pcm_s16le", "-f", "aiff"])
    assert (lib / "c.aif").read_bytes()[8:12] == b"AIFC"
    [job] = scan(lib, out, "aiff")
    assert job.action == Action.CONVERT
    process_job(job, "aiff")
    assert job.dst.read_bytes()[8:12] == b"AIFF"


def test_build_command_maps_only_attached_pictures(tmp_path):
    for target in ("mp3", "aac"):
        cmd = build_command("ffmpeg", tmp_path / "a.mp4", tmp_path / "a.part", target,
                            EncodeParams(44100, 2, 0))
        assert "0:v:disp:attached_pic?" in cmd and "0:v:0?" not in cmd


def test_music_video_to_mp3_drops_video(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_music_video(lib / "clip.mp4")
    [job] = scan(lib, out, "mp3")
    process_job(job, "mp3")
    assert probe(job.dst).codec == "mp3"
    assert not any("Video:" in s for s in _streams(job.dst))


def test_music_video_to_aac_is_converted_without_video(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_music_video(lib / "clip.mp4")
    [job] = scan(lib, out, "aac")
    assert job.action == Action.CONVERT
    process_job(job, "aac")
    streams = _streams(job.dst)
    assert any("Audio: aac" in s for s in streams)
    assert not any("Video:" in s for s in streams)


def test_wav_source_id3_tags_reach_wav_output(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    src = make_audio(lib / "hires.wav", codec_args=["-c:a", "pcm_s24le"], rate=96000)
    w = WAVE(src)
    w.add_tags()
    w.tags.add(id3.TIT2(encoding=3, text=["T"]))
    w.tags.add(id3.TPE1(encoding=3, text=["A"]))
    w.save()
    [job] = scan(lib, out, "wav")
    process_job(job, "wav")
    tags = WAVE(job.dst).tags
    assert tags["TIT2"].text == ["T"] and tags["TPE1"].text == ["A"]


def test_wma_source_tags_reach_wav_output(tmp_path):
    lib, out = tmp_path / "lib", tmp_path / "out"
    make_audio(lib / "w.wma", codec_args=["-c:a", "wmav2", "-b:a", "128k"])
    [job] = scan(lib, out, "wav")
    process_job(job, "wav")
    tags = WAVE(job.dst).tags
    assert tags["TIT2"].text == ["T"] and tags["TPE1"].text == ["A"]
