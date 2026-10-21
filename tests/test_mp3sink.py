import io

import numpy as np

from audiorec.mp3sink import Mp3StreamSink


def test_mp3sink_produces_decodable_frames():
    rate = 48000
    t = np.arange(rate) / rate
    mono = (np.sin(2 * np.pi * 440 * t) * 20000).astype(np.int16)
    pcm = np.repeat(mono, 2).tobytes()                 # stereo interleaved
    buf = io.BytesIO()
    sink = Mp3StreamSink(buf, rate)
    sink.write(pcm)
    sink.finish()
    out = buf.getvalue()
    assert out[:2] == b"\xff\xfb" or out[:3] == b"ID3"
    assert 320_000 / 8 * 0.85 <= len(out) <= 320_000 / 8 * 1.15
