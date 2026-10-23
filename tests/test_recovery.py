"""Crash recovery on startup: leftovers are offered one at a time, newest first."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np

from audiorec.gui.state import State
from audiorec.session import PCM_NAME, Session
from tests.test_main_window import make_window  # noqa: F401 - fixture

RATE = 48000
NOW = datetime(2026, 10, 1, 12, 0, 0)


def leftover(win, seconds=1.0, age_h=0, **fields) -> Session:
    s = Session.create(win._root, RATE, "Speakers", now=NOW - timedelta(hours=age_h))
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    x = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
    s.write(np.repeat(x[:, None], 2, axis=1).tobytes())
    s.close()
    if fields:
        s.update_fields(**fields)
    return s


def answers(win, *replies):
    win.asked = []
    queue = list(replies)

    def ask(started, length_s):
        win.asked.append((started, length_s))
        return queue.pop(0)

    win._ask_recovery = ask


def wait_stopped(qtbot, win):
    qtbot.waitUntil(lambda: win.state is State.STOPPED, timeout=3000)


def test_recovery_open_restores_fields_and_stopped(qtbot, make_window):  # noqa: F811
    win = make_window()
    s = leftover(win, title="X", artist="A", album="Alb", year="2020", genre="G", track="3",
                 format="flac", folder=str(win._root / "dest"))
    answers(win, "open")
    win.check_leftovers()
    wait_stopped(qtbot, win)
    assert win.session.folder == s.folder
    assert win.title_edit.text() == "X"
    assert win.artist_edit.text() == "A"
    assert win.album_edit.text() == "Alb"
    assert win.year_edit.text() == "2020"
    assert win.genre_edit.text() == "G"
    assert win.track_edit.text() == "3"
    assert win.format_combo.currentData() == "flac"
    assert win.folder_edit.text() == str(win._root / "dest")
    assert win.filename_edit.text().endswith(".flac")
    assert "X" in win.filename_edit.text()
    assert win.waveform.selection() == (0, s.frames)
    assert win.save_button.isEnabled()
    assert win.main_button.property("icon_name") == "play"
    assert win.asked[0][1] == 1.0


def test_recovery_delete_removes_folder(make_window):  # noqa: F811
    win = make_window()
    s = leftover(win)
    answers(win, "delete")
    win.check_leftovers()
    assert not s.folder.exists()
    assert win.state is State.READY


def test_recovery_delete_declined_asks_again(make_window):  # noqa: F811
    win = make_window()
    s = leftover(win)
    answers(win, "delete", "later")
    confirms = iter([False])
    win._confirm = lambda title, text: next(confirms)
    win.check_leftovers()
    assert len(win.asked) == 2
    assert s.folder.exists()


def test_recovery_later_keeps_folder(make_window):  # noqa: F811
    win = make_window()
    s = leftover(win)
    answers(win, "later")
    win.check_leftovers()
    assert (s.folder / PCM_NAME).exists()
    assert win.state is State.READY and win.session is None


def test_no_leftovers_no_dialog(make_window):  # noqa: F811
    win = make_window()
    answers(win)
    win.check_leftovers()
    assert win.asked == []


def test_check_does_nothing_when_not_ready(qtbot, make_window):  # noqa: F811
    win = make_window()
    win.main_button.click()
    answers(win)
    win.check_leftovers()
    assert win.asked == []
    assert win.session.folder.exists()      # the fresh, empty session was not scanned away


def test_two_leftovers_second_asked_after_first_saved(qtbot, make_window):  # noqa: F811
    win = make_window()
    newer = leftover(win, age_h=0, title="new")
    older = leftover(win, age_h=1, title="old")
    answers(win, "open", "later")
    win.check_leftovers()
    wait_stopped(qtbot, win)
    assert len(win.asked) == 1 and win.session.folder == newer.folder
    # A session started meanwhile must not be rescanned away: only the saved list is used.
    win._root.joinpath("later_empty").mkdir()
    win.filename_edit.setText("out.wav")
    win.format_combo.setCurrentIndex(win.format_combo.findData("wav"))
    win.filename_edit.setText("out.wav")
    with qtbot.waitSignal(win.export_finished, timeout=5000):
        win.save_button.click()
    assert len(win.asked) == 2
    assert win.asked[1][0] == older.started
    assert older.folder.exists()
    assert win._root.joinpath("later_empty").exists()
    assert win.state is State.READY


def test_later_skips_only_that_session(qtbot, make_window):  # noqa: F811
    win = make_window()
    leftover(win, age_h=0, title="new")
    older = leftover(win, age_h=1, title="old")
    answers(win, "later", "open")
    win.check_leftovers()
    wait_stopped(qtbot, win)
    assert win.session.folder == older.folder
    assert win.title_edit.text() == "old"


def test_discard_then_next_leftover_asked(qtbot, make_window):  # noqa: F811
    win = make_window()
    leftover(win, age_h=0)
    older = leftover(win, age_h=1)
    answers(win, "open", "later")
    win.check_leftovers()
    wait_stopped(qtbot, win)
    win.discard_button.click()
    assert len(win.asked) == 2 and win.asked[1][0] == older.started


def test_leftover_scan_runs_once(make_window):  # noqa: F811
    win = make_window()
    leftover(win)
    answers(win, "later")
    win.check_leftovers()
    win.check_leftovers()
    assert len(win.asked) == 1
