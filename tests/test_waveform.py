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


def _click_at_frame(qtbot, w, frame):
    from PySide6.QtCore import QPointF, Qt

    vb = w._plot.getPlotItem().vb
    sp = vb.mapViewToScene(QPointF(frame, 0.5))
    pos = w._plot.mapFromScene(sp)
    qtbot.mouseClick(w._plot.viewport(), Qt.LeftButton, pos=pos)


def test_real_click_on_empty_area_seeks(qtbot):
    w = make(qtbot)
    w.resize(600, 300)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w._redraw()
    w.set_editable(True)
    w.set_selection(100, 900)
    with qtbot.waitSignal(w.seekRequested, timeout=1000) as sig:
        _click_at_frame(qtbot, w, 500)
    assert abs(sig.args[0] - 500) <= 20


def test_real_click_on_marker_or_playhead_does_not_seek(qtbot):
    w = make(qtbot)
    w.resize(600, 300)
    w.append_peaks(np.zeros((100, 2), np.float32))
    w._redraw()
    w.set_editable(True)
    w.set_selection(100, 900)
    w.set_playhead(500)
    got = []
    w.seekRequested.connect(got.append)
    for f in (100, 900, 500):
        _click_at_frame(qtbot, w, f)
    assert got == []


def _wheel(qtbot, w, dy=0, dx=0, frame=None, mods=None):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication

    vp = w._plot.viewport()
    if frame is None:
        pos = QPointF(vp.width() / 2, vp.height() / 2)
    else:
        sp = w._plot.getPlotItem().vb.mapViewToScene(QPointF(frame, 0.5))
        pos = QPointF(w._plot.mapFromScene(sp))
    ev = QWheelEvent(pos, QPointF(vp.mapToGlobal(pos.toPoint())), QPoint(), QPoint(dx, dy),
                     Qt.NoButton, mods or Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(vp, ev)


def make_zoomable(qtbot):
    w = make(qtbot)  # 1000 Hz -> 10 frames per block
    w.resize(600, 300)
    w.append_peaks(np.zeros((6000, 2), np.float32))  # 60 000 frames
    w._redraw()
    w.set_editable(True)
    return w


def test_wheel_zooms_around_cursor(qtbot):
    w = make_zoomable(qtbot)
    _wheel(qtbot, w, dy=120 * 4, frame=30000)
    x0, x1 = w.view_range()
    assert x1 - x0 < 60000 * 0.5
    assert x0 < 30000 < x1
    assert abs((30000 - x0) / (x1 - x0) - 0.5) < 0.05  # anchor kept in place
    assert w._plot.getPlotItem().vb.viewRange()[0] == [x0, x1]


def test_zoom_limits(qtbot):
    w = make_zoomable(qtbot)
    for _ in range(100):
        w.zoom(0.5, 100)
    x0, x1 = w.view_range()
    assert x1 - x0 == w._min_span() and x0 <= 100 <= x1
    for _ in range(100):
        w.zoom(2, 100)
    assert w._view is None and w.view_range() == (0.0, 60000.0)


def test_pan_clamps_to_recording(qtbot):
    from PySide6.QtCore import Qt

    w = make_zoomable(qtbot)
    w.zoom(0.1, 0)
    span = w.view_range()[1] - w.view_range()[0]
    _wheel(qtbot, w, dy=-120, mods=Qt.ShiftModifier)
    assert w.view_range()[0] == span * 0.1
    _wheel(qtbot, w, dx=-120 * 1000)
    assert w.view_range() == (60000 - span, 60000)


def test_double_click_resets_and_recording_cannot_zoom(qtbot):
    from PySide6.QtCore import Qt

    w = make_zoomable(qtbot)
    w.zoom(0.1, 30000)
    vb = w._plot.getPlotItem().vb
    sp = vb.mapViewToScene(vb.viewRect().center())
    pos = w._plot.mapFromScene(sp)
    vp = w._plot.viewport()
    # a real double-click; pyqtgraph reports it on the release after DblClick
    qtbot.mouseClick(vp, Qt.LeftButton, pos=pos)
    qtbot.mouseDClick(vp, Qt.LeftButton, pos=pos)
    qtbot.mouseRelease(vp, Qt.LeftButton, pos=pos)
    assert w._view is None
    w.set_editable(False)
    w.zoom(0.1, 30000)
    assert w._view is None


def test_zoomed_redraw_draws_visible_blocks_only(qtbot):
    w = make_zoomable(qtbot)
    w.zoom(0.01, 30000)
    x, _ = w._curve.getData()
    x0, x1 = w.view_range()
    assert x.min() >= x0 - 20 and x.max() <= x1 + 20
    assert len(x) // 2 >= (x1 - x0) / 10  # full 10 ms detail


def test_zoomed_view_follows_playhead(qtbot):
    w = make_zoomable(qtbot)
    w.zoom(0.1, 0)  # view 0..6000
    w.set_playhead(3000)
    assert w.view_range() == (0, 6000)  # on screen: no jump
    w.set_playhead(6500)
    x0, x1 = w.view_range()
    assert x1 - x0 == 6000 and x0 == 6500 - 300
    w.set_playhead(100)  # e.g. playback finished, back to selection start
    assert w.view_range() == (0, 6000)  # clamped at the recording start
    w.set_playhead(59990)
    assert w.view_range() == (54000, 60000)  # clamped at the end
    w.reset_zoom()
    w.set_playhead(100)
    assert w._view is None  # unzoomed: never zooms in on its own
