"""
Cut a selection out of a session's lossless PCM, encode it once, tag it.
Runs in a worker thread; writes to `<dest>.part` and renames on success so a
failure never leaves a half-written file under the final name.
"""
from __future__ import annotations

import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import soundfile as sf
from mutagen import id3
from mutagen.flac import FLAC, Picture
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from audiorec.mp3sink import MP3_RATES, build_encoder
from audiorec.naming import FORMATS
from audiorec.session import Session

MIN_SELECTION_S = 0.5
_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


class SelectionTooShort(ValueError):
    pass


class FormatNotAllowed(ValueError):
    pass


@dataclass
class Tags:
    title: str = ""
    artist: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    track: str = ""
    cover: Path | None = None


def format_allowed(fmt: str, rate: int) -> bool:
    return fmt in FORMATS and (fmt != "mp3" or rate in MP3_RATES)


def selection_ok(start: int, end: int, rate: int) -> bool:
    return end - start >= MIN_SELECTION_S * rate


def export(session: Session, start: int, end: int, fmt: str, dest: Path, tags: Tags,
           chunk_frames: int = 1 << 18,
           progress: Callable[[float], None] | None = None) -> Path:
    if not selection_ok(start, end, session.rate):
        raise SelectionTooShort(f"selection shorter than {MIN_SELECTION_S}s")
    if not format_allowed(fmt, session.rate):
        raise FormatNotAllowed(f"{fmt} is not available at {session.rate} Hz")
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    try:
        cover = _read_cover(tags.cover)
        total = max(1, min(end, session.frames) - max(0, start))
        _encode(session, start, end, fmt, part, chunk_frames, total, progress)
        _write_tags(part, fmt, tags, cover)
        os.replace(part, dest)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return dest


def _encode(session: Session, start: int, end: int, fmt: str, part: Path,
            chunk_frames: int, total: int,
            progress: Callable[[float], None] | None) -> None:
    done = 0
    chunks = session.iter_frames(start, end, chunk_frames)

    def pump(write: Callable[[object], None]) -> None:
        nonlocal done
        for chunk in chunks:
            write(chunk)
            done += len(chunk)
            if progress:
                progress(min(1.0, done / total))

    if fmt == "wav":
        with wave.open(str(part), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(session.rate)
            pump(lambda c: w.writeframes(c.tobytes()))
    elif fmt == "flac":
        with sf.SoundFile(str(part), mode="w", samplerate=session.rate, channels=2,
                          format="FLAC", subtype="PCM_16") as f:
            pump(f.write)
    else:
        enc = build_encoder(session.rate)
        with open(part, "wb") as f:
            pump(lambda c: f.write(enc.encode(c.tobytes())))
            f.write(enc.flush())


def _read_cover(cover: Path | None) -> tuple[bytes, str] | None:
    if cover is None:
        return None
    mime = _MIME.get(Path(cover).suffix.lower())
    if mime is None:
        raise ValueError(f"unsupported cover type: {Path(cover).suffix!r}")
    return Path(cover).read_bytes(), mime


def _write_tags(part: Path, fmt: str, tags: Tags, cover: tuple[bytes, str] | None) -> None:
    # Explicit mutagen classes: the .part extension defeats auto-detection.
    if fmt == "flac":
        f = FLAC(part)
        for key, val in (("TITLE", tags.title), ("ARTIST", tags.artist),
                         ("ALBUM", tags.album), ("DATE", tags.year),
                         ("GENRE", tags.genre), ("TRACKNUMBER", tags.track)):
            if val:
                f[key] = val
        if cover:
            pic = Picture()
            pic.type = 3
            pic.data, pic.mime = cover
            f.add_picture(pic)
        f.save()
        return
    f = MP3(part) if fmt == "mp3" else WAVE(part)
    if f.tags is None:
        f.add_tags()
    t = f.tags
    for frame, val in ((id3.TIT2, tags.title), (id3.TPE1, tags.artist),
                       (id3.TALB, tags.album), (id3.TDRC, tags.year),
                       (id3.TCON, tags.genre), (id3.TRCK, tags.track)):
        if val:
            t.add(frame(encoding=id3.Encoding.UTF8, text=[val]))
    if cover:
        t.add(id3.APIC(encoding=id3.Encoding.UTF8, mime=cover[1], type=3, desc="",
                       data=cover[0]))
    f.save(v2_version=4)
