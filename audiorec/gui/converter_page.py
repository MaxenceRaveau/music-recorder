"""
Converter page: pick a library, a destination and a format, Scan to see what
would happen per folder, untick folders, then Convert. Scan and Convert both
run on a QThread so the window (and a recording on the other page) never
stalls; the runner's results arrive from its pool threads through a Qt signal.
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QProgressBar, QPushButton, QStackedWidget,
                               QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
                               QVBoxLayout, QWidget)

from audiorec.convert.formats import TARGETS
from audiorec.convert.runner import Runner
from audiorec.convert.scan import (Action, Job, folder_groups, pending_counts, scan,
                                   validate_paths)
from audiorec.gui.settings import AppSettings

LOOSE_TRACKS = "(tracks in this folder)"
CLOSE_MESSAGE = "A conversion is running. Stop it and quit?\nFinished files are kept."
WARN_COLOR = "#b35c00"
FAIL_COLOR = "#c0392b"
GB = 1024 ** 3
GROUP_ROLE = Qt.UserRole

STYLE = """
QLabel#summaryNumber { font-size: 22px; font-weight: bold; }
QLabel#summaryKey, QLabel#hint { color: palette(placeholder-text); }
QFrame#pane { border: 1px solid palette(mid); border-radius: 6px; }
QLineEdit { font-family: Consolas, "Courier New", monospace; }
"""


class _ScanWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(object)           # list[Job], or None when cancelled
    failed = Signal(str)

    def __init__(self, library: Path, dest: Path, target: str) -> None:
        super().__init__()
        self._args = (library, dest, target)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    @Slot()
    def run(self) -> None:
        try:
            jobs = scan(*self._args, progress=self.progress.emit, cancelled=self._cancel.is_set)
        except Exception as e:  # noqa: BLE001 - a dead thread would hang the page
            self.failed.emit(str(e) or type(e).__name__)
            return
        self.finished.emit(jobs)


class _ConvertWorker(QObject):
    started = Signal(object)            # Job (emitted from pool threads)
    result = Signal(object, str, str)   # Job, status, message (emitted from pool threads)
    failed = Signal(str)                # the runner itself crashed
    finished = Signal(bool)             # True if not stopped

    def __init__(self, jobs: list[Job], target: str, workers: int | None) -> None:
        super().__init__()
        self.runner = Runner(jobs, target, workers=workers, on_result=self.result.emit,
                             on_start=self.started.emit)

    @Slot()
    def run(self) -> None:
        try:
            completed = self.runner.run()
        except Exception as e:  # noqa: BLE001 - a dead thread would leave the page converting
            self.failed.emit(str(e) or type(e).__name__)
            completed = False
        self.finished.emit(completed)


def _free_bytes(dest: Path) -> tuple[str, int] | None:
    """Free space on the drive dest will land on (dest itself may not exist yet)."""
    p = dest.absolute()
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        free = shutil.disk_usage(p).free
    except OSError:
        return None
    return (p.drive.rstrip(":") or p.anchor), free


class ConverterPage(QWidget):
    def __init__(self, settings: AppSettings, *, workers: int | None = None) -> None:
        super().__init__()
        self._settings = settings
        self._workers = workers
        self._mode = "idle"                 # idle | scanning | converting
        self._thread: QThread | None = None
        self._worker: _ScanWorker | _ConvertWorker | None = None
        self._jobs: list[Job] | None = None
        self._scanned: tuple[Path, Path, str] | None = None
        self._stats: dict[str, list[int]] = {}  # group -> [convert, copy, done, bytes]
        self._counts = {"converted": 0, "copied": 0, "done": 0, "failed": 0}
        self._failures: list[str] = []
        self._run_error: str | None = None
        self._free: tuple[str, int] | None = None   # destination drive, read once per scan
        # Unticking a folder emits itemChanged for every descendant: recompute once.
        self._summary_timer = QTimer(self, singleShot=True, interval=0)
        self._summary_timer.timeout.connect(lambda: self._update_summary())
        self._build_ui()
        self.library_edit.setText(str(settings.conv_library or ""))
        self.dest_edit.setText(str(settings.conv_dest or ""))
        self.format_combo.setCurrentIndex(self.format_combo.findData(settings.conv_format))
        self.library_edit.textChanged.connect(self._on_paths_changed)
        self.dest_edit.textChanged.connect(self._on_paths_changed)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self._clear_scan()

    # ---- construction -----------------------------------------------------
    def _build_ui(self) -> None:
        self.setStyleSheet(STYLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)

        grid = QGridLayout()
        self.library_edit, self.dest_edit = QLineEdit(), QLineEdit()
        self.library_browse, self.dest_browse = QPushButton("Browse…"), QPushButton("Browse…")
        self.scan_button = QPushButton("Scan")
        self.format_combo = QComboBox()
        for t in TARGETS.values():
            self.format_combo.addItem(t.label, t.key)
        grid.addWidget(QLabel("Library"), 0, 0)
        grid.addWidget(self.library_edit, 0, 1)
        grid.addWidget(self.library_browse, 0, 2)
        grid.addWidget(self.scan_button, 0, 3)
        grid.addWidget(QLabel("Destination"), 1, 0)
        grid.addWidget(self.dest_edit, 1, 1)
        grid.addWidget(self.dest_browse, 1, 2)
        grid.addWidget(QLabel("Format"), 2, 0)
        grid.addWidget(self.format_combo, 2, 1, Qt.AlignLeft)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)

        split = QHBoxLayout()
        split.setSpacing(12)
        split.addWidget(self._build_tree_pane(), 6)
        self.right = QStackedWidget()
        self.right.addWidget(self._build_summary_pane())
        self.right.addWidget(self._build_run_pane())
        split.addWidget(self.right, 5)
        lay.addLayout(split, 1)

        self.library_browse.clicked.connect(lambda: self._browse(self.library_edit, "Library folder"))
        self.dest_browse.clicked.connect(lambda: self._browse(self.dest_edit, "Destination folder"))
        self.scan_button.clicked.connect(self._on_scan)
        self.select_all.clicked.connect(lambda: self._set_all(Qt.Checked))
        self.select_none.clicked.connect(lambda: self._set_all(Qt.Unchecked))
        self.tree.itemChanged.connect(self._summary_timer.start)
        self.convert_button.clicked.connect(self._on_convert)
        self.stop_button.clicked.connect(self._on_stop)
        self.copy_failures_button.clicked.connect(self._copy_failures)

    def _build_tree_pane(self) -> QFrame:
        pane = QFrame(objectName="pane")
        v = QVBoxLayout(pane)
        head = QHBoxLayout()
        self.select_all, self.select_none = QPushButton("Select all"), QPushButton("Select none")
        head.addWidget(self.select_all)
        head.addWidget(self.select_none)
        head.addStretch(1)
        head.addWidget(QLabel("files to process", objectName="hint"))
        v.addLayout(head)
        self.tree = QTreeWidget(columnCount=2, headerHidden=True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        v.addWidget(self.tree, 1)
        return pane

    def _build_summary_pane(self) -> QFrame:
        pane = QFrame(objectName="pane")
        v = QVBoxLayout(pane)
        numbers = []
        for key in ("to convert", "to copy as-is", "already in destination"):
            n = QLabel("0", objectName="summaryNumber")
            v.addWidget(n)
            v.addWidget(QLabel(key, objectName="summaryKey"))
            numbers.append(n)
        self.summary_convert, self.summary_copy, self.summary_done = numbers
        self.size_label = QLabel(wordWrap=True)
        v.addWidget(self.size_label)
        v.addStretch(1)
        self.convert_button = QPushButton("Convert 0 files", default=True)
        v.addWidget(self.convert_button)
        return pane

    def _build_run_pane(self) -> QFrame:
        pane = QFrame(objectName="pane")
        v = QVBoxLayout(pane)
        self.current_label = QLabel(wordWrap=True)
        self.counts_label = QLabel()
        self.log = QListWidget()
        buttons = QHBoxLayout()
        self.stop_button = QPushButton("Stop")
        self.copy_failures_button = QPushButton("Copy failures")
        buttons.addWidget(self.copy_failures_button)
        buttons.addStretch(1)
        buttons.addWidget(self.stop_button)
        for w in (self.current_label, self.counts_label):
            v.addWidget(w)
        v.addWidget(self.log, 1)
        v.addLayout(buttons)
        return pane

    # ---- inputs -----------------------------------------------------------
    @staticmethod
    def _path(edit: QLineEdit) -> Path | None:
        text = edit.text().strip().strip('"')
        return Path(text) if text else None

    def _target(self) -> str:
        return self.format_combo.currentData()

    def _browse(self, edit: QLineEdit, title: str) -> None:
        folder = self._pick_folder(title, self._path(edit))
        if folder is not None:
            edit.setText(str(folder))

    def _on_paths_changed(self) -> None:
        self._settings.conv_library = self._path(self.library_edit)
        self._settings.conv_dest = self._path(self.dest_edit)
        self._clear_scan()

    def _on_format_changed(self) -> None:
        self._settings.conv_format = self._target()
        self._clear_scan()

    def _apply_controls(self) -> None:
        idle = self._mode == "idle"
        for w in (self.library_edit, self.dest_edit, self.library_browse, self.dest_browse,
                  self.format_combo):
            w.setEnabled(idle)
        self.scan_button.setEnabled(self._mode != "converting")
        self.scan_button.setText("Cancel scan" if self._mode == "scanning" else "Scan")
        editable = idle and self._jobs is not None and self.right.currentIndex() == 0
        for w in (self.tree, self.select_all, self.select_none):
            w.setEnabled(editable)
        self.stop_button.setEnabled(self._mode == "converting")
        self.copy_failures_button.setEnabled(idle and bool(self._failures))
        self._update_summary()

    # ---- scan -------------------------------------------------------------
    def _clear_scan(self) -> None:
        self._jobs, self._scanned, self._stats = None, None, {}
        self.tree.clear()
        self.right.setCurrentIndex(0)
        self.progress.hide()
        self.size_label.setText("Scan the library to see what will be converted.")
        self.size_label.setStyleSheet("")
        self._apply_controls()

    def _on_scan(self) -> None:
        if self._mode == "scanning":
            self._worker.cancel()
            self.scan_button.setEnabled(False)
            return
        library, dest = self._path(self.library_edit), self._path(self.dest_edit)
        if library is None:
            message = "Library folder not found."
        else:
            message = validate_paths(library, dest or Path(""))
        if message:
            self._error("Cannot scan", message)
            return
        self._clear_scan()
        self._failures = []
        worker = _ScanWorker(library, dest, self._target())
        worker.progress.connect(self._on_scan_progress)
        worker.finished.connect(self._on_scan_finished)
        worker.failed.connect(self._on_scan_failed)
        self._scanned = (library, dest, self._target())
        self.progress.setRange(0, 0)
        self.progress.show()
        self._start("scanning", worker)

    def _start(self, mode: str, worker: QObject) -> None:
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        self._thread, self._worker, self._mode = thread, worker, mode
        self._apply_controls()
        thread.start()

    def _finish_thread(self) -> None:
        thread, self._thread, self._worker = self._thread, None, None
        self._mode = "idle"
        if thread is not None:
            thread.quit()
            thread.wait()

    def _on_scan_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _on_scan_failed(self, message: str) -> None:
        self._finish_thread()
        self._clear_scan()
        self._error("Scan failed", message)

    def _on_scan_finished(self, jobs: list[Job] | None) -> None:
        self._finish_thread()
        if jobs is None:
            self._clear_scan()
            return
        scanned = self._scanned
        self._clear_scan()
        self._jobs, self._scanned = jobs, scanned
        self._free = _free_bytes(scanned[1])
        for j in jobs:
            s = self._stats.setdefault(j.group, [0, 0, 0, 0])
            if j.action in (Action.CONVERT, Action.COPY):
                s[0 if j.action == Action.CONVERT else 1] += 1
                s[3] += j.est_bytes
            elif j.action == Action.DONE:
                s[2] += 1
        self._build_tree(jobs)
        self._apply_controls()

    def _build_tree(self, jobs: list[Job]) -> None:
        groups = folder_groups(jobs)
        counts = pending_counts(jobs)
        folders = {"/".join(g.split("/")[:n]) for g in groups if g
                   for n in range(1, g.count("/") + 2)}
        parents = {f.rpartition("/")[0] for f in folders}
        flags = Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate

        def add(parent: QTreeWidgetItem | None, name: str, count: int,
                group: str | None) -> QTreeWidgetItem:
            item = QTreeWidgetItem([name, str(count)])
            item.setFlags(flags)
            item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            if group is not None:
                item.setData(0, GROUP_ROLE, group)
                item.setCheckState(0, Qt.Checked)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            return item

        self.tree.blockSignals(True)
        items: dict[str, QTreeWidgetItem | None] = {"": None}
        if "" in groups:
            add(None, LOOSE_TRACKS, self._pending(""), "")
        for f in sorted(folders, key=lambda f: f.lower().split("/")):
            parent = items[f.rpartition("/")[0]]
            has_kids = f in parents
            items[f] = add(parent, f.rpartition("/")[2], counts.get(f, 0),
                           None if has_kids else f)
            if has_kids and f in self._stats:
                add(items[f], LOOSE_TRACKS, self._pending(f), f)
        self.tree.blockSignals(False)
        self.tree.expandToDepth(0)

    def _pending(self, group: str) -> int:
        s = self._stats.get(group, [0, 0])
        return s[0] + s[1]

    def _set_all(self, state: Qt.CheckState) -> None:
        self.tree.blockSignals(True)
        for item in self._leaves():
            item.setCheckState(0, state)
        self.tree.blockSignals(False)
        self.tree.viewport().update()
        self._update_summary()

    def _leaves(self) -> list[QTreeWidgetItem]:
        out, it = [], QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().childCount() == 0:
                out.append(it.value())
            it += 1
        return out

    def _selected_groups(self) -> set[str]:
        return {item.data(0, GROUP_ROLE) for item in self._leaves()
                if item.checkState(0) == Qt.Checked}

    def _update_summary(self) -> None:
        if self.right.currentIndex() != 0:
            return
        conv = copy = done = size = 0
        if self._jobs is not None:
            for g in self._selected_groups():
                c, p, d, b = self._stats.get(g, [0, 0, 0, 0])
                conv, copy, done, size = conv + c, copy + p, done + d, size + b
        self.summary_convert.setText(str(conv))
        self.summary_copy.setText(str(copy))
        self.summary_done.setText(str(done))
        self.convert_button.setText(f"Convert {conv + copy} files")
        self.convert_button.setEnabled(self._mode == "idle" and conv + copy > 0)
        if self._jobs is None:
            return
        text, warn = f"Estimated output: ~{size / GB:.1f} GB", False
        if self._free is not None:
            drive, nbytes = self._free
            text += f" · Free on {drive}: {nbytes / GB:.0f} GB"
            if size > nbytes:
                text, warn = text + " — not enough space", True
        self.size_label.setText(text)
        self.size_label.setStyleSheet(f"color: {WARN_COLOR};" if warn else "")

    # ---- convert ----------------------------------------------------------
    def _on_convert(self) -> None:
        groups = self._selected_groups()
        chosen = [j for j in self._jobs if j.group in groups]
        todo = [j for j in chosen if j.action in (Action.CONVERT, Action.COPY)]
        if not todo:
            return
        self._counts = {"converted": 0, "copied": 0, "failed": 0,
                        "done": sum(j.action == Action.DONE for j in chosen)}
        self._failures, self._run_error = [], None
        self.log.clear()
        self.right.setCurrentIndex(1)
        self.progress.setRange(0, len(todo))
        self.progress.setValue(0)
        self.progress.show()
        self.current_label.setText("Starting…")
        for j in chosen:
            if j.action == Action.COLLISION:
                self._on_result(j, "failed", j.note)
        worker = _ConvertWorker(todo, self._scanned[2], self._workers)
        worker.started.connect(self._on_job_started)
        worker.result.connect(self._on_result)
        worker.failed.connect(self._on_convert_failed)
        worker.finished.connect(self._on_convert_finished)
        self._start("converting", worker)

    def _rel(self, job: Job) -> str:
        return job.src.relative_to(self._scanned[0]).as_posix()

    def _on_job_started(self, job: Job) -> None:
        if self._mode == "converting" and self.stop_button.isEnabled():
            self.current_label.setText(self._rel(job))

    def _on_convert_failed(self, message: str) -> None:
        self._run_error = message
        self.log.addItem(f"Error  {message}")
        self.log.item(self.log.count() - 1).setForeground(QBrush(QColor(FAIL_COLOR)))

    def _on_result(self, job: Job, status: str, message: str) -> None:
        self._counts[status] += 1
        rel = self._rel(job)
        if status == "failed":
            self._failures.append(f"{job.src} — {message}")
            self.log.addItem(f"Failed  {rel} — {message}")
            self.log.item(self.log.count() - 1).setForeground(QBrush(QColor(FAIL_COLOR)))
        else:
            self.log.addItem(f"{status.capitalize()}  {rel}")
        if job.action != Action.COLLISION:      # collisions are listed before the run
            self.progress.setValue(self.progress.value() + 1)
        self.log.scrollToBottom()
        c = self._counts
        self.counts_label.setText(f"Converted {c['converted']} · Copied {c['copied']} · "
                                  f"Already done {c['done']} · Failed {c['failed']}")

    def _on_stop(self) -> None:
        if self._mode == "converting":
            self._worker.runner.stop()
            self.stop_button.setEnabled(False)
            self.current_label.setText("Stopping…")

    def _on_convert_finished(self, completed: bool) -> None:
        self._finish_thread()
        failed = self._counts["failed"]
        if self._run_error is not None:
            status = f"Conversion stopped by an error: {self._run_error}"
        elif not completed:
            status = "Stopped — finished files are kept."
        elif failed:
            status = f"Finished — {failed} failed."
        else:
            status = "Finished."
        self.current_label.setText(status)
        self._apply_controls()

    def _copy_failures(self) -> None:
        self._set_clipboard("\n".join(self._failures))

    # ---- closing ----------------------------------------------------------
    def confirm_close(self) -> bool:
        """Ask whether quitting is OK. No side effects. A scan is harmless to
        cancel (shutdown does it), so only a running conversion asks."""
        return self._mode != "converting" or self._confirm("Quit Audio Recorder", CLOSE_MESSAGE)

    def shutdown(self) -> None:
        """Stop the scan or conversion and wait for its thread."""
        worker = self._worker
        if worker is None:
            return
        worker.finished.disconnect()
        worker.failed.disconnect()
        if isinstance(worker, _ScanWorker):
            worker.cancel()
        else:
            worker.started.disconnect()
            worker.result.disconnect()
            worker.runner.stop()
        self._finish_thread()
        self._apply_controls()

    # ---- dialogs (wrapped so tests can replace them) -----------------------
    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.No)
        return answer == QMessageBox.Yes

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)

    def _set_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _pick_folder(self, title: str, start: Path | None) -> Path | None:
        folder = QFileDialog.getExistingDirectory(self, title, str(start or ""))
        return Path(folder) if folder else None
