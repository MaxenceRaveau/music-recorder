import numpy as np
import pytest

from audiorec.peaks import PeakAccumulator, block_frames, peaks_from_file


def stereo(mono) -> bytes:
    a = np.asarray(mono, dtype=np.int16)
    return np.column_stack([a, a]).tobytes()


def test_block_frames():
    assert block_frames(48000) == 480
    assert block_frames(10) == 1


def test_peaks_block_minmax():
    acc = PeakAccumulator(1000)  # block = 10 frames
    pcm = stereo(np.r_[np.full(10, 16384), np.full(10, -32768)])
    out = acc.add(pcm)
    assert out.shape == (2, 2)
    assert out.dtype == np.float32
    assert out[0, 1] == pytest.approx(0.5)
    assert out[1, 0] == pytest.approx(-1.0)


def test_peaks_carry_partial_block():
    acc = PeakAccumulator(1000)
    assert acc.add(stereo([0, 0, 0, 0, 100])).shape == (0, 2)
    out = acc.add(stereo([0, 0, 0, 0, -200]))
    assert out.shape == (1, 2)
    assert out[0, 0] == pytest.approx(-200 / 32768)
    assert out[0, 1] == pytest.approx(100 / 32768)
    assert acc.flush().shape == (0, 2)


def test_flush_emits_partial_block():
    acc = PeakAccumulator(1000)
    acc.add(stereo(np.arange(13)))
    out = acc.flush()
    assert out.shape == (1, 2)
    assert out[0, 1] == pytest.approx(12 / 32768)


def test_peaks_from_file_matches_live(tmp_path):
    rng = np.random.default_rng(0)
    pcm = stereo(rng.integers(-32768, 32767, 1234))
    path = tmp_path / "a.pcm"
    path.write_bytes(pcm)
    acc = PeakAccumulator(1000)
    live = np.concatenate([acc.add(pcm), acc.flush()])
    seen = []
    got = peaks_from_file(path, 1000, progress=seen.append, chunk_frames=77)
    assert np.array_equal(got, live)
    assert seen and seen[-1] == pytest.approx(1.0)
    assert seen == sorted(seen)


def test_peaks_from_empty_file(tmp_path):
    path = tmp_path / "a.pcm"
    path.write_bytes(b"")
    assert peaks_from_file(path, 1000).shape == (0, 2)
