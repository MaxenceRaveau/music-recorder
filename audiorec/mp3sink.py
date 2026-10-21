"""Streaming MP3 encoder sink: 320 kbps CBR, flat memory however long it runs."""
from __future__ import annotations

from typing import BinaryIO

import lameenc

BITRATE_KBPS = 320
MP3_RATES = {32000, 44100, 48000}       # rates MPEG-1 Layer III accepts


def build_encoder(rate: int) -> lameenc.Encoder:
    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE_KBPS)
    enc.set_in_sample_rate(rate)
    enc.set_channels(2)
    enc.set_quality(2)                  # 2 = high quality, 7 = fastest
    return enc


class Mp3StreamSink:
    def __init__(self, f: BinaryIO, rate: int) -> None:
        self._f = f
        self._enc = build_encoder(rate)

    def write(self, pcm: bytes) -> None:
        self._f.write(self._enc.encode(pcm))

    def finish(self) -> None:
        self._f.write(self._enc.flush())
