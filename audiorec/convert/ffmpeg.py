"""
Convert or copy one library file with the bundled ffmpeg. Output is always
written as `<final>.part` and renamed on success, so a crash or cancel never
leaves a half-written file under the real name. ffmpeg's `-f` is explicit
because the `.part` extension hides the container from it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import mutagen
from mutagen import id3
from mutagen.aiff import AIFF
from mutagen.flac import FLAC
from mutagen.mp4 import MP4, MP4Cover
from mutagen.wave import WAVE

from .rules import EncodeParams, encode_params
from .scan import Action, Job

_FLAGS = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW

_EASY_TO_ID3 = {
    "title": id3.TIT2, "artist": id3.TPE1, "album": id3.TALB,
    "date": id3.TDRC, "genre": id3.TCON, "tracknumber": id3.TRCK,
}


def _remove(path: Path) -> None:
    # Right after a kill, AV or indexers can briefly hold the file on Windows.
    for _ in range(5):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.1)


class JobFailed(Exception):
    pass


class JobCancelled(Exception):
    pass


def build_command(exe: str, src: Path, part: Path, target: str, params: EncodeParams) -> list[str]:
    cmd = [exe, "-hide_banner", "-nostats", "-loglevel", "error", "-nostdin", "-y", "-i", str(src),
           "-map", "0:a:0", "-map", "0:v:0?", "-map_metadata", "0",
           "-ar", str(params.rate), "-ac", str(params.channels)]
    if target == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3",
                "-c:v", "copy", "-disposition:v:0", "attached_pic", "-f", "mp3"]
    elif target == "aac":
        cmd += ["-c:a", "aac", "-b:a", "320k",
                "-c:v", "copy", "-disposition:v:0", "attached_pic", "-f", "ipod"]
    elif target == "wav":
        cmd += ["-c:a", f"pcm_s{params.bits}le", "-vn", "-f", "wav"]
    elif target == "aiff":
        cmd += ["-c:a", f"pcm_s{params.bits}be", "-write_id3v2", "1", "-vn", "-f", "aiff"]
    else:
        raise ValueError(f"unknown target {target!r}")
    return cmd + [str(part)]


def _source_cover(src: Path) -> tuple[bytes, str] | None:
    f = mutagen.File(src)
    if f is None:
        return None
    if isinstance(f, FLAC) and f.pictures:
        return f.pictures[0].data, f.pictures[0].mime
    if isinstance(f, MP4):
        covers = (f.tags or {}).get("covr")
        if covers:
            mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(covers[0]), mime
    if isinstance(f.tags, id3.ID3):
        apic = f.tags.getall("APIC")
        if apic:
            return apic[0].data, apic[0].mime
    return None


def _write_id3_chunk(src: Path, part: Path, target: str) -> None:
    # CDJs and DJ software read ID3 in the RIFF/AIFF chunk, not LIST INFO.
    easy = mutagen.File(src, easy=True)
    cover = _source_cover(src)
    f = WAVE(part) if target == "wav" else AIFF(part)
    if f.tags is None:
        f.add_tags()
    for key, frame in _EASY_TO_ID3.items():
        vals = easy.get(key) if easy is not None else None
        if vals:
            f.tags.add(frame(encoding=3, text=[str(v) for v in vals]))
    if cover:
        f.tags.add(id3.APIC(encoding=3, mime=cover[1], type=3, desc="", data=cover[0]))
    f.save()


def _run_ffmpeg(cmd: list[str], register: Callable, cancelled: Callable[[], bool]) -> None:
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.PIPE, creationflags=_FLAGS)
    try:
        register(p)
        while True:
            try:
                _, err = p.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if cancelled():
                    p.kill()
                    p.communicate()
                    raise JobCancelled()
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
        register(None)
    if cancelled():
        raise JobCancelled()
    if p.returncode != 0:
        lines = [ln.strip() for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()]
        raise JobFailed(lines[-1] if lines else f"ffmpeg exited with code {p.returncode}")


def process_job(job: Job, target: str, *,
                register: Callable[[subprocess.Popen | None], None] = lambda p: None,
                cancelled: Callable[[], bool] = lambda: False) -> None:
    if job.action not in (Action.CONVERT, Action.COPY):
        raise ValueError(f"cannot process a {job.action.value} job")
    if cancelled():
        raise JobCancelled()
    part = job.dst.with_name(job.dst.name + ".part")
    try:
        job.dst.parent.mkdir(parents=True, exist_ok=True)
        if job.action == Action.COPY:
            shutil.copyfile(job.src, part)
        else:
            cmd = build_command(imageio_ffmpeg.get_ffmpeg_exe(), job.src, part, target,
                                encode_params(job.info, target))
            _run_ffmpeg(cmd, register, cancelled)
            if target in ("wav", "aiff"):
                _write_id3_chunk(job.src, part, target)
        if cancelled():
            raise JobCancelled()
        os.replace(part, job.dst)
    except (JobFailed, JobCancelled):
        _remove(part)
        raise
    except Exception as e:  # OSError, mutagen errors
        _remove(part)
        raise JobFailed(str(e)) from e
