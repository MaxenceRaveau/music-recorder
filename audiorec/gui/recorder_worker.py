"""
QObject bridge between the Qt-free CaptureLoop and the window. Meant to be
moved to a QThread: connect ``thread.started`` to ``run``. Peaks are batched
per status tick so the GUI thread gets a handful of signals, not one per chunk.
"""
from __future__ import annotations

import threading
from typing import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal, Slot

from audiorec.capture import CaptureLoop, ChunkSource, Status, StopReason
from audiorec.peaks import PeakAccumulator
from audiorec.session import Session


class RecorderWorker(QObject):
    peaks = Signal(object)              # float32 (n, 2) min/max rows
    status = Signal(object, float)      # frames_written (object: int32 overflows past ~12 h at 48 kHz), peak dBFS
    finished = Signal(object)           # StopReason, after the last peaks

    def __init__(self, source_factory: Callable[[], ChunkSource], session: Session,
                 rate: int, channels: int) -> None:
        super().__init__()
        self._factory, self._session = source_factory, session
        self._rate, self._channels = rate, channels
        self._lock = threading.Lock()
        self._loop: CaptureLoop | None = None
        # Desired state, so a request made before run() builds the loop still counts.
        self._want_paused = False
        self._want_stop = False
        self._pending: list[np.ndarray] = []
        self._acc = PeakAccumulator(rate)

    def pause(self) -> None:
        with self._lock:
            self._want_paused = True
            if self._loop:
                self._loop.pause()

    def resume(self) -> None:
        with self._lock:
            self._want_paused = False
            if self._loop:
                self._loop.resume()

    def stop(self) -> None:
        with self._lock:
            self._want_stop = True
            if self._loop:
                self._loop.stop()

    def _on_pcm(self, pcm: bytes) -> None:
        block = self._acc.add(pcm)
        if len(block):
            self._pending.append(block)

    def _emit_pending(self, tail: np.ndarray | None = None) -> None:
        blocks = self._pending
        if tail is not None and len(tail):
            blocks = blocks + [tail]
        self._pending = []
        if blocks:
            self.peaks.emit(np.concatenate(blocks))

    def _on_status(self, st: Status) -> None:
        self._emit_pending()
        self.status.emit(st.frames_written, st.peak_dbfs)

    @Slot()
    def run(self) -> None:
        try:
            source = self._factory()
            loop = CaptureLoop(source, self._session, self._rate, self._channels,
                               on_status=self._on_status, on_pcm=self._on_pcm)
            with self._lock:
                self._loop = loop
                if self._want_paused:
                    loop.pause()
                if self._want_stop:
                    loop.stop()
            reason = loop.run()
            self._emit_pending(self._acc.flush())
        except Exception as e:  # noqa: BLE001 - a dead thread would hang the GUI
            reason = StopReason("device_lost", str(e))
        finally:
            self._session.close()           # release the handle; readers open their own
        self.finished.emit(reason)
