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
