"""
Output file naming: Windows-safe sanitizing, the "Artist - Title" default
name, and extension swapping for format changes.
"""
from __future__ import annotations

import re
from datetime import datetime

FORMATS = ("mp3", "flac", "wav")
EXT = {"mp3": ".mp3", "flac": ".flac", "wav": ".wav"}

MAX_STEM = 180
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}


def sanitize(name: str) -> str:
    """Make a stem safe for a Windows filename.

    The cap comes before stripping trailing dots/spaces, so the result can
    never end in one of them after truncation.
    """
    stem = _ILLEGAL.sub("_", name)[:MAX_STEM].rstrip(". ")
    # Windows reserves CON.txt as well as CON, so compare the part before the first dot.
    if stem.split(".", 1)[0].upper() in _RESERVED:
        stem = "_" + stem
    return stem


def build_filename(artist: str, title: str, fmt: str, started: datetime) -> str:
    artist = artist.strip()
    title = title.strip()
    if artist and title:
        raw = f"{artist} - {title}"
    else:
        raw = artist or title
    stem = sanitize(raw) if raw else ""
    if not stem:
        stem = started.strftime("Recording %Y%m%d_%H%M%S")
    return stem + EXT[fmt]


def with_extension(name: str, fmt: str) -> str:
    """Swap a known audio extension for the one of ``fmt``, or append it."""
    lowered = name.lower()
    for ext in EXT.values():
        if lowered.endswith(ext):
            return name[: -len(ext)] + EXT[fmt]
    return name + EXT[fmt]
