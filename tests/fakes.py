"""Test doubles shared by capture-loop tests (and later the GUI worker tests)."""
from __future__ import annotations

import queue
from typing import Callable

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
