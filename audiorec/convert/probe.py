"""
Read the technical facts about an audio file (codec, rate, bit depth...) that
the conversion rules need. Never raises: an unreadable or unsupported file
comes back as codec "other" so the caller just converts it.

Container "wav"/"aiff"/"mp4" means the plain variant a CDJ-2000 reads; the
look-alikes (WAVE_FORMAT_EXTENSIBLE, RF64, AIFF-C, MP4 with a video track)
report "other" so they are converted rather than copied.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mutagen
import soundfile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4, Atoms

_MP3_PROFILE = {1: "mpeg1", 2: "mpeg2", 2.5: "mpeg25"}
_AAC_PROFILE = {"mp4a.40.2": "lc", "mp4a.40.5": "he", "mp4a.40.29": "he"}
_PCM_BITS = {"PCM_U8": 8, "PCM_S8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}


@dataclass(frozen=True)
class AudioInfo:
    codec: str  # mp3 aac alac flac pcm float other
    container: str  # mp3 mp4 wav aiff other
    profile: str  # AAC: lc/he/""; MP3: mpeg1/mpeg2/mpeg25
    rate: int
    bits: int  # 0 if not applicable
    channels: int
    bitrate_kbps: int
    duration_s: float


_OTHER = AudioInfo("other", "other", "", 0, 0, 0, 0, 0.0)


def _has_video_track(path: Path) -> bool:
    # Cover art lives in moov/udta/meta/ilst/covr, never in a trak.
    with open(path, "rb") as fh:
        atoms = Atoms(fh)
        for trak in atoms[b"moov"].findall(b"trak"):
            try:
                hdlr = trak[b"mdia", b"hdlr"]
            except KeyError:
                continue
            ok, data = hdlr.read(fh)
            if ok and data[8:12] == b"vide":
                return True
    return False


def _pcm(path: Path) -> AudioInfo:
    info = soundfile.info(str(path))
    fmt = info.format
    if fmt == "WAV":  # WAVEX and RF64 are reported separately by libsndfile
        container = "wav"
    elif fmt == "AIFF":
        with open(path, "rb") as fh:
            container = "aiff" if fh.read(12)[8:12] == b"AIFF" else "other"  # not AIFC
    else:
        container = "other"
    if info.subtype in _PCM_BITS:
        codec, bits = "pcm", _PCM_BITS[info.subtype]
    elif info.subtype in ("FLOAT", "DOUBLE"):
        codec, bits = "float", 32 if info.subtype == "FLOAT" else 64
    else:
        codec, bits = "other", 0
    kbps = round(info.samplerate * info.channels * bits / 1000)
    return AudioInfo(codec, container, "", info.samplerate, bits, info.channels,
                     kbps, info.duration)


def probe(path: Path) -> AudioInfo:
    try:
        f = mutagen.File(path)
        if f is None:
            return _OTHER
        i = f.info
        common = dict(rate=int(i.sample_rate), channels=int(i.channels),
                      duration_s=float(i.length))
        if isinstance(f, MP3):
            if i.layer != 3:  # mutagen also accepts MPEG Layer I/II streams
                return _OTHER
            return AudioInfo("mp3", "mp3", _MP3_PROFILE.get(i.version, ""), bits=0,
                             bitrate_kbps=int(i.bitrate // 1000), **common)
        if isinstance(f, MP4):
            kbps = int(i.bitrate // 1000)
            if _has_video_track(path):
                return AudioInfo("aac" if i.codec in _AAC_PROFILE else "other", "other",
                                 _AAC_PROFILE.get(i.codec, ""), bits=0,
                                 bitrate_kbps=kbps, **common)
            if i.codec in _AAC_PROFILE:
                profile = _AAC_PROFILE[i.codec]
                # Implicit-SBR HE-AAC is signalled as plain LC with half the
                # real sample rate; no real LC file is this low in practice.
                if profile == "lc" and common["rate"] <= 24000:
                    profile = "he"
                return AudioInfo("aac", "mp4", profile, bits=0,
                                 bitrate_kbps=kbps, **common)
            if i.codec == "alac":
                return AudioInfo("alac", "mp4", "", bits=int(i.bits_per_sample),
                                 bitrate_kbps=kbps, **common)
            return AudioInfo("other", "mp4", "", bits=0, bitrate_kbps=kbps, **common)
        if isinstance(f, FLAC):
            return AudioInfo("flac", "other", "", bits=int(i.bits_per_sample),
                             bitrate_kbps=int(i.bitrate // 1000), **common)
        if type(f).__name__ in ("WAVE", "AIFF"):
            return _pcm(path)
        return AudioInfo("other", "other", "", bits=0,
                         bitrate_kbps=int((getattr(i, "bitrate", 0) or 0) // 1000), **common)
    except Exception:
        return _OTHER
