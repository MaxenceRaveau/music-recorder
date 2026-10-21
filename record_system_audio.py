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
import sys
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio

from audiorec.capture import (CaptureLoop, DeviceError, LoopbackSource, Status,
                              list_loopback_devices, pick_device)
from audiorec.mp3sink import MP3_RATES, Mp3StreamSink


def print_devices(p: pyaudio.PyAudio) -> None:
    for dev in list_loopback_devices(p):
        print(f"[{dev['index']:>3}] {dev['name']}  "
              f"({int(dev['defaultSampleRate'])} Hz, {dev['maxInputChannels']} ch)")


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
    source = LoopbackSource(p, dev)

    print(f"Recording '{dev['name']}' @ {rate} Hz, {channels} ch -> {path}")
    print("Press Ctrl+C to stop.\n")

    with path.open("wb") as f:
        sink = Mp3StreamSink(f, rate)

        def on_status(st: Status) -> None:
            secs = int(st.frames_written / rate)
            hh, mm, ss = secs // 3600, secs % 3600 // 60, secs % 60
            level = f"{st.peak_dbfs:6.1f} dBFS" if np.isfinite(st.peak_dbfs) else "  silent  "
            print(f"\r● REC {hh:02d}:{mm:02d}:{ss:02d} | {f.tell() / 1e6:7.2f} MB | peak {level}",
                  end="", flush=True)

        loop = CaptureLoop(source, sink, rate, channels, on_status=on_status,
                           status_interval_s=1.0)
        result = []
        worker = threading.Thread(target=lambda: result.append(loop.run()))
        worker.start()
        try:
            while worker.is_alive():
                worker.join(0.2)        # short joins keep Ctrl+C responsive
        except KeyboardInterrupt:
            loop.stop()                 # run() drains the queue and closes the stream
            worker.join()
        sink.finish()

    if result and result[0].kind != "user":
        print(f"\n\nRecording ended early ({result[0].kind}): {result[0].message}",
              file=sys.stderr)
    secs = loop.frames_written / rate
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
            print_devices(p)
            return
        try:
            dev = pick_device(p, args.device)
        except DeviceError as e:
            sys.exit(f"{e} Run with --list.")
        record(p, dev, args.outdir)


if __name__ == "__main__":
    main()
