"""
recorder_gui.py

Desktop window for recording Windows system audio (WASAPI loopback), trimming
the take and saving it as MP3 320 kbps, FLAC or WAV with tags.

Usage:
    python recorder_gui.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from audiorec.gui.main_window import MainWindow, PyAudioDeviceProvider
from audiorec.gui.settings import AppSettings
from audiorec.session import default_root


def acquire_single_instance_lock(path: Path) -> QLockFile | None:
    """Lock ``path``, or return None if another instance holds it. Keep the
    returned lock alive for the app's lifetime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    # 0 turns off age-based staleness, so a long-running instance keeps its lock;
    # a lock left by a crashed process is still reclaimed (its PID is gone).
    lock.setStaleLockTime(0)
    return lock if lock.tryLock(0) else None


def install_excepthook(log_path: Path) -> None:
    """Append uncaught tracebacks to ``log_path``: under pythonw there is no
    console, so they would otherwise vanish."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except OSError:
            pass                        # logging must never raise from the hook
        previous(exc_type, exc, tb)

    sys.excepthook = hook


def main() -> int:
    app_dir = default_root().parent
    install_excepthook(app_dir / "error.log")
    app = QApplication(sys.argv)
    app.setApplicationName("Audio Recorder")
    lock = acquire_single_instance_lock(app_dir / "app.lock")
    if lock is None:
        QMessageBox.information(None, "Audio Recorder", "Audio Recorder is already running.")
        return 0
    provider = PyAudioDeviceProvider()
    app.aboutToQuit.connect(provider.close)
    window = MainWindow(AppSettings(), default_root(), provider)
    window.show()
    QTimer.singleShot(0, window.check_leftovers)
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
