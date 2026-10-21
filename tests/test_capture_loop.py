from audiorec.capture import (BYTES_PER_FRAME, CaptureLoop, DeviceLost, StopReason,
                              Status)
from tests.fakes import FakeClock, FakeSource, ListSink

RATE = 1000
A, B, C = (bytes([i]) * 8 for i in (1, 2, 3))      # 2 stereo frames each


def make(script_fn, sink=None, **kw):
    """script_fn(loop) -> script, so scripts can reference loop.stop etc."""
    clock = FakeClock()
    sink = sink or ListSink()
    source = FakeSource([], clock)
    loop = CaptureLoop(source, sink, RATE, 2, clock=clock, **kw)
    source._script = script_fn(loop)
    return loop, sink, source, clock


def test_loop_writes_chunks_in_order():
    loop, sink, source, _ = make(lambda l: [A, B, C, l.stop])
    assert loop.run() == StopReason("user")
    assert sink.data == A + B + C
    assert loop.frames_written == 6
    assert source.closed


def test_loop_pads_silence_after_gap():
    loop, sink, _, _ = make(lambda l: ["empty"] * 10 + [l.stop])
    loop.run()
    assert abs(loop.frames_written - 1000) <= 1       # float clock rounding
    assert sink.data == bytes(loop.frames_written * BYTES_PER_FRAME)


def test_loop_no_padding_while_paused():
    def script(l):
        return ["empty"] * 10 + [l.pause] + ["empty"] * 20 + [l.resume, l.stop]
    loop, sink, _, _ = make(script)
    loop.run()
    # 1 s before the pause, nothing during the 2 s pause, nothing owed after resume
    assert abs(loop.frames_written - 1000) <= 1


def test_loop_padding_resumes_after_pause_without_catching_up():
    def script(l):
        return (["empty"] * 10 + [l.pause] + ["empty"] * 20 + [l.resume]
                + ["empty"] * 10 + [l.stop])
    loop, _, _, _ = make(script)
    loop.run()
    # 1 s + 1 s of unpaused time; the 2 s paused never counts. The gap
    # tolerance restarts at resume, hence up to 0.25 s of lag at the end.
    assert 1000 + 700 <= loop.frames_written <= 2000


def test_loop_drops_chunks_while_paused():
    loop, sink, _, _ = make(lambda l: [A, l.pause, "empty", B, l.resume, C, l.stop])
    loop.run()
    assert sink.data == A + C


def test_loop_keeps_chunks_queued_before_pause():
    # pause() requested before run(): A and B are already queued when it is applied
    loop, sink, _, _ = make(lambda l: [A, B, "empty", C, l.stop])
    loop.pause()
    loop.run()
    assert sink.data == A + B           # C arrives while paused and is dropped


def test_loop_stop_without_data():
    loop, sink, _, _ = make(lambda l: [l.stop])
    assert loop.run() == StopReason("user") and loop.frames_written == 0
    assert sink.chunks == []


def test_loop_stop_drains_queued_chunks():
    loop, sink, _, _ = make(lambda l: [l.stop, A, B])
    loop.run()
    assert sink.data == A + B


def test_loop_device_lost_returns_reason():
    loop, sink, source, _ = make(lambda l: [A, DeviceLost("unplugged")])
    assert loop.run() == StopReason("device_lost", "unplugged")
    assert sink.data == A and source.closed


def test_loop_write_error_returns_reason():
    class Full:
        def write(self, pcm):
            raise OSError("disk full")
    loop, _, source, _ = make(lambda l: [A], sink=Full())
    assert loop.run() == StopReason("write_error", "disk full")
    assert source.closed


def test_loop_on_pcm_sees_padding():
    seen: list[bytes] = []
    loop, sink, _, _ = make(lambda l: [A] + ["empty"] * 10 + [l.stop], on_pcm=seen.append)
    loop.run()
    assert b"".join(seen) == sink.data
    assert abs(len(sink.data) // BYTES_PER_FRAME - 1000) <= 1


def test_loop_reports_status():
    got: list[Status] = []
    loop, _, _, _ = make(lambda l: [A] + ["empty"] * 3 + [l.stop],
                         on_status=got.append, status_interval_s=0.2)
    loop.run()
    assert got and 0 < got[-1].frames_written <= loop.frames_written
    assert not got[-1].paused
