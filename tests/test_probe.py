from pathlib import Path

import pytest

from audiorec.convert.formats import is_audio
from audiorec.convert.probe import probe
from tests.audio_files import make_audio


@pytest.mark.parametrize("name,args,expect", [
    ("a.mp3",  ["-c:a", "libmp3lame", "-b:a", "192k"], dict(codec="mp3", container="mp3", profile="mpeg1", bitrate_kbps=192)),
    ("a.m4a",  ["-c:a", "aac", "-b:a", "256k"],        dict(codec="aac", container="mp4", profile="lc")),
    ("b.m4a",  ["-c:a", "alac"],                        dict(codec="alac", container="mp4")),
    ("a.flac", ["-c:a", "flac"],                        dict(codec="flac")),
    ("a.wav",  ["-c:a", "pcm_s24le"],                   dict(codec="pcm", container="wav", bits=24)),
    ("f.wav",  ["-c:a", "pcm_f32le"],                   dict(codec="float", container="wav")),
    ("a.aiff", ["-c:a", "pcm_s16be"],                   dict(codec="pcm", container="aiff", bits=16)),
    ("a.ogg",  ["-c:a", "libvorbis"],                   dict(codec="other")),
])
def test_probe_reads_codec_and_container(tmp_path, name, args, expect):
    info = probe(make_audio(tmp_path / name, codec_args=args, rate=48000))
    assert info.rate == 48000 and info.channels == 2 and info.duration_s == pytest.approx(1.0, abs=0.1)
    for k, v in expect.items():
        assert getattr(info, k) == v


def test_probe_garbage_is_other(tmp_path):
    p = tmp_path / "x.flac"; p.write_bytes(b"not audio")
    assert probe(p).codec == "other"


def test_is_audio_ignores_images_and_case():
    assert is_audio(Path("A.FLAC")) and not is_audio(Path("cover.jpg")) and not is_audio(Path("list.m3u"))
