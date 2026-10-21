"""Player tests: the PortAudio callback body is driven directly, no device."""
from __future__ import annotations

import numpy as np
import pytest
import sounddevice

from audiorec.playback import Player


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.started = self.stopped = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


def make_player():
    streams: list[FakeStream] = []

    def factory(**kw):
        s = FakeStream(**kw)
        streams.append(s)
        return s

    return Player(48000, stream_factory=factory), streams


def ramp(n):
    a = np.arange(n, dtype=np.int16)
    return np.stack([a, -a], axis=1)


def test_fill_copies_and_advances():
    p, _ = make_player()
    p.load(ramp(100), offset=10)
    p.play()
    out = np.zeros((40, 2), dtype=np.float32)
    assert p._fill(out, 40) is True
    assert p.position == 50
    assert out[5, 0] == pytest.approx(5 / 32768)
    assert out[5, 1] == pytest.approx(-5 / 32768)


def test_fill_pads_zero_and_finishes_at_end():
    p, _ = make_player()
    done = []
    p.on_finished = lambda: done.append((p.position, p.playing))
    p.load(ramp(30), offset=100)
    p.play()
    out = np.ones((50, 2), dtype=np.float32)
    assert p._fill(out, 50) is False
    assert out[29, 0] == pytest.approx(29 / 32768)
    assert not out[30:].any()
    assert done == [(100, False)]
    assert p.position == 100 and not p.playing


def test_play_from_frame_seeks_absolute():
    p, _ = make_player()
    p.load(ramp(1000), offset=1000)
    p.play(1500)
    out = np.zeros((10, 2), dtype=np.float32)
    p._fill(out, 10)
    assert out[0, 0] == pytest.approx(500 / 32768)
    assert p.position == 1510


def test_play_outside_selection_clamps():
    p, _ = make_player()
    p.load(ramp(100), offset=1000)
    p.play(5)
    assert p.position == 1000
    p.play(99999)
    assert p.position == 1099


def test_pause_keeps_position():
    p, streams = make_player()
    p.load(ramp(100), offset=0)
    p.play()
    p._fill(np.zeros((25, 2), dtype=np.float32), 25)
    p.pause()
    assert not p.playing and p.position == 25
    assert streams[0].stopped and streams[0].closed
    p.play()
    assert len(streams) == 2 and p.playing and p.position == 25


def test_stream_factory_args_and_callback_stop():
    p, streams = make_player()
    p.load(ramp(10), offset=0)
    p.play()
    kw = streams[0].kw
    assert (kw["samplerate"], kw["channels"], kw["dtype"]) == (48000, 2, "float32")
    out = np.zeros((20, 2), dtype=np.float32)
    with pytest.raises(sounddevice.CallbackStop):
        kw["callback"](out, 20, None, None)


def test_load_while_playing_stops_first():
    p, streams = make_player()
    p.load(ramp(10), offset=0)
    p.play()
    p.load(ramp(10), offset=50)
    assert not p.playing and streams[0].closed and p.position == 50
