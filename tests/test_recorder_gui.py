"""App entry helpers: the single-instance lock and the error log for pythonw."""
from __future__ import annotations

import sys

from recorder_gui import acquire_single_instance_lock, install_excepthook


def test_second_lock_on_same_path_is_refused(tmp_path):
    path = tmp_path / "AudioRecorder" / "app.lock"
    first = acquire_single_instance_lock(path)
    assert first is not None
    try:
        assert acquire_single_instance_lock(path) is None
    finally:
        first.unlock()
    again = acquire_single_instance_lock(path)      # free again once released
    assert again is not None
    again.unlock()


def test_excepthook_appends_traceback_and_chains(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: seen.append(exc))
    log = tmp_path / "AudioRecorder" / "error.log"
    install_excepthook(log)
    for msg in ("first boom", "second boom"):
        try:
            raise RuntimeError(msg)
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    text = log.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: first boom" in text and "RuntimeError: second boom" in text
    assert [e[0] for e in seen] == [RuntimeError, RuntimeError]
