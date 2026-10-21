"""
On-disk working copy of one recording: headerless stereo int16 `audio.pcm`
plus `session.json`. `Session.write` is the capture Sink; the file is flushed
on every write so a crash loses at most the last chunk.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterator

import numpy as np

from audiorec.capture import BYTES_PER_FRAME

PCM_NAME = "audio.pcm"
META_NAME = "session.json"


def default_root() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "AudioRecorder" / "sessions"


class Session:
    def __init__(self, folder: Path, rate: int, device_name: str,
                 started: datetime, fields: dict[str, str] | None = None):
        self.folder = folder
        self.rate = rate
        self.device_name = device_name
        self.started = started
        self.fields: dict[str, str] = dict(fields or {})
        self._pcm_path = folder / PCM_NAME
        self._file = None
        self._lock = threading.Lock()  # write() runs on the worker, update_fields() on the GUI thread

    @classmethod
    def create(cls, root: Path, rate: int, device_name: str,
               now: datetime | None = None) -> Session:
        now = now or datetime.now()
        root.mkdir(parents=True, exist_ok=True)
        base = now.strftime("%Y%m%d_%H%M%S")
        n = 1
        while True:
            folder = root / (base if n == 1 else f"{base}_{n}")
            try:
                folder.mkdir()
                break
            except FileExistsError:
                n += 1
        (folder / PCM_NAME).touch()
        s = cls(folder, rate, device_name, now)
        s._write_meta()
        return s

    @classmethod
    def open(cls, folder: Path) -> Session:
        meta = json.loads((folder / META_NAME).read_text(encoding="utf-8"))
        return cls(folder, int(meta["rate"]), meta["device_name"],
                   datetime.fromisoformat(meta["started"]), meta.get("fields", {}))

    def write(self, pcm: bytes) -> None:
        with self._lock:
            if self._file is None:
                self._file = open(self._pcm_path, "ab")
            self._file.write(pcm)
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    @property
    def frames(self) -> int:
        try:
            return os.path.getsize(self._pcm_path) // BYTES_PER_FRAME
        except FileNotFoundError:
            return 0

    def update_fields(self, **fields: str) -> None:
        with self._lock:
            self.fields.update(fields)
            self._write_meta()

    def _write_meta(self) -> None:
        meta = {"rate": self.rate, "device_name": self.device_name,
                "started": self.started.isoformat(), "fields": self.fields}
        tmp = self.folder / (META_NAME + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        os.replace(tmp, self.folder / META_NAME)

    def iter_frames(self, start: int, end: int, chunk_frames: int = 1 << 18) -> Iterator[np.ndarray]:
        start = max(0, start)
        end = min(end, self.frames)
        if start >= end:
            return
        with open(self._pcm_path, "rb") as f:
            f.seek(start * BYTES_PER_FRAME)
            pos = start
            while pos < end:
                raw = f.read(min(chunk_frames, end - pos) * BYTES_PER_FRAME)
                n = len(raw) // BYTES_PER_FRAME
                if n == 0:
                    return
                yield np.frombuffer(raw, dtype=np.int16, count=n * 2).reshape(n, 2)
                pos += n

    def read_frames(self, start: int, end: int) -> np.ndarray:
        chunks = list(self.iter_frames(start, end))
        return np.concatenate(chunks) if chunks else np.empty((0, 2), dtype=np.int16)

    def delete(self) -> None:
        self.close()
        shutil.rmtree(self.folder, ignore_errors=True)


def find_leftovers(root: Path) -> list[Session]:
    """Sessions left by a crash, newest first. Empty ones are deleted."""
    if not root.is_dir():
        return []
    found: list[Session] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        try:
            s = Session.open(folder)
        except (OSError, ValueError, KeyError):
            continue
        if s.frames == 0:
            s.delete()
        else:
            found.append(s)
    return sorted(found, key=lambda s: s.started, reverse=True)
