"""
USB Formatter page: pick a USB stick, confirm by typing its drive letter, and
format it FAT32 through the elevated helper. The page itself never runs
elevated and never writes to a disk: it lists sticks (on a QThread, since the
PowerShell query takes a moment), starts the helper with a UAC prompt, and
polls the helper's progress file. The FormatRequest is built from the disk the
user confirmed, so a list refresh can never redirect the format elsewhere.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel,
                               QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
                               QWidget)

from audiorec.session import default_root
from audiorec.usb import launcher, progress
from audiorec.usb.disks import UsbDisk, clean_label, list_usb_disks
from audiorec.usb.helper import FormatRequest

CLOSE_MESSAGE = "A USB stick is being formatted.\nIt will finish in the background. Quit anyway?"
NO_DISK = "No USB stick found"
UNEXPECTED = "the helper stopped unexpectedly."
WARN_COLOR = "#b35c00"
POLL_MS = 200
BAR_MAX = 1000


def _default_progress_dir() -> Path:
    return default_root().parent / "usb"


class _ListWorker(QObject):
    finished = Signal(object)           # list[UsbDisk]
    failed = Signal(str)

    def __init__(self, list_disks: Callable[[], list[UsbDisk]]) -> None:
        super().__init__()
        self._list_disks = list_disks

    @Slot()
    def run(self) -> None:
        try:
            disks = self._list_disks()
        except Exception as e:  # noqa: BLE001 - a dead thread would hang the page
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.finished.emit(disks)


class EraseDialog(QDialog):
    """Erase becomes available only once the user typed the expected text."""

    def __init__(self, disk: UsbDisk, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Erase USB stick")
        if disk.letters:
            drives = "drives " + ", ".join(f"{letter}:" for letter in disk.letters)
            self.expected = disk.letters[0].upper()
        else:
            drives = "no drive letter"
            words = disk.model.split()
            self.expected = words[0].upper() if words else "ERASE"
        lay = QVBoxLayout(self)
        self.text_label = QLabel(
            f"Everything on {disk.model} ({disk.size / 1e9:.1f} GB, {drives}) will be erased.",
            wordWrap=True)
        lay.addWidget(self.text_label)
        self.confirm_edit = QLineEdit(placeholderText=f"Type {self.expected} to confirm")
        lay.addWidget(self.confirm_edit)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.erase_button = self.buttons.addButton("Erase", QDialogButtonBox.DestructiveRole)
        self.erase_button.setAutoDefault(False)
        self.erase_button.setEnabled(False)
        cancel = self.buttons.button(QDialogButtonBox.Cancel)
        cancel.setDefault(True)
        lay.addWidget(self.buttons)
        self.erase_button.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        self.confirm_edit.textChanged.connect(self._on_text)

    def _on_text(self, text: str) -> None:
        self.erase_button.setEnabled(text.strip().upper() == self.expected)


class UsbPage(QWidget):
    def __init__(self, *, list_disks: Callable[[], list[UsbDisk]] = list_usb_disks,
                 start: Callable[[FormatRequest], launcher.Launch | None] = launcher.start,
                 progress_dir: Path | None = None) -> None:
        super().__init__()
        self._list_disks = list_disks
        self._start = start
        self._progress_dir = progress_dir
        self._disks: list[UsbDisk] = []
        self._loaded = False
        self._thread: QThread | None = None
        self._worker: _ListWorker | None = None
        self._launch: launcher.Launch | None = None
        self._progress_path: Path | None = None
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._build_ui()
        self._fill_combo([])
        self._apply_controls()

    # ---- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        grid = QGridLayout()
        self.disk_combo = QComboBox()
        self.refresh_button = QPushButton("Refresh")
        self.label_edit = QLineEdit(maxLength=32)
        self.info_label = QLabel("FAT32 · MBR · whole stick")
        grid.addWidget(QLabel("USB stick"), 0, 0)
        grid.addWidget(self.disk_combo, 0, 1)
        grid.addWidget(self.refresh_button, 0, 2)
        grid.addWidget(QLabel("Label"), 1, 0)
        grid.addWidget(self.label_edit, 1, 1)
        grid.addWidget(self.info_label, 2, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.format_button = QPushButton("Format…")
        lay.addWidget(self.format_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, BAR_MAX)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.status_label = QLabel(wordWrap=True)
        lay.addWidget(self.status_label)
        lay.addStretch(1)

        self.refresh_button.clicked.connect(self.refresh)
        self.disk_combo.currentIndexChanged.connect(self._on_disk_changed)
        self.label_edit.editingFinished.connect(
            lambda: self.label_edit.setText(clean_label(self.label_edit.text())))
        self.format_button.clicked.connect(self._on_format)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._loaded:
            self.refresh()

    # ---- state ------------------------------------------------------------
    def _formatting(self) -> bool:
        return self._launch is not None

    def _apply_controls(self) -> None:
        idle = not self._formatting()
        listing = self._thread is not None
        self.disk_combo.setEnabled(idle and bool(self._disks))
        self.label_edit.setEnabled(idle and bool(self._disks))
        self.refresh_button.setEnabled(idle and not listing)
        self.format_button.setEnabled(idle and not listing and self._current_disk() is not None)

    def _set_status(self, text: str, warn: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {WARN_COLOR};" if warn else "")

    def _current_disk(self) -> UsbDisk | None:
        i = self.disk_combo.currentIndex()
        return self._disks[i] if 0 <= i < len(self._disks) else None

    # ---- listing ----------------------------------------------------------
    def refresh(self) -> None:
        """List the USB sticks on a worker thread."""
        self._loaded = True
        if self._thread is not None or self._formatting():
            return
        worker = _ListWorker(self._list_disks)
        worker.finished.connect(self._on_listed)
        worker.failed.connect(self._on_list_failed)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._thread, self._worker = thread, worker
        self._apply_controls()
        thread.start()

    def _finish_thread(self) -> None:
        thread, self._thread, self._worker = self._thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()

    def _fill_combo(self, disks: list[UsbDisk]) -> None:
        self._disks = list(disks)
        self.disk_combo.blockSignals(True)
        self.disk_combo.clear()
        if disks:
            for d in disks:
                self.disk_combo.addItem(d.display)
        else:
            self.disk_combo.addItem(NO_DISK)
        self.disk_combo.setCurrentIndex(0)
        self.disk_combo.blockSignals(False)
        self._on_disk_changed()

    def _on_listed(self, disks: list[UsbDisk]) -> None:
        self._finish_thread()
        self._fill_combo(disks)
        self._apply_controls()

    def _on_list_failed(self, message: str) -> None:
        self._finish_thread()
        self._fill_combo([])
        self._set_status(f"Couldn't list USB sticks: {message}", warn=True)
        self._apply_controls()

    def _on_disk_changed(self) -> None:
        disk = self._current_disk()
        self.label_edit.setText(clean_label(disk.label) if disk is not None else "")
        self._apply_controls()

    # ---- format -----------------------------------------------------------
    def _on_format(self) -> None:
        disk = self._current_disk()
        if disk is None or self._formatting() or not self._ask_erase(disk):
            return
        folder = self._progress_dir or _default_progress_dir()
        req = FormatRequest(disk.number, disk.serial, disk.size,
                            clean_label(self.label_edit.text()),
                            folder / f"format-{disk.number}-{time.time_ns()}.jsonl")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            launch = self._start(req)
        except OSError as e:
            self._set_status(f"Couldn't start the format: {e.strerror or e}", warn=True)
            return
        if launch is None:
            self._set_status("Cancelled — nothing was changed.")
            return
        self._launch, self._progress_path = launch, req.progress_path
        self.progress.setValue(0)
        self.progress.show()
        self._set_status("Starting…")
        self._apply_controls()
        self._timer.start()

    def _poll(self) -> None:
        launch = self._launch
        if launch is None:
            self._timer.stop()
            return
        wait_error = None
        try:
            exited = launch.wait(0)
        except OSError as e:
            exited, wait_error = True, str(e.strerror or e)
        # Read after checking for exit, so the last lines written before exit count.
        state = progress.read(self._progress_path)
        self.progress.setValue(round(max(0.0, min(1.0, state.fraction)) * BAR_MAX))
        if state.message and not exited:
            self._set_status(state.message)
        if not exited:
            return
        self._timer.stop()
        if state.done:
            self.progress.setValue(BAR_MAX)
            self._set_status(state.message)
        elif state.error is not None:
            self._set_status(f"Format failed: {state.error}", warn=True)
        else:
            self._set_status(f"Format failed: {wait_error or UNEXPECTED}", warn=True)
        self._launch = None
        launch.close()
        self._apply_controls()
        self.refresh()

    # ---- closing ----------------------------------------------------------
    def confirm_close(self) -> bool:
        """Ask whether quitting is OK. No side effects."""
        return not self._formatting() or self._confirm("Quit Audio Recorder", CLOSE_MESSAGE)

    def shutdown(self) -> None:
        """Stop polling and the list worker. The helper is left to finish."""
        self._timer.stop()
        worker = self._worker
        if worker is not None:
            worker.finished.disconnect()
            worker.failed.disconnect()
            self._finish_thread()

    # ---- dialogs (wrapped so tests can replace them) -----------------------
    def _ask_erase(self, disk: UsbDisk) -> bool:
        return EraseDialog(disk, self).exec() == QDialog.Accepted

    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.No)
        return answer == QMessageBox.Yes

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)
