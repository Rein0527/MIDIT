# modules/ui/tracks_panel.py
# =============================================================================
#  Imports / Constants
# =============================================================================
from __future__ import annotations
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

from ..core.models import Project, Track
from ..utils.config import COLOR_POOL, GM_NAMES

MIME_TRACK_IDX = "application/x-daw-track-index"


# =============================================================================
#  SECTION 0: Widgets Helpers (Tooltip VolumeSlider)
# =============================================================================
class VolumeSlider(QtWidgets.QSlider):
    """
    QSlider(0..127) -> 顯示 0..100 的提示數值。按下/拖動時在游標旁顯示。
    - 保留原本的 valueChanged(int) 訊號（0..127），不影響外部行為。
    """
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setTracking(True)
        self.setRange(0, 127)

    def _value_to_percent(self, v: int) -> int:
        return max(0, min(100, round(int(v) * 100 / 127)))

    def _show_tip(self):
        pct = self._value_to_percent(self.value())
        pos = QtGui.QCursor.pos()
        QtWidgets.QToolTip.showText(pos, f"{pct}%", self)

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mousePressEvent(e)
        self._show_tip()

    def mouseMoveEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mouseMoveEvent(e)
        if e.buttons() & Qt.LeftButton:
            self._show_tip()

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(e)
        QtWidgets.QToolTip.hideText()

    def leaveEvent(self, e: QtCore.QEvent) -> None:
        QtWidgets.QToolTip.hideText()
        return super().leaveEvent(e)

# =============================================================================
#  SECTION A: 單一音軌項目（TrackItem）
#  - 顏色選擇、名稱編輯、Program/Volume、Mute/Solo、View:Active、Freeze、Lock
#  - 可啟動拖曳，用於排序 / 拖入群組
# =============================================================================
class TrackItem(QtWidgets.QFrame):
    # ---- Signals -------------------------------------------------------------
    clicked = QtCore.Signal()
    programChanged = QtCore.Signal(int)
    volumeChanged = QtCore.Signal(int)
    muteChanged = QtCore.Signal(bool)
    soloChanged = QtCore.Signal(bool)
    colorChanged = QtCore.Signal(QtGui.QColor)
    nameChanged = QtCore.Signal(str)
    deleteClicked = QtCore.Signal()
    viewActiveChanged = QtCore.Signal(bool)
    collapsedChanged = QtCore.Signal(bool)
    freezeChanged = QtCore.Signal(bool)
    lockChanged = QtCore.Signal(bool)

    # ---- Init ---------------------------------------------------------------
    def __init__(self, index: int, track: Track, color: QtGui.QColor):
        super().__init__()
        self.index = index
        self.track = track

        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setObjectName("TrackItem")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        # -- row: head（色塊 / 名稱 / Ch / 刪除） ------------------------------
        head = QtWidgets.QHBoxLayout(); head.setSpacing(6)

        # ▼/▶ disclosure
        self.toggle_btn = QtWidgets.QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setToolTip("Expand / Collapse")
        self.toggle_btn.setFixedWidth(22)
        self.toggle_btn.toggled.connect(self._on_toggle)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(16, 16)
        self._set_color(color)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.mousePressEvent = self._choose_color  # type: ignore[assignment]

        self.name_edit = QtWidgets.QLineEdit(track.name)
        self.name_edit.setFrame(False)
        self.name_edit.setStyleSheet("font-weight:600; background:transparent; border:none;")
        self.name_edit.editingFinished.connect(self._rename)

        ch_lab = QtWidgets.QLabel(f"Ch {track.channel}")
        ch_lab.setStyleSheet("color:#bbb;")

        btn_del = QtWidgets.QToolButton()
        btn_del.setText("🗑")
        btn_del.clicked.connect(lambda: self.deleteClicked.emit())

        head.addWidget(self.toggle_btn)
        head.addWidget(self.swatch)
        head.addWidget(self.name_edit, 1)
        head.addWidget(ch_lab)
        head.addWidget(btn_del)
        lay.addLayout(head)

        # === 可收合的 body 容器 ===============================================
        self.body_widget = QtWidgets.QWidget()
        body_v = QtWidgets.QVBoxLayout(self.body_widget)
        body_v.setContentsMargins(0, 0, 0, 0)
        body_v.setSpacing(6)

        # -- row: Program + Volume --------------------------------------------
        row2 = QtWidgets.QHBoxLayout()
        self.prog = QtWidgets.QComboBox()
        self.prog.addItems([f"{i:03d} {n}" for i, n in enumerate(GM_NAMES)])
        self.prog.setCurrentIndex(track.program)
        self.prog.currentIndexChanged.connect(self.programChanged)

        self.vol = VolumeSlider(Qt.Horizontal)
        self.vol.setValue(track.volume)
        self.vol.valueChanged.connect(self.volumeChanged)

        row2.addWidget(QtWidgets.QLabel("Prog"))
        row2.addWidget(self.prog, 1)
        row2.addWidget(QtWidgets.QLabel("Vol"))
        row2.addWidget(self.vol, 1)
        body_v.addLayout(row2)

        # -- row: M / S / F / L + View:Active --------------------------------
        row3 = QtWidgets.QHBoxLayout()
        btn_m = QtWidgets.QToolButton(); btn_m.setText("M"); btn_m.setCheckable(True); btn_m.setChecked(track.mute)
        btn_s = QtWidgets.QToolButton(); btn_s.setText("S"); btn_s.setCheckable(True); btn_s.setChecked(track.solo)
        btn_m.toggled.connect(self.muteChanged)
        btn_s.toggled.connect(self.soloChanged)

        # freeze
        self.btn_freeze = QtWidgets.QToolButton()
        self.btn_freeze.setText("F")
        self.btn_freeze.setCheckable(True)
        self.btn_freeze.setChecked(bool(getattr(track, "frozen", False)))
        self.btn_freeze.toggled.connect(self._emit_freeze)

        self.snow = QtWidgets.QLabel("❄️")
        self.snow.setVisible(bool(getattr(track, "frozen", False)))
        self.snow.setToolTip("Frozen")

        # lock
        self.btn_lock = QtWidgets.QToolButton()
        self.btn_lock.setText("L")
        self.btn_lock.setCheckable(True)
        self.btn_lock.setChecked(bool(getattr(track, "locked", False)))
        self.btn_lock.toggled.connect(self._emit_lock)

        self.padlock = QtWidgets.QLabel("🔒")
        self.padlock.setVisible(bool(getattr(track, "locked", False)))
        self.padlock.setToolTip("Locked")

        # view only
        self.view_chk = QtWidgets.QCheckBox("View: Active")
        self.view_chk.setChecked(getattr(track, "view_active", False))
        self.view_chk.toggled.connect(self.viewActiveChanged)

        # 排列：M / S / F / ❄️ / 🔒 / View: Active
        row3.addWidget(btn_m)
        row3.addWidget(btn_s)
        row3.addWidget(self.btn_freeze)
        row3.addWidget(self.snow)
        row3.addWidget(self.btn_lock)
        row3.addWidget(self.padlock)
        row3.addWidget(self.view_chk)
        row3.addStretch(1)
        body_v.addLayout(row3)

        lay.addWidget(self.body_widget)

        # === 初始化收窄狀態 ====================================================
        self._collapsed = bool(getattr(track, "collapsed", False))
        self.set_collapsed(self._collapsed, emit=False)

        # --- 拖曳狀態 ----------------------------------------------------------
        self._list_index = self.index
        self._press_pos = QtCore.QPoint()

    # ---- Freeze slot ---------------------------------------------------------
    def _emit_freeze(self, on: bool):
        self.snow.setVisible(bool(on))
        self.freezeChanged.emit(bool(on))

    # ---- Lock slot -----------------------------------------------------------
    def _emit_lock(self, on: bool):
        self.padlock.setVisible(bool(on))
        self.lockChanged.emit(bool(on))

    # ---- Collapse ------------------------------------------------------------
    def _on_toggle(self, checked: bool):
        self.set_collapsed(checked)

    def set_collapsed(self, collapsed: bool, *, emit: bool = True):
        self._collapsed = bool(collapsed)
        self.toggle_btn.setChecked(self._collapsed)
        self.toggle_btn.setText("▶" if self._collapsed else "▼")
        self.body_widget.setVisible(not self._collapsed)
        if emit:
            self.collapsedChanged.emit(self._collapsed)

    # ---- Public --------------------------------------------------------------
    def set_list_index(self, idx: int):
        self._list_index = int(idx)

    def set_active(self, active: bool):
        self.setProperty("active", active)
        self.style().unpolish(self); self.style().polish(self); self.update()

    # ---- Internal: 顏色/名稱/拖曳 -------------------------------------------
    def _set_color(self, color: QtGui.QColor):
        self.track.color = color
        self.swatch.setStyleSheet(f"background:{color.name()}; border:1px solid #333;")

    def _choose_color(self, e: QtGui.QMouseEvent):
        col = QtWidgets.QColorDialog.getColor(self.track.color, self, "Select Track Color")
        if col.isValid():
            self._set_color(col)
            self.colorChanged.emit(col)

    def _rename(self):
        new_name = (self.name_edit.text() or "").strip() or "Track"
        self.track.name = new_name
        self.nameChanged.emit(new_name)

    def mousePressEvent(self, e: QtGui.QMouseEvent):
        self.clicked.emit()
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position().toPoint()
        return super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QtGui.QMouseEvent):
        if e.buttons() & Qt.LeftButton:
            if (e.position().toPoint() - self._press_pos).manhattanLength() > QtWidgets.QApplication.startDragDistance():
                self._start_drag(); return
        return super().mouseMoveEvent(e)

    def _start_drag(self):
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setData(MIME_TRACK_IDX, str(self._list_index).encode("utf-8"))
        drag.setMimeData(mime)
        pm = self.grab()
        drag.setPixmap(pm)
        drag.setHotSpot(QtCore.QPoint(pm.width() // 2, pm.height() // 2))
        drag.exec(Qt.MoveAction)

# =============================================================================
#  SECTION A.1: 群組項目（GroupItem）
# =============================================================================
class GroupItem(QtWidgets.QFrame):
    clicked = QtCore.Signal()
    deleteRequested = QtCore.Signal()
    muteChanged = QtCore.Signal(bool)
    soloChanged = QtCore.Signal(bool)
    colorChanged = QtCore.Signal(QtGui.QColor)
    nameChanged = QtCore.Signal(str)
    collapsedChanged = QtCore.Signal(bool)

    def __init__(self, panel: "TracksPanel", group: object):
        super().__init__()
        self.panel = panel
        self.group = group

        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setObjectName("GroupItem")

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

        # --- head row ---
        head = QtWidgets.QHBoxLayout(); head.setSpacing(6)

        self.toggle_btn = QtWidgets.QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setText("▶" if bool(getattr(group, "collapsed", False)) else "▼")
        self.toggle_btn.toggled.connect(self._on_toggle)

        self.swatch = QtWidgets.QLabel()
        self.swatch.setFixedSize(16, 16)
        self._set_color(getattr(group, "color", None) or QtGui.QColor("#888"))
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.mousePressEvent = self._choose_color

        self.name_edit = QtWidgets.QLineEdit(getattr(group, "name", "Group"))
        self.name_edit.setFrame(False)
        self.name_edit.setStyleSheet("font-weight:700; background:transparent; border:none;")
        self.name_edit.editingFinished.connect(self._rename)

        btn_m = QtWidgets.QToolButton(); btn_m.setText("M"); btn_m.setCheckable(True); btn_m.setChecked(bool(getattr(group, "mute", False)))
        btn_s = QtWidgets.QToolButton(); btn_s.setText("S"); btn_s.setCheckable(True); btn_s.setChecked(bool(getattr(group, "solo", False)))
        btn_m.toggled.connect(self.muteChanged)
        btn_s.toggled.connect(self.soloChanged)

        btn_del = QtWidgets.QToolButton()
        btn_del.setText("🗑")
        btn_del.setToolTip("Delete Group (tracks will be kept)")
        btn_del.clicked.connect(lambda: self.deleteRequested.emit())

        head.addWidget(self.toggle_btn)
        head.addWidget(self.swatch)
        head.addWidget(self.name_edit, 1)
        head.addWidget(btn_m)
        head.addWidget(btn_s)
        head.addWidget(btn_del)
        lay.addLayout(head)

        # --- body（子軌容器 + drop 區） ---
        self.body = QtWidgets.QFrame()
        self.body.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.body.setAcceptDrops(True)
        self.body.installEventFilter(self)
        bv = QtWidgets.QVBoxLayout(self.body)
        bv.setContentsMargins(6, 6, 6, 6)
        bv.setSpacing(6)

        # 永遠保留一個「音軌高度」的投遞區（最後一個）
        self.drop_placeholder = QtWidgets.QFrame()
        self.drop_placeholder.setFixedHeight(64)
        self.drop_placeholder.setStyleSheet("border:1px dashed #5aa0ff;")
        bv.addWidget(self.drop_placeholder)

        lay.addWidget(self.body)

        self.set_collapsed(bool(getattr(group, "collapsed", False)), emit=False)

    def add_child_item(self, w: QtWidgets.QWidget):
        v: QtWidgets.QVBoxLayout = self.body.layout()
        v.insertWidget(max(0, v.count() - 1), w)

    def _on_toggle(self, checked: bool):
        self.set_collapsed(checked)

    def set_collapsed(self, collapsed: bool, *, emit=True):
        self.toggle_btn.setText("▶" if collapsed else "▼")
        self.body.setVisible(not collapsed)
        try:
            setattr(self.group, "collapsed", bool(collapsed))
        except Exception:
            pass
        if emit:
            self.collapsedChanged.emit(bool(collapsed))

    def _set_color(self, color):
        c = color if isinstance(color, QtGui.QColor) else QtGui.QColor(color)
        try:
            setattr(self.group, "color", c)
        except Exception:
            pass
        self.swatch.setStyleSheet(f"background:{c.name()}; border:1px solid #333;")

    def _choose_color(self, e: QtGui.QMouseEvent):
        col = QtWidgets.QColorDialog.getColor(getattr(self.group, "color", QtGui.QColor("#888")), self, "Select Group Color")
        if col.isValid():
            self._set_color(col)
            self.colorChanged.emit(col)

    def _rename(self):
        new_name = (self.name_edit.text() or "").strip() or "Group"
        try:
            setattr(self.group, "name", new_name)
        except Exception:
            pass
        self.nameChanged.emit(new_name)

    def eventFilter(self, obj: QtCore.QObject, e: QtCore.QEvent) -> bool:
        if obj is self.body:
            t = e.type()
            if t == QtCore.QEvent.DragEnter:
                de: QtGui.QDragEnterEvent = e  # type: ignore
                if de.mimeData().hasFormat(MIME_TRACK_IDX):
                    de.acceptProposedAction(); return True
            elif t == QtCore.QEvent.DragMove:
                dm: QtGui.QDragMoveEvent = e  # type: ignore
                if dm.mimeData().hasFormat(MIME_TRACK_IDX):
                    dm.acceptProposedAction(); return True
            elif t == QtCore.QEvent.Drop:
                dp: QtGui.QDropEvent = e  # type: ignore
                if dp.mimeData().hasFormat(MIME_TRACK_IDX):
                    try:
                        src_idx = int(bytes(dp.mimeData().data(MIME_TRACK_IDX)).decode("utf-8"))
                    except Exception:
                        return True
                    gid = getattr(self.group, "id", None)
                    if gid and 0 <= src_idx < len(self.panel.p.tracks):
                        try:
                            setattr(self.panel.p.tracks[src_idx], "group_id", gid)
                        except Exception:
                            pass
                        self.panel._build()
                    dp.acceptProposedAction()
                    return True
        return super().eventFilter(obj, e)


# =============================================================================
#  SECTION B: 左側音軌面板（TracksPanel）
#  - 產生 GroupItem + TrackItem 清單
#  - 頂層 body 支援拖放重排；拖到頂層 = 脫離群組
# =============================================================================
class TracksPanel(QtWidgets.QWidget):
    # ---- Signals -------------------------------------------------------------
    selectAll = QtCore.Signal()
    selectIndex = QtCore.Signal(int)
    programChanged = QtCore.Signal(int, int)
    volumeChanged = QtCore.Signal(int, int)
    muteChanged = QtCore.Signal(int, bool)
    soloChanged = QtCore.Signal(int, bool)
    colorChanged = QtCore.Signal(int, QtGui.QColor)
    nameChanged = QtCore.Signal(int, str)
    deleteTrack = QtCore.Signal(int)
    addTrack = QtCore.Signal()
    addGroup = QtCore.Signal()
    viewActiveChanged = QtCore.Signal(int, bool)
    freezeToggled = QtCore.Signal(int, bool)
    lockToggled = QtCore.Signal(int, bool)

    # 群組相關（由外層接）
    groupMuteChanged = QtCore.Signal(str, bool)
    groupSoloChanged = QtCore.Signal(str, bool)
    groupNameChanged = QtCore.Signal(str, str)
    groupColorChanged = QtCore.Signal(str, QtGui.QColor)
    groupCollapsedChanged = QtCore.Signal(str, bool)
    groupDeleteRequested = QtCore.Signal(str)

    # ---- Init ---------------------------------------------------------------
    def __init__(self, proj: Project):
        super().__init__()
        self.p = proj
        self.items: List[TrackItem] = []
        self.group_items: dict[str, GroupItem] = {}
        self.active_index: Optional[int] = None
        self.item_by_index: dict[int, TrackItem] = {}
        self.setObjectName("TracksPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
QWidget#TracksPanel { background: palette(Window); }

QFrame#TrackItem[active="true"] {
    border: 2px solid #5aa0ff;
    border-radius: 6px;
    background: rgba(90,160,255,0.08);
}
QFrame#TrackItem[active="false"], QFrame#TrackItem {
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 6px;
}
""")

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # Add Group / Add Track
        self.add_group_btn = QtWidgets.QPushButton("＋ Add Group")
        self.add_group_btn.clicked.connect(lambda: self.addGroup.emit())
        outer.addWidget(self.add_group_btn, 0)

        self.add_btn = QtWidgets.QPushButton("＋ Add Track")
        self.add_btn.clicked.connect(lambda: self.addTrack.emit())
        outer.addWidget(self.add_btn, 0)

        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        self.body = QtWidgets.QWidget()
        self.body.setAcceptDrops(True)
        self.body.installEventFilter(self)
        self.v = QtWidgets.QVBoxLayout(self.body)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(10)
        self.scroll.setWidget(self.body)

        self._drop_line = QtWidgets.QFrame(self.body)
        self._drop_line.setFrameShape(QtWidgets.QFrame.HLine)
        self._drop_line.setStyleSheet("color:#5aa0ff; background:#5aa0ff;")
        self._drop_line.setFixedHeight(2)
        self._drop_line.hide()

        self._build()

    def set_active_index(self, idx: Optional[int]):
        self.active_index = idx
        for it in self.item_by_index.values():
            it.set_active(False)
        if idx is not None and idx in self.item_by_index:
            self.item_by_index[idx].set_active(True)

    def _build(self):
        while self.v.count():
            item = self.v.takeAt(0)
            if w := item.widget():
                w.deleteLater()

        self._drop_line.hide()

        self.item_by_index.clear()

        allf = QtWidgets.QFrame(); allf.setFrameShape(QtWidgets.QFrame.StyledPanel)
        l = QtWidgets.QHBoxLayout(allf); l.setContentsMargins(8, 6, 8, 6)

        lab = QtWidgets.QLabel("All Tracks")
        lab.setStyleSheet("font-weight:700;")
        l.addWidget(lab)

        l.addStretch(1)

        vol_lab = QtWidgets.QLabel("Vol")
        vol_lab.setToolTip("Master Volume")
        vol_lab.setStyleSheet("padding-right:6px;")
        l.addWidget(vol_lab, 0, Qt.AlignRight)

        self.master_slider = QtWidgets.QSlider(Qt.Horizontal)
        self.master_slider.setRange(0, 100)
        self.master_slider.setValue(int(getattr(self.p, "master_volume", 100)))
        self.master_slider.setFixedWidth(90)
        self.master_slider.setMaximumHeight(14)
        self.master_slider.setToolTip("Master Volume")
        l.addWidget(self.master_slider, 0, Qt.AlignRight)

        def _on_master(v: int):
            self.p.master_volume = int(v)
            if self.master_slider.isSliderDown():
                QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"{int(v)}%")

        self.master_slider.valueChanged.connect(_on_master)
        self.master_slider.sliderPressed.connect(
            lambda: QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"{self.master_slider.value()}%")
        )

        # 可選：右鍵點滑桿→精確輸入
        def _master_context_menu(pos):
            menu = QtWidgets.QMenu(self.master_slider)
            act_set = menu.addAction("Set Value…")
            if menu.exec_(self.master_slider.mapToGlobal(pos)) is act_set:
                val, ok = QtWidgets.QInputDialog.getInt(self.master_slider, "Master Volume",
                                                        "0–100 (%)", self.master_slider.value(), 0, 100, 1)
                if ok:
                    self.master_slider.setValue(val)

        self.master_slider.setContextMenuPolicy(Qt.CustomContextMenu)
        self.master_slider.customContextMenuRequested.connect(_master_context_menu)

        # 點 All Tracks → selectAll
        allf.mousePressEvent = lambda e: self.selectAll.emit()  # type: ignore[assignment]
        self.v.addWidget(allf)

        # 先建立所有 GroupItem（若 Project 未定義 groups，略過）
        self.group_items = {}
        for g in list(getattr(self.p, "groups", [])):
            gi = GroupItem(self, g)
            gi.muteChanged.connect(lambda v, gid=g.id: self.groupMuteChanged.emit(gid, v))
            gi.soloChanged.connect(lambda v, gid=g.id: self.groupSoloChanged.emit(gid, v))
            gi.colorChanged.connect(lambda col, gid=g.id: self.groupColorChanged.emit(gid, col))
            gi.nameChanged.connect(lambda name, gid=g.id: self.groupNameChanged.emit(gid, name))
            gi.collapsedChanged.connect(lambda c, gid=g.id: self.groupCollapsedChanged.emit(gid, c))
            gi.deleteRequested.connect(lambda gid=g.id: self.groupDeleteRequested.emit(gid))
            self.v.addWidget(gi)
            self.group_items[getattr(g, "id")] = gi

        # 再建立 TrackItem；依 group_id 放到對應群組或留在頂層
        items_top: List[TrackItem] = []
        for i, tr in enumerate(self.p.tracks):
            # 若 color 還不是 QColor，就從 COLOR_POOL 指派預設色
            if not isinstance(tr.color, QtGui.QColor):
                r, g, b = COLOR_POOL[i % len(COLOR_POOL)]
                tr.color = QtGui.QColor(r, g, b)

            item = TrackItem(i, tr, tr.color)
            item.set_list_index(i)

            # 登錄到扁平索引表（★關鍵：不分群組）
            self.item_by_index[i] = item

            # 對應信號轉接到面板（帶 index）
            item.clicked.connect(lambda i=i: self.selectIndex.emit(i))
            item.programChanged.connect(lambda prog, i=i: self.programChanged.emit(i, prog))
            item.volumeChanged.connect(lambda vol, i=i: self.volumeChanged.emit(i, vol))
            item.muteChanged.connect(lambda v, i=i: self.muteChanged.emit(i, v))
            item.soloChanged.connect(lambda v, i=i: self.soloChanged.emit(i, v))
            item.colorChanged.connect(lambda col, i=i: self.colorChanged.emit(i, col))
            item.nameChanged.connect(lambda name, i=i: self.nameChanged.emit(i, name))
            item.deleteClicked.connect(lambda i=i: self.deleteTrack.emit(i))
            item.viewActiveChanged.connect(lambda v, i=i: self.viewActiveChanged.emit(i, v))
            item.freezeChanged.connect(lambda on, i=i: self.freezeToggled.emit(i, bool(on)))
            item.lockChanged.connect(lambda on, i=i: self.lockToggled.emit(i, bool(on)))

            try:
                item.set_collapsed(bool(getattr(tr, "collapsed", False)), emit=False)
            except Exception:
                pass
            item.collapsedChanged.connect(lambda c, i=i: setattr(self.p.tracks[i], "collapsed", bool(c)))

            gid = getattr(tr, "group_id", None)
            if gid and gid in self.group_items:
                self.group_items[gid].add_child_item(item)
            else:
                self.v.addWidget(item)
                items_top.append(item)

        self.items = items_top  # 只把「頂層」TrackItem 存起來用於頂層排序
        self.v.addStretch(1)
        # 重新套用現有選取（會用扁平索引表正確高亮）
        self.set_active_index(self.active_index)

    # =============================================================================
    #  SECTION C: 頂層拖放排序（攔在 body）
    #    - 只作用於「頂層」TrackItem 的順序
    #    - 拖到頂層 = 脫離群組 (track.group_id=None)
    # =============================================================================
    def _index_at_pos(self, pos_y: int) -> int:
        if not getattr(self, "items", None):
            return 0
        for i, w in enumerate(self.items):
            top = w.mapTo(self.body, QtCore.QPoint(0, 0)).y()
            bottom = top + w.height()
            mid = (top + bottom) // 2
            if pos_y < mid:
                return i
            if top <= pos_y <= bottom:
                return i + 1 if pos_y >= mid else i
        return len(self.items)

    def eventFilter(self, obj: QtCore.QObject, e: QtCore.QEvent) -> bool:
        if obj is self.body:
            t = e.type()
            if t == QtCore.QEvent.DragEnter:
                de: QtGui.QDragEnterEvent = e
                if de.mimeData().hasFormat(MIME_TRACK_IDX):
                    de.acceptProposedAction(); return True

            # ---- DragMove -----------------------------------------------------
            elif t == QtCore.QEvent.DragMove:
                dm: QtGui.QDragMoveEvent = e
                if dm.mimeData().hasFormat(MIME_TRACK_IDX):
                    dm.acceptProposedAction()
                    body_pos = dm.position().toPoint()
                    ins_idx = self._index_at_pos(body_pos.y())

                    if ins_idx <= 0 and self.items:
                        y = self.items[0].mapTo(self.body, QtCore.QPoint(0, 0)).y()
                    elif ins_idx >= len(self.items) and self.items:
                        last = self.items[-1]
                        y = last.mapTo(self.body, QtCore.QPoint(0, 0)).y() + last.height()
                    else:
                        y = self.items[ins_idx].mapTo(self.body, QtCore.QPoint(0, 0)).y() if self.items else 0

                    self._drop_line.setGeometry(6, y, self.body.width() - 12, 2)
                    self._drop_line.show()
                    return True

            # ---- DragLeave ----------------------------------------------------
            elif t == QtCore.QEvent.DragLeave:
                self._drop_line.hide(); return True

            # ---- Drop（頂層） --------------------------------------------------
            elif t == QtCore.QEvent.Drop:
                dp: QtGui.QDropEvent = e
                self._drop_line.hide()
                if not dp.mimeData().hasFormat(MIME_TRACK_IDX):
                    return True

                try:
                    src_idx = int(bytes(dp.mimeData().data(MIME_TRACK_IDX)).decode("utf-8"))
                except Exception:
                    return True

                body_pos = dp.position().toPoint()
                dst_idx = self._index_at_pos(body_pos.y())

                n = len(getattr(self, "items", []))
                src_idx = max(0, min(len(self.p.tracks) - 1, src_idx))
                dst_idx = max(0, min(n, dst_idx))

                if 0 <= src_idx < len(self.p.tracks):
                    tr = self.p.tracks.pop(src_idx)
                    try:
                        setattr(tr, "group_id", None)
                    except Exception:
                        pass

                    dst_real = max(0, min(len(self.p.tracks), dst_idx))
                    self.p.tracks.insert(dst_real, tr)

                    self._build()
                    if self.active_index is not None:
                        self.selectIndex.emit(self.active_index)

                dp.acceptProposedAction()
                return True

        return super().eventFilter(obj, e)
