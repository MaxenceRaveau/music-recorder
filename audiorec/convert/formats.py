"""
Target output formats for the converted library, and which file extensions
count as audio (everything else in the library is ignored, never copied).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    ext: str


# Dict order is the order shown in the UI; "mp3" is the default.
TARGETS: dict[str, Target] = {
    "mp3": Target("mp3", "MP3 320 kbps", ".mp3"),
    "aac": Target("aac", "AAC/M4A 320 kbps", ".m4a"),
    "wav": Target("wav", "WAV", ".wav"),
    "aiff": Target("aiff", "AIFF", ".aiff"),
}

AUDIO_EXTS = frozenset({
    ".mp3", ".flac", ".wav", ".wave", ".aif", ".aiff", ".aifc", ".m4a", ".mp4",
    ".m4b", ".aac", ".alac", ".ogg", ".oga", ".opus", ".wma", ".ape", ".wv",
    ".mpc", ".dsf", ".dff", ".ac3", ".mka", ".webm", ".caf",
})


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTS
