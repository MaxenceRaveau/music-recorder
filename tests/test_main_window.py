"""MainWindow tests: a fake device provider feeds a paced sine, dialogs are
replaced through the window's wrapper methods, playback uses a fake stream."""
from __future__ import annotations

import threading
import time
import wave

import numpy as np

import pytest
from PySide6.QtCore import QSettings, Qt

from audiorec.gui.main_window import BAD_NAME_MESSAGE, MainWindow
from audiorec.gui.settings import AppSettings
from audiorec.gui.state import State
from audiorec.mp3sink import mp3_rate_message
from audiorec.playback import Player
from tests.fakes import SineSource

RATE = 48000


def device(index, name, rate=RATE, channels=2):
    return {"index": index, "name": name, "defaultSampleRate": float(rate),
            "maxInputChannels": channels}


class FakeProvider:
    def __init__(self, devices, default_index=0):
        self.devices = devices
        self.default_index = default_index
        self.sources: list[SineSource] = []

    def list(self):
        return list(self.devices)

    def default(self):
        return self.devices[self.default_index]

    def open(self, dev):
        src = SineSource(int(dev["defaultSampleRate"]), int(dev["maxInputChannels"]))
        self.sources.append(src)
        return src


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


@pytest.fixture
def make_window(qtbot, tmp_path):
    windows = []

    def make(devices=None, default_index=0, **values):
        qs = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        values.setdefault("last_folder", str(tmp_path / "out"))
        for k, v in values.items():
            qs.setValue(k, v)
        provider = FakeProvider(devices or [device(1, "Speakers")], default_index)
        win = MainWindow(AppSettings(qs), tmp_path / "sessions", provider,
                         player_factory=lambda rate: Player(rate, stream_factory=FakeStream))
        win.calls = []
        win._warn = lambda title, text: win.calls.append(("warn", text))
        win._error = lambda title, text: win.calls.append(("error", text))
        win._confirm = lambda title, text: True
        win._ask_overwrite = lambda path: True
        win.provider = provider
        windows.append(win)
        return win

    yield make
    for win in windows:
        win._confirm = lambda title, text: True
        win.close()


def record(qtbot, win, seconds=0.6):
    win.main_button.click()
    assert win.state is State.RECORDING
    qtbot.waitUntil(lambda: win.session is not None
                    and win.session.frames >= seconds * win.session.rate, timeout=3000)


def stop(qtbot, win):
    win.stop_button.click()
    qtbot.waitUntil(lambda: win.state is State.STOPPED, timeout=3000)


def test_initial_state_ready(make_window):
    win = make_window()
    assert win.state is State.READY
    assert not win.stop_button.isEnabled()
    assert win.main_button.isEnabled()
    assert win.main_button.property("icon_name") == "record"
    assert not win.save_panel.isEnabled()
    assert not win.waveform._hint.isHidden()
    assert win.timer_label.text() == "00:00:00"
    assert win.device_combo.currentText() == "Speakers"
    assert win.format_combo.currentData() == "mp3"


def test_record_pause_resume_stop_flow(qtbot, make_window):
    win = make_window()
    win.main_button.click()
    assert win.state is State.RECORDING
    assert win.main_button.property("icon_name") == "pause"
    assert win.stop_button.isEnabled()
    assert not win.device_combo.isEnabled()
    win.main_button.click()
    assert win.state is State.PAUSED
    assert win.main_button.property("icon_name") == "record"
    win.main_button.click()
    assert win.state is State.RECORDING
    assert win.main_button.property("icon_name") == "pause"
    qtbot.waitUntil(lambda: win.session.frames >= 0.6 * RATE, timeout=3000)
    stop(qtbot, win)
    assert win.main_button.property("icon_name") == "play"
    assert not win.stop_button.isEnabled()
    assert win.save_panel.isEnabled()
    assert win.save_button.isEnabled()
    assert win.format_combo.isEnabled() and not win.device_combo.isEnabled()
    assert win.timer_label.text() == "00:00:00"
    assert win.times_label.text().startswith("start 00:00:00.0 · length 00:00 · end ")
    assert win.calls == []


def test_filename_autofill_until_user_edits(qtbot, make_window):
    win = make_window(last_format="flac")
    record(qtbot, win)
    stop(qtbot, win)
    assert win.filename_edit.text() == win.session.started.strftime("Recording %Y%m%d_%H%M%S.flac")
    qtbot.keyClicks(win.artist_edit, "Band")
    qtbot.keyClicks(win.title_edit, "Song")
    assert win.filename_edit.text() == "Band - Song.flac"
    win.filename_edit.clear()
    qtbot.keyClicks(win.filename_edit, "mine.flac")
    qtbot.keyClicks(win.title_edit, " two")
    assert win.filename_edit.text() == "mine.flac"


def test_title_and_artist_editable_while_recording(qtbot, make_window):
    win = make_window(last_format="flac")
    record(qtbot, win)
    assert win.save_panel.isEnabled()
    assert win.title_edit.isEnabled() and win.artist_edit.isEnabled()
    for w in (win.filename_edit, win.folder_edit, win.browse_button, win.details_toggle,
              win.album_edit, win.discard_button, win.save_button):
        assert not w.isEnabled()
    qtbot.keyClicks(win.artist_edit, "Band")
    qtbot.keyClicks(win.title_edit, "Song")
    qtbot.waitUntil(lambda: win.session.fields.get("title") == "Song", timeout=2000)
    win.main_button.click()
    assert win.state is State.PAUSED and win.title_edit.isEnabled()
    assert not win.save_button.isEnabled()
    win.main_button.click()
    stop(qtbot, win)
    assert win.filename_edit.text() == "Band - Song.flac"
    assert win.filename_edit.isEnabled() and win.album_edit.isEnabled()
    assert win.save_button.isEnabled()


def test_format_change_swaps_extension(qtbot, make_window):
    win = make_window(last_format="flac")
    record(qtbot, win)
    stop(qtbot, win)
    qtbot.keyClicks(win.title_edit, "Song")
    win.format_combo.setCurrentIndex(win.format_combo.findData("wav"))
    assert win.filename_edit.text() == "Song.wav"
    win.filename_edit.clear()
    qtbot.keyClicks(win.filename_edit, "custom.wav")
    win.format_combo.setCurrentIndex(win.format_combo.findData("mp3"))
    assert win.filename_edit.text() == "custom.mp3"


def test_form_fields_saved_to_session_debounced(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    qtbot.keyClicks(win.album_edit, "Live")
    qtbot.waitUntil(lambda: win.session.fields.get("album") == "Live", timeout=2000)


def test_save_writes_file_and_deletes_session(qtbot, make_window, tmp_path):
    win = make_window(last_format="wav")
    record(qtbot, win)
    stop(qtbot, win)
    folder = win.session.folder
    qtbot.keyClicks(win.title_edit, "Take")
    with qtbot.waitSignal(win.export_finished, timeout=5000) as sig:
        win.save_button.click()
    assert sig.args == [True]
    out = tmp_path / "out" / "Take.wav"
    with wave.open(str(out)) as w:
        assert w.getframerate() == RATE
        assert w.getnframes() >= 0.5 * RATE
    assert not folder.exists()
    assert win.state is State.READY
    assert win.session is None
    assert win.title_edit.text() == ""
    assert str(out) in win.statusBar().currentMessage()
    s = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    assert s.last_folder == tmp_path / "out"
    assert s.last_format == "wav"
    assert s.last_device_name == "Speakers"


def test_save_asks_before_overwriting(qtbot, make_window, tmp_path):
    win = make_window(last_format="wav")
    record(qtbot, win)
    stop(qtbot, win)
    (tmp_path / "out").mkdir()
    existing = tmp_path / "out" / win.filename_edit.text()
    existing.write_bytes(b"keep")
    asked = []
    win._ask_overwrite = lambda path: asked.append(path) or False
    win.save_button.click()
    assert asked == [existing]
    assert existing.read_bytes() == b"keep"
    assert win.state is State.STOPPED


def test_save_failure_keeps_session(qtbot, make_window, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a folder")
    win = make_window(last_format="wav", last_folder=str(blocker))
    record(qtbot, win)
    stop(qtbot, win)
    folder = win.session.folder
    with qtbot.waitSignal(win.export_finished, timeout=5000) as sig:
        win.save_button.click()
    assert sig.args == [False]
    assert [kind for kind, _ in win.calls] == ["error"]
    assert win.state is State.STOPPED
    assert folder.exists()
    assert win.save_button.isEnabled()


def test_discard_deletes_session(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    folder = win.session.folder
    win.discard_button.click()
    assert not folder.exists()
    assert win.state is State.READY
    assert not win.save_panel.isEnabled()
    assert not win.waveform._hint.isHidden()


def test_discard_cancelled_keeps_session(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    win._confirm = lambda title, text: False
    win.discard_button.click()
    assert win.session.folder.exists()
    assert win.state is State.STOPPED


def test_device_lost_goes_to_stopped_with_warning(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    win.provider.sources[-1].lose.set()
    qtbot.waitUntil(lambda: win.state is State.STOPPED, timeout=3000)
    assert win.calls[0][0] == "warn"
    assert win.calls[0][1].startswith(
        "Recording stopped: device disconnected — what was recorded is kept.")
    assert win.session.frames > 0
    assert win.save_panel.isEnabled()


def test_missing_remembered_device_falls_back(make_window):
    devs = [device(1, "Speakers"), device(2, "Headphones")]
    win = make_window(devices=devs, default_index=1, last_device_name="Gone")
    assert win.device_combo.currentText() == "Headphones"
    win2 = make_window(devices=devs, default_index=1, last_device_name="Speakers")
    assert win2.device_combo.currentText() == "Speakers"


def test_mp3_disabled_when_rate_unsupported(qtbot, make_window):
    win = make_window(devices=[device(1, "Studio", rate=96000)])
    assert win.format_combo.currentData() == "mp3"
    assert not win.mp3_warning.isHidden()
    assert win.mp3_warning.text() == mp3_rate_message("Studio", 96000)
    record(qtbot, win)
    stop(qtbot, win)
    item = win.format_combo.model().item(win.format_combo.findData("mp3"))
    assert not item.isEnabled()
    assert item.toolTip() == mp3_rate_message("Studio", 96000)
    assert win.format_combo.currentData() == "flac"
    assert win.filename_edit.text().endswith(".flac")
    assert win.save_button.isEnabled()


def test_play_toggles_icon_and_returns_on_finish(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    win.main_button.click()
    assert win.main_button.property("icon_name") == "pause"
    # The real Player calls on_finished on the PortAudio thread.
    win._player.stop()
    t = threading.Thread(target=win._player.on_finished)
    t.start()
    t.join()
    qtbot.waitUntil(lambda: win.main_button.property("icon_name") == "play", timeout=1000)


def test_close_while_recording_keeps_session(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    folder = win.session.folder
    win.close()
    assert win.provider.sources[-1].closed
    assert folder.exists()


def test_discard_after_playing_really_removes_folder(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    win.main_button.click()                     # loads the session's frames into the player
    assert win._player.playing
    assert isinstance(win._player._frames, np.memmap)
    out = np.zeros((100, 2), np.float32)
    win._player._fill(out, 100)                 # the callback body slices the view as-is
    expected = win.session.read_frames(0, 100) / 32768.0
    assert np.allclose(out, expected)
    folder = win.session.folder
    win.discard_button.click()
    assert not folder.exists()                  # no lingering mapping kept the file open


def test_player_load_is_fast_on_long_session(qtbot, make_window):
    win = make_window()
    record(qtbot, win)
    stop(qtbot, win)
    minutes10 = 10 * 60 * RATE
    with open(win.session.folder / "audio.pcm", "ab") as f:
        f.truncate(minutes10 * 4)               # sparse zeros: 10 min of silence
    win.waveform.append_peaks(np.zeros((60_000, 2), np.float32))
    win.waveform.set_editable(True)            # flushes peaks, selects all
    assert win._selection() == (0, minutes10)
    win._player_dirty = True
    t0 = time.perf_counter()
    win._ensure_player_loaded()
    elapsed = time.perf_counter() - t0
    print(f"\n_ensure_player_loaded on 10 min: {elapsed * 1000:.1f} ms")
    assert elapsed < 0.05
    win.discard_button.click()


def type_filename(qtbot, win, name):
    win.filename_edit.clear()
    qtbot.keyClicks(win.filename_edit, name)


def test_save_adds_missing_extension(qtbot, make_window, tmp_path):
    win = make_window(last_format="mp3")
    record(qtbot, win)
    stop(qtbot, win)
    type_filename(qtbot, win, "One More Time")
    with qtbot.waitSignal(win.export_finished, timeout=5000) as sig:
        win.save_button.click()
    assert sig.args == [True]
    assert (tmp_path / "out" / "One More Time.mp3").is_file()
    assert not (tmp_path / "out" / "One More Time").exists()


def test_save_swaps_wrong_extension_and_shows_it(qtbot, make_window, tmp_path):
    win = make_window(last_format="flac")
    record(qtbot, win)
    stop(qtbot, win)
    type_filename(qtbot, win, "Song.mp3")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "Song.flac").write_bytes(b"keep")
    asked = []
    win._ask_overwrite = lambda path: asked.append(path) or False
    win.save_button.click()
    assert asked == [tmp_path / "out" / "Song.flac"]
    assert win.filename_edit.text() == "Song.flac"
    assert win.state is State.STOPPED


def test_save_empty_name_uses_generated_name(qtbot, make_window, tmp_path):
    win = make_window(last_format="wav")
    record(qtbot, win)
    stop(qtbot, win)
    win.filename_edit.selectAll()
    qtbot.keyClick(win.filename_edit, Qt.Key_Backspace)    # a user edit: auto-fill stops
    qtbot.keyClicks(win.title_edit, "Take")
    assert win.filename_edit.text() == ""
    with qtbot.waitSignal(win.export_finished, timeout=5000) as sig:
        win.save_button.click()
    assert sig.args == [True]
    assert (tmp_path / "out" / "Take.wav").is_file()


@pytest.mark.parametrize("name", ["AC/DC - TNT", "What?", r"..\x"])
def test_save_rejects_bad_file_name(qtbot, make_window, tmp_path, name):
    win = make_window(last_format="wav")
    record(qtbot, win)
    stop(qtbot, win)
    type_filename(qtbot, win, name)
    win.save_button.click()
    assert [kind for kind, _ in win.calls] == ["error"]
    assert win.calls[0][1] == BAD_NAME_MESSAGE
    assert win._export_task is None
    assert win.state is State.STOPPED
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())
    assert not (tmp_path / "x.wav").exists()


def test_markers_locked_while_saving_and_restored_on_failure(qtbot, make_window, monkeypatch):
    import audiorec.gui.main_window as mw
    gate = threading.Event()

    def failing_export(*args, **kw):
        gate.wait(3)
        raise OSError("disk full")

    monkeypatch.setattr(mw, "export", failing_export)
    win = make_window(last_format="wav")
    record(qtbot, win, seconds=1.0)
    stop(qtbot, win)
    start, end = win._selection()
    win.waveform.set_selection(start + RATE // 10, end)
    trimmed = win._selection()
    win.main_button.click()                    # play once so a player is loaded
    win.main_button.click()
    win.save_button.click()
    assert win.state is State.SAVING
    assert not win.waveform._editable
    with qtbot.waitSignal(win.export_finished, timeout=5000):
        gate.set()
    assert win.state is State.STOPPED
    assert win.waveform._editable
    assert win._selection() == trimmed
    assert win._player_dirty                   # the next Play reloads the selection


def _slow_export(ok):
    def job(session, start, end, fmt, dest, tags, progress=None):
        time.sleep(0.3)
        if not ok:
            raise OSError("disk full")
        dest.write_bytes(b"audio")
        return dest
    return job


@pytest.mark.parametrize("ok", [True, False])
def test_close_while_saving(qtbot, make_window, monkeypatch, tmp_path, ok):
    import audiorec.gui.main_window as mw
    monkeypatch.setattr(mw, "export", _slow_export(ok))
    win = make_window(last_format="wav")
    record(qtbot, win)
    stop(qtbot, win)
    folder = win.session.folder
    type_filename(qtbot, win, "Take")
    win.save_button.click()
    assert win.state is State.SAVING
    win.close()
    assert (tmp_path / "out" / "Take.wav").exists() is ok
    assert folder.exists() is not ok           # deleted only once it is safely saved


def test_device_fails_to_open_returns_to_ready(qtbot, make_window, tmp_path):
    win = make_window()

    def broken_open(dev):
        raise OSError("Device unavailable")

    win.provider.open = broken_open
    win.main_button.click()
    assert win.state is State.RECORDING
    qtbot.waitUntil(lambda: win.state is State.READY, timeout=3000)
    assert win.session is None
    assert win.calls == [("error", "Device unavailable")]
    sessions = tmp_path / "sessions"
    assert not sessions.exists() or not any(sessions.iterdir())
    assert win.main_button.isEnabled()
    assert win.main_button.property("icon_name") == "record"
    assert not win.stop_button.isEnabled()
    assert win.device_combo.isEnabled()
    assert not win.save_panel.isEnabled()
