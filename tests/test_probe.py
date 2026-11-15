from pathlib import Path

import numpy as np
import pytest
import soundfile

from audiorec.convert.formats import is_audio
from audiorec.convert.probe import probe
from tests.audio_files import make_audio, make_music_video


@pytest.mark.parametrize("name,args,expect", [
    ("a.mp3",  ["-c:a", "libmp3lame", "-b:a", "192k"], dict(codec="mp3", container="mp3", profile="mpeg1", bitrate_kbps=192)),
    ("a.m4a",  ["-c:a", "aac", "-b:a", "256k"],        dict(codec="aac", container="mp4", profile="lc")),
    ("b.m4a",  ["-c:a", "alac"],                        dict(codec="alac", container="mp4")),
    ("a.flac", ["-c:a", "flac"],                        dict(codec="flac")),
    # ffmpeg writes 24-bit PCM as WAVE_FORMAT_EXTENSIBLE: not a plain WAV for the CDJ.
    ("a.wav",  ["-c:a", "pcm_s24le"],                   dict(codec="pcm", container="other", bits=24)),
    ("f.wav",  ["-c:a", "pcm_f32le"],                   dict(codec="float", container="wav")),
    ("s.wav",  ["-c:a", "pcm_s16le"],                   dict(codec="pcm", container="wav", bits=16)),
    ("a.aiff", ["-c:a", "pcm_s16be"],                   dict(codec="pcm", container="aiff", bits=16)),
    # little-endian PCM in AIFF is written as AIFF-C (sowt)
    ("c.aiff", ["-c:a", "pcm_s16le", "-f", "aiff"],     dict(codec="pcm", container="other", bits=16)),
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


def test_probe_low_rate_aac_is_treated_as_he(tmp_path):
    # Implicit-SBR HE-AAC looks like LC at half the rate; mimic that header.
    p = make_audio(tmp_path / "he.m4a", codec_args=["-c:a", "aac", "-b:a", "64k"], rate=22050)
    assert probe(p).profile == "he"


def test_probe_mpeg_layer2_in_mp3_file_is_other(tmp_path):
    p = make_audio(tmp_path / "x.mp3", codec_args=["-c:a", "mp2", "-f", "mp2"])
    assert probe(p).codec == "other"


def test_probe_plain_pcm_24bit_wav_is_wav(tmp_path):
    p = tmp_path / "p.wav"
    soundfile.write(str(p), np.zeros((4800, 2), dtype="int32"), 48000, subtype="PCM_24", format="WAV")
    info = probe(p)
    assert (info.codec, info.container, info.bits) == ("pcm", "wav", 24)


def test_probe_mp4_with_video_track_is_not_plain_mp4(tmp_path):
    info = probe(make_music_video(tmp_path / "clip.mp4"))
    assert info.codec == "aac" and info.container == "other"
