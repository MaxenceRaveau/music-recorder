"""
Defaults remembered between runs (save folder, format, device), on top of
QSettings. Tests pass an INI-backed QSettings so the registry is untouched.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

import audiorec
from audiorec.naming import FORMATS

# Same place the CLI writes to: recordings/ next to record_system_audio.py.
DEFAULT_FOLDER = Path(audiorec.__file__).resolve().parent.parent / "recordings"


class AppSettings:
    def __init__(self, qsettings: QSettings | None = None) -> None:
        self._s = qsettings if qsettings is not None else QSettings("AudioRecorder", "AudioRecorder")

    def _str(self, key: str) -> str | None:
        value = self._s.value(key)
        return str(value) if value not in (None, "") else None

    @property
    def last_folder(self) -> Path:
        value = self._str("last_folder")
        return Path(value) if value else DEFAULT_FOLDER

    @last_folder.setter
    def last_folder(self, folder: Path) -> None:
        self._s.setValue("last_folder", str(folder))
        self._s.sync()

    @property
    def last_format(self) -> str:
        value = self._str("last_format")
        return value if value in FORMATS else "mp3"

    @last_format.setter
    def last_format(self, fmt: str) -> None:
        self._s.setValue("last_format", fmt)
        self._s.sync()

    @property
    def last_device_name(self) -> str | None:
        return self._str("last_device_name")

    @last_device_name.setter
    def last_device_name(self, name: str | None) -> None:
        self._s.setValue("last_device_name", name or "")
        self._s.sync()
