"""
Waveform display: live min/max envelope while recording, then a selection
(two draggable markers, greyed-out outside) and a playhead after Stop.
After Stop the wheel zooms around the cursor, Shift+wheel (or a horizontal
wheel) scrolls, and a double-click fits the whole recording again.
All x values are absolute frames.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from audiorec.export import MIN_SELECTION_S
from audiorec.peaks import block_frames

HINT_TEXT = "Waveform appears here when you start recording"
_LINE_HIT_PX = 4  # clicks this close to a marker/playhead are not seeks
_MAX_COLUMNS = 4000  # more than a screen can show; keeps redraw cheap
_ZOOM_STEP = 1.25  # view span factor per wheel notch
_PAN_STEP = 0.1  # fraction of the view scrolled per wheel notch
_MIN_PX_PER_BLOCK = 4  # deepest zoom: one peak block is this many pixels wide
_FOLLOW_MARGIN = 0.05  # a paged view puts the playhead this far from its left edge
ZOOM_TIP = "Wheel: zoom  ·  Shift+wheel: scroll  ·  Double-click: show all"


class WaveformView(QWidget):
    selectionChanged = Signal(int, int)
    seekRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self._rate = 48000
        self._block = block_frames(self._rate)
        self._chunks: list[np.ndarray] = []
        self._peaks = np.empty((0, 2), np.float32)
        self._dirty = False
        self._editable = False
        self._sel = (0, 0)
        self._view: tuple[float, float] | None = None  # None = whole recording

        self._plot = pg.PlotWidget()
        self._plot.setMouseEnabled(False, False)
        self._plot.setMenuEnabled(False)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.hideButtons()
        self._plot.setYRange(-1, 1, padding=0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._plot)

        # connect="pairs" draws one vertical (min..max) segment per column.
        self._curve = self._plot.plot(pen=pg.mkPen("#4a90d9", width=1), connect="pairs")
        grey = pg.mkBrush(128, 128, 128, 140)
        self._shade_l = pg.LinearRegionItem((0, 0), movable=False, brush=grey)
        self._shade_r = pg.LinearRegionItem((0, 0), movable=False, brush=grey)
        for s in (self._shade_l, self._shade_r):
            s.setZValue(5)
            self._plot.addItem(s)
        red = pg.mkPen("r", width=2)
        self._start_line = pg.InfiniteLine(0, movable=True, pen=red)
        self._end_line = pg.InfiniteLine(0, movable=True, pen=red)
        self._start_line.sigPositionChanged.connect(lambda: self._on_marker(True))
        self._end_line.sigPositionChanged.connect(lambda: self._on_marker(False))
        for ln in (self._start_line, self._end_line):
            ln.setZValue(10)
            self._plot.addItem(ln)
        self._playhead = pg.InfiniteLine(0, movable=False, pen=pg.mkPen("w", width=1))
        self._playhead.setZValue(11)
        self._plot.addItem(self._playhead)

        self._hint = QLabel(HINT_TEXT, self._plot)
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hint.setStyleSheet("color: grey; background: transparent;")

        self._plot.scene().sigMouseClicked.connect(self._on_scene_click)
        self._plot.viewport().installEventFilter(self)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self.clear()

    # ---- public API -------------------------------------------------------
    def clear(self) -> None:
        self._chunks.clear()
        self._peaks = np.empty((0, 2), np.float32)
        self._dirty = False
        self._sel = (0, 0)
        self._view = None
        self._curve.setData([], [])
        self._plot.setXRange(0, 1, padding=0)
        self.set_editable(False)
        self.set_playhead(None)
        self._hint.show()
        self._layout_hint()

    def set_rate(self, rate: int) -> None:
        self._rate = rate
        self._block = block_frames(rate)

    def append_peaks(self, peaks: np.ndarray) -> None:
        if not len(peaks):
            return
        self._chunks.append(np.asarray(peaks, np.float32))
        self._hint.hide()
        self._dirty = True

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self._flush()
        self._view = None
        self.setToolTip(ZOOM_TIP if editable else "")
        if editable:
            self._sel = (0, self._total())
            self._apply_sel()
        for item in (self._start_line, self._end_line, self._shade_l, self._shade_r):
            item.setVisible(editable)

    def selection(self) -> tuple[int, int]:
        return self._sel

    def set_selection(self, start: int, end: int) -> None:
        self._commit(*self._clamp(int(start), int(end)))

    def set_playhead(self, frame: int | None) -> None:
        if frame is None:
            self._playhead.setVisible(False)
        else:
            self._playhead.setValue(frame)
            self._playhead.setVisible(True)
            self._follow(frame)

    def view_range(self) -> tuple[float, float]:
        if self._view is None:
            return 0.0, float(max(self._total(), 1))
        return self._view

    def zoom(self, factor: float, anchor: float) -> None:
        """Scale the visible span by `factor` (<1 zooms in), keeping `anchor` in place."""
        total = self._total()
        if not self._editable or not total:
            return
        x0, x1 = self.view_range()
        span = min(max((x1 - x0) * factor, self._min_span()), total)
        left = anchor - (anchor - x0) * span / (x1 - x0)
        self._set_view(left, left + span)

    def pan(self, fraction: float) -> None:
        """Scroll by `fraction` of the visible span (positive = later)."""
        if not self._editable or self._view is None:
            return
        x0, x1 = self._view
        d = (x1 - x0) * fraction
        self._set_view(x0 + d, x1 + d)

    def reset_zoom(self) -> None:
        if self._view is not None:
            self._view = None
            self._redraw()

    # ---- internals --------------------------------------------------------
    def _total(self) -> int:
        return len(self._peaks) * self._block

    def _min_len(self) -> int:
        return int(MIN_SELECTION_S * self._rate)

    def _min_span(self) -> int:
        px = max(self._plot.viewport().width(), 1)
        return max(px // _MIN_PX_PER_BLOCK, 1) * self._block

    def _set_view(self, x0: float, x1: float) -> None:
        total, span = self._total(), x1 - x0
        if span >= total:
            self._view = None
        else:
            x0 = min(max(x0, 0.0), total - span)
            self._view = (x0, x0 + span)
        self._redraw()

    def _follow(self, frame: int) -> None:
        """Page a zoomed view so the playhead stays on screen."""
        if self._view is None:
            return
        x0, x1 = self._view
        if not x0 <= frame <= x1:
            left = frame - (x1 - x0) * _FOLLOW_MARGIN
            self._set_view(left, left + x1 - x0)

    def _clamp(self, start: int, end: int) -> tuple[int, int]:
        total, min_len = self._total(), self._min_len()
        if total < min_len:
            return 0, total
        if start > end:
            start, end = end, start
        start = min(max(start, 0), total)
        end = min(max(end, 0), total)
        if end - start < min_len:
            end = min(start + min_len, total)
            start = end - min_len
        return start, end

    def _commit(self, start: int, end: int) -> None:
        changed = (start, end) != self._sel
        self._sel = (start, end)
        self._apply_sel()  # also snaps a dragged marker back when clamped
        if changed:
            self.selectionChanged.emit(start, end)

    def _on_marker(self, is_start: bool) -> None:
        a, b = self._sel
        v = int(self._start_line.value() if is_start else self._end_line.value())
        min_len, total = self._min_len(), self._total()
        if total < min_len:
            self._commit(0, total)
        elif is_start:
            self._commit(min(max(v, 0), b - min_len), b)
        else:
            self._commit(a, max(min(v, total), a + min_len))

    def _apply_sel(self) -> None:
        s, e = self._sel
        for ln, v in ((self._start_line, s), (self._end_line, e)):
            ln.blockSignals(True)
            ln.setValue(v)
            ln.blockSignals(False)
        self._shade_l.setRegion((0, s))
        self._shade_r.setRegion((e, max(self._total(), e)))

    def _flush(self) -> None:
        if self._chunks:
            self._peaks = np.concatenate([self._peaks, *self._chunks])
            self._chunks.clear()

    def _tick(self) -> None:
        if self._dirty:
            self._redraw()

    def _redraw(self) -> None:
        self._flush()
        self._dirty = False
        n = len(self._peaks)
        if n == 0:
            self._curve.setData([], [])
            return
        x0, x1 = self.view_range()
        # only the visible blocks (plus one each side so edges stay drawn)
        i0 = max(int(x0 // self._block) - 1, 0)
        i1 = min(int(x1 // self._block) + 2, n)
        peaks, group = self._peaks[i0:i1], 1
        n = len(peaks)
        if n > _MAX_COLUMNS:
            # decimate: combine `group` blocks into one (min of mins, max of maxes)
            group = -(-n // _MAX_COLUMNS)
            pad = (-n) % group
            if pad:  # repeat the last block so min/max are unaffected
                peaks = np.concatenate([peaks, np.repeat(peaks[-1:], pad, axis=0)])
            r = peaks.reshape(-1, group, 2)
            peaks = np.stack([r[:, :, 0].min(axis=1), r[:, :, 1].max(axis=1)], axis=1)
        x = np.repeat((i0 + np.arange(len(peaks), dtype=np.float64) * group) * self._block, 2)
        self._curve.setData(x, peaks.reshape(-1), connect="pairs")
        self._plot.setXRange(x0, x1, padding=0)
        if self._editable:
            self._apply_sel()

    def _on_scene_click(self, ev) -> None:
        if ev.button() != Qt.LeftButton:
            return
        if ev.double():
            self.reset_zoom()
            return
        vb = self._plot.getPlotItem().vb
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        # A plain click on a line is not accepted by InfiniteLine, so skip it here.
        sx = ev.scenePos().x()
        for ln in (self._start_line, self._end_line, self._playhead):
            if ln.isVisible():
                lx = vb.mapViewToScene(QPointF(ln.value(), 0)).x()
                if abs(sx - lx) <= _LINE_HIT_PX:
                    return
        self._on_click_view_x(vb.mapSceneToView(ev.scenePos()).x())

    def _on_click_view_x(self, x: float) -> None:
        total = self._total()
        if total:
            self.seekRequested.emit(int(min(max(x, 0), total)))

    def eventFilter(self, obj, ev) -> bool:
        if obj is self._plot.viewport() and ev.type() == QEvent.Wheel:
            self._on_wheel(ev)
            return True
        return super().eventFilter(obj, ev)

    def _on_wheel(self, ev) -> None:
        d = ev.angleDelta()
        if ev.modifiers() & Qt.ShiftModifier or (d.x() and not d.y()):
            self.pan(-(d.y() or d.x()) / 120 * _PAN_STEP)
        elif d.y():
            scene = self._plot.mapToScene(ev.position().toPoint())
            anchor = self._plot.getPlotItem().vb.mapSceneToView(scene).x()
            self.zoom(_ZOOM_STEP ** (-d.y() / 120), anchor)

    def _layout_hint(self) -> None:
        self._hint.setGeometry(self._plot.rect())

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._layout_hint()
