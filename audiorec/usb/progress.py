"""
Progress channel from the elevated helper to the app. The helper runs as a
separate (elevated) process, so it appends one JSON object per line to a file
the app polls. Lines are only ever appended, so the reader may catch a line
half-written: only newline-terminated lines that parse count, and the last
such line is the current state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Progress:
    fraction: float = 0.0
    message: str = ""
    done: bool = False
    error: str | None = None


def write_line(
    path: Path,
    *,
    fraction: float = 0.0,
    message: str = "",
    done: bool = False,
    error: str | None = None,
) -> None:
    line = json.dumps({"fraction": fraction, "message": message, "done": done, "error": error})
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def _parse(line: bytes) -> Progress | None:
    try:
        d = json.loads(line.decode("utf-8"))
        error = d.get("error")
        return Progress(
            fraction=float(d.get("fraction", 0.0)),
            message=str(d.get("message", "")),
            done=bool(d.get("done", False)),
            error=None if error is None else str(error),
        )
    except (ValueError, TypeError, AttributeError):
        return None


def read(path: Path) -> Progress:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return Progress()
    state = Progress()
    # The last element is either empty or an unterminated (partial) line.
    for line in data.split(b"\n")[:-1]:
        parsed = _parse(line)
        if parsed is not None:
            state = parsed
    return state
