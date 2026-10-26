"""
Min/max peak blocks of the mono mix, for the waveform display. The same
accumulator serves live capture and rebuilding from a saved file, so both
produce identical arrays.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import numpy as np

from audiorec.capture import BYTES_PER_FRAME

BLOCK_S = 0.01
_EMPTY = np.empty((0, 2), dtype=np.float32)


def block_frames(rate: int) -> int:
    return max(1, round(rate * BLOCK_S))


class PeakAccumulator:
    def __init__(self, rate: int):
        self._block = block_frames(rate)
        self._carry = np.empty(0, dtype=np.float32)

    def add(self, pcm: bytes) -> np.ndarray:
        """Return the (min, max) rows of blocks completed by this chunk."""
        usable = len(pcm) - len(pcm) % BYTES_PER_FRAME
        frames = np.frombuffer(pcm, dtype=np.int16, count=usable // 2).reshape(-1, 2)
        mono = frames.astype(np.float32).mean(axis=1) / 32768.0
        data = np.concatenate([self._carry, mono]) if len(self._carry) else mono
        n = len(data) // self._block
        self._carry = data[n * self._block:].copy()
        if n == 0:
            return _EMPTY
        return self._minmax(data[:n * self._block].reshape(n, self._block))

    def flush(self) -> np.ndarray:
        """Emit the trailing partial block, if any."""
        if not len(self._carry):
            return _EMPTY
        out = self._minmax(self._carry.reshape(1, -1))
        self._carry = np.empty(0, dtype=np.float32)
        return out

    @staticmethod
    def _minmax(blocks: np.ndarray) -> np.ndarray:
        return np.stack([blocks.min(axis=1), blocks.max(axis=1)], axis=1).astype(np.float32)


def peaks_from_file(
    path: Path,
    rate: int,
    progress: Callable[[float], None] | None = None,
    chunk_frames: int = 1 << 20,
) -> np.ndarray:
    total = path.stat().st_size // BYTES_PER_FRAME
    acc = PeakAccumulator(rate)
    parts: list[np.ndarray] = []
    done = 0
    with open(path, "rb") as f:
        while done < total:
            want = min(chunk_frames, total - done)
            raw = f.read(want * BYTES_PER_FRAME)
            if not raw:
                break
            parts.append(acc.add(raw))
            done += len(raw) // BYTES_PER_FRAME
            if progress:
                progress(done / total)
    parts.append(acc.flush())
    return np.concatenate(parts) if parts else _EMPTY.copy()
