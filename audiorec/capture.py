"""
Qt-free WASAPI loopback capture: device listing, PCM conversion, level
metering, a pausable wall clock and a sink-agnostic capture loop.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

import numpy as np
import pyaudiowpatch as pyaudio

CHUNK_FRAMES = 1024
POLL_S = 0.1                            # queue timeout, also stop latency
GAP_TOLERANCE_S = 0.25                  # idle time before silence is padded
BYTES_PER_FRAME = 4                     # stereo int16


def list_loopback_devices(p: pyaudio.PyAudio) -> list[dict]:
    return list(p.get_loopback_device_info_generator())


def to_stereo_int16(raw: bytes, channels: int) -> bytes:
    """MP3 is stereo at most: duplicate mono, keep FL/FR from surround layouts."""
    if channels == 2:
        return raw
    x = np.frombuffer(raw, dtype=np.int16).reshape(-1, channels)
    if channels == 1:
        return np.repeat(x, 2, axis=1).tobytes()
    return np.ascontiguousarray(x[:, :2]).tobytes()


def peak_dbfs(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype=np.int16)
    if x.size == 0:
        return float("-inf")
    peak = int(np.abs(x.astype(np.int32)).max())
    return 20 * np.log10(max(peak, 1) / 32768)


class PauseClock:
    """Wall clock that stops counting while paused; yields frames owed so far."""

    def __init__(self, rate: int) -> None:
        self._rate = rate
        self._start = 0.0
        self._paused_total = 0.0
        self._paused_at: float | None = None

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    def start(self, now: float) -> None:
        self._start = now
        self._paused_total = 0.0
        self._paused_at = None

    def pause(self, now: float) -> None:
        if self._paused_at is None:
            self._paused_at = now

    def resume(self, now: float) -> None:
        if self._paused_at is not None:
            self._paused_total += now - self._paused_at
            self._paused_at = None

    def frames_due(self, now: float) -> int:
        t = self._paused_at if self._paused_at is not None else now
        return int((t - self._start - self._paused_total) * self._rate)


class DeviceLost(Exception):
    """The capture stream stopped delivering (device unplugged or disabled)."""


class Sink(Protocol):
    def write(self, pcm: bytes) -> None: ...


class ChunkSource(Protocol):
    def get(self, timeout: float) -> bytes:
        """Next raw chunk; raises queue.Empty on timeout, DeviceLost if gone."""

    def close(self) -> None: ...


class LoopbackSource:
    """Opens a loopback stream; the PortAudio callback feeds a queue."""

    def __init__(self, p: pyaudio.PyAudio, dev: dict) -> None:
        self.rate = int(dev["defaultSampleRate"])
        self.channels = int(dev["maxInputChannels"])
        self.name = dev["name"]
        self._q: queue.Queue[bytes] = queue.Queue()

        def callback(in_data, frame_count, time_info, status):
            self._q.put(in_data)
            return (None, pyaudio.paContinue)

        self._stream = p.open(
            format=pyaudio.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=CHUNK_FRAMES,
            stream_callback=callback,
        )

    def get(self, timeout: float) -> bytes:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            if not self._stream.is_active():
                raise DeviceLost("Audio device stopped delivering data.") from None
            raise

    def close(self) -> None:
        self._stream.stop_stream()
        self._stream.close()


@dataclass
class Status:
    frames_written: int
    peak_dbfs: float                    # peak since the previous status
    paused: bool


@dataclass
class StopReason:
    kind: Literal["user", "device_lost", "write_error"]
    message: str = ""


class _SinkFailed(Exception):
    pass


class CaptureLoop:
    """Pulls chunks from a source into a sink, keeping the audio aligned to the
    (pause-adjusted) wall clock. pause/resume/stop are thread-safe requests that
    run() applies between polls."""

    def __init__(
        self,
        source: ChunkSource,
        sink: Sink,
        rate: int,
        channels: int,
        on_status: Callable[[Status], None] | None = None,
        on_pcm: Callable[[bytes], None] | None = None,
        status_interval_s: float = 0.05,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._source, self._sink = source, sink
        self._rate, self._channels = rate, channels
        self._on_status, self._on_pcm = on_status, on_pcm
        self._status_interval_s = status_interval_s
        self._clock = clock
        self._stop = threading.Event()
        self._want_paused = False       # last pause()/resume() wins, order kept
        self._frames = 0
        self._peak = float("-inf")

    @property
    def frames_written(self) -> int:
        return self._frames

    def pause(self) -> None:
        self._want_paused = True

    def resume(self) -> None:
        self._want_paused = False

    def stop(self) -> None:
        self._stop.set()

    def _write(self, pcm: bytes, real: bool = True) -> None:
        try:
            self._sink.write(pcm)
        except OSError as e:
            raise _SinkFailed(str(e)) from e
        self._frames += len(pcm) // BYTES_PER_FRAME
        if real:                        # padding must not register as a level
            self._peak = max(self._peak, peak_dbfs(pcm))
        if self._on_pcm:
            self._on_pcm(pcm)

    def _drain(self) -> None:
        """Write whatever the source holds right now."""
        while True:
            try:
                raw = self._source.get(0)
            except queue.Empty:
                return
            self._write(to_stereo_int16(raw, self._channels))

    def run(self) -> StopReason:
        clock = PauseClock(self._rate)
        now = last_data = last_status = self._clock()
        clock.start(now)
        try:
            while not self._stop.is_set():
                want_paused = self._want_paused
                if want_paused and not clock.paused:
                    self._drain()               # keep audio captured before the pause
                    clock.pause(self._clock())
                elif not want_paused and clock.paused:
                    now = self._clock()
                    clock.resume(now)
                    last_data = now

                try:
                    raw = self._source.get(POLL_S)
                except queue.Empty:
                    now = self._clock()
                    # WASAPI loopback delivers nothing while the desktop is
                    # silent. Pad zeros so the file stays aligned to wall clock.
                    if not clock.paused and now - last_data >= GAP_TOLERANCE_S:
                        behind = clock.frames_due(now) - self._frames
                        if behind > 0:
                            self._write(bytes(behind * BYTES_PER_FRAME), real=False)
                else:
                    if not clock.paused:
                        last_data = self._clock()
                        self._write(to_stereo_int16(raw, self._channels))

                now = self._clock()
                if self._on_status and now - last_status >= self._status_interval_s:
                    self._on_status(Status(self._frames, self._peak, clock.paused))
                    self._peak, last_status = float("-inf"), now

            if not clock.paused:
                self._drain()                   # flush what the callback queued last
            return StopReason("user")
        except DeviceLost as e:
            return StopReason("device_lost", str(e))
        except _SinkFailed as e:
            return StopReason("write_error", str(e))
        finally:
            self._source.close()
