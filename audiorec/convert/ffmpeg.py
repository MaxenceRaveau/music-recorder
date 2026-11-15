"""
Convert or copy one library file with the bundled ffmpeg. Output is always
written as `<final>.part` and renamed on success, so a crash or cancel never
leaves a half-written file under the real name. ffmpeg's `-f` is explicit
because the `.part` extension hides the container from it.

ffmpeg's wav muxer writes 24-bit PCM as WAVE_FORMAT_EXTENSIBLE, which the
CDJ-2000 doesn't read, so such output is re-wrapped with a plain PCM header.
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
import soundfile
from mutagen import id3
from mutagen.aiff import AIFF
from mutagen.asf import ASF
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
_ID3_TEXT_FRAMES = ("TIT2", "TPE1", "TALB", "TDRC", "TCON", "TRCK")
_ASF_TO_ID3 = {
    "Title": id3.TIT2, "Author": id3.TPE1, "WM/AlbumTitle": id3.TALB,
    "WM/Year": id3.TDRC, "WM/Genre": id3.TCON, "WM/TrackNumber": id3.TRCK,
}
_WAVE_FORMAT_PCM = 1
_REWRAP_FRAMES = 1 << 16


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
           "-map", "0:a:0", "-map", "0:v:disp:attached_pic?", "-map_metadata", "0",
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


def _source_text_frames(src: Path) -> list[id3.Frame]:
    # Easy keys don't exist for WAVE/AIFF/ASF, so read their tags directly.
    raw = mutagen.File(src)
    if raw is not None and isinstance(raw.tags, id3.ID3):
        return [fr for name in _ID3_TEXT_FRAMES for fr in raw.tags.getall(name)]
    if isinstance(raw, ASF) and raw.tags:
        # ASF can hold one value twice (content description + extended).
        return [frame(encoding=3, text=list(dict.fromkeys(str(v) for v in raw.tags[key])))
                for key, frame in _ASF_TO_ID3.items() if raw.tags.get(key)]
    easy = mutagen.File(src, easy=True)
    if easy is None:
        return []
    return [frame(encoding=3, text=[str(v) for v in easy[key]])
            for key, frame in _EASY_TO_ID3.items() if easy.get(key)]


def _wav_format_tag(path: Path) -> int:
    with open(path, "rb") as fh:
        fh.seek(12)  # past "RIFF" <size> "WAVE"
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                raise ValueError("no fmt chunk in WAV output")
            size = int.from_bytes(hdr[4:], "little")
            if hdr[:4] == b"fmt ":
                return int.from_bytes(fh.read(2), "little")
            fh.seek(size + (size & 1), os.SEEK_CUR)


def _rewrap_plain_wav(part: Path, tmp: Path) -> None:
    """Rewrite `part` with a WAVE_FORMAT_PCM header, through `tmp`."""
    with soundfile.SoundFile(str(part)) as inp:
        with soundfile.SoundFile(str(tmp), "w", inp.samplerate, inp.channels,
                                 subtype=inp.subtype, format="WAV") as out:
            for k, v in inp.copy_metadata().items():
                setattr(out, k, v)
            for block in inp.blocks(_REWRAP_FRAMES, dtype="int32"):
                out.write(block)
    os.replace(tmp, part)


def _write_id3_chunk(src: Path, part: Path, target: str) -> None:
    # CDJs and DJ software read ID3 in the RIFF/AIFF chunk, not LIST INFO.
    frames = _source_text_frames(src)
    cover = _source_cover(src)
    f = WAVE(part) if target == "wav" else AIFF(part)
    if f.tags is None:
        f.add_tags()
    for frame in frames:
        f.tags.add(frame)
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
    rewrap = job.dst.with_name(job.dst.name + ".pcm.part")
    try:
        job.dst.parent.mkdir(parents=True, exist_ok=True)
        if job.action == Action.COPY:
            shutil.copyfile(job.src, part)
        else:
            cmd = build_command(imageio_ffmpeg.get_ffmpeg_exe(), job.src, part, target,
                                encode_params(job.info, target))
            _run_ffmpeg(cmd, register, cancelled)
            if target == "wav" and _wav_format_tag(part) != _WAVE_FORMAT_PCM:
                _rewrap_plain_wav(part, rewrap)
            if target in ("wav", "aiff"):
                _write_id3_chunk(job.src, part, target)
        if cancelled():
            raise JobCancelled()
        os.replace(part, job.dst)
    except (JobFailed, JobCancelled):
        _remove(part)
        _remove(rewrap)
        raise
    except Exception as e:  # OSError, mutagen and soundfile errors
        _remove(part)
        _remove(rewrap)
        raise JobFailed(str(e)) from e
