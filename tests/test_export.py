from __future__ import annotations

import base64
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.wave import WAVE

from audiorec.export import (FormatNotAllowed, SelectionTooShort, Tags, export,
                             format_allowed, selection_ok)
from audiorec.naming import EXT, FORMATS
from audiorec.session import Session

RATE = 48000
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture
def session(tmp_path):
    s = Session.create(tmp_path / "sessions", RATE, "dev")
    t = np.arange(2 * RATE) / RATE
    mono = (np.sin(2 * np.pi * 440 * t) * 12000).astype(np.int16)
    s.write(np.column_stack([mono, mono // 2]).tobytes())
    s.close()
    return s


@pytest.fixture
def png(tmp_path):
    p = tmp_path / "cover.png"
    p.write_bytes(PNG)
    return p


def duration_of(path: Path) -> float:
    if path.suffix == ".mp3":
        return MP3(path).info.length
    if path.suffix == ".wav":
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    info = sf.info(str(path))
    return info.frames / info.samplerate


def read_tags(path: Path) -> dict:
    if path.suffix == ".flac":
        f = FLAC(path)
        one = lambda k: f[k][0] if k in f else ""
        return {"title": one("title"), "artist": one("artist"), "album": one("album"),
                "year": one("date"), "genre": one("genre"), "track": one("tracknumber"),
                "has_cover": any(p.type == 3 for p in f.pictures)}
    f = MP3(path) if path.suffix == ".mp3" else WAVE(path)
    t = f.tags
    one = lambda k: str(t[k].text[0]) if k in t else ""
    return {"title": one("TIT2"), "artist": one("TPE1"), "album": one("TALB"),
            "year": one("TDRC"), "genre": one("TCON"), "track": one("TRCK"),
            "has_cover": any(p.type == 3 for p in t.getall("APIC"))}


@pytest.mark.parametrize("fmt", FORMATS)
def test_export_trims_to_selection(session, tmp_path, fmt):
    out = export(session, 24000, 72000, fmt, tmp_path / f"x{EXT[fmt]}", Tags())
    assert duration_of(out) == pytest.approx(1.0, abs=0.06)


@pytest.mark.parametrize("fmt", ["wav", "flac"])
def test_export_lossless_is_bit_exact(session, tmp_path, fmt):
    out = export(session, 24000, 72000, fmt, tmp_path / f"x{EXT[fmt]}", Tags())
    data, rate = sf.read(str(out), dtype="int16", always_2d=True)
    assert rate == RATE
    assert np.array_equal(data, session.read_frames(24000, 72000))


@pytest.mark.parametrize("fmt", FORMATS)
def test_tags_unicode_roundtrip(session, tmp_path, fmt, png):
    tags = Tags(title="Été 日本", artist="Motörhead", album="A", year="1999",
                genre="Rock", track="3/12", cover=png)
    out = export(session, 0, 48000, fmt, tmp_path / f"t{EXT[fmt]}", tags)
    assert read_tags(out) == {"title": "Été 日本", "artist": "Motörhead", "album": "A",
                              "year": "1999", "genre": "Rock", "track": "3/12",
                              "has_cover": True}


@pytest.mark.parametrize("fmt", FORMATS)
def test_empty_tags_are_omitted(session, tmp_path, fmt):
    out = export(session, 0, 48000, fmt, tmp_path / f"e{EXT[fmt]}", Tags(title="Only"))
    got = read_tags(out)
    assert got["title"] == "Only"
    assert not got["artist"] and not got["has_cover"]


def test_bad_cover_suffix_raises_and_leaves_nothing(session, tmp_path):
    bad = tmp_path / "c.gif"
    bad.write_bytes(b"GIF89a")
    with pytest.raises(ValueError):
        export(session, 0, 48000, "wav", tmp_path / "b.wav", Tags(cover=bad))
    assert not (tmp_path / "b.wav").exists()
    assert not (tmp_path / "b.wav.part").exists()


def test_export_rejects_short_selection(session, tmp_path):
    assert not selection_ok(0, 23999, RATE) and selection_ok(0, 24000, RATE)
    with pytest.raises(SelectionTooShort):
        export(session, 0, 23999, "wav", tmp_path / "s.wav", Tags())


def test_export_rejects_mp3_at_96k(tmp_path):
    s = Session.create(tmp_path / "s96", 96000, "dev")
    s.write(np.zeros((96000, 2), dtype=np.int16).tobytes())
    s.close()
    assert not format_allowed("mp3", 96000) and format_allowed("flac", 96000)
    with pytest.raises(FormatNotAllowed):
        export(s, 0, 96000, "mp3", tmp_path / "x.mp3", Tags())


def test_export_reads_in_chunks(session, tmp_path, monkeypatch):
    sizes = []
    orig = session.iter_frames

    def spy(start, end, chunk_frames=1 << 18):
        for c in orig(start, end, chunk_frames):
            sizes.append(len(c))
            yield c

    monkeypatch.setattr(session, "iter_frames", spy)
    export(session, 0, 48000, "wav", tmp_path / "c.wav", Tags(), chunk_frames=4800)
    assert len(sizes) >= 10 and max(sizes) <= 4800


def test_export_progress_ends_at_one(session, tmp_path):
    seen = []
    export(session, 0, 48000, "flac", tmp_path / "p.flac", Tags(),
           chunk_frames=4800, progress=seen.append)
    assert seen and all(0 <= v <= 1 for v in seen) and seen[-1] == 1.0


def test_export_failure_leaves_no_file(session, tmp_path):
    dest = tmp_path / "dir.wav"
    dest.mkdir()
    (dest / "keep.txt").write_text("x")
    with pytest.raises(OSError):
        export(session, 0, 48000, "wav", dest, Tags())
    assert dest.is_dir() and [p.name for p in dest.iterdir()] == ["keep.txt"]
    assert not (tmp_path / "dir.wav.part").exists()


def test_export_overwrites_existing(session, tmp_path):
    dest = tmp_path / "o.wav"
    dest.write_bytes(b"old")
    export(session, 0, 48000, "wav", dest, Tags())
    assert duration_of(dest) == pytest.approx(1.0, abs=0.01)
