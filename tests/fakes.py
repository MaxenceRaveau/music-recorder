"""Test doubles shared by capture-loop tests (and later the GUI worker tests)."""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable

import numpy as np

from audiorec.capture import DeviceLost


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeSource:
    """Scripted ChunkSource.

    Script items: ``bytes`` (a chunk), ``"empty"`` (a poll that times out, so the
    fake clock advances by the requested timeout), a ``DeviceLost`` instance
    (raised), or a callable (an action such as ``loop.stop``: it runs, then the
    poll counts as empty without time passing). When the script runs out,
    ``get(0)`` reports empty (so a drain terminates); a blocking ``get`` fails.
    """

    def __init__(self, script: list, clock: FakeClock) -> None:
        self._script = list(script)
        self._clock = clock
        self.closed = False

    def get(self, timeout: float) -> bytes:
        if not self._script:
            if timeout == 0:
                raise queue.Empty
            raise AssertionError("script exhausted: end it with a stop action")
        item = self._script.pop(0)
        if isinstance(item, bytes):
            return item
        if isinstance(item, DeviceLost):
            raise item
        if isinstance(item, Callable):
            item()
            raise queue.Empty
        self._clock.t += timeout
        raise queue.Empty

    def close(self) -> None:
        self.closed = True


class ListSink:
    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def write(self, pcm: bytes) -> None:
        self.chunks.append(pcm)

    @property
    def data(self) -> bytes:
        return b"".join(self.chunks)


class SineSource:
    """Endless ChunkSource paced like a real device: a 440 Hz tone in 10 ms
    chunks until closed. Setting ``lose`` makes the next poll raise DeviceLost."""

    def __init__(self, rate: int, channels: int = 2) -> None:
        self.rate, self.channels = rate, channels
        self._chunk = max(1, rate // 100)
        self._n = 0
        self.lose = threading.Event()
        self.closed = False

    def get(self, timeout: float) -> bytes:
        if self.lose.is_set():
            raise DeviceLost("device unplugged")
        if timeout == 0:                # drain: nothing queued in a fake
            raise queue.Empty
        time.sleep(self._chunk / self.rate)
        t = (np.arange(self._chunk) + self._n) / self.rate
        self._n += self._chunk
        x = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
        return np.repeat(x[:, None], self.channels, axis=1).tobytes()

    def close(self) -> None:
        self.closed = True
