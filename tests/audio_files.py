"""Test helper: synthesize small audio files with the bundled ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg


def make_audio(path: Path, *, codec_args: list[str], seconds: float = 1.0,
               rate: int = 44100, channels: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
        "-ac", str(channels), "-ar", str(rate),
        "-metadata", "title=T", "-metadata", "artist=A",
        *codec_args, str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({r.returncode}): {r.stderr}")
    return path


def make_music_video(path: Path, *, seconds: float = 1.0) -> Path:
    """An .mp4 with a real H.264 video track plus AAC-LC audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}:sample_rate=44100",
        "-ac", "2", "-c:v", "libx264", "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-metadata", "title=T", "-metadata", "artist=A", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({r.returncode}): {r.stderr}")
    return path


def wav_fmt_tag(path: Path) -> int:
    """The wFormatTag of a WAV file's fmt chunk (1 = PCM, 0xFFFE = extensible)."""
    data = path.read_bytes()
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        if cid == b"fmt ":
            return int.from_bytes(data[pos + 8:pos + 10], "little")
        pos += 8 + size + (size & 1)
    raise ValueError("no fmt chunk")
