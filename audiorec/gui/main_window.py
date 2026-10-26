"""
The recorder's one window: source/format, transport, waveform and save panel.
Every event goes through state.next_state(), and controls_for() decides which
widgets are live. Capture runs on a QThread, export on the thread pool, and
playback ends arrive from the PortAudio thread through a Qt signal.
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

import pyaudiowpatch as pyaudio
from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
                               QMainWindow, QMessageBox, QProgressBar, QProgressDialog, QSizePolicy, QStyle,
                               QToolButton, QVBoxLayout, QWidget)

from audiorec.capture import ChunkSource, LoopbackSource, StopReason, list_loopback_devices
from audiorec.export import export, format_allowed, selection_ok
from audiorec.gui.recorder_worker import RecorderWorker
from audiorec.gui.save_panel import SavePanel
from audiorec.gui.settings import AppSettings
from audiorec.gui.state import Event, State, controls_for, next_state
from audiorec.gui.waveform import WaveformView
from audiorec.mp3sink import MP3_RATES, mp3_rate_message
from audiorec.naming import build_filename, is_valid_filename, with_extension
from audiorec.playback import Player
from audiorec.peaks import peaks_from_file
from audiorec.session import PCM_NAME, Session, find_leftovers

FORMAT_LABELS = (("mp3", "MP3 320 kbps"), ("flac", "FLAC"), ("wav", "WAV"))
STOP_MESSAGES = {
    "device_lost": "Recording stopped: device disconnected — what was recorded is kept.",
    "write_error": "Recording stopped: could not write to disk — what was recorded is kept.",
}
CLOSE_MESSAGES = {
    State.RECORDING: "A recording is in progress. Stop it and quit?\n"
                     "What was recorded is kept and can be recovered next time.",
    State.STOPPED: "This recording has not been saved. Quit anyway?\n"
                   "It is kept and can be recovered next time.",
    State.SAVING: "Saving is in progress. Quit once it finishes?",
}
CLOSE_MESSAGES[State.PAUSED] = CLOSE_MESSAGES[State.RECORDING]
BAD_NAME_MESSAGE = r"""A file name can't be ".." or contain \ / : * ? " < > |"""
METER_FLOOR_DB = -60
MAIN_TIPS = {"record": "Record", "pause": "Pause", "play": "Play selection"}

STYLE = """
QToolButton#mainButton { border: 1px solid palette(mid); border-radius: 28px;
                         background: palette(button); }
QToolButton#mainButton:hover, QToolButton#stopButton:hover { background: palette(midlight); }
QToolButton#stopButton { border: 1px solid palette(mid); border-radius: 4px;
                         background: palette(button); }
QLabel#timerLabel { font-family: Consolas, "Courier New", monospace; font-size: 22px; }
QLabel#peakLabel, QLabel#timesLabel { font-family: Consolas, "Courier New", monospace;
                                      color: palette(placeholder-text); }
QLabel#mp3Warning { color: #b35c00; }
QProgressBar#levelBar { max-height: 10px; }
"""


def hms(frames: int, rate: int) -> str:
    s = frames // rate
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def hms_tenths(frames: int, rate: int) -> str:
    s, tenth = divmod(frames * 10 // rate, 10)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}.{tenth}"


def mmss(frames: int, rate: int) -> str:
    s = frames // rate
    return f"{s // 60:02d}:{s % 60:02d}"


class DeviceProvider(Protocol):
    def list(self) -> list[dict]: ...
    def default(self) -> dict: ...
    def open(self, dev: dict) -> ChunkSource: ...


class PyAudioDeviceProvider:
    """One PyAudio instance for the app's lifetime; call close() on quit."""

    def __init__(self) -> None:
        self._p = pyaudio.PyAudio()

    def list(self) -> list[dict]:
        return list_loopback_devices(self._p)

    def default(self) -> dict:
        return self._p.get_default_wasapi_loopback()

    def open(self, dev: dict) -> LoopbackSource:
        return LoopbackSource(self._p, dev)

    def close(self) -> None:
        self._p.terminate()


class _ExportSignals(QObject):
    progress = Signal(float)
    done = Signal(object)       # Path
    failed = Signal(str)


class _ExportTask(QRunnable):
    def __init__(self, job: Callable[[Callable[[float], None]], Path]) -> None:
        super().__init__()
        self.setAutoDelete(False)       # the window owns it; avoids a dangling wrapper
        self.signals = _ExportSignals()
        self._job = job
        self.succeeded = False      # read after waitForDone(), when signals are disconnected

    def run(self) -> None:
        try:
            path = self._job(self.signals.progress.emit)
        except Exception as e:  # noqa: BLE001 - anything uncaught would leave SAVING forever
            self.signals.failed.emit(str(e) or type(e).__name__)
            return
        self.succeeded = True
        self.signals.done.emit(path)


class _PlayerBridge(QObject):
    finished = Signal()         # emitted on the PortAudio thread, delivered queued


def _record_icon(size: int = 28) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#d9342b"))
    p.drawEllipse(4, 4, size - 8, size - 8)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    export_finished = Signal(bool)

    def __init__(self, settings: AppSettings, session_root: Path,
                 device_provider: DeviceProvider, *,
                 player_factory: Callable[[int], Player] = Player) -> None:
        super().__init__()
        self._settings = settings
        self._root = Path(session_root)
        self._provider = device_provider
        self._player_factory = player_factory
        self.state = State.READY
        self.session: Session | None = None
        self._devices: list[dict] = []
        self._thread: QThread | None = None
        self._worker: RecorderWorker | None = None
        self._stopping = False
        self._player: Player | None = None
        self._player_dirty = True
        self._play_from = 0
        self._export_task: _ExportTask | None = None
        self._export_params: tuple[Path, str] | None = None
        self._saving_selection = (0, 0)
        self._pending_leftovers: list[Session] = []
        self._leftovers_checked = False
        self._recovery_task: _ExportTask | None = None     # peak rebuild of a recovered session
        self._recovery_dialog: QProgressDialog | None = None

        self._player_bridge = _PlayerBridge(self)
        self._player_bridge.finished.connect(self._on_play_finished)
        self._fields_timer = self._timer(500, self._save_fields, single=True)
        self._playhead_timer = self._timer(30, self._update_playhead)
        # Marker drags fire many selection changes; re-read audio once they settle.
        self._reload_timer = self._timer(150, self._reload_while_playing, single=True)

        self.setWindowTitle("Audio Recorder")
        self._build_ui()
        self._load_devices()
        self.format_combo.setCurrentIndex(self.format_combo.findData(settings.last_format))
        self.folder_edit.setText(str(settings.last_folder))
        self._update_format_options()

    # ---- construction -----------------------------------------------------
    def _timer(self, ms: int, slot: Callable[[], None], single: bool = False) -> QTimer:
        t = QTimer(self)
        t.setInterval(ms)
        t.setSingleShot(single)
        t.timeout.connect(slot)
        return t

    def _build_ui(self) -> None:
        self.setStyleSheet(STYLE)
        style = self.style()
        self._icons = {"record": _record_icon(),
                       "pause": style.standardIcon(QStyle.SP_MediaPause),
                       "play": style.standardIcon(QStyle.SP_MediaPlay)}
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 8)
        lay.setSpacing(10)
        self.setCentralWidget(root)

        top = QHBoxLayout()
        self.device_combo = QComboBox()
        self.device_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.format_combo = QComboBox()
        for fmt, label in FORMAT_LABELS:
            self.format_combo.addItem(label, fmt)
        self.format_combo.setItemData(self.format_combo.findData("wav"),
                                      "WAV tags are not read by every player.", Qt.ToolTipRole)
        top.addWidget(QLabel("Source"))
        top.addWidget(self.device_combo, 1)
        top.addSpacing(12)
        top.addWidget(QLabel("Format"))
        top.addWidget(self.format_combo)
        lay.addLayout(top)
        self.mp3_warning = QLabel(objectName="mp3Warning", wordWrap=True)
        self.mp3_warning.hide()
        lay.addWidget(self.mp3_warning)

        transport = QHBoxLayout()
        transport.setSpacing(10)
        self.main_button = QToolButton(objectName="mainButton")
        self.main_button.setFixedSize(56, 56)
        self.main_button.setIconSize(self.main_button.size() / 2)
        self.stop_button = QToolButton(objectName="stopButton", toolTip="Stop")
        self.stop_button.setFixedSize(40, 40)
        self.stop_button.setIcon(style.standardIcon(QStyle.SP_MediaStop))
        self.timer_label = QLabel("00:00:00", objectName="timerLabel")
        self.level_bar = QProgressBar(objectName="levelBar", textVisible=False)
        self.level_bar.setRange(METER_FLOOR_DB, 0)
        self.peak_label = QLabel(objectName="peakLabel")
        self.peak_label.setMinimumWidth(self.peak_label.fontMetrics().horizontalAdvance("-60.0 dBFS") + 6)
        for w in (self.main_button, self.stop_button, self.timer_label):
            transport.addWidget(w)
        transport.addSpacing(8)
        transport.addWidget(self.level_bar, 1)
        transport.addWidget(self.peak_label)
        lay.addLayout(transport)

        self.waveform = WaveformView()
        self.times_label = QLabel(objectName="timesLabel", alignment=Qt.AlignCenter)
        lay.addWidget(self.waveform, 1)
        lay.addWidget(self.times_label)

        lay.addWidget(self._build_save_panel())
        self._progress = QProgressBar(maximumWidth=160, textVisible=False)
        self._progress.setRange(0, 100)
        self._progress.hide()
        self.statusBar().addPermanentWidget(self._progress)
        self._reset_meter()
        self.resize(780, 600)

        self.main_button.clicked.connect(self._on_main)
        self.stop_button.clicked.connect(self._on_stop)
        self.device_combo.currentIndexChanged.connect(self._update_format_options)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.waveform.selectionChanged.connect(self._on_selection)
        self.waveform.seekRequested.connect(self._on_seek)

    def _build_save_panel(self) -> SavePanel:
        self.save_panel = panel = SavePanel()
        # Flat aliases: the window's public widget names (tests drive these).
        for name in ("title_edit", "artist_edit", "filename_edit", "folder_edit", "browse_button",
                     "details_toggle", "album_edit", "year_edit", "genre_edit", "track_edit",
                     "cover_edit", "cover_button", "discard_button", "save_button"):
            setattr(self, name, getattr(panel, name))
        panel.edited.connect(self._schedule_fields_save)
        self.browse_button.clicked.connect(self._on_browse)
        self.cover_button.clicked.connect(self._on_choose_cover)
        self.discard_button.clicked.connect(self._on_discard)
        self.save_button.clicked.connect(self._on_save)
        return panel

    def _load_devices(self) -> None:
        try:
            self._devices = list(self._provider.list())
        except Exception:  # noqa: BLE001 - no devices is shown as a disabled Record
            self._devices = []
        for dev in self._devices:
            self.device_combo.addItem(dev["name"])
        names = [d["name"] for d in self._devices]
        want = self._settings.last_device_name
        if want in names:
            idx = names.index(want)
        else:  # missing remembered device: fall back to the default silently
            try:
                default = self._provider.default()
            except Exception:  # noqa: BLE001
                default = {}
            idx = next((i for i, d in enumerate(self._devices)
                        if d.get("index") == default.get("index")), 0)
        if self._devices:
            self.device_combo.setCurrentIndex(idx)

    # ---- state ------------------------------------------------------------
    def _fire(self, event: Event) -> None:
        self.state = next_state(self.state, event)
        self._update_format_options()

    def _format(self) -> str:
        return self.format_combo.currentData()

    def _device(self) -> dict | None:
        i = self.device_combo.currentIndex()
        return self._devices[i] if 0 <= i < len(self._devices) else None

    def _rate_and_name(self) -> tuple[int, str]:
        if self.session is not None:
            return self.session.rate, self.session.device_name
        dev = self._device()
        return (int(dev["defaultSampleRate"]), dev["name"]) if dev else (0, "")

    def _selection(self) -> tuple[int, int]:
        start, end = self.waveform.selection()
        # Peak blocks round up, so the view can reach a few frames past the audio.
        return start, min(end, self.session.frames if self.session else 0)

    def _playing(self) -> bool:
        return self._player is not None and self._player.playing

    def _update_format_options(self) -> None:
        """Disable MP3 after recording at a rate it can't take; warn about it before."""
        rate, name = self._rate_and_name()
        msg = mp3_rate_message(name, rate) if rate and rate not in MP3_RATES else ""
        locked = bool(msg) and self.session is not None
        mp3 = self.format_combo.findData("mp3")
        item = self.format_combo.model().item(mp3)
        item.setEnabled(not locked)
        item.setToolTip(msg if locked else "")
        self.mp3_warning.setText(msg)
        if locked and self.format_combo.currentIndex() == mp3:
            self.format_combo.setCurrentIndex(self.format_combo.findData("flac"))
        self._apply_controls()

    def _apply_controls(self) -> None:
        rate, _ = self._rate_and_name()
        sel_ok = self.session is not None and selection_ok(*self._selection(), self.session.rate)
        fmt_ok = bool(rate) and format_allowed(self._format(), rate)
        c = controls_for(self.state, selection_ok=sel_ok, format_ok=fmt_ok, playing=self._playing())
        if self.main_button.property("icon_name") != c.main_icon:
            self.main_button.setIcon(self._icons[c.main_icon])
            self.main_button.setProperty("icon_name", c.main_icon)
        tip = "Resume" if self.state is State.PAUSED else MAIN_TIPS[c.main_icon]
        self.main_button.setToolTip(tip)
        no_device = self.state is State.READY and (not self._devices or self._recovery_task is not None)
        self.main_button.setEnabled(c.main_enabled and not self._stopping and not no_device)
        self.stop_button.setEnabled(c.stop_enabled and not self._stopping)
        self.device_combo.setEnabled(c.source_enabled)
        self.format_combo.setEnabled(c.format_enabled)
        capturing = self.state in (State.RECORDING, State.PAUSED)
        self.save_panel.setEnabled(c.save_panel_enabled or capturing)
        self.save_panel.set_names_only(capturing)
        self.save_button.setEnabled(c.save_enabled)
        self.mp3_warning.setVisible(self.state is State.READY and self._format() == "mp3"
                                    and bool(self.mp3_warning.text()))

    # ---- recording --------------------------------------------------------
    def _on_main(self) -> None:
        if self.state is State.READY:
            self._start_recording()
        elif self.state is State.RECORDING and self._worker:
            self._worker.pause()
            self._fire(Event.MAIN)
        elif self.state is State.PAUSED and self._worker:
            self._worker.resume()
            self._fire(Event.MAIN)
        elif self.state is State.STOPPED:
            self._toggle_play()

    def _start_recording(self) -> None:
        dev = self._device()
        if dev is None:
            return
        rate, channels = int(dev["defaultSampleRate"]), int(dev["maxInputChannels"])
        try:
            session = Session.create(self._root, rate, dev["name"])
        except OSError as e:
            self._error("Cannot start recording", str(e))
            return
        self.session = session
        self.waveform.clear()
        self.waveform.set_rate(rate)
        provider = self._provider
        worker = RecorderWorker(lambda: provider.open(dev), session, rate, channels)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.peaks.connect(self.waveform.append_peaks)
        worker.status.connect(self._on_status)
        worker.finished.connect(self._on_capture_finished)
        self._thread, self._worker = thread, worker
        self._fire(Event.MAIN)
        thread.start()

    def _on_status(self, frames: int, peak_db: float) -> None:
        if self.session is None:
            return
        self.timer_label.setText(hms(int(frames), self.session.rate))
        if math.isfinite(peak_db):
            self.level_bar.setValue(max(METER_FLOOR_DB, min(0, round(peak_db))))
            self.peak_label.setText(f"{peak_db:5.1f} dBFS")
        else:
            self._reset_meter()

    def _reset_meter(self) -> None:
        self.level_bar.setValue(METER_FLOOR_DB)
        self.peak_label.setText("  -∞ dBFS")

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._stopping = True       # wait for the worker's last peaks and finished
            self._worker.stop()
            self._apply_controls()

    def _finish_thread(self) -> None:
        thread, self._thread, self._worker = self._thread, None, None
        if thread is not None:
            thread.quit()
            thread.wait()

    def _on_capture_finished(self, reason: StopReason) -> None:
        self._finish_thread()
        self._stopping = False
        if reason.kind != "user" and self.session.frames == 0:
            self._abort_failed_start(reason)
            return
        self._fire(Event.STOP if reason.kind == "user" else Event.CAPTURE_ENDED)
        self.waveform.set_editable(True)
        self._reset_meter()
        self._release_player()
        self._play_from = self._selection()[0]
        self.save_panel.start_naming(self.session.started, self._format())
        self._update_times()
        self._apply_controls()
        if reason.kind != "user":
            text = STOP_MESSAGES.get(reason.kind, STOP_MESSAGES["device_lost"])
            if reason.message:
                text += f"\n\n{reason.message}"
            self._warn("Recording stopped", text)

    def _abort_failed_start(self, reason: StopReason) -> None:
        """The device never delivered audio (it failed to open): drop the empty
        session, which holds nothing worth recovering, and go back to READY."""
        self._delete_session()
        # The state machine has no RECORDING -> READY edge, and it should not: a
        # capture that recorded anything always ends in STOPPED. With nothing
        # recorded there is nothing to stop on, so we set the state directly.
        self.state = State.READY
        self._reset_to_ready()
        self._error("Could not start recording",
                    reason.message or STOP_MESSAGES.get(reason.kind, STOP_MESSAGES["device_lost"]))

    # ---- selection and playback ------------------------------------------
    def _update_times(self) -> None:
        if self.session is None or self.state not in (State.STOPPED, State.SAVING):
            self.times_label.setText("")
            return
        rate = self.session.rate
        start, end = self._selection()
        self.times_label.setText(f"start {hms_tenths(start, rate)} · length "
                                 f"{mmss(end - start, rate)} · end {hms_tenths(end, rate)}")

    def _on_selection(self, start: int, end: int) -> None:
        if self.state is not State.STOPPED:
            return
        self._player_dirty = True
        if self._playing():
            self._reload_timer.start()
        self._update_times()
        self._apply_controls()

    def _ensure_player_loaded(self) -> Player:
        if self._player is None:
            self._player = self._player_factory(self.session.rate)
            self._player.on_finished = self._player_bridge.finished.emit
        if self._player_dirty:
            start, end = self._selection()
            # A memory-mapped view: no copy, so this stays instant on long takes.
            self._player.load(self.session.frames_view(start, end), start)
            self._player_dirty = False
        return self._player

    def _play(self, frame: int) -> None:
        start, end = self._selection()
        if not start <= frame < end:
            frame = start
        try:
            self._ensure_player_loaded().play(from_frame=frame)
        except Exception as e:  # noqa: BLE001 - no output device, driver errors
            self._error("Playback failed", str(e) or type(e).__name__)
            return
        self._playhead_timer.start()

    def _toggle_play(self) -> None:
        if self._playing():
            self._player.pause()
            self._play_from = self._player.position
            self._playhead_timer.stop()
        else:
            self._play(self._play_from)
        self._apply_controls()

    def _reload_while_playing(self) -> None:
        if self._playing() and self.state is State.STOPPED:
            self._play(self._player.position)

    def _on_seek(self, frame: int) -> None:
        if self.state is not State.STOPPED:
            return
        if self._playing():
            self._play(frame)
        else:
            self._play_from = frame
            self.waveform.set_playhead(frame)

    def _update_playhead(self) -> None:
        if self._player is not None:
            self.waveform.set_playhead(self._player.position)
        if not self._playing():
            self._playhead_timer.stop()

    def _on_play_finished(self) -> None:
        self._playhead_timer.stop()
        if self.state is State.STOPPED:
            self._play_from = self._selection()[0]
            self.waveform.set_playhead(self._play_from)
        self._apply_controls()

    def _stop_playback(self) -> None:
        self._playhead_timer.stop()
        self._reload_timer.stop()
        if self._player is not None:
            self._player.stop()

    def _release_player(self) -> None:
        """Drop the player and its memory-mapped frames; Windows won't delete a mapped file."""
        self._stop_playback()
        if self._player is not None:
            self._player.unload()
        self._player, self._player_dirty = None, True

    # ---- save panel -------------------------------------------------------
    def _on_format_changed(self) -> None:
        if self.session is not None:
            self.save_panel.set_format(self._format())
            self._schedule_fields_save()
        self._apply_controls()

    def _form_fields(self) -> dict[str, str]:
        return {**self.save_panel.fields(), "format": self._format()}

    def _schedule_fields_save(self) -> None:
        if self.session is not None and self.state in (State.RECORDING, State.PAUSED, State.STOPPED):
            self._fields_timer.start()

    def _save_fields(self) -> None:
        if self.session is None:
            return
        try:
            self.session.update_fields(**self._form_fields())
        except OSError as e:
            self.statusBar().showMessage(f"Could not save the form to the session: {e}", 5000)

    def _on_browse(self) -> None:
        folder = self._pick_folder(self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _on_choose_cover(self) -> None:
        path = self._pick_cover()
        if path:
            self.cover_edit.setText(path)

    def _on_save(self) -> None:
        if self.state is not State.STOPPED or self.session is None:
            return
        fmt = self._format()
        name = self.filename_edit.text().strip() or build_filename(
            self.artist_edit.text(), self.title_edit.text(), fmt, self.session.started)
        if not is_valid_filename(name):
            self._error("Cannot save", BAD_NAME_MESSAGE)
            return
        name = with_extension(name, fmt)
        self.filename_edit.setText(name)        # show what is actually written
        folder = Path(self.folder_edit.text().strip() or self._settings.last_folder)
        dest = folder / name
        if dest.exists() and not self._ask_overwrite(dest):
            return
        session, tags = self.session, self.save_panel.tags()
        start, end = self._selection()
        self._saving_selection = self.waveform.selection()
        self.waveform.set_editable(False)       # no marker drags while the take is exported
        self._stop_playback()
        self._fields_timer.stop()
        self._save_fields()

        def job(progress: Callable[[float], None]) -> Path:
            folder.mkdir(parents=True, exist_ok=True)   # created lazily, at save time
            return export(session, start, end, fmt, dest, tags, progress=progress)

        task = _ExportTask(job)
        task.signals.progress.connect(self._on_export_progress)
        task.signals.done.connect(self._on_export_done)
        task.signals.failed.connect(self._on_export_failed)
        self._export_task, self._export_params = task, (folder, fmt)
        self._fire(Event.SAVE)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._progress.setValue(0)
        self._progress.show()
        self.statusBar().showMessage(f"Saving {dest.name}…")
        QThreadPool.globalInstance().start(task)

    def _on_export_progress(self, fraction: float) -> None:
        self._progress.setValue(round(fraction * 100))

    def _end_export(self) -> None:
        QApplication.restoreOverrideCursor()
        self._progress.hide()
        self._export_task = None

    def _on_export_done(self, path: Path) -> None:
        self._end_export()
        folder, fmt = self._export_params
        self._remember_and_delete(folder, fmt)
        self._fire(Event.SAVED)
        self._reset_to_ready()
        self.statusBar().showMessage(f"Saved {path}")
        self.export_finished.emit(True)
        self._offer_pending_leftovers()

    def _remember_and_delete(self, folder: Path, fmt: str) -> None:
        """The export is on disk: remember its settings and drop the session."""
        self._settings.last_folder = folder
        self._settings.last_format = fmt
        self._settings.last_device_name = self.session.device_name
        self._delete_session()

    def _on_export_failed(self, message: str) -> None:
        self._end_export()
        self._fire(Event.EXPORT_FAILED)
        # set_editable(True) selects everything, so put the user's trim back.
        self.waveform.set_editable(True)
        self.waveform.set_selection(*self._saving_selection)
        self._player_dirty = True
        self._update_times()
        self.statusBar().clearMessage()
        self._error("Save failed", message)
        self.export_finished.emit(False)

    def _on_discard(self) -> None:
        if self.state is not State.STOPPED:
            return
        if not self._confirm("Discard recording", "Delete this recording? This cannot be undone."):
            return
        self._delete_session()
        self._fire(Event.DISCARDED)
        self._reset_to_ready()
        self.statusBar().showMessage("Recording discarded", 5000)
        self._offer_pending_leftovers()

    def _delete_session(self) -> None:
        # Only in STOPPED/SAVING, so the worker is done; stop everything else
        # that could still touch the folder.
        self._fields_timer.stop()
        self._release_player()
        self.session.delete()
        self.session = None

    def _reset_to_ready(self) -> None:
        self.waveform.clear()
        self.save_panel.reset()
        self.timer_label.setText("00:00:00")
        self._reset_meter()
        self._update_times()
        self._update_format_options()

    # ---- crash recovery ---------------------------------------------------
    def check_leftovers(self) -> None:
        """Offer sessions left by a crash. Scans the disk once, at startup only: a
        session created later has an empty pcm file that find_leftovers would delete."""
        if self._leftovers_checked or self.state is not State.READY or self.session is not None:
            return
        self._leftovers_checked = True
        self._pending_leftovers = find_leftovers(self._root)
        self._offer_pending_leftovers()

    def _offer_pending_leftovers(self) -> None:
        """Ask about the remaining leftovers, newest first, until one is opened."""
        while self._pending_leftovers:
            if (self.state is not State.READY or self.session is not None
                    or self._recovery_task is not None):
                return
            s = self._pending_leftovers.pop(0)
            answer = self._ask_recovery(s.started, s.frames / s.rate)
            if answer == "open":
                self._open_leftover(s)
                return
            if answer == "delete":
                if self._confirm("Delete recording", "Delete this recording? This cannot be undone."):
                    s.delete()
                else:
                    self._pending_leftovers.insert(0, s)    # not confirmed: ask again
            # "later": leave it on disk and move on to the next one

    def _open_leftover(self, session: Session) -> None:
        pcm = session.folder / PCM_NAME
        task = _ExportTask(lambda progress: peaks_from_file(pcm, session.rate, progress))
        dialog = QProgressDialog("Loading the recording…", "", 0, 100, self)
        dialog.setCancelButton(None)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(1000)         # only appears for slow rebuilds
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        task.signals.progress.connect(lambda f: dialog.setValue(round(f * 100)))
        task.signals.done.connect(lambda peaks: self._on_recovery_done(session, peaks))
        task.signals.failed.connect(lambda msg: self._on_recovery_failed(msg))
        self._recovery_task, self._recovery_dialog = task, dialog
        self._apply_controls()
        QThreadPool.globalInstance().start(task)

    def _end_recovery(self) -> None:
        if self._recovery_dialog is not None:
            self._recovery_dialog.close()
            self._recovery_dialog.deleteLater()
        self._recovery_task = self._recovery_dialog = None

    def _on_recovery_failed(self, message: str) -> None:
        self._end_recovery()
        self._apply_controls()
        self._error("Cannot open recording", message)   # the session stays on disk
        self._offer_pending_leftovers()

    def _on_recovery_done(self, session: Session, peaks) -> None:
        self._end_recovery()
        self._enter_recovered_session(session, peaks)

    def _enter_recovered_session(self, session: Session, peaks) -> None:
        self.session = session
        self.waveform.clear()
        self.waveform.set_rate(session.rate)
        self.waveform.append_peaks(peaks)
        self.waveform.set_editable(True)
        f = session.fields
        for name in ("title", "artist", "album", "year", "genre", "track", "cover"):
            getattr(self, f"{name}_edit").setText(f.get(name, ""))
        if f.get("folder"):
            self.folder_edit.setText(f["folder"])
        fmt_idx = self.format_combo.findData(f.get("format"))
        if fmt_idx >= 0:
            self.format_combo.setCurrentIndex(fmt_idx)
        self.save_panel.start_naming(session.started, self._format())
        if f.get("filename"):
            self.filename_edit.setText(f["filename"])
        # The state machine has no READY -> STOPPED edge, and it should not: a
        # recording only ever reaches STOPPED by being captured here. A recovered
        # session arrives already captured, so we set the state directly.
        self.state = State.STOPPED
        self._release_player()
        self._play_from = self._selection()[0]
        self._update_times()
        self._update_format_options()       # applies the controls too

    # ---- closing ----------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._recovery_task is not None:     # still rebuilding peaks: drop the result
            for sig in (self._recovery_task.signals.done, self._recovery_task.signals.failed):
                sig.disconnect()
            QThreadPool.globalInstance().waitForDone()
            self._end_recovery()
        if self.state is not State.READY:
            if not self._confirm("Quit Audio Recorder", CLOSE_MESSAGES[self.state]):
                event.ignore()
                return
            self._shutdown()
        super().closeEvent(event)

    def _shutdown(self) -> None:
        """Stop background work but keep the session on disk for recovery."""
        if self._worker is not None:
            self._worker.finished.disconnect(self._on_capture_finished)
            self._worker.stop()
            self._finish_thread()
        if self._export_task is not None:
            for sig in (self._export_task.signals.done, self._export_task.signals.failed):
                sig.disconnect()
            QThreadPool.globalInstance().waitForDone()
            succeeded = self._export_task.succeeded
            self._end_export()
            if succeeded:                       # saved: nothing left to recover
                self._remember_and_delete(*self._export_params)
        self._release_player()
        if self._fields_timer.isActive():
            self._fields_timer.stop()
            self._save_fields()

    # ---- dialogs (wrapped so tests can replace them) -----------------------
    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.No)
        return answer == QMessageBox.Yes

    def _warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self, title, text)

    def _error(self, title: str, text: str) -> None:
        QMessageBox.critical(self, title, text)

    def _ask_overwrite(self, path: Path) -> bool:
        box = QMessageBox(QMessageBox.Question, "File exists",
                          f"{path.name} already exists in {path.parent}.\nOverwrite it?",
                          parent=self)
        overwrite = box.addButton("Overwrite", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        return box.clickedButton() is overwrite

    def _ask_recovery(self, started: datetime, length_s: float) -> str:
        """Returns "open", "delete" or "later"."""
        box = QMessageBox(QMessageBox.Question, "Unsaved recording",
                          f"An unsaved recording from {started:%Y-%m-%d %H:%M} "
                          f"({int(length_s) // 60:02d}:{int(length_s) % 60:02d}) was found.",
                          parent=self)
        open_btn = box.addButton("Open", QMessageBox.AcceptRole)
        delete_btn = box.addButton("Delete", QMessageBox.DestructiveRole)
        later_btn = box.addButton("Later", QMessageBox.RejectRole)
        box.setEscapeButton(later_btn)
        box.exec()
        clicked = box.clickedButton()
        return "open" if clicked is open_btn else "delete" if clicked is delete_btn else "later"

    def _pick_folder(self, start: str) -> str:
        return QFileDialog.getExistingDirectory(self, "Save to", start)

    def _pick_cover(self) -> str:
        path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", "",
                                              "Images (*.jpg *.jpeg *.png)")
        return path
