"""
record_system_audio.py

Record everything played on a Windows output device (WASAPI loopback) into a
single 320 kbps MP3 per session. Encoding is streamed to disk, so memory stays
flat no matter how long you record. Stop with Ctrl+C.

Install:
    pip install PyAudioWPatch lameenc numpy

Usage:
    python record_system_audio.py                  # default output device
    python record_system_audio.py --list           # show loopback devices
    python record_system_audio.py --device 12 --outdir D:/recordings
"""
from __future__ import annotations

import argparse
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

import lameenc
import numpy as np
import pyaudiowpatch as pyaudio

BITRATE_KBPS = 320
MP3_RATES = {32000, 44100, 48000}       # rates MPEG-1 Layer III accepts
CHUNK_FRAMES = 1024
POLL_S = 0.1                            # queue timeout, also Ctrl+C latency
GAP_TOLERANCE_S = 0.25                  # idle time before silence is padded
BYTES_PER_FRAME = 4                     # stereo int16


def list_loopback_devices(p: pyaudio.PyAudio) -> None:
    for dev in p.get_loopback_device_info_generator():
        print(f"[{dev['index']:>3}] {dev['name']}  "
              f"({int(dev['defaultSampleRate'])} Hz, {dev['maxInputChannels']} ch)")


def pick_device(p: pyaudio.PyAudio, index: int | None) -> dict:
    if index is not None:
        dev = p.get_device_info_by_index(index)
        if not dev.get("isLoopbackDevice"):
            sys.exit(f"Device {index} is not a WASAPI loopback device. Run with --list.")
        return dev
    try:
        return p.get_default_wasapi_loopback()
    except (OSError, LookupError):
        sys.exit("No default WASAPI loopback device found. Run with --list.")


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


def build_encoder(rate: int) -> lameenc.Encoder:
    enc = lameenc.Encoder()
    enc.set_bit_rate(BITRATE_KBPS)
    enc.set_in_sample_rate(rate)
    enc.set_channels(2)
    enc.set_quality(2)                  # 2 = high quality, 7 = fastest
    return enc


def record(p: pyaudio.PyAudio, dev: dict, outdir: Path) -> Path:
    rate = int(dev["defaultSampleRate"])
    channels = int(dev["maxInputChannels"])
    if rate not in MP3_RATES:
        sys.exit(
            f"'{dev['name']}' runs at {rate} Hz, MP3 only supports 32/44.1/48 kHz.\n"
            "Set it to 48000 Hz: Sound settings > device > Properties > Advanced."
        )

    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"desktop_audio_{datetime.now():%Y%m%d_%H%M%S}.mp3"
    enc = build_encoder(rate)
    q: queue.Queue[bytes] = queue.Queue()

    def callback(in_data, frame_count, time_info, status):
        q.put(in_data)
        return (None, pyaudio.paContinue)

    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=dev["index"],
        frames_per_buffer=CHUNK_FRAMES,
        stream_callback=callback,
    )

    print(f"Recording '{dev['name']}' @ {rate} Hz, {channels} ch -> {path}")
    print("Press Ctrl+C to stop.\n")

    start = last_data = last_status = time.perf_counter()
    frames_written = 0
    peak = float("-inf")

    with path.open("wb") as f:
        try:
            while True:
                try:
                    pcm = to_stereo_int16(q.get(timeout=POLL_S), channels)
                    last_data = time.perf_counter()
                    peak = max(peak, peak_dbfs(pcm))
                except queue.Empty:
                    # WASAPI loopback delivers nothing while the desktop is
                    # silent. Pad zeros so the file stays aligned to wall clock.
                    now = time.perf_counter()
                    if now - last_data < GAP_TOLERANCE_S:
                        continue
                    behind = int((now - start) * rate) - frames_written
                    if behind <= 0:
                        continue
                    pcm = bytes(behind * BYTES_PER_FRAME)

                f.write(enc.encode(pcm))
                frames_written += len(pcm) // BYTES_PER_FRAME

                now = time.perf_counter()
                if now - last_status >= 1.0:
                    secs = int(frames_written / rate)
                    hh, mm, ss = secs // 3600, secs % 3600 // 60, secs % 60
                    size_mb = f.tell() / 1e6
                    level = f"{peak:6.1f} dBFS" if np.isfinite(peak) else "  silent  "
                    print(f"\r● REC {hh:02d}:{mm:02d}:{ss:02d} | {size_mb:7.2f} MB | peak {level}",
                          end="", flush=True)
                    peak, last_status = float("-inf"), now
        except KeyboardInterrupt:
            pass
        finally:
            stream.stop_stream()
            stream.close()
            while not q.empty():        # flush what the callback queued last
                pcm = to_stereo_int16(q.get_nowait(), channels)
                f.write(enc.encode(pcm))
                frames_written += len(pcm) // BYTES_PER_FRAME
            f.write(enc.flush())

    secs = frames_written / rate
    print(f"\n\nSaved {path} ({secs:.1f} s, {path.stat().st_size / 1e6:.2f} MB)")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Record Windows system audio to MP3 320 kbps.")
    parser.add_argument("--list", action="store_true", help="list loopback devices and exit")
    parser.add_argument("--device", type=int, default=None, help="loopback device index")
    parser.add_argument("--outdir", type=Path, default=Path("recordings"), help="output folder")
    args = parser.parse_args()

    with pyaudio.PyAudio() as p:
        if args.list:
            list_loopback_devices(p)
            return
        record(p, pick_device(p, args.device), args.outdir)


if __name__ == "__main__":
    main()