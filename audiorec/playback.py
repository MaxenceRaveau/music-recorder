"""
Qt-free preview playback of the selected audio range through sounddevice.
"""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np
import sounddevice


class Player:
    """Plays an int16 stereo selection; `position` is an absolute frame."""

    def __init__(self, rate: int, stream_factory=sounddevice.OutputStream) -> None:
        self._rate = rate
        self._factory = stream_factory
        self._lock = threading.Lock()   # _fill runs on the PortAudio thread
        self._frames = np.zeros((0, 2), dtype=np.int16)
        self._offset = 0
        self._pos = 0                   # local index into _frames
        self._playing = False
        self._stream = None
        self.on_finished: Callable[[], None] | None = None

    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playing

    @property
    def position(self) -> int:
        with self._lock:
            return self._offset + self._pos

    def load(self, frames: np.ndarray, offset: int) -> None:
        self.stop()
        with self._lock:
            self._frames = frames
            self._offset = offset
            self._pos = 0

    def play(self, from_frame: int | None = None) -> None:
        self._close_stream()
        with self._lock:
            n = len(self._frames)
            if n == 0:
                return
            if from_frame is not None:
                self._pos = min(max(from_frame - self._offset, 0), n - 1)
            self._playing = True
        self._stream = self._factory(
            samplerate=self._rate, channels=2, dtype="float32", callback=self._callback
        )
        self._stream.start()

    def pause(self) -> None:
        with self._lock:
            self._playing = False
        self._close_stream()

    def stop(self) -> None:
        self.pause()
        with self._lock:
            self._pos = 0

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    def _callback(self, outdata, frames, time, status) -> None:
        if not self._fill(outdata, frames):
            raise sounddevice.CallbackStop

    def _fill(self, outdata: np.ndarray, frames: int) -> bool:
        with self._lock:
            chunk = self._frames[self._pos:self._pos + frames]
            n = len(chunk)
            outdata[:n] = chunk * (1.0 / 32768.0)
            outdata[n:frames] = 0
            self._pos += n
            finished = n < frames or self._pos >= len(self._frames)
            if finished:
                self._pos = 0
                self._playing = False
        if finished and self.on_finished is not None:
            self.on_finished()
        return not finished
