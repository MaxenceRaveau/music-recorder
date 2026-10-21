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


def test_mp3_rate_message_matches_cli_guidance():
    from audiorec.mp3sink import mp3_rate_message
    assert mp3_rate_message("Speakers", 96000) == (
        "'Speakers' runs at 96000 Hz, MP3 only supports 32/44.1/48 kHz.\n"
        "Set it to 48000 Hz: Sound settings > device > Properties > Advanced."
    )
