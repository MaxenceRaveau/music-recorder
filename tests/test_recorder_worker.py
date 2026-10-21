import numpy as np
from PySide6.QtCore import QThread

from audiorec.capture import DeviceLost, StopReason
from audiorec.gui.recorder_worker import RecorderWorker
from audiorec.peaks import block_frames
from audiorec.session import Session
from tests.fakes import FakeClock, FakeSource

RATE = 1000


def chunk(frames=100, value=1000):
    return np.full(frames * 2, value, dtype=np.int16).tobytes()


def start(qtbot, tmp_path, make_script, factory=None):
    session = Session.create(tmp_path, RATE, "fake")
    clock = FakeClock()
    holder = {}
    if factory is None:
        def factory():
            return FakeSource(make_script(holder["worker"]), clock)
    worker = RecorderWorker(factory, session, RATE, 2)
    holder["worker"] = worker
    # the fake clock is the source's, but the loop uses real time; scripts end with stop
    blocks, reasons = [], []
    worker.peaks.connect(blocks.append)
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    worker.finished.connect(reasons.append)
    with qtbot.waitSignal(worker.finished, timeout=5000):
        thread.start()
    thread.wait(5000)
    return session, blocks, reasons[0]


def test_worker_writes_session_and_emits_peaks(qtbot, tmp_path):
    session, blocks, reason = start(
        qtbot, tmp_path, lambda w: [chunk(), chunk(), chunk(), w.stop])
    assert reason.kind == "user"
    assert session.frames == 300
    total = sum(len(b) for b in blocks)
    assert total == -(-300 // block_frames(RATE))
    assert all(b.dtype == np.float32 and b.shape[1] == 2 for b in blocks)


def test_worker_stop_emits_finished_user(qtbot, tmp_path):
    _, _, reason = start(qtbot, tmp_path, lambda w: ["empty", w.stop])
    assert reason == StopReason("user")


def test_worker_device_lost_emits_reason(qtbot, tmp_path):
    session, _, reason = start(
        qtbot, tmp_path, lambda w: [chunk(), DeviceLost("gone")])
    assert reason == StopReason("device_lost", "gone")
    assert session.frames == 100


def test_worker_factory_failure_emits_device_lost(qtbot, tmp_path):
    def boom():
        raise RuntimeError("no device")
    _, _, reason = start(qtbot, tmp_path, None, factory=boom)
    assert reason == StopReason("device_lost", "no device")


def test_stop_before_run_still_takes_effect(qtbot, tmp_path):
    session = Session.create(tmp_path, RATE, "fake")
    clock = FakeClock()
    worker = RecorderWorker(lambda: FakeSource(["empty"] * 3, clock), session, RATE, 2)
    worker.stop()
    reasons = []
    worker.finished.connect(reasons.append)
    worker.run()
    assert reasons == [StopReason("user")]


def test_status_signal_carries_frames_beyond_int32(qtbot, tmp_path):
    session = Session.create(tmp_path, RATE, "fake")
    worker = RecorderWorker(lambda: None, session, RATE, 2)
    got = []
    worker.status.connect(lambda frames, db: got.append((frames, db)))
    worker.status.emit(3_000_000_000, -12.5)
    assert got == [(3_000_000_000, -12.5)]
