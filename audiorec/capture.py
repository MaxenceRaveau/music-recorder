"""
Pure, Qt-free helpers for WASAPI loopback capture: device selection, PCM
conversion, level metering and a pausable wall clock.
"""
from __future__ import annotations

import numpy as np
import pyaudiowpatch as pyaudio

CHUNK_FRAMES = 1024
POLL_S = 0.1                            # queue timeout, also stop latency
GAP_TOLERANCE_S = 0.25                  # idle time before silence is padded
BYTES_PER_FRAME = 4                     # stereo int16


class DeviceError(Exception):
    """No usable loopback device (raised instead of exiting, so a GUI can show it)."""


def list_loopback_devices(p: pyaudio.PyAudio) -> list[dict]:
    return list(p.get_loopback_device_info_generator())


def pick_device(p: pyaudio.PyAudio, index: int | None) -> dict:
    if index is not None:
        dev = p.get_device_info_by_index(index)
        if not dev.get("isLoopbackDevice"):
            raise DeviceError(f"Device {index} is not a WASAPI loopback device.")
        return dev
    try:
        return p.get_default_wasapi_loopback()
    except (OSError, LookupError):
        raise DeviceError("No default WASAPI loopback device found.") from None


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
