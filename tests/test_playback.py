"""Player tests: the PortAudio callback body is driven directly, no device."""
from __future__ import annotations

import threading

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
        self.stop_thread = threading.get_ident()

    def close(self):
        self.closed = True
        self.close_thread = threading.get_ident()


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


def _run_cb_on_thread(stream, frames):
    out = np.zeros((frames, 2), dtype=np.float32)
    box = {}

    def target():
        box["tid"] = threading.get_ident()
        try:
            stream.kw["callback"](out, frames, None, None)
        except sounddevice.CallbackStop:
            box["stopped"] = True

    t = threading.Thread(target=target)
    t.start()
    t.join()
    return box


def test_on_finished_calling_stop_does_not_close_on_callback_thread():
    p, streams = make_player()
    p.load(ramp(10), offset=0)
    p.on_finished = p.stop
    p.play()
    box = _run_cb_on_thread(streams[0], 20)
    assert box["stopped"]
    assert not streams[0].stopped and not streams[0].closed
    assert not p.playing and p.position == 0
    p.pause()  # next call from another thread retires it
    assert streams[0].stopped and streams[0].closed
    assert streams[0].close_thread != box["tid"]


def test_on_finished_calling_play_restarts_safely():
    p, streams = make_player()
    p.load(ramp(100), offset=1000)
    p.on_finished = lambda: p.play(1050)
    p.play()
    p._pos = 95  # near the end
    box = _run_cb_on_thread(streams[0], 20)
    assert not streams[0].stopped and not streams[0].closed
    assert len(streams) == 2 and streams[1].started
    assert p.playing and p.position == 1050
    p.pause()
    assert streams[0].closed and streams[1].closed
    assert streams[0].close_thread != box["tid"]


def test_play_factory_failure_leaves_not_playing():
    def boom(**kw):
        raise RuntimeError("no device")

    p = Player(48000, stream_factory=boom)
    p.load(ramp(10), offset=0)
    with pytest.raises(RuntimeError):
        p.play()
    assert not p.playing


def test_play_start_failure_closes_stream_and_not_playing():
    made = []

    class BadStart(FakeStream):
        def start(self):
            raise RuntimeError("start failed")

    def factory(**kw):
        made.append(BadStart(**kw))
        return made[-1]

    p = Player(48000, stream_factory=factory)
    p.load(ramp(10), offset=0)
    with pytest.raises(RuntimeError):
        p.play()
    assert not p.playing and made[0].closed
    p.pause()  # must not raise or touch the dead stream again


def test_unload_stops_and_drops_frames():
    p, streams = make_player()
    frames = ramp(100)
    p.load(frames, offset=10)
    p.play()
    p.unload()
    assert not p.playing
    assert streams[0].closed
    assert p._frames is not frames and len(p._frames) == 0
    p.play()                            # nothing loaded: a no-op, not an error
    assert not p.playing
