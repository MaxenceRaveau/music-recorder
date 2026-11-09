"""
Read the technical facts about an audio file (codec, rate, bit depth...) that
the conversion rules need. Never raises: an unreadable or unsupported file
comes back as codec "other" so the caller just converts it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import mutagen
import soundfile
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4

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


def _pcm(path: Path) -> AudioInfo:
    info = soundfile.info(str(path))
    fmt = info.format
    container = "wav" if fmt in ("WAV", "WAVEX", "RF64") else "aiff" if fmt == "AIFF" else "other"
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
            return AudioInfo("mp3", "mp3", _MP3_PROFILE.get(i.version, ""), bits=0,
                             bitrate_kbps=int(i.bitrate // 1000), **common)
        if isinstance(f, MP4):
            kbps = int(i.bitrate // 1000)
            if i.codec in _AAC_PROFILE:
                return AudioInfo("aac", "mp4", _AAC_PROFILE[i.codec], bits=0,
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
