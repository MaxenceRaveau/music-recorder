"""AppWindow tests: sidebar navigation, embedded recorder, close arbitration."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLabel, QWidget

from audiorec.gui.app_window import AppWindow
from audiorec.gui.main_window import MainWindow
from audiorec.gui.settings import AppSettings
from audiorec.playback import Player
from tests.fakes import FakeProvider, FakeStream, device


@pytest.fixture
def make_recorder(tmp_path):
    def make():
        qs = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        qs.setValue("last_folder", str(tmp_path / "out"))
        win = MainWindow(AppSettings(qs), tmp_path / "sessions",
                         FakeProvider([device(1, "Speakers")]),
                         player_factory=lambda rate: Player(rate, stream_factory=FakeStream))
        win._confirm = lambda title, text: True
        return win
    return make


def test_sidebar_switches_pages(qtbot, make_recorder):
    other = QLabel("x")
    win = AppWindow(make_recorder(), [("Converter", other)])
    qtbot.addWidget(win)
    assert [win.sidebar.item(i).text() for i in range(win.sidebar.count())] == ["Recorder", "Converter"]
    win.sidebar.setCurrentRow(1)
    assert win.stack.currentWidget() is other


def test_close_refused_by_a_page_keeps_window(qtbot, make_recorder):
    page = QWidget()
    page.request_close = lambda: False
    win = AppWindow(make_recorder(), [("Converter", page)])
    qtbot.addWidget(win)
    win.show()
    assert win.close() is False and win.isVisible()


def test_close_asks_recorder_first_and_accepts(qtbot, make_recorder):
    order = []
    rec = make_recorder()
    rec.request_close = lambda: order.append("recorder") or True
    page = QWidget()
    page.request_close = lambda: order.append("page") or True
    win = AppWindow(rec, [("Converter", page)])
    qtbot.addWidget(win)
    win.show()
    assert win.close() is True
    assert order == ["recorder", "page"]


def test_recorder_declining_stops_before_pages(qtbot, make_recorder):
    rec = make_recorder()
    rec.request_close = lambda: False
    asked = []
    page = QWidget()
    page.request_close = lambda: asked.append(1) or True
    win = AppWindow(rec, [("Converter", page)])
    qtbot.addWidget(win)
    win.show()
    assert win.close() is False and not asked


def test_converter_settings_round_trip(tmp_path):
    ini = str(tmp_path / "s.ini")
    s = AppSettings(QSettings(ini, QSettings.IniFormat))
    assert s.conv_library is None and s.conv_dest is None and s.conv_format == "mp3"
    s.conv_library, s.conv_dest, s.conv_format = tmp_path / "lib", tmp_path / "dst", "aiff"
    s2 = AppSettings(QSettings(ini, QSettings.IniFormat))
    assert (s2.conv_library, s2.conv_dest, s2.conv_format) == (tmp_path / "lib", tmp_path / "dst", "aiff")
    s2.conv_format = "bogus"
    assert s2.conv_format == "mp3"
