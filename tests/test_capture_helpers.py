import numpy as np
import pytest

from audiorec.capture import PauseClock, peak_dbfs, to_stereo_int16


def test_to_stereo_mono_duplicates():
    raw = np.array([1, -2], dtype=np.int16).tobytes()
    assert np.frombuffer(to_stereo_int16(raw, 1), np.int16).tolist() == [1, 1, -2, -2]


def test_to_stereo_surround_keeps_front_pair():
    raw = np.array([1, 2, 3, 4, 5, 6], dtype=np.int16).tobytes()
    assert np.frombuffer(to_stereo_int16(raw, 6), np.int16).tolist() == [1, 2]


def test_peak_dbfs_full_scale_and_empty():
    assert peak_dbfs(np.array([-32768], np.int16).tobytes()) == pytest.approx(0.0)
    assert peak_dbfs(b"") == float("-inf")


def test_pause_clock_excludes_paused_time():
    c = PauseClock(48000)
    c.start(10.0)
    assert c.frames_due(11.0) == 48000
    c.pause(11.0)
    assert c.frames_due(15.0) == 48000          # frozen while paused
    c.resume(15.0)
    assert c.frames_due(16.0) == 96000          # 4 s of pause excluded


def test_pause_clock_double_pause_is_noop():
    c = PauseClock(1000)
    c.start(0.0)
    c.pause(1.0)
    c.pause(2.0)
    c.resume(3.0)
    assert c.frames_due(4.0) == 2000
