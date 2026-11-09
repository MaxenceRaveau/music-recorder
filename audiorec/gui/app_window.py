"""
Top-level window: a sidebar of pages (the recorder first, then e.g. the
converter) over a stacked widget. The recorder stays a MainWindow, embedded as
a plain widget so its tests and behaviour are untouched.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QHBoxLayout, QListWidget, QMainWindow, QStackedWidget,
                               QWidget)

from audiorec.gui.main_window import MainWindow


class AppWindow(QMainWindow):
    def __init__(self, recorder: MainWindow, pages: list[tuple[str, QWidget]] = ()) -> None:
        super().__init__()
        self.setWindowTitle("Audio Recorder")
        self.resize(960, 640)
        self.recorder = recorder
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(150)
        self.stack = QStackedWidget()
        recorder.setWindowFlags(Qt.Widget)
        self._pages: list[QWidget] = [recorder]
        self._add("Recorder", recorder)
        for label, page in pages:
            self._pages.append(page)
            self._add(label, page)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

    def _add(self, label: str, page: QWidget) -> None:
        self.sidebar.addItem(label)
        self.stack.addWidget(page)

    def check_leftovers(self) -> None:
        self.recorder.check_leftovers()

    def closeEvent(self, event: QCloseEvent) -> None:
        # Two phases: every page confirms first (no side effects), and only when
        # all agree is any background work stopped.
        for page in self._pages:
            confirm = getattr(page, "confirm_close", None)
            if confirm is not None and not confirm():
                event.ignore()
                return
        for page in self._pages:
            shutdown = getattr(page, "shutdown", None)
            if shutdown is not None:
                shutdown()
        super().closeEvent(event)
