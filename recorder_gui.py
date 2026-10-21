"""
recorder_gui.py

Desktop window for recording Windows system audio (WASAPI loopback), trimming
the take and saving it as MP3 320 kbps, FLAC or WAV with tags.

Usage:
    python recorder_gui.py
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from audiorec.gui.main_window import MainWindow, PyAudioDeviceProvider
from audiorec.gui.settings import AppSettings
from audiorec.session import default_root


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Audio Recorder")
    provider = PyAudioDeviceProvider()
    app.aboutToQuit.connect(provider.close)
    window = MainWindow(AppSettings(), default_root(), provider)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
