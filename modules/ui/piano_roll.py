# modules/ui/piano_roll.py
# =============================================================================
#  Imports / Constants
# =============================================================================
from __future__ import annotations
import math
from typing import List, Tuple, Set, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from ..core.models import Project, Note, Marker
from ..core.transport import Transport
from ..utils.config import COLOR_POOL

MIME_NOTES = "application/x-daw-notes-json;v=1"

# =============================================================================
#  PianoRoll 主視圖
# =============================================================================
class PianoRoll(QtWidgets.QGraphicsView):
    notePreviewOn = QtCore.Signal(int)
    notePreviewOff = QtCore.Signal(int)

    GUTTER_W = 90   # 左邊鍵盤欄寬
    RULER_H = 26    # 頂端尺規高度（固定顯示 BAR/BEAT）

    # -------------------------------------------------------------------------
    #  建構 & 初始設定
    # -------------------------------------------------------------------------
    def __init__(self, proj: Project, transport: Transport):
        super().__init__()
        self.p = proj
        self.t = transport

        # QGraphics 設定
        self.scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self.scene)
        self.setRenderHint(QtGui.QPainter.Antialiasing, False)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        self.setCacheMode(QtWidgets.QGraphicsView.CacheNone)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setStyleSheet("background: transparent;")

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        # grid / geometry
        self.snap = self.p.ppq // 4
        self.px_per_tick = 0.08
        self.px_per_note = 12
        self.low = 0
        self.high = 127

        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)

        # 狀態
        self.active_track = 0
        self.visible_tracks: Optional[Set[int]] = None
        self.note_items: List[Tuple[Note, QtWidgets.QGraphicsRectItem, int]] = []
        self.playhead: Optional[QtWidgets.QGraphicsLineItem] = None
        self._last_tick = 0
        self.follow_playhead: bool = True
        # === View flags ===
        self.follow_playhead: bool = True
        self.show_note_labels: bool = False
        self._label_items: list[QtWidgets.QGraphicsSimpleTextItem] = []

        # 選取：以 id(note) 維持
        self._selected_ids: set[int] = set()

        # 滑鼠定位：記錄最後一次 scene 位置（給貼上用）
        self._last_scene_pos: Optional[QtCore.QPointF] = None

        # 拖曳/框選狀態
        self._press_pos_view: Optional[QtCore.QPoint] = None
        self._press_pos_scene: Optional[QtCore.QPointF] = None
        self._pressed_item: Optional[QtWidgets.QGraphicsRectItem] = None
        self._dragging_notes: bool = False
        self._moved_since_press: bool = False

        # 拖曳錨點
        self._drag_origin_scene: Optional[QtCore.QPointF] = None
        self._drag_origin_notes: Optional[List[Tuple[Note, int, int]]] = None  # (note, start0, pitch0)
        self._undo_pushed_for_drag: bool = False

        # 橡皮筋（Ctrl 框選）
        self._rubber_band = QtWidgets.QRubberBand(QtWidgets.QRubberBand.Rectangle, self)
        self._rb_origin_scene: Optional[QtCore.QPointF] = None
        self._rb_active: bool = False

        # 內部剪貼簿（本視窗備援）
        self._clipboard: List[Note] = []
        self._clipboard_span: Tuple[int, int] = (0, 0)

        # Undo/Redo：List[Track][Note] 的快照
        self._undo_stack: List[list[list[Note]]] = []
        self._redo_stack: List[list[list[Note]]] = []

        # --- 固定尺規：為頂端尺規預留空間，建立尺規與左上角遮片 ---
        self.setViewportMargins(0, self.RULER_H, 0, 0)
        self._ruler = _TimeRuler(self)
        self._corner = QtWidgets.QWidget(self)
        self._corner.setFixedSize(self.GUTTER_W, self.RULER_H)
        self._corner.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)

        # --- 建立主題聯動色票，並套到背景/角塊 ---
        self._rebuild_theme_colors()          # （唯一版本，已去除重複定義）
        self.setBackgroundBrush(self.col_bg)
        self._corner.setStyleSheet(f"background: {self.col_ruler_bg.name()};")

        # 初次繪製
        self.draw_grid()
        self.refresh_notes()
        self._touch_project()
        self.t.tickChanged.connect(self._on_tick)

        # gutter 試彈
        self._key_down: Optional[int] = None

        # 捲動同步重繪
        self.horizontalScrollBar().valueChanged.connect(
            lambda _: (self._invalidate_gutter(), self._ruler.update())
        )
        self.verticalScrollBar().valueChanged.connect(lambda _: self._invalidate_gutter())
        self._marker_items: list[tuple[object, QtWidgets.QGraphicsLineItem, QtWidgets.QGraphicsSimpleTextItem]] = []

    # -------------------------------------------------------------------------
    #  THEME / Colors（輔助函式 + 主題顏色計算） 
    # -------------------------------------------------------------------------
    def _blend(self, a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
        return QtGui.QColor(
            int(a.red()   * (1 - t) + b.red()   * t),
            int(a.green() * (1 - t) + b.green() * t),
            int(a.blue()  * (1 - t) + b.blue()  * t),
            int(a.alpha() * (1 - t) + b.alpha() * t),
        )

    def _luma(self, c: QtGui.QColor) -> float:
        r, g, b = c.redF(), c.greenF(), c.blueF()
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _rebuild_theme_colors(self):
        pal = self.palette() or QtWidgets.QApplication.palette()
        window  = pal.color(QtGui.QPalette.Window)
        base    = pal.color(QtGui.QPalette.Base)
        alt     = pal.color(QtGui.QPalette.AlternateBase)
        text    = pal.color(QtGui.QPalette.Text)
        hl      = pal.color(QtGui.QPalette.Highlight)

        is_light = self._luma(base) >= 0.60

        # 編輯工作區
        self.col_bg             = base
        self.col_grid_minor     = self._blend(base, text, 0.22 if is_light else 0.18)
        self.col_grid_major     = self._blend(base, hl,   0.45 if is_light else 0.35)
        self.col_bar_alt_bg     = self._blend(base, alt,  0.48 if is_light else 0.40)

        # 播放頭/循環/選取（播放頭固定高可見度，但不再覆蓋整體主題色）
        self.col_playhead       = QtGui.QColor(255, 70, 70, 210)
        self.col_loop_fill      = QtGui.QColor(hl); self.col_loop_fill.setAlpha(70 if is_light else 60)
        self.col_loop_border    = QtGui.QColor(hl); self.col_loop_border.setAlpha(180 if is_light else 150)
        self.col_selection_fill = QtGui.QColor(hl); self.col_selection_fill.setAlpha(80 if is_light else 70)
        self.col_selection_border = QtGui.QColor(hl); self.col_selection_border.setAlpha(210 if is_light else 200)
        self.col_note_border    = QtGui.QColor(text); self.col_note_border.setAlpha(200 if is_light else 180)

        # 鍵條（工作區黑白鍵條紋）：Light 時強化黑鍵對比，改善「黑鍵不明顯」
        self.col_key_white_bg_work = self._blend(base, text, 0.08 if is_light else 0.06)
        self.col_key_black_bg_work = self._blend(base, text, 0.28 if is_light else 0.12)

        # 左側 gutter（鋼琴鍵區）
        self.col_gutter_bg      = self._blend(window, base, 0.70 if is_light else 0.50)
        self.col_gutter_white   = self._blend(self.col_gutter_bg, text, 0.22 if is_light else 0.22)
        self.col_gutter_black   = self._blend(self.col_gutter_bg, QtGui.QColor(0,0,0), 0.50 if is_light else 0.25)
        self.col_gutter_border  = self._blend(self.col_gutter_bg, text, 0.45 if is_light else 0.35)
        self.col_gutter_c_label = self._blend(text, base, 0.0)

        # 尺規
        self.col_ruler_bg       = self._blend(window, base, 0.15 if is_light else 0.25)
        self.col_ruler_text     = self._blend(text, base, 0.0)
        self.col_ruler_bar_pen  = self._blend(base, hl, 0.50 if is_light else 0.40)
        self.col_ruler_beat_pen = self._blend(base, text, 0.45 if is_light else 0.35)
        self.col_ruler_bottom   = self._blend(base, text, 0.40 if is_light else 0.30)

    def changeEvent(self, e: QtCore.QEvent) -> None:
        if e.type() in (QtCore.QEvent.PaletteChange, QtCore.QEvent.StyleChange):
            try:
                self._rebuild_theme_colors()
                self.setBackgroundBrush(self.col_bg)
                if hasattr(self, "_corner") and self._corner:
                    self._corner.setStyleSheet(f"background: {self.col_ruler_bg.name()};")
                self.draw_grid()
                self.refresh_notes()
                self._touch_project()
                self._ruler.update()
                self.viewport().update()
            except Exception:
                pass
        super().changeEvent(e)

    # -------------------------------------------------------------------------
    #  Helpers 公用 API / Undo-Redo / 小工具
    # -------------------------------------------------------------------------
    def set_visible_tracks(self, indices: Optional[Set[int]]):
        self.visible_tracks = None if (not indices) else set(indices)

    def _snapshot_notes(self) -> list[list[Note]]:
        return [
            [Note(n.pitch, n.vel, n.start, n.dur, n.channel) for n in tr.notes]
            for tr in self.p.tracks
        ]

    def _apply_snapshot(self, snap: list[list[Note]]):
        for i, tr in enumerate(self.p.tracks):
            tr.notes = [Note(n.pitch, n.vel, n.start, n.dur, n.channel)
                        for n in (snap[i] if i < len(snap) else [])]
        self.refresh_notes()
        self._touch_project()

    def push_undo(self):
        self._undo_stack.append(self._snapshot_notes())
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        cur = self._snapshot_notes()
        prev = self._undo_stack.pop()
        self._redo_stack.append(cur)
        self._apply_snapshot(prev)

    def redo(self):
        if not self._redo_stack:
            return
        cur = self._snapshot_notes()
        nxt = self._redo_stack.pop()
        self._undo_stack.append(cur)
        self._apply_snapshot(nxt)

    def _px_center(self, x: float) -> float:
        return round(x)

    def _invalidate_gutter(self):
        vp = self.viewport().rect()
        self.viewport().update(QtCore.QRect(0, 0, self.GUTTER_W, vp.height()))

    def tick_to_x(self, t: int | float) -> float:
        px = float(getattr(self, "px_per_tick", 0.08))
        if not (px > 1e-6):
            px = 1e-6
        return self.GUTTER_W + (float(t) * px)

    def x_to_tick(self, x: float) -> int:
        x_work = max(0.0, x - self.GUTTER_W)
        raw_ticks = x_work / max(1e-9, self.px_per_tick)
        g = max(1, int(self.snap))
        return int(math.floor(raw_ticks / g) * g)

    def _x_to_tick_float(self, x_scene: float) -> float:
        return max(0.0, (max(0.0, x_scene - self.GUTTER_W) / max(1e-9, self.px_per_tick)))

    def pitch_to_y(self, p: int) -> float:
        return (self.high - p) * self.px_per_note

    def y_to_pitch(self, y: float) -> int:
        p = self.high - int(y / self.px_per_note)
        return max(self.low, min(self.high, p))

    def _current_center_tick(self) -> int:
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        cx = vis.center().x()
        return max(0, int(max(0.0, cx - self.GUTTER_W) / max(1e-6, self.px_per_tick)))

    def zoom(self, factor: float):
        center_tick = self._current_center_tick()
        new_px = self.px_per_tick * factor
        self.px_per_tick = max(0.02, min(2.0, new_px))
        self.draw_grid()
        self.refresh_notes()
        self._touch_project()
        self.centerOn(self.tick_to_x(center_tick), (self.high - self.low + 1) * self.px_per_note / 2)
        self._invalidate_gutter()
        self._ruler.update()

    def _scene_pos_from_event(self, ev: QtGui.QMouseEvent) -> QtCore.QPointF:
        return self.mapToScene(ev.pos())

    def _zoom_at_view_pos(self, view_pos: QtCore.QPoint, factor: float):
        if view_pos.x() < self.GUTTER_W:
            return
        scene_before = self.mapToScene(view_pos)
        anchor_tick = self._x_to_tick_float(scene_before.x())
        new_px = self.px_per_tick * factor
        self.px_per_tick = max(0.02, min(2.0, new_px))
        self.draw_grid()
        self.refresh_notes()
        self._touch_project()
        target_x = self.tick_to_x(anchor_tick)
        scene_after = self.mapToScene(view_pos)
        dx = target_x - scene_after.x()
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        self.centerOn(vis.center().x() + dx, vis.center().y())
        self._invalidate_gutter()
        self._ruler.update()

    def resizeEvent(self, e: QtGui.QResizeEvent) -> None:
        super().resizeEvent(e)
        vp = self.viewport().geometry()
        self._ruler.setGeometry(self.GUTTER_W, 0, max(0, vp.width() - self.GUTTER_W), self.RULER_H)
        self._corner.move(0, 0)

    def scrollContentsBy(self, dx: int, dy: int):
        super().scrollContentsBy(dx, dy)
        self._invalidate_gutter()

    def _note_name(self, pitch: int) -> str:
        names = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']
        return f"{names[pitch % 12]}{pitch // 12 - 1}"   # 60 -> C4

    def _clear_note_labels(self) -> None:
        for it in getattr(self, "_label_items", []):
            try:
                if it is not None and it.scene() is not None:
                    self.scene.removeItem(it)
            except RuntimeError:
                pass
        self._label_items = []

    def _refresh_note_labels(self) -> None:
        self._clear_note_labels()
        if not getattr(self, "show_note_labels", False):
            return
        if self.px_per_note < 10:
            return

        labels: list[QtWidgets.QGraphicsSimpleTextItem] = []
        for (n, rect_it, _ti) in getattr(self, "note_items", []):
            if rect_it is None:
                continue
            r = rect_it.rect()
            if r.width() < 12:
                continue

            txt = QtWidgets.QGraphicsSimpleTextItem(self._note_name(n.pitch), rect_it)
            fm = QtGui.QFontMetricsF(txt.font())
            w = max(1.0, fm.horizontalAdvance(txt.text()))
            h = max(1.0, fm.height())
            sx = min(1.0, (r.width() - 4) / w)
            sy = min(1.0, (r.height() - 2) / h)
            s = min(sx, sy)
            if s < 1.0:
                f = txt.font()
                f.setPointSizeF(max(6.0, f.pointSizeF() * s))
                txt.setFont(f)

            txt.setBrush(QtGui.QBrush(Qt.black))
            txt.setPos(r.left() + 2, r.top() + 1)
            labels.append(txt)

        self._label_items = labels

    def set_show_note_labels(self, on: bool) -> None:
        self.show_note_labels = bool(on)
        self._refresh_note_labels()

    def draw_grid(self):
        self.scene.clear()
        self._marker_items = []
        self.note_items = []
        self.playhead = None

        ppq = max(1, int(self.p.ppq))
        ts_map = list(getattr(self.p, "timesig_map", []) or [])
        if not ts_map:
            class _T: pass
            _t = _T(); _t.tick=0; _t.num=4; _t.den=4
            ts_map = [_t]
        ts_map = sorted(ts_map, key=lambda x: int(getattr(x, "tick", 0)))

        def ticks_per_bar_at(num: int, den: int) -> int:
            ticks_per_beat = int(round(ppq * (4 / max(1, den))))
            return max(1, ticks_per_beat * max(1, num)), ticks_per_beat

        max_tick = 0
        for tr in self.p.tracks:
            for n in tr.notes:
                if n.end > max_tick:
                    max_tick = n.end
        base_tick = max(max_tick, getattr(self.t, 'loop_b', ppq * 16))
        total_ticks = max(base_tick, ppq * 16)

        w_work = total_ticks * self.px_per_tick
        h = (self.high - self.low + 1) * self.px_per_note
        total_w = self.GUTTER_W + w_work

        for pitch in range(self.low, self.high + 1):
            y = self.pitch_to_y(pitch)
            isb = (pitch % 12) in [1, 3, 6, 8, 10]
            col = self.col_key_white_bg_work if isb else self.col_key_black_bg_work
            self.scene.addRect(self.GUTTER_W, y, w_work, self.px_per_note,
                               QtGui.QPen(Qt.NoPen), QtGui.QBrush(col))

        pen_bar = QtGui.QPen(self.col_grid_major); pen_bar.setWidth(2)
        pen_beat = QtGui.QPen(self.col_grid_minor); pen_beat.setWidth(1)

        segs = []
        for i, ts in enumerate(ts_map):
            t0 = int(getattr(ts, "tick", 0))
            t1 = int(getattr(ts_map[i+1], "tick", total_ticks)) if i+1 < len(ts_map) else total_ticks
            segs.append((t0, t1, int(getattr(ts, "num", 4)), int(getattr(ts, "den", 4))))

        for (seg_start, seg_end, num, den) in segs:
            tpb, tpbeat = ticks_per_bar_at(num, den)

            bar_tick = seg_start
            while bar_tick <= seg_end:
                x_line = self._px_center(self.tick_to_x(bar_tick))
                self.scene.addLine(x_line, 0, x_line, h, pen_bar)
                for b in range(1, num):
                    bt = bar_tick + b * tpbeat
                    if bt > seg_end: break
                    xb = self._px_center(self.tick_to_x(bt))
                    self.scene.addLine(xb, 0, xb, h, pen_beat)
                bar_tick += tpb

        step = max(1, int(self.snap))
        if step < ppq * 16:
            thin_col = QtGui.QColor(self.col_grid_minor); thin_col.setAlpha(120)
            thin = QtGui.QPen(thin_col); thin.setStyle(Qt.DashLine)
            x_tick = 0
            while x_tick <= total_ticks:
                x = self._px_center(self.tick_to_x(x_tick))
                self.scene.addLine(x, 0, x, h, thin)
                x_tick += step

        if getattr(self.t, "loop_on", False) and getattr(self.t, "loop_a", None) is not None and getattr(self.t, "loop_b", None) is not None:
            xa = self._px_center(self.tick_to_x(self.t.loop_a))
            xb = self._px_center(self.tick_to_x(max(self.t.loop_b, self.t.loop_a + 1)))
            loop_pen = QtGui.QPen(self.col_loop_border); loop_pen.setWidth(2)
            self.scene.addLine(xa, 0, xa, h, loop_pen)
            self.scene.addLine(xb, 0, xb, h, loop_pen)
            xa_rect = self.tick_to_x(self.t.loop_a)
            xb_rect = self.tick_to_x(max(self.t.loop_b, self.t.loop_a + 1))
            self.scene.addRect(xa_rect, 0, max(1.0, xb_rect - xa_rect), 18, QtGui.QPen(Qt.NoPen), QtGui.QBrush(self.col_loop_fill))
            lf2 = QtGui.QColor(self.col_loop_fill); lf2.setAlpha(max(10, self.col_loop_fill.alpha() - 30))
            self.scene.addRect(xa_rect, 18, max(1.0, xb_rect - xa_rect), h - 18, QtGui.QPen(Qt.NoPen), QtGui.QBrush(lf2))

        self.setSceneRect(0, 0, total_w, h)
        self._ensure_playhead()
        self._ruler.update()

    def refresh_notes(self):
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        
        for _, it, _ in list(getattr(self, 'note_items', [])):
            try:
                if it is not None and it.scene() is not None:
                    self.scene.removeItem(it)
            except RuntimeError:
                pass
        self.note_items = []

        tracks = getattr(self.p, "tracks", [])
        if self.visible_tracks is None:
            tr_iter = list(enumerate(tracks))
        else:
            vis = sorted(self.visible_tracks)
            tr_iter = [(i, tracks[i]) for i in vis if 0 <= i < len(tracks)]

        for ti, tr in tr_iter:
            base = tr.color if isinstance(tr.color, QtGui.QColor) \
                   else QtGui.QColor(*COLOR_POOL[ti % len(COLOR_POOL)])
            pen = QtGui.QPen(base.darker(160))
            if ti == self.active_track:
                pen.setWidth(2)
            fill = QtGui.QBrush(base)

            locked = bool(getattr(tr, "locked", False))
            opacity = 0.6 if locked else 1.0

            for n in getattr(tr, "notes", []):
                r = QtCore.QRectF(self.tick_to_x(n.start), self.pitch_to_y(n.pitch),
                                  max(2.0, n.dur * self.px_per_tick), self.px_per_note - 1)
                it = self.scene.addRect(r, pen, fill)
                it.setOpacity(opacity)
                it.setToolTip(f"{tr.name} ch{n.channel} note={n.pitch} vel={n.vel}")
                it.setData(0, n)
                it.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
                it.setSelected(id(n) in self._selected_ids)
                self.note_items.append((n, it, ti))

        sep_pen = QtGui.QPen(self.col_gutter_border)
        sep_pen.setWidth(1)
        h = (self.high - self.low + 1) * self.px_per_note
        self.scene.addLine(self._px_center(self.GUTTER_W), 0, self._px_center(self.GUTTER_W), h, sep_pen)

        self._ensure_playhead()
        self._refresh_note_labels()
        self._refreshing = False

    def drawForeground(self, painter: QtGui.QPainter, rect: QtCore.QRectF):
        painter.save()
        painter.resetTransform()
        vp = self.viewport().rect()
        vp_h = vp.height()

        painter.setClipRect(0, 0, self.GUTTER_W, vp_h)

        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        y0 = vis.top()

        painter.fillRect(0, 0, self.GUTTER_W, vp_h, self.col_gutter_bg)

        max_rows = int(math.ceil(vp_h / self.px_per_note)) + 2
        for i in range(max_rows):
            y_scene = y0 + i * self.px_per_note
            pitch = self.y_to_pitch(y_scene + self.px_per_note / 2)
            if pitch < self.low or pitch > self.high:
                continue
            is_black = (pitch % 12) in [1, 3, 6, 8, 10]
            col = self.col_gutter_black if is_black else self.col_gutter_white
            y_vp = int((self.pitch_to_y(pitch) - y0))
            painter.fillRect(0, y_vp, self.GUTTER_W, self.px_per_note, col)
            if not is_black:
                painter.setPen(self.col_gutter_border)
                painter.drawRect(0, y_vp, self.GUTTER_W, self.px_per_note)

            if pitch % 12 == 0:
                painter.setPen(self.col_gutter_c_label)
                octave = (pitch // 12) - 1
                painter.drawText(QtCore.QRect(4, y_vp, self.GUTTER_W - 8, self.px_per_note),
                                 Qt.AlignVCenter | Qt.AlignLeft, f"C{octave}")

        painter.setPen(self.col_gutter_border)
        painter.drawLine(self.GUTTER_W, 0, self.GUTTER_W, vp_h)
        painter.restore()

    def _update_selected_cache_from_items(self):
        self._selected_ids = {
            id(i.data(0))
            for n, i, _ in self.note_items
            if i.isSelected() and isinstance(i.data(0), Note)
        }

    def _get_items_under_pos(self, scene_pos: QtCore.QPointF):
        return [it for it in self.scene.items(scene_pos) if isinstance(it.data(0), Note)]

    def _selected_items(self) -> List[QtWidgets.QGraphicsRectItem]:
        return [i for _, i, _ in self.note_items if i.isSelected()]

    def copy_selection(self):
        items = self._selected_items()
        if not items:
            return
        notes = [i.data(0) for i in items]
        min_t = min(n.start for n in notes)
        max_t = max(n.start + n.dur for n in notes)

        payload = {
            "ppq": int(self.p.ppq),
            "notes": [
                {"pitch": n.pitch, "vel": n.vel, "start": int(n.start - min_t), "dur": int(n.dur), "channel": n.channel}
                for n in notes
            ],
            "span": int(max_t - min_t)
        }

        import json
        mime = QtCore.QMimeData()
        mime.setData(MIME_NOTES, QtCore.QByteArray(json.dumps(payload).encode("utf-8")))
        mime.setText(json.dumps(payload))
        QtWidgets.QApplication.clipboard().setMimeData(mime)

        rel = [Note(pitch=d["pitch"], vel=d["vel"], start=d["start"], dur=d["dur"], channel=d["channel"])
               for d in payload["notes"]]
        self._clipboard = rel
        self._clipboard_span = (0, payload["span"])

    def _current_paste_anchor(self) -> tuple[int, Optional[int]]:
        if isinstance(self._last_scene_pos, QtCore.QPointF):
            sp = self._last_scene_pos
        else:
            sp = self.mapToScene(self.viewport().rect().center())
        anchor_tick = self.x_to_tick(sp.x())
        anchor_pitch = self.y_to_pitch(sp.y()) if sp is not None else None
        return anchor_tick, anchor_pitch

    def paste_clipboard(self):
        import json
        cb = QtWidgets.QApplication.clipboard()
        md = cb.mimeData()

        src_ppq = None
        notes_data = None
        span = None

        if md and md.hasFormat(MIME_NOTES):
            try:
                payload = json.loads(bytes(md.data(MIME_NOTES)).decode("utf-8"))
                src_ppq = int(payload.get("ppq", self.p.ppq))
                notes_data = payload.get("notes", [])
                span = int(payload.get("span", 0))
            except Exception:
                notes_data = None

        if notes_data is None and md and md.hasText():
            try:
                payload = json.loads(md.text())
                src_ppq = int(payload.get("ppq", self.p.ppq))
                notes_data = payload.get("notes", [])
                span = int(payload.get("span", 0))
            except Exception:
                notes_data = None

        if notes_data is None and self._clipboard:
            src_ppq = self.p.ppq
            notes_data = [
                {"pitch": n.pitch, "vel": n.vel, "start": int(n.start), "dur": int(n.dur), "channel": n.channel}
                for n in self._clipboard
            ]
            span = self._clipboard_span[1]

        if not notes_data:
            return

        scale = 1.0
        try:
            if src_ppq and src_ppq > 0 and src_ppq != self.p.ppq:
                scale = float(self.p.ppq) / float(src_ppq)
        except Exception:
            scale = 1.0

        anchor_tick, anchor_pitch = self._current_paste_anchor()
        base_pitch = notes_data[0]["pitch"] if notes_data else None

        tr = self.p.tracks[self.active_track] if 0 <= self.active_track < len(self.p.tracks) else None
        if tr is None:
            return
            
        if self._active_track_locked():
            QtWidgets.QToolTip.showText(self.mapToGlobal(self.viewport().rect().center()), "此軌已鎖定，無法貼上")
            return

        self.push_undo()

        new_notes = []
        for d in notes_data:
            t = max(0, int(round(anchor_tick + d["start"] * scale)))
            p = int(d["pitch"])
            if anchor_pitch is not None and base_pitch is not None:
                p = int(anchor_pitch + (p - base_pitch))
            p = max(self.low, min(self.high, p))
            new_notes.append(Note(
                pitch=p, vel=int(d["vel"]), start=t,
                dur=max(1, int(round(d["dur"] * scale))), channel=tr.channel
            ))

        tr.notes.extend(new_notes)
        self._selected_ids = {id(n) for n in new_notes}
        self.refresh_notes()
        self._touch_project()

    def cut_selection(self):
        self.copy_selection()
        self.delete_selection()

    def delete_selection(self):
        items = self._selected_items()
        if not items:
            return
        self.push_undo()
        sel_ids = {id(it.data(0)) for it in items if isinstance(it.data(0), Note)}
        any_blocked = False
        for i, tr in enumerate(self.p.tracks):
            if bool(getattr(tr, "locked", False)):
                if any(id(n) in sel_ids for n in tr.notes):
                    any_blocked = True
                continue
            tr.notes = [n for n in tr.notes if id(n) not in sel_ids]

        if any_blocked:
            QtWidgets.QToolTip.showText(self.mapToGlobal(self.viewport().rect().center()), "有鎖定的軌未刪除（已略過）")

        self._selected_ids.clear()
        self.refresh_notes()
        self._touch_project()

    def select_all_active_track(self):
        if not (0 <= self.active_track < len(self.p.tracks)):
            return
        tr = self.p.tracks[self.active_track]
        self._selected_ids = {id(n) for n in tr.notes}
        self.refresh_notes()
        self._touch_project()

    def _active_track_locked(self) -> bool:
        return 0 <= self.active_track < len(self.p.tracks) and bool(getattr(self.p.tracks[self.active_track], "locked", False))

    def _item_track_index(self, item: QtWidgets.QGraphicsRectItem) -> int | None:
        for n, it, ti in getattr(self, "note_items", []):
            if it is item:
                return ti
        return None

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self._press_pos_view = e.pos()
        sp = self._scene_pos_from_event(e)
        self._press_pos_scene = sp
        self._last_scene_pos = sp
        self._moved_since_press = False
        self._dragging_notes = False
        self._pressed_item = None
        self._undo_pushed_for_drag = False


        # （新增）在左側鋼琴鍵盤按下 → 立即試彈
        if e.button() == Qt.LeftButton and e.position().x() < self.GUTTER_W:
            vis = self.mapToScene(self.viewport().rect()).boundingRect()
            scene_y = vis.top() + e.position().y()
            pitch = self.y_to_pitch(scene_y)

            self._key_down = pitch
            self.notePreviewOn.emit(pitch)
            self.viewport().update()
            e.accept()
            return

        # ===== 尺規點擊：跳到最近的 Marker =====
        if e.button() == Qt.LeftButton and self._ruler.geometry().contains(e.pos()):
            scene_pt = self.mapToScene(QtCore.QPoint(e.pos().x(), self.RULER_H + 1))
            scene_x = scene_pt.x()
            m, _ = self._nearest_marker_at_scene_x(scene_x, px_tol=8)
            if m is not None:
                try:
                    self.t.set_pos(int(m.tick))
                except Exception:
                    pass
                self.centerOn(self.tick_to_x(int(m.tick)), self.viewport().rect().center().y())
                e.accept()
                return

        # 左鍵選單
        if e.button() == Qt.RightButton and e.position().x() >= self.GUTTER_W:
            pos_scene = self._scene_pos_from_event(e)
            hit_items = self._get_items_under_pos(pos_scene)
            if hit_items:
                it = hit_items[0]
                self._show_note_menu(it, e.globalPosition().toPoint())
                e.accept()
                return
            else:
                menu = QtWidgets.QMenu(self)
                act_paste = menu.addAction("Paste")

                if not hasattr(self, "paste_clipboard") or not callable(getattr(self, "paste_clipboard")):
                    act_paste.setEnabled(False)

                chosen = menu.exec(e.globalPosition().toPoint())
                if chosen is act_paste:
                    try:
                        self.paste_clipboard()
                    except TypeError:
                        try:
                            self.paste_clipboard(int(self.playhead_tick))
                        except Exception:
                            pass
                e.accept()
                return

        if e.button() == Qt.LeftButton:
            if self._active_track_locked():
                QtWidgets.QToolTip.showText(e.globalPosition().toPoint(), "此軌已鎖定，無法新增音符")
                e.accept()
                return

        if e.button() == Qt.LeftButton:
            if (e.modifiers() & Qt.ControlModifier):
                item = self.itemAt(e.pos())
                if item is None or not isinstance(getattr(item, 'data', lambda *_: None)(0), Note):
                    self._rb_origin_scene = self._scene_pos_from_event(e)
                    self._rb_active = True

                    cur_scene = self._scene_pos_from_event(e)
                    tl_scene = QtCore.QPointF(min(self._rb_origin_scene.x(), cur_scene.x()),
                                              min(self._rb_origin_scene.y(), cur_scene.y()))
                    br_scene = QtCore.QPointF(max(self._rb_origin_scene.x(), cur_scene.x()),
                                              max(self._rb_origin_scene.y(), cur_scene.y()))
                    tl_view = self.mapFromScene(tl_scene)
                    br_view = self.mapFromScene(br_scene)
                    self._rubber_band.setGeometry(QtCore.QRect(tl_view, br_view).normalized())
                    self._rubber_band.show()
                    e.accept()
                    return

            scene_pos = self._scene_pos_from_event(e)
            hit_items = self._get_items_under_pos(scene_pos)
            if hit_items:
                it = hit_items[0]
                self._pressed_item = it
                ti = self._item_track_index(it)
                if ti is not None and 0 <= ti < len(self.p.tracks) and bool(getattr(self.p.tracks[ti], "locked", False)):
                    QtWidgets.QToolTip.showText(e.globalPosition().toPoint(), "此軌已鎖定，無法拖曳編輯")
                    e.accept()
                    return

                if not it.isSelected():
                    for i in self._selected_items():
                        i.setSelected(False)
                    it.setSelected(True)
                self._update_selected_cache_from_items()

                self._drag_origin_scene = scene_pos
                sel_items = self._selected_items()
                self._drag_origin_notes = [(i.data(0), i.data(0).start, i.data(0).pitch) for i in sel_items]
                e.accept()
                return

            if e.position().x() >= self.GUTTER_W:
                pos = self._scene_pos_from_event(e)
                self._last_scene_pos = pos
                tick = self.x_to_tick(pos.x())
                pitch = self.y_to_pitch(pos.y())
                tr = self.p.tracks[self.active_track] if 0 <= self.active_track < len(self.p.tracks) else None
                if tr is None:
                    return
                self.push_undo()
                self._drawing = Note(pitch=pitch, vel=96, start=tick,
                                     dur=max(self.snap, self.snap), channel=tr.channel)
                tr.notes.append(self._drawing)
                self._selected_ids = {id(self._drawing)}
                self.refresh_notes()
                self._touch_project()
                e.accept()
                return

        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        sp = self._scene_pos_from_event(e)
        self._last_scene_pos = sp

        if self._key_down is not None:
            vis = self.mapToScene(self.viewport().rect()).boundingRect()
            scene_y = vis.top() + e.position().y()
            pitch = self.y_to_pitch(scene_y)
            if pitch != self._key_down:
                self.notePreviewOff.emit(self._key_down)
                self._key_down = pitch
                self.notePreviewOn.emit(pitch)
            return

        if self._rb_active and self._rb_origin_scene is not None:
            vp = self.viewport().rect()
            x, y = e.pos().x(), e.pos().y()
            edge = 24
            v_sb = self.verticalScrollBar()
            h_sb = self.horizontalScrollBar()

            if y < vp.top() + edge:
                v_sb.setValue(v_sb.value() - max(1, int((edge - (y - vp.top())) / 2)))
            elif y > vp.bottom() - edge:
                v_sb.setValue(v_sb.value() + max(1, int(((y - (vp.bottom() - edge))) / 2)))

            if x > self.GUTTER_W:
                if x < self.GUTTER_W + edge:
                    h_sb.setValue(h_sb.value() - max(1, int((edge - (x - self.GUTTER_W)) / 2)))
                elif x > vp.right() - edge:
                    h_sb.setValue(h_sb.value() + max(1, int(((x - (vp.right() - edge))) / 2)))

            cur_scene = sp
            tl_scene = QtCore.QPointF(min(self._rb_origin_scene.x(), cur_scene.x()),
                                      min(self._rb_origin_scene.y(), cur_scene.y()))
            br_scene = QtCore.QPointF(max(self._rb_origin_scene.x(), cur_scene.x()),
                                      max(self._rb_origin_scene.y(), cur_scene.y()))
            tl_view = self.mapFromScene(tl_scene)
            br_view = self.mapFromScene(br_scene)
            rect_view = QtCore.QRect(tl_view, br_view).normalized()
            self._rubber_band.setGeometry(rect_view)

            scene_rect = QtCore.QRectF(tl_scene, br_scene).normalized()
            for _, it, _ in self.note_items:
                it.setSelected(it.sceneBoundingRect().intersects(scene_rect))
            self._update_selected_cache_from_items()

            self._moved_since_press = True
            e.accept()
            return

        if self._pressed_item is not None and self._drag_origin_scene is not None and self._drag_origin_notes is not None:
            if not self._dragging_notes:
                if (e.pos() - self._press_pos_view).manhattanLength() < max(6, int(QtWidgets.QApplication.startDragDistance()*1.2)):
                    e.accept(); return
                self._dragging_notes = True
                if not self._undo_pushed_for_drag:
                    self.push_undo()
                    self._undo_pushed_for_drag = True

            self._moved_since_press = True

            dx = sp.x() - self._drag_origin_scene.x()
            dy = sp.y() - self._drag_origin_scene.y()

            raw_dt_ticks = int(round(dx / self.px_per_tick))
            g = max(1, int(self.snap))
            dt_ticks = raw_dt_ticks if (e.modifiers() & Qt.AltModifier) else (raw_dt_ticks // g) * g
            dp_semi = int(round(-dy / self.px_per_note))

            if dt_ticks == 0 and dp_semi == 0:
                e.accept(); return

            for (n, start0, pitch0) in self._drag_origin_notes:
                n.start = max(0, int(start0 + dt_ticks))
                n.pitch = max(self.low, min(self.high, int(pitch0 + dp_semi)))

            self.refresh_notes()
            self._touch_project()
            e.accept(); return

        if hasattr(self, '_drawing') and self._drawing:
            end = self.x_to_tick(sp.x())
            self._drawing.dur = max(self.snap, end - self._drawing.start)
            self.refresh_notes()
            self._touch_project()
            e.accept(); return

        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent):
        if self._rb_active:
            self._rubber_band.hide()
            self._rb_active = False
            self._rb_origin_scene = None
            e.accept(); return

        if self._key_down is not None:
            self.notePreviewOff.emit(self._key_down)
            self._key_down = None
            self.viewport().update()

        self._pressed_item = None
        self._moved_since_press = False
        self._dragging_notes = False
        self._drag_origin_scene = None
        self._drag_origin_notes = None
        self._undo_pushed_for_drag = False
        self._drawing = None

        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e: QtGui.QKeyEvent):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selection()
            return
        return super().keyPressEvent(e)

    def leaveEvent(self, e: QtCore.QEvent):
        if self._key_down is not None:
            self.notePreviewOff.emit(self._key_down)
            self._key_down = None
        return super().leaveEvent(e)

    def wheelEvent(self, e: QtGui.QWheelEvent):
        if (e.modifiers() & Qt.ControlModifier) and e.position().x() >= self.GUTTER_W:
            dy = e.angleDelta().y()
            if dy != 0:
                factor = 1.2 if dy > 0 else (1/1.2)
                self._zoom_at_view_pos(e.position().toPoint(), factor)
            e.accept()
            return

        if e.position().x() < self.GUTTER_W:
            return super().wheelEvent(e)

        pos = self.mapToScene(e.position().toPoint())
        for it in self.scene.items(pos):
            n = it.data(0)
            if isinstance(n, Note):
                self.push_undo()
                delta = 1 if e.angleDelta().y() > 0 else -1
                if e.modifiers() & Qt.ShiftModifier:
                    delta *= 10
                n.vel = int(max(1, min(127, n.vel + delta)))
                it.setToolTip(f"vel={n.vel}")
                break
        super().wheelEvent(e)

    def _ensure_playhead(self):
        h = (self.high - self.low + 1) * self.px_per_note
        if self.playhead is None:
            pen = QtGui.QPen(self.col_playhead)
            pen.setWidth(2)
            self.playhead = self.scene.addLine(0, 0, 0, h, pen)
            self.playhead.setZValue(10)
        else:
            try:
                line = self.playhead.line()
                self.playhead.setLine(line.x1(), 0, line.x2(), h)
            except RuntimeError:
                pen = QtGui.QPen(self.col_playhead)
                pen.setWidth(2)
                self.playhead = self.scene.addLine(0, 0, 0, h, pen)
                self.playhead.setZValue(10)

    def _on_tick(self, tick: int):
        self._ensure_playhead()
        x = self.tick_to_x(tick)
        self.playhead.setLine(x, 0, x, (self.high - self.low + 1) * self.px_per_note)

        if getattr(self, "follow_playhead", True):
            if getattr(self, "follow_playhead", True):
                vis = self.mapToScene(self.viewport().rect()).boundingRect()
                margin = 120
                if tick + self.p.ppq // 8 < getattr(self, "_last_tick", 0):
                    self.centerOn(x, vis.center().y())
                else:
                    if x < vis.left() + margin or x > vis.right() - margin:
                        self.centerOn(x, vis.center().y())

        self._last_tick = tick
        self._invalidate_gutter()

    def _prompt_text(self, title: str, preset: str = "") -> str | None:
        text, ok = QtWidgets.QInputDialog.getText(self, title, "Name:", text=preset)
        return str(text).strip() if ok and str(text).strip() else None

    def _nearest_marker_at_scene_x(self, scene_x: float, px_tol: int = 8):
        marks = getattr(self.p, "markers", []) or []
        if not marks:
            return None, float("inf")

        ruler_x = self.mapFromScene(QtCore.QPointF(scene_x, 0)).x() - self.GUTTER_W

        best = float("inf")
        hit = None
        for m in marks:
            x_scene = self.tick_to_x(int(m.tick))
            x_ruler = self.mapFromScene(QtCore.QPointF(x_scene, 0)).x() - self.GUTTER_W
            dx = abs(x_ruler - ruler_x)
            if dx < best:
                best = dx; hit = m
        return (hit, best) if best <= px_tol else (None, best)
    
    def _edit_targets(self, clicked_note: "Note") -> list["Note"]:
        sel_items = self._selected_items()
        if sel_items and any(id(clicked_note) == id(i.data(0)) for i in sel_items):
            return [i.data(0) for i in sel_items]
        return [clicked_note]

    def _apply_to_notes(self, notes: list["Note"], fn):
        if not notes:
            return
        self.push_undo()
        for n in notes:
            fn(n)
        for (n, it, _ti) in self.note_items:
            if n in notes and it is not None:
                it.setToolTip(f"vel={n.vel}")
        self.refresh_notes()
        self._touch_project()

    def _clamp_vel(self, v: int) -> int:
        return int(max(1, min(127, int(v))))

    def _prompt_velocity(self, preset: int | None = None) -> int | None:
        val, ok = QtWidgets.QInputDialog.getInt(
            self, "Set Velocity", "Velocity (1–127):",
            int(preset if preset is not None else 96), 1, 127, 1
        )
        return int(val) if ok else None

    def _show_note_menu(self, clicked_item: QtWidgets.QGraphicsRectItem, global_pos: QtCore.QPoint):
        n = clicked_item.data(0)
        if not isinstance(n, Note):
            return

        if not clicked_item.isSelected():
            for it in self._selected_items():
                it.setSelected(False)
            clicked_item.setSelected(True)
            self._update_selected_cache_from_items()

        targets = self._edit_targets(n)

        menu = QtWidgets.QMenu(self)

        act_cut  = menu.addAction("Cut")
        act_copy = menu.addAction("Copy")
        menu.addSeparator()
        act_set  = menu.addAction("Set Velocity…")
        menu.addSeparator()
        act_del  = menu.addAction("Delete")

        chosen = menu.exec(global_pos)
        if chosen is None:
            return

        if chosen is act_cut:
            self.copy_selection()
            self.delete_selection()
            return

        if chosen is act_copy:
            self.copy_selection()
            return

        if chosen is act_set:
            new_v = self._prompt_velocity(n.vel)
            if new_v is not None:
                self._apply_to_notes(targets, lambda nn: setattr(nn, "vel", self._clamp_vel(new_v)))
            return

        if chosen is act_del:
            if self._selected_items():
                self.delete_selection()
            else:
                self.push_undo()
                for tr in self.p.tracks:
                    try:
                        tr.notes.remove(n)
                    except ValueError:
                        pass
                self._selected_ids.discard(id(n))
                self.refresh_notes()
                self._touch_project()
            return

    def _touch_project(self):
        try:
            self.p.on_notes_changed()
        except Exception:
            pass


# =============================================================================
#  固定頂端尺規
# =============================================================================
class _TimeRuler(QtWidgets.QWidget):
    def __init__(self, roll: "PianoRoll"):
        super().__init__(roll)
        self.roll = roll
        self._scrub_active = False
        self._last_scrub_tick = None
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, False)
        self._refreshing = False

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        import math
        r = ev.rect()
        p = QtGui.QPainter(self)
        p.fillRect(r, self.roll.col_ruler_bg)

        view = self.roll
        vis_scene = view.mapToScene(view.viewport().rect()).boundingRect()
        t0f = view._x_to_tick_float(vis_scene.left())
        t1f = view._x_to_tick_float(vis_scene.right())

        ppq = max(1, int(view.p.ppq))
        t0 = max(0, int(math.floor(t0f) - ppq))
        t1 = max(t0 + 1, int(math.ceil(t1f) + ppq))

        def x_from_tick(t: float) -> int:
            x_scene = view.tick_to_x(t)
            x_view  = view.mapFromScene(QtCore.QPointF(x_scene, 0)).x()
            return int(x_view - view.GUTTER_W)

        ts_map = list(getattr(view.p, "timesig_map", []) or [])
        if not ts_map:
            class _T: pass
            _t = _T(); _t.tick = 0; _t.num = 4; _t.den = 4
            ts_map = [_t]
        ts_map = sorted(ts_map, key=lambda x: int(getattr(x, "tick", 0)))

        def ticks_per_bar(num: int, den: int) -> tuple[int, int]:
            tpbeat = int(round(ppq * (4 / max(1, den))))
            return max(1, tpbeat * max(1, num)), tpbeat

        def bars_before(tick: int) -> int:
            bars = 0
            n = len(ts_map)
            for i, ts in enumerate(ts_map):
                seg_start = int(getattr(ts, "tick", 0))
                seg_end   = int(getattr(ts_map[i+1], "tick", tick)) if i+1 < n else tick
                if seg_end <= seg_start:
                    continue
                num = int(getattr(ts, "num", 4)); den = int(getattr(ts, "den", 4))
                tpb, _ = ticks_per_bar(num, den)
                span = min(tick, seg_end) - seg_start
                if span > 0:
                    bars += span // tpb
                if tick <= seg_end:
                    break
            return bars

        segs = []
        for i, ts in enumerate(ts_map):
            seg_start0 = int(getattr(ts, "tick", 0))
            seg_end0   = int(getattr(ts_map[i+1], "tick", 1 << 30)) if i+1 < len(ts_map) else (1 << 30)
            if seg_end0 <= t0:   continue
            if seg_start0 >= t1: break
            segs.append((max(seg_start0, t0), min(seg_end0, t1),
                         int(getattr(ts, "num", 4)), int(getattr(ts, "den", 4)),
                         seg_start0))

        pen_bar  = QtGui.QPen(view.col_ruler_bar_pen);  pen_bar.setWidth(2)
        pen_beat = QtGui.QPen(view.col_ruler_beat_pen); pen_beat.setWidth(1)
        fm = self.fontMetrics()

        for (seg_left, seg_right, num, den, seg_orig_start) in segs:
            tpb, tpbeat = ticks_per_bar(num, den)

            if seg_left <= seg_orig_start:
                first_bar_tick = seg_orig_start
            else:
                k0 = (seg_left - seg_orig_start + tpb - 1) // tpb  # ceil
                first_bar_tick = seg_orig_start + k0 * tpb

            try:
                if seg_left <= seg_orig_start < seg_right:
                    first_bar_tick = seg_orig_start
                    x_bar0 = x_from_tick(first_bar_tick)
                    x_bar1 = x_from_tick(first_bar_tick + tpb) if (first_bar_tick + tpb) < seg_right else None

                    txt_ts = f"{num}/{den} • {float(view.p.bpm_at(max(0, seg_orig_start))):.2f} BPM"
                    tw = fm.horizontalAdvance(txt_ts)

                    if x_bar1 is not None:
                        mid = (x_bar0 + x_bar1) // 2
                        place_x = max(6, min(mid - tw // 2, self.width() - tw - 6))
                    else:
                        place_x = max(6, min(x_bar0 + 8, self.width() - tw - 6))

                    p.setPen(QtGui.QPen(view.col_ruler_text))
                    p.drawText(place_x, int(fm.ascent() + 4), txt_ts)
            except Exception:
                pass

            bar_tick = first_bar_tick
            while bar_tick < seg_right:
                x = x_from_tick(bar_tick)
                if -60 <= x <= self.width() + 60:
                    p.setPen(pen_bar)
                    p.drawLine(x, 0, x, self.height())
                    p.setPen(QtGui.QPen(view.col_ruler_text))
                    p.drawText(x + 4, int(self.height() * 0.85), str(bars_before(bar_tick) + 1))
                    p.setPen(pen_beat)
                    for b in range(1, max(1, num)):
                        bt = bar_tick + b * tpbeat
                        if bt >= seg_right: break
                        xb = x_from_tick(bt)
                        if -40 <= xb <= self.width() + 40:
                            p.drawLine(xb, 0, xb, int(self.height() * 0.55))
                bar_tick += tpb

        marks = getattr(self.roll.p, "markers", []) or []
        if marks:
            pen_m = QtGui.QPen(QtGui.QColor("#ffd166")); pen_m.setWidth(2)
            p.setPen(pen_m); p.setBrush(QtGui.QBrush(QtGui.QColor("#ffd166")))
            for m in marks:
                x = x_from_tick(float(getattr(m, "tick", 0)))
                if -60 <= x <= self.width() + 60:
                    p.drawLine(x, 0, x, self.height()-1)
                    txt = str(getattr(m, "name", ""))
                    p.drawText(x + 4, int(self.height() * 0.85) - fm.descent(), txt)

        p.setPen(QtGui.QPen(self.roll.col_ruler_bottom))
        p.drawLine(0, self.height()-1, self.width(), self.height()-1)

    def _pos_to_tick(self, pos: QtCore.QPointF) -> int:
        view = self.roll
        x_in_view = pos.x() + view.GUTTER_W
        x_scene = view.mapToScene(int(x_in_view), 0).x()
        return int(round(view._x_to_tick_float(x_scene)))

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            tick = self._pos_to_tick(e.position())
            self._scrub_active = True
            self._last_scrub_tick = tick
            self.setCursor(Qt.SplitHCursor)
            self.grabMouse()
            if hasattr(self.roll, "t") and hasattr(self.roll.t, "set_pos"):
                self.roll.t.set_pos(tick)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        if self._scrub_active:
            tick = self._pos_to_tick(e.position())
            if tick != self._last_scrub_tick:
                self._last_scrub_tick = tick
                if hasattr(self.roll, "t") and hasattr(self.roll.t, "set_pos"):
                    self.roll.t.set_pos(tick)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and self._scrub_active:
            self._scrub_active = False
            self._last_scrub_tick = None
            self.unsetCursor()
            self.releaseMouse()
        super().mouseReleaseEvent(e)
        
    def contextMenuEvent(self, e: QtGui.QContextMenuEvent) -> None:
        roll = self.roll
        view_x = int(e.pos().x()) + roll.GUTTER_W
        scene_x = roll.mapToScene(view_x, 0).x()

        hit_m, _ = roll._nearest_marker_at_scene_x(scene_x, px_tol=8)

        menu = QtWidgets.QMenu(self)
        act_add = menu.addAction("Add Marker Here")
        if hit_m is not None:
            act_rename = menu.addAction(f'Rename \"{hit_m.name}\"')
            act_delete = menu.addAction(f'Delete \"{hit_m.name}\"')
        else:
            act_rename = act_delete = None

        chosen = menu.exec(e.globalPos())
        if not chosen:
            return

        if chosen == act_add:
            tick = int(roll.x_to_tick(scene_x))
            name, ok = QtWidgets.QInputDialog.getText(self, "Add Marker", "Name:", text="Section")
            if ok and str(name).strip():
                roll.p.markers.append(Marker(tick=tick, name=str(name).strip()))
                roll.p.markers.sort(key=lambda m: m.tick)
                self.update()
            return

        if act_rename and chosen == act_rename:
            new_name, ok = QtWidgets.QInputDialog.getText(self, "Rename Marker", "Name:", text=hit_m.name)
            if ok and str(new_name).strip():
                hit_m.name = str(new_name).strip()
                self.update()
            return

        if act_delete and chosen == act_delete:
            roll.p.markers = [m for m in roll.p.markers if m is not hit_m]
            self.update()
            return
