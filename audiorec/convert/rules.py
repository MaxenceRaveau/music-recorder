"""
Decide, for one source file and one target format, whether the file can be
copied as-is onto the CDJ stick or must be converted, and which sample rate,
bit depth and channel count the converted output uses. The CDJ-2000 only
plays 44.1/48 kHz, 16/24-bit PCM, MPEG-1 Layer III and AAC-LC, so anything
outside those limits is converted even when the container already matches.
"""
from __future__ import annotations

from dataclasses import dataclass

from audiorec.convert.probe import AudioInfo

_CDJ_RATES = (44100, 48000)
_LOSSY_BITRATE_KBPS = 320

# target -> (codec, container, extra check on the source)
_COPY_RULES = {
    "mp3": ("mp3", "mp3", lambda i: i.profile == "mpeg1"),
    "aac": ("aac", "mp4", lambda i: i.profile == "lc" and i.bitrate_kbps <= _LOSSY_BITRATE_KBPS),
    "wav": ("pcm", "wav", lambda i: i.bits in (16, 24)),
    "aiff": ("pcm", "aiff", lambda i: i.bits in (16, 24)),
}


@dataclass(frozen=True)
class EncodeParams:
    rate: int
    channels: int
    bits: int  # 0 for the lossy targets


def out_rate(rate: int) -> int:
    if rate in _CDJ_RATES:
        return rate
    if rate in (88200, 176400):
        return 44100
    if rate in (96000, 192000):
        return 48000
    return 44100


def can_copy(info: AudioInfo, target: str) -> bool:
    if target not in _COPY_RULES:
        return False
    codec, container, extra = _COPY_RULES[target]
    return (info.rate in _CDJ_RATES and info.codec == codec
            and info.container == container and extra(info))


def encode_params(info: AudioInfo, target: str) -> EncodeParams:
    rate = out_rate(info.rate)
    channels = 1 if info.channels == 1 else 2
    if target in ("mp3", "aac"):
        return EncodeParams(rate, channels, 0)
    bits = 16 if info.bits <= 16 else 24
    return EncodeParams(rate, channels, bits)


def estimate_bytes(info: AudioInfo, target: str, src_size: int, copy: bool) -> int:
    if copy:
        return src_size
    if target in ("mp3", "aac"):
        return int(info.duration_s * _LOSSY_BITRATE_KBPS * 1000 / 8)
    p = encode_params(info, target)
    return int(info.duration_s * p.rate * p.channels * p.bits / 8)
