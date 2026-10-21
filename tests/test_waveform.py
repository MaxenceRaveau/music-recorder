import time

import numpy as np

from audiorec.gui.waveform import HINT_TEXT, WaveformView


def make(qtbot, rate=1000):
    w = WaveformView()
    qtbot.addWidget(w)
    w.show()
    w.set_rate(rate)
    return w


def test_clear_shows_hint(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((10, 2), np.float32))
    assert not w._hint.isVisible()
    w.clear()
    assert w._hint.isVisible()
    assert w._hint.text() == HINT_TEXT
    assert w.minimumHeight() >= 170


def test_append_then_selection_spans_all(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    assert w.selection() == (0, 1000)


def test_set_selection_emits_signal(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    with qtbot.waitSignal(w.selectionChanged) as sig:
        w.set_selection(100, 800)
    assert sig.args == [100, 800]
    assert w.selection() == (100, 800)


def test_markers_cannot_cross(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    w.set_selection(900, 100)
    s, e = w.selection()
    assert e - s >= 500 and 0 <= s and e <= 1000


def test_selection_clamped_to_total_and_short_total(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    w.set_selection(-50, 5000)
    assert w.selection() == (0, 1000)
    w2 = make(qtbot)
    w2.append_peaks(np.zeros((20, 2), np.float32))  # 200 frames < 500
    w2.set_editable(True)
    w2.set_selection(50, 100)
    assert w2.selection() == (0, 200)


def test_marker_drag_clamps_and_emits_once(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    got = []
    w.selectionChanged.connect(lambda a, b: got.append((a, b)))
    w._start_line.setValue(900)
    assert got == [(500, 1000)]
    assert w._start_line.value() == 500


def test_playhead_and_editable(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w.set_editable(True)
    w.set_playhead(300)
    assert w._playhead.isVisible() and w._playhead.value() == 300
    w.set_playhead(None)
    assert not w._playhead.isVisible()
    w.set_editable(False)
    assert not w._start_line.isVisible()


def test_click_emits_seek(qtbot):
    w = make(qtbot)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w._redraw()
    with qtbot.waitSignal(w.seekRequested) as sig:
        w._on_click_view_x(250.7)
    assert sig.args == [250]


def test_large_peaks_redraw_fast(qtbot):
    w = make(qtbot, rate=48000)
    w.append_peaks(np.random.uniform(-1, 1, (1_000_000, 2)).astype(np.float32))
    t = time.perf_counter()
    w._redraw()
    assert time.perf_counter() - t < 0.1
