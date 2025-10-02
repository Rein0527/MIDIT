# modules/ui/main_window.py
# =============================================================================
#  Imports / Constants
# =============================================================================
from __future__ import annotations

import logging, math, os, subprocess, sys, shutil, tempfile
from typing import Optional
from PySide6 import QtCore, QtGui, QtWidgets, QtMultimedia
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

from ..audio.synth import MidiOut, SynthOut
from ..core import io_midi
from ..core.models import Message, Project, TempoChange, Track, mido, TrackGroup, Marker
from ..core.transport import Transport
from ..utils.config import load_config, save_config, COLOR_POOL, DEFAULT_BPM, DEFAULT_LATENCY_PROFILE, MAX_TRACKS
from .piano_roll import PianoRoll
from .preferences_dialog import PreferencesDialog
from .theme import apply_theme as _apply_theme
from .tracks_panel import TracksPanel


class _TimeSigChange:
    __slots__ = ("tick", "num", "den")
    def __init__(self, tick: int, num: int, den: int):
        self.tick = int(tick)
        self.num  = int(num)
        self.den  = int(den)

class ExportMidiDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, total_tracks=0):
        super().__init__(parent)
        self.setWindowTitle("Export MIDI Options")
        self.setModal(True)
        self.chk_only_solo    = QtWidgets.QCheckBox("僅匯出 Solo 軌")
        self.chk_only_visible = QtWidgets.QCheckBox("僅匯出可見軌")
        self.chk_ignore_mute  = QtWidgets.QCheckBox("忽略 Mute（不排除靜音軌）")
        self.lbl_preview = QtWidgets.QLabel(f"將匯出 0 軌 / 全部 {total_tracks} 軌")
        self.lbl_preview.setStyleSheet("color:#aaa;")
        btn_ok = QtWidgets.QPushButton("匯出")
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.chk_only_solo)
        layout.addWidget(self.chk_only_visible)
        layout.addWidget(self.chk_ignore_mute)
        layout.addSpacing(6)
        layout.addWidget(self.lbl_preview)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1); row.addWidget(btn_ok); row.addWidget(btn_cancel)
        layout.addSpacing(6); layout.addLayout(row)

    def set_preview(self, n_filtered: int, total: int):
        self.lbl_preview.setText(f"將匯出 {n_filtered} 軌 / 全部 {total} 軌")

    def values(self):
        return dict(
            only_solo=self.chk_only_solo.isChecked(),
            only_visible=self.chk_only_visible.isChecked(),
            ignore_mute=self.chk_ignore_mute.isChecked(),
        )

class ExportWavDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, preset_sf: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Export Audio (WAV) – Fluidsynth")
        self.setModal(True)
        self.setMinimumWidth(520)

        # Widgets
        self.ed_sf = QtWidgets.QLineEdit(preset_sf)
        self.btn_browse = QtWidgets.QPushButton("瀏覽…")
        self.btn_browse.clicked.connect(self._pick_sf)
        self.cmb_rate = QtWidgets.QComboBox()
        self.cmb_rate.addItems(["44100", "48000", "96000"])
        self.cmb_rate.setCurrentText("48000")
        self.cmb_format = QtWidgets.QComboBox()
        self.cmb_format.addItems(["s16", "s24", "s32", "float"])
        self.cmb_format.setCurrentText("s24")
        self.chk_reverb = QtWidgets.QCheckBox("啟用 Reverb")
        self.chk_chorus = QtWidgets.QCheckBox("啟用 Chorus")
        self.chk_reverb.setChecked(False)
        self.chk_chorus.setChecked(False)
        self.cmb_gain = QtWidgets.QComboBox()
        self.cmb_gain.addItems(["0.2","0.4","0.6","0.8","1.0","1.2"])
        self.cmb_gain.setCurrentText("0.6")

        # Layout
        form = QtWidgets.QFormLayout()
        h_sf = QtWidgets.QHBoxLayout()
        h_sf.addWidget(self.ed_sf, 1)
        h_sf.addWidget(self.btn_browse)
        form.addRow("SoundFont (.sf2)：", h_sf)
        form.addRow("取樣率 (Hz)：", self.cmb_rate)
        form.addRow("位元格式：", self.cmb_format)

        row_fx = QtWidgets.QHBoxLayout()
        row_fx.addWidget(self.chk_reverb)
        row_fx.addWidget(self.chk_chorus)
        row_fx.addStretch(1)
        form.addRow("效果：", row_fx)

        form.addRow("輸出增益：", self.cmb_gain)

        btn_ok = QtWidgets.QPushButton("匯出")
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_ok.clicked.connect(self._accept_if_valid)
        btn_cancel.clicked.connect(self.reject)

        row_btn = QtWidgets.QHBoxLayout()
        row_btn.addStretch(1)
        row_btn.addWidget(btn_ok)
        row_btn.addWidget(btn_cancel)

        root = QtWidgets.QVBoxLayout(self)
        root.addLayout(form)
        root.addSpacing(8)
        root.addLayout(row_btn)

    def _pick_sf(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "選擇 SoundFont", "", "SoundFont (*.sf2)"
        )
        if path:
            self.ed_sf.setText(path)

    def _accept_if_valid(self):
        p = self.ed_sf.text().strip()
        if not p or not os.path.isfile(p):
            QtWidgets.QMessageBox.warning(self, "路徑無效", "請選擇有效的 .sf2 檔案。")
            return
        self.accept()

    def values(self):
        return dict(
            sf=self.ed_sf.text().strip(),
            rate=self.cmb_rate.currentText().strip(),
            fmt=self.cmb_format.currentText().strip(),
            reverb=self.chk_reverb.isChecked(),
            chorus=self.chk_chorus.isChecked(),
            gain=self.cmb_gain.currentText().strip(),
        )

# =============================================================================
#  Main Window
# =============================================================================
class MainWindow(QtWidgets.QMainWindow):
    # -------------------------------------------------------------------------
    #  建構：設定、核心物件、UI 佈局、訊號、初始路由
    # -------------------------------------------------------------------------
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MIDIT")
        self.resize(1500, 860)

        # ----- 設定與 logging -----
        self.cfg = load_config()
        self._mark_session_start()
        self._init_logging(log_to_file=self.cfg["developer"].get("log_to_file", True))
        logging.getLogger().setLevel(
            logging.DEBUG if self.cfg["developer"].get("debug_log", False) else logging.INFO
        )
        logging.info("App started with debug_log=%s", self.cfg["developer"].get("debug_log", False))

        # 主題（以 cfg 為準）
        self._apply_theme_from_cfg()

        # ----- Core -----
        self.proj = Project()
        self.synth = SynthOut()
        self.midi = MidiOut()
        self.trans = Transport(self.proj, self.synth, self.midi)
        _initial_bpm_from_cfg = self._apply_defaults_from_config()


        # 顏色歸一（core.color 可能是 (r,g,b) 或 None）
        self._coerce_track_colors()

        # ----- Layout -----
        splitter = QtWidgets.QSplitter(Qt.Horizontal)
        splitter.setObjectName("MainSplitter")
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            """
            QSplitter#MainSplitter { background: palette(Window); }
            QSplitter#MainSplitter::handle:horizontal { width: 1px; background: palette(Mid); }
            QSplitter#MainSplitter::handle:vertical   { height: 1px; background: palette(Mid); }
            """
        )

        self.tracks_panel = TracksPanel(self.proj)
        self.roll = PianoRoll(self.proj, self.trans)

        right = QtWidgets.QWidget()
        right_lay = QtWidgets.QHBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        right_lay.addWidget(self.roll, 1)

        splitter.addWidget(self.tracks_panel)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.roll.setFocusPolicy(Qt.StrongFocus)
        self.roll.setFocus()

        # ----- Signals -----
        # 試彈（來自 PianoRoll 左側鍵盤）
        self.roll.notePreviewOn.connect(lambda p: self._preview_note(p, True))
        self.roll.notePreviewOff.connect(lambda p: self._preview_note(p, False))

        # TracksPanel 事件
        self.tracks_panel.selectAll.connect(self._select_all_tracks)
        self.tracks_panel.selectIndex.connect(self._select_track_index)
        self.tracks_panel.programChanged.connect(self._set_track_program)
        self.tracks_panel.volumeChanged.connect(self._set_track_volume)
        self.tracks_panel.muteChanged.connect(self._set_track_mute)
        self.tracks_panel.soloChanged.connect(self._set_track_solo)
        self.tracks_panel.colorChanged.connect(self._set_track_color)
        self.tracks_panel.nameChanged.connect(self._set_track_name)
        self.tracks_panel.deleteTrack.connect(self._delete_track)
        self.tracks_panel.addTrack.connect(self._add_track)
        self.tracks_panel.viewActiveChanged.connect(self._set_track_view_active)
        
        # 確保專案有 groups 容器（初次載入／開新檔）
        self._ensure_groups_list()
        self.tracks_panel.addGroup.connect(self._add_group)
        self.tracks_panel.groupMuteChanged.connect(self._on_group_mute)
        self.tracks_panel.groupSoloChanged.connect(self._on_group_solo)
        self.tracks_panel.groupNameChanged.connect(self._on_group_rename)
        self.tracks_panel.groupColorChanged.connect(self._on_group_color)
        self.tracks_panel.groupCollapsedChanged.connect(self._on_group_collapse)
        self.tracks_panel.groupDeleteRequested.connect(self._on_group_delete)

        # Menu / Toolbar
        self._build_menubar()
        self._build_toolbar()

        self._maybe_restore_autosave()
        self._auto_backup_init()

        # Transport 狀態顯示
        self.trans.playingChanged.connect(self._on_playing_changed)
        self.trans.tickChanged.connect(self._update_time_label)
        self._update_time_label(0)

        # 依偏好設定選擇輸出（system / fluidsynth）
        self._apply_output_from_cfg()

        # 其他初始狀態
        self._sync_loop_spins()
        self._update_visible_tracks()
        self._ensure_valid_active_track()
        self._update_add_button()
        
        # --- Status Bar ------------------------------------------------------------
        self.status = QtWidgets.QStatusBar(self)
        self.setStatusBar(self.status)
        self.speed_spin.valueChanged.connect(lambda _v: self._refresh_status_bpm())

        # 建立幾個固定欄位（用等寬字體比較整齊）
        mono_css = "font-family: 'Consolas','Menlo',monospace;"

        self.sb_pos   = QtWidgets.QLabel("Bar 1 Beat 1");    self.sb_pos.setStyleSheet(mono_css)
        self.sb_bpm   = QtWidgets.QLabel("BPM --");          self.sb_bpm.setStyleSheet(mono_css)
        self.sb_vel   = QtWidgets.QLabel("VEL --");          self.sb_vel.setStyleSheet(mono_css)
        self.sb_notes = QtWidgets.QLabel("Notes 0");         self.sb_notes.setStyleSheet(mono_css)
        self.sb_duration  = QtWidgets.QLabel("Duration 0.0s"); self.sb_duration.setStyleSheet(mono_css)
        self.sb_loop  = QtWidgets.QLabel("Loop Off");        self.sb_loop.setStyleSheet(mono_css)
        self.sb_lock = QtWidgets.QLabel("")     
        self.sb_lock.setStyleSheet("font-family: 'Consolas','Menlo',monospace; color:#ff5555;")

        # 依序放上去+
        for w in (self.sb_pos, self.sb_vel, self.sb_bpm, self.sb_notes, self.sb_duration, self.sb_loop, self.sb_lock):
            self.status.addPermanentWidget(w)

        # 初始一次
        self._refresh_status_all()

        # 連線事件：tick 位置、BPM 改變、場景選取變動、Loop 相關
        self.trans.tickChanged.connect(lambda _t: self._refresh_status_pos())
        self.trans.tickChanged.connect(lambda _t: self._refresh_status_bpm())

        # QGraphicsScene 有 selectionChanged 訊號
        try:
            self.roll.scene.selectionChanged.connect(self._refresh_status_selection)
        except Exception:
            pass
            
        # === Freeze 播放器管理 ===
        self._freeze_players: dict[int, tuple[QMediaPlayer, QAudioOutput]] = {}
        self._last_tick_for_freeze: int | None = None

        # 讓 TracksPanel 的 F 事件接到處理器
        self.tracks_panel.freezeToggled.connect(self._toggle_track_freeze)

        # 播放/暫停時控制凍結播放器
        self.trans.playingChanged.connect(self._on_playing_for_freeze)
        # tick 變化用來偵測跳轉/Loop 折返 → 對齊位置
        self.trans.tickChanged.connect(self._on_tick_for_freeze)
        
        # Track lock 
        self.tracks_panel.lockToggled.connect(self._toggle_track_lock) 
        
        # 上次成功存檔的修訂號
        self._last_saved_rev = int(getattr(self.proj, "rev", 0))
        self._current_filename = "未命名"

    # =============================================================================
    #  Logging
    # =============================================================================
    def _init_logging(self, *, log_to_file: bool = True):
        root = logging.getLogger()
        if getattr(self, "_logging_inited", False):
            return
        root.setLevel(logging.INFO)

        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        # Console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

        # File
        if log_to_file:
            try:
                import pathlib

                proj_root = pathlib.Path(__file__).resolve().parents[2]
                log_dir = proj_root / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
                fh.setFormatter(fmt)
                root.addHandler(fh)
                logging.info("Logging to %s", str(log_dir / "app.log"))
            except Exception as e:
                logging.warning("File logging disabled: %s", e)

        self._logging_inited = True

    # =============================================================================
    #  Menubar
    # =============================================================================
    def _build_menubar(self):
        menubar = self.menuBar()

        # ----- File -----
        file_menu = menubar.addMenu("&File")

        act_new = QtGui.QAction("New File", self)
        act_new.setShortcut(QtGui.QKeySequence("Ctrl+N"))
        act_new.triggered.connect(self._new_midi)
        file_menu.addAction(act_new)

        act_save_proj = QtGui.QAction("Save File…", self)
        act_save_proj.setShortcut(QtGui.QKeySequence("Ctrl+S"))
        act_save_proj.triggered.connect(self.save_project_file)
        file_menu.addAction(act_save_proj)

        act_load_proj = QtGui.QAction("Load File…", self)
        act_load_proj.setShortcut(QtGui.QKeySequence("Ctrl+O"))
        act_load_proj.triggered.connect(self.load_project_file)
        file_menu.addAction(act_load_proj)
        
        act_close = QtGui.QAction("Close File", self)
        act_close.setShortcut(QtGui.QKeySequence("Ctrl+W"))
        act_close.triggered.connect(self.close_midi)
        file_menu.addAction(act_close)

        file_menu.addSeparator()
        
        act_open = QtGui.QAction("Import MIDI", self)
        act_open.triggered.connect(self.open_midi)
        file_menu.addAction(act_open)

        act_save = QtGui.QAction("Export MIDI", self)
        act_save.triggered.connect(self.save_midi)
        file_menu.addAction(act_save)

        # Export Audio (WAV) - Fluidsynth 渲染
        act_export_wav = QtGui.QAction("Export Audio (WAV)", self)
        act_export_wav.triggered.connect(self.export_wav_via_fluidsynth)
        file_menu.addAction(act_export_wav)

        file_menu.addSeparator()
     
        act_close = QtGui.QAction("Exit", self)
        act_close.setShortcut(QtGui.QKeySequence("Ctrl+Q"))
        act_close.triggered.connect(self.close)
        file_menu.addAction(act_close)

        # ----- Edit -----
        edit_menu = menubar.addMenu("&Edit")

        # Undo / Redo
        act_undo = QtGui.QAction("Undo", self)
        act_undo.setShortcuts([QtGui.QKeySequence.Undo])
        act_undo.triggered.connect(self.roll.undo)
        edit_menu.addAction(act_undo)

        act_redo = QtGui.QAction("Redo", self)
        act_redo.setShortcuts([QtGui.QKeySequence.Redo, QtGui.QKeySequence("Ctrl+Shift+Z"), QtGui.QKeySequence("Ctrl+Y")])
        act_redo.triggered.connect(self.roll.redo)
        edit_menu.addAction(act_redo)

        edit_menu.addSeparator()

        # Cut / Copy / Paste
        act_cut = QtGui.QAction("Cut", self)
        act_cut.setShortcuts([QtGui.QKeySequence.Cut])
        act_cut.triggered.connect(self.roll.cut_selection)
        edit_menu.addAction(act_cut)

        act_copy = QtGui.QAction("Copy", self)
        act_copy.setShortcuts([QtGui.QKeySequence.Copy])
        act_copy.triggered.connect(self.roll.copy_selection)
        edit_menu.addAction(act_copy)

        act_paste = QtGui.QAction("Paste", self)
        act_paste.setShortcuts([QtGui.QKeySequence.Paste])
        act_paste.triggered.connect(self.roll.paste_clipboard)
        edit_menu.addAction(act_paste)

        edit_menu.addSeparator()

        # Select All / Delete
        act_selall = QtGui.QAction("Select All", self)
        act_selall.setShortcuts([QtGui.QKeySequence.SelectAll])
        act_selall.triggered.connect(self.roll.select_all_active_track)
        edit_menu.addAction(act_selall)

        act_delete = QtGui.QAction("Delete", self)
        act_delete.setShortcuts([QtGui.QKeySequence.Delete, QtGui.QKeySequence(Qt.Key_Backspace)])
        act_delete.triggered.connect(self.roll.delete_selection)
        edit_menu.addAction(act_delete)

        # 熱鍵在整個 App 範圍生效
        for a in (act_undo, act_redo, act_cut, act_copy, act_paste, act_selall, act_delete, act_close):
            a.setShortcutContext(Qt.ApplicationShortcut)
            self.addAction(a)

        # ----- View -----
        view_menu = menubar.addMenu("&View")

        # 5.1) Auto-scroll（跟隨播放頭）
        act_follow = QtGui.QAction("Follow Playhead (Auto-scroll)", self, checkable=True)
        try:
            _auto_scroll_cfg = bool(self.cfg.get("view", {}).get("auto_scroll", True))
        except Exception:
            _auto_scroll_cfg = True
        act_follow.setChecked(_auto_scroll_cfg)

        # 套用到 PianoRoll（啟動時）
        try:
            self.roll.follow_playhead = bool(_auto_scroll_cfg)
        except Exception:
            pass

        def _toggle_follow(on: bool):
            self.cfg.setdefault("view", {})["auto_scroll"] = bool(on)
            try:
                save_config(self.cfg)
            except Exception:
                pass
            try:
                self.roll.follow_playhead = bool(on)
            except Exception:
                pass

        act_follow.toggled.connect(_toggle_follow)
        view_menu.addAction(act_follow)

        # 5.2) Note Labels（顯示音名文字）
        act_note_labels = QtGui.QAction("Show Note Labels", self, checkable=True)
        try:
            _labels_cfg = bool(self.cfg.get("view", {}).get("show_note_labels", False))
        except Exception:
            _labels_cfg = False
        act_note_labels.setChecked(_labels_cfg)

        # 套用到 PianoRoll（啟動時）
        try:
            self.roll.set_show_note_labels(bool(_labels_cfg))
        except Exception:
            pass

        def _toggle_labels(on: bool):
            self.cfg.setdefault("view", {})["show_note_labels"] = bool(on)
            try:
                save_config(self.cfg)
            except Exception:
                pass
            try:
                # 直接走公開方法，會自動重繪/清除
                self.roll.set_show_note_labels(bool(on))
            except Exception:
                pass

        act_note_labels.toggled.connect(_toggle_labels)
        view_menu.addAction(act_note_labels)

        # ----- Track -----
        track_menu = menubar.addMenu("&Track")

        act_import_tracks = QtGui.QAction("Import Tracks", self)
        act_import_tracks.triggered.connect(self.import_track_presets)
        track_menu.addAction(act_import_tracks)

        act_export_tracks = QtGui.QAction("Export Tracks", self)
        act_export_tracks.triggered.connect(self.export_track_presets)
        track_menu.addAction(act_export_tracks)
        
        # ----- Marker -----
        marker_menu = menubar.addMenu("&Marker")

        act_add_here = QtGui.QAction("Add Marker at Playhead", self)
        act_add_here.setShortcut(QtGui.QKeySequence("Ctrl+M"))
        act_add_here.triggered.connect(self._add_marker_at_playhead)
        marker_menu.addAction(act_add_here)

        marker_menu.addSeparator()

        act_prev = QtGui.QAction("Previous Marker", self)
        act_prev.setShortcut(QtGui.QKeySequence("Alt+,"))
        act_prev.triggered.connect(lambda: self._jump_marker(-1))
        marker_menu.addAction(act_prev)

        act_next = QtGui.QAction("Next Marker", self)
        act_next.setShortcut(QtGui.QKeySequence("Alt+."))
        act_next.triggered.connect(lambda: self._jump_marker(+1))
        marker_menu.addAction(act_next)

        for a in (act_add_here, act_prev, act_next):
            a.setShortcutContext(Qt.ApplicationShortcut)
            self.addAction(a)

        # ----- Time -----
        time_menu = menubar.addMenu("&Time")
        act_timesig = QtGui.QAction("Timesig…", self)
        act_timesig.setShortcut(QtGui.QKeySequence("Ctrl+T"))
        act_timesig.triggered.connect(self._open_timesig_tempo_dialog)
        time_menu.addAction(act_timesig)

        # ----- Setting -----
        self._build_setting_menu(menubar)

    def _build_setting_menu(self, menubar: QtWidgets.QMenuBar):
        setting_menu = menubar.addMenu("&Setting")

        # 1) Preferences...
        act_pref = QtGui.QAction("Preferences...", self)
        act_pref.setShortcut(QtGui.QKeySequence("Ctrl+,"))
        act_pref.triggered.connect(self._open_preferences)
        setting_menu.addAction(act_pref)

        # 2) Audio / MIDI Settings...
        act_audio = QtGui.QAction("Audio / MIDI Settings", self)
        act_audio.triggered.connect(lambda: QtWidgets.QMessageBox.information(self, "Audio / MIDI", "TODO: 裝置/緩衝/取樣率/FluidSynth 開關"))
        setting_menu.addAction(act_audio)

        # 3) Latency Profile
        lat_menu = setting_menu.addMenu("Latency Profile")
        group = QtGui.QActionGroup(self)
        group.setExclusive(True)
        self._latency_actions = {}

        self._latency_actions["low"] = QtGui.QAction("Low (省電)", self, checkable=True)
        self._latency_actions["low"].triggered.connect(lambda: self._set_latency_profile_ui("low"))
        group.addAction(self._latency_actions["low"])
        lat_menu.addAction(self._latency_actions["low"])

        self._latency_actions["medium"] = QtGui.QAction("Medium (預設)", self, checkable=True)
        self._latency_actions["medium"].triggered.connect(lambda: self._set_latency_profile_ui("medium"))
        group.addAction(self._latency_actions["medium"])
        lat_menu.addAction(self._latency_actions["medium"])

        self._latency_actions["high"] = QtGui.QAction("High (低延遲)", self, checkable=True)
        self._latency_actions["high"].triggered.connect(lambda: self._set_latency_profile_ui("high"))
        group.addAction(self._latency_actions["high"])
        lat_menu.addAction(self._latency_actions["high"])

        cur = getattr(self.trans, "_latency_profile", None) or DEFAULT_LATENCY_PROFILE
        if cur not in self._latency_actions:
            cur = DEFAULT_LATENCY_PROFILE
        for key, act in self._latency_actions.items():
            act.setChecked(key == cur)

        # 4) Developer / Experimental
        dev_menu = setting_menu.addMenu("Developer / Experimental")
        act_debug = QtGui.QAction("Enable debug log", self, checkable=True)
        act_debug.setChecked(bool(self.cfg["developer"].get("debug_log", False)))

        def _toggle_debug_log(on: bool):
            logging.getLogger().setLevel(logging.DEBUG if on else logging.INFO)
            logging.info("Debug log %s", "enabled" if on else "disabled")
            self.cfg["developer"]["debug_log"] = bool(on)
            save_config(self.cfg)

        act_debug.toggled.connect(_toggle_debug_log)
        dev_menu.addAction(act_debug)

    def _set_latency_profile_ui(self, profile: str):
        self.trans.set_latency_profile(profile)
        cur = getattr(self.trans, "_latency_profile", profile)
        for key, act in getattr(self, "_latency_actions", {}).items():
            act.setChecked(key == cur)

    # =============================================================================
    #  Toolbar（Transport / View / Tempo / Output）
    # =============================================================================
    def _build_toolbar(self):
        style = QtWidgets.QApplication.style()

        # ----- 第一排：Transport + Loop + 時間 -----
        tb1 = self.addToolBar("Transport")
        tb1.setMovable(True)

        self.act_play = QtGui.QAction(style.standardIcon(QtWidgets.QStyle.SP_MediaPlay), "Play", self)
        self.act_play.triggered.connect(self._toggle_play)
        tb1.addAction(self.act_play)
        # 新增：Space 全域快捷
        self.act_play.setShortcut(QtGui.QKeySequence("Space"))
        self.act_play.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(self.act_play)

        act_stop = QtGui.QAction(style.standardIcon(QtWidgets.QStyle.SP_MediaStop), "Stop", self)
        act_stop.triggered.connect(self.trans.stop)
        tb1.addAction(act_stop)

        # ★ 改成成員變數，方便掛快捷與同步
        self.act_loop = QtGui.QAction("Loop", self)
        self.act_loop.setCheckable(True)
        self.act_loop.toggled.connect(self._toggle_loop)
        tb1.addAction(self.act_loop)
        # 新增：L 全域快捷
        self.act_loop.setShortcut(QtGui.QKeySequence("L"))
        self.act_loop.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(self.act_loop)

        act_rew4 = QtGui.QAction(style.standardIcon(QtWidgets.QStyle.SP_MediaSkipBackward), "Rewind", self)
        act_rew4.setToolTip("倒轉 4 小節")
        act_rew4.triggered.connect(lambda: self._jump_bars(-4))
        tb1.addAction(act_rew4)
        # 新增：Ctrl+Left 全域快捷
        act_rew4.setShortcut(QtGui.QKeySequence("Ctrl+Left"))
        act_rew4.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(act_rew4)

        act_fwd4 = QtGui.QAction(style.standardIcon(QtWidgets.QStyle.SP_MediaSkipForward), "Forward", self)
        act_fwd4.setToolTip("快轉 4 小節")
        act_fwd4.triggered.connect(lambda: self._jump_bars(+4))
        tb1.addAction(act_fwd4)
        # 新增：Ctrl+Right 全域快捷
        act_fwd4.setShortcut(QtGui.QKeySequence("Ctrl+Right"))
        act_fwd4.setShortcutContext(Qt.ApplicationShortcut)
        self.addAction(act_fwd4)

        tb1.addSeparator()
        tb1.addWidget(QtWidgets.QLabel("Loop Start:"))
        self.loop_start_spin = QtWidgets.QSpinBox()
        self.loop_start_spin.setRange(1, 9999)
        self.loop_start_spin.setValue(1)
        tb1.addWidget(self.loop_start_spin)

        tb1.addWidget(QtWidgets.QLabel("End:"))
        self.loop_end_spin = QtWidgets.QSpinBox()
        self.loop_end_spin.setRange(1, 9999)
        self.loop_end_spin.setValue(16)
        tb1.addWidget(self.loop_end_spin)

        act_apply_loop = QtGui.QAction("Apply", self)
        act_apply_loop.setToolTip("套用 Loop Start/End")
        act_apply_loop.triggered.connect(self._apply_loop_from_spins)
        tb1.addAction(act_apply_loop)

        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        tb1.addWidget(spacer)

        self.time_label = QtWidgets.QLabel("00:00:00 / 00:00:00")
        self.time_label.setStyleSheet("font-family: 'Consolas','Menlo',monospace; font-weight:600;")
        tb1.addWidget(self.time_label)

        # 換行
        self.addToolBarBreak()

        # ----- 第二排(1)：View / Bars -----
        tb2 = self.addToolBar("View / Bars")
        tb2.setMovable(True)

        act_zoom_in = QtGui.QAction("Zoom +", self)
        act_zoom_in.triggered.connect(self._zoom_in)
        act_zoom_out = QtGui.QAction("Zoom -", self)
        act_zoom_out.triggered.connect(self._zoom_out)
        tb2.addAction(act_zoom_in)
        tb2.addAction(act_zoom_out)

        tb2.addSeparator()
        tb2.addWidget(QtWidgets.QLabel("Bars:"))
        self.bar_add_spin = QtWidgets.QSpinBox()
        self.bar_add_spin.setRange(1, 256)
        self.bar_add_spin.setValue(4)
        tb2.addWidget(self.bar_add_spin)

        act_add_bar = QtGui.QAction("+ Bar", self)
        act_add_bar.triggered.connect(lambda: self._add_bar(self.bar_add_spin.value()))
        tb2.addAction(act_add_bar)
        act_remove_bar = QtGui.QAction("- Bar", self)
        act_remove_bar.triggered.connect(lambda: self._remove_bar(self.bar_add_spin.value()))
        tb2.addAction(act_remove_bar)

        tb2.addSeparator()
        tb2.addWidget(QtWidgets.QLabel("Snap:"))
        self.snap_combo = QtWidgets.QComboBox()
        snap_items = ["1/2", "1/4", "1/8", "1/12", "1/16", "1/24", "1/32"]
        self.snap_combo.addItems(snap_items)

        # ---- 初始化：優先讀 config 的 view.snap ----
        snap_from_cfg = None
        try:
            snap_from_cfg = self.cfg.get("view", {}).get("snap")
        except Exception:
            snap_from_cfg = None

        if snap_from_cfg in snap_items:
            # 直接用上次使用的設定
            self.snap_combo.setCurrentText(snap_from_cfg)
            # 也把實際 snap 套進去，避免 UI 顯示與底層不一致
            self._set_snap(snap_from_cfg)
        else:
            # 沒有 config，就用你原本的推導法當 fallback
            try:
                denom = max(1, int(self.proj.ppq // max(1, self.roll.snap)))
                initial = f"1/{denom}"
                self.snap_combo.setCurrentText(initial if initial in snap_items else "1/4")
            except Exception:
                self.snap_combo.setCurrentText("1/4")
            # 同步一次底層
            self._set_snap(self.snap_combo.currentText())

        # ---- 變更時：保存到 config 並套用 ----
        def _on_snap_changed(text: str):
            # 寫回 config
            self.cfg.setdefault("view", {})["snap"] = text
            try:
                save_config(self.cfg)  # 你專案裡已有 save_config 的話就用它
            except Exception:
                pass
            # 套用
            self._set_snap(text)

        self.snap_combo.currentTextChanged.connect(_on_snap_changed)
        tb2.addWidget(self.snap_combo)

        # ----- 第二排(2)：Tempo -----
        tb3 = self.addToolBar("Tempo")
        tb3.setMovable(True)

        tb3.addWidget(QtWidgets.QLabel("Speed:"))
        self.speed_spin = QtWidgets.QDoubleSpinBox()
        self.speed_spin.setRange(0.25, 4.0)
        self.speed_spin.setSingleStep(0.05)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.00)
        self.speed_spin.setToolTip("播放速度倍率（不改 MIDI 與 BPM）")
        self.speed_spin.valueChanged.connect(self.trans.set_speed)
        self.speed_spin.valueChanged.connect(lambda _v: self._apply_speed_to_freeze_players())
        tb3.addWidget(self.speed_spin)

        # ----- 第二排(3)：Output -----
        tb4 = self.addToolBar("Output")
        tb4.setMovable(True)
        tb4.addWidget(QtWidgets.QLabel("Output:"))
        self.output_label = QtWidgets.QLabel("(initializing...)")
        self.output_label.setMinimumWidth(260)
        tb4.addWidget(self.output_label)

        btn_load = QtWidgets.QToolButton()
        btn_load.setText("Load source")
        btn_load.setToolTip("載入 SoundFont（.sf2）並切至 FluidSynth 輸出")
        btn_load.clicked.connect(self._on_load_sf2)
        tb4.addWidget(btn_load)

        btn_reset = QtWidgets.QToolButton()
        btn_reset.setText("Reset")
        btn_reset.setToolTip("恢復為系統預設 MIDI 輸出（例如 Microsoft GS Wavetable）")
        btn_reset.clicked.connect(self._on_reset_output)
        tb4.addWidget(btn_reset)

        # 外觀一致
        icon_sz = QtCore.QSize(16, 16)
        row_h = 20

        def _h(w):
            try:
                w.setFixedHeight(row_h)
            except Exception:
                pass

        for tb in (tb1, tb2, tb3, tb4):
            tb.setAllowedAreas(Qt.TopToolBarArea)
            tb.setIconSize(icon_sz)
            tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)

        for w in (self.loop_start_spin, self.loop_end_spin, self.bar_add_spin, self.snap_combo, self.speed_spin, btn_load, btn_reset):
            _h(w)

    # =============================================================================
    #  Transport / View / Loop / Tempo
    # =============================================================================
    def _toggle_play(self):
        if self.trans.playing:
            self.trans.pause()
        else:
            self.trans.start()

    def _on_playing_changed(self, playing: bool):
        style = QtWidgets.QApplication.style()
        if playing:
            self.act_play.setIcon(style.standardIcon(QtWidgets.QStyle.SP_MediaPause))
            self.act_play.setText("Pause")
        else:
            self.act_play.setIcon(style.standardIcon(QtWidgets.QStyle.SP_MediaPlay))
            self.act_play.setText("Play")

    def _toggle_loop(self, on: bool):
        self.trans.loop_on = on
        self.roll.draw_grid()
        self.roll.refresh_notes()
        self._update_time_label(None)   # ★ 新增：切換當下就更新顯示
        self._refresh_status_loop()

    def _jump_bars(self, bars_delta: int):
        step_ticks = int(bars_delta) * self._ticks_per_bar()
        cur = int(getattr(self.trans, "pos", 0))

        if getattr(self.trans, "loop_on", False):
            a = int(getattr(self.trans, "loop_a", 0))
            b = int(getattr(self.trans, "loop_b", a + self._ticks_per_bar()))
            if b <= a:
                b = a + self._ticks_per_bar()
            span = max(1, b - a)
            new_pos = cur + step_ticks
            off = (new_pos - a) % span
            new_pos = a + off
        else:
            new_pos = max(0, cur + step_ticks)

        self.trans.set_pos(new_pos)
        self._update_time_label(None)

    def _zoom_in(self):
        self.roll.zoom(1.2)

    def _zoom_out(self):
        self.roll.zoom(1 / 1.2)

    def _set_bpm(self, v: float):
        if self.proj.tempo_map:
            self.proj.tempo_map[0].bpm = v
        else:
            self.proj.tempo_map = [TempoChange(0, v)]
        self.trans.reanchor()
        self._update_time_label(None)
        self._refresh_status_bpm()

    def _set_snap(self, txt: str):
        try:
            denom = int(txt.split("/")[-1])
            self.roll.snap = max(1, self.proj.ppq // denom)
        except Exception:
            self.roll.snap = max(1, self.proj.ppq // 16)
        self.roll.draw_grid()
        self.roll.refresh_notes()

    def _format_hms(self, sec: float) -> str:
        if sec < 0:
            sec = 0
        s = int(sec + 0.5)
        h = s // 3600
        s %= 3600
        m = s // 60
        s %= 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def _ticks_per_bar(self, tick: int | None = None) -> int:
        ppq = max(1, int(self.proj.ppq))
        if tick is None:
            tick = int(getattr(self.trans, "pos", 0))
        num, den = 4, 4
        try:
            arr = getattr(self.proj, "timesig_map", []) or []
            for ts in sorted(arr, key=lambda x: int(getattr(x, "tick", 0))):
                if int(getattr(ts, "tick", 0)) <= tick:
                    num = int(getattr(ts, "num", 4))
                    den = int(getattr(ts, "den", 4))
                else:
                    break
        except Exception:
            pass
        ticks_per_beat = int(round(ppq * (4 / max(1, den))))
        return max(1, ticks_per_beat * max(1, num))

    def _max_tick(self) -> int:
        mt = 0
        for tr in self.proj.tracks:
            for n in tr.notes:
                if n.end > mt:
                    mt = n.end
        return mt

    def _update_time_label(self, tick: Optional[int] = None):
        if tick is None:
            tick = getattr(self.trans, "pos", 0)

        try:
            if self.trans.loop_on:
                a = int(getattr(self.trans, "loop_a", 0))
                b = int(getattr(self.trans, "loop_b", a + self._ticks_per_bar()))
                if b <= a:
                    b = a + self._ticks_per_bar()

                # 區段長度（秒）
                total_sec = self.proj.seconds_between(a, b)

                # 目前時間顯示為「區段內已播放時間」
                # 若播放頭在區段外，做環狀折返顯示（等效於實際 Loop 行為）
                span = max(1, b - a)
                rel_tick = ((int(tick) - a) % span) + a
                cur_sec = self.proj.seconds_between(a, rel_tick)

            else:
                # 原行為：全曲
                cur_sec = self.proj.seconds_between(0, int(tick))

                total_ticks = self._max_tick()
                if total_ticks <= 0:
                    total_ticks = getattr(self.trans, "loop_b", self.proj.ppq * 16)
                total_sec = self.proj.seconds_between(0, int(total_ticks))

            self.time_label.setText(f"{self._format_hms(cur_sec)} / {self._format_hms(total_sec)}")

        except Exception:
            self.time_label.setText("00:00:00 / 00:00:00")

    def _add_bar(self, bars_to_add: int = 4):
        cur_bars = int(self.trans.loop_b / max(1, self._ticks_per_bar()))
        new_bars = cur_bars + max(1, int(bars_to_add))
        self.trans.loop_b = new_bars * self._ticks_per_bar()
        if self.trans.loop_a >= self.trans.loop_b:
            self.trans.loop_a = max(0, self.trans.loop_b - self._ticks_per_bar())
        self.roll.draw_grid()
        self.roll.refresh_notes()
        self._update_time_label(None)
        self._sync_loop_spins()
        self._ensure_valid_active_track()

    def _remove_bar(self, bars_to_remove: int = 4):
        bars_to_remove = max(1, int(bars_to_remove))
        cur_bars = int(self.trans.loop_b / max(1, self._ticks_per_bar()))
        last_note_tick = self._max_tick()
        min_bars_by_notes = int(math.ceil(last_note_tick / max(1, self._ticks_per_bar()))) if last_note_tick > 0 else 1
        min_bars_by_loop_a = int(self.trans.loop_a / max(1, self._ticks_per_bar())) + 1
        min_bars_allowed = max(1, min_bars_by_notes, min_bars_by_loop_a)
        new_bars = max(min_bars_allowed, cur_bars - bars_to_remove)
        self.trans.loop_b = new_bars * self._ticks_per_bar()
        if self.trans.pos >= self.trans.loop_b:
            self.trans.set_pos(self.trans.loop_b - 1)
        self.roll.draw_grid()
        self.roll.refresh_notes()
        self._update_time_label(None)
        self._sync_loop_spins()
        self._ensure_valid_active_track()

    def _ticks_from_bar_start(self, bar1_based: int) -> int:
        return max(0, (int(bar1_based) - 1) * self._ticks_per_bar())

    def _bar1_from_tick_end(self, tick: int) -> int:
        return max(1, int(math.ceil(tick / max(1, self._ticks_per_bar()))))

    def _bar1_from_tick_start(self, tick: int) -> int:
        return max(1, int(tick // max(1, self._ticks_per_bar())) + 1)

    def _sync_loop_spins(self):
        start_bar = self._bar1_from_tick_start(self.trans.loop_a)
        end_bar = self._bar1_from_tick_end(self.trans.loop_b)
        self.loop_start_spin.blockSignals(True)
        self.loop_end_spin.blockSignals(True)
        self.loop_start_spin.setValue(start_bar)
        self.loop_end_spin.setValue(max(start_bar, end_bar))
        self.loop_start_spin.blockSignals(False)
        self.loop_end_spin.blockSignals(False)

    def _apply_loop_from_spins(self):
        s_bar = self.loop_start_spin.value()
        e_bar = self.loop_end_spin.value()
        if e_bar < s_bar:
            e_bar = s_bar
            self.loop_end_spin.setValue(e_bar)

        self.trans.loop_a = self._ticks_from_bar_start(s_bar)
        self.trans.loop_b = self._ticks_from_bar_start(e_bar)
        if self.trans.loop_b <= self.trans.loop_a:
            self.trans.loop_b = self.trans.loop_a + self._ticks_per_bar()

        if self.trans.pos < self.trans.loop_a or self.trans.pos >= self.trans.loop_b:
            self.trans.set_pos(self.trans.loop_a)

        self.roll.draw_grid()
        self.roll.refresh_notes()
        self._update_time_label(None)
        self._refresh_status_loop()

    # =============================================================================
    #  Track handlers
    # =============================================================================
    def _select_all_tracks(self):
        self.tracks_panel.set_active_index(None)
        self.roll.refresh_notes()
        self._ensure_valid_active_track()

    def _select_track_index(self, idx: int):
        self.roll.active_track = idx
        self.tracks_panel.set_active_index(idx)
        self.roll.refresh_notes()
        self._ensure_valid_active_track()

    def _set_track_program(self, idx: int, prog: int):
        tr = self.proj.tracks[idx]
        tr.program = prog
        if Message and self.midi.enabled:
            self.midi.send(Message("program_change", program=prog, channel=tr.channel))
        self.synth.program(tr.channel, 0, prog)
        self.proj.mark_dirty()

    def _set_track_volume(self, idx: int, vol: int):
        tr = self.proj.tracks[idx]
        tr.volume = vol
        if Message and self.midi.enabled:
            self.midi.send(Message("control_change", control=7, value=vol, channel=tr.channel))
        self.synth.cc(tr.channel, 7, vol)
        
        if idx in self._freeze_players:
            vol_pct = max(0, min(100, round(vol * 100 / 127)))
            self._freeze_players[idx][1].setVolume(vol_pct / 100.0)
        self.proj.mark_dirty()

    def _set_track_mute(self, idx: int, v: bool):
        self.proj.tracks[idx].mute = v
        self.proj.mark_dirty()

    def _set_track_solo(self, idx: int, v: bool):
        self.proj.tracks[idx].solo = v
        self.proj.mark_dirty()

    def _set_track_color(self, idx: int, col: QtGui.QColor):
        self.proj.tracks[idx].color = col
        self.roll.refresh_notes()
        self.proj.mark_dirty()

    def _set_track_name(self, idx: int, name: str):
        self.proj.tracks[idx].name = name
        self.roll.refresh_notes()
        self.proj.mark_dirty()

    def _set_track_view_active(self, idx: int, v: bool):
        if 0 <= idx < len(self.proj.tracks):
            self.proj.tracks[idx].view_active = v
        self._update_visible_tracks()
        self.proj.mark_dirty()

    def _update_visible_tracks(self):
        chosen = {i for i, t in enumerate(self.proj.tracks) if getattr(t, "view_active", False)}
        self.roll.set_visible_tracks(chosen if chosen else None)
        self.roll.refresh_notes()
        self._ensure_valid_active_track()

    def _ensure_valid_active_track(self) -> int:
        n = len(getattr(self.proj, "tracks", []))
        if n <= 0:
            self.roll.active_track = -1
            if hasattr(self.tracks_panel, "set_active_index"):
                self.tracks_panel.set_active_index(None)
            return -1
        idx = int(getattr(self.roll, "active_track", 0))
        if idx < 0 or idx >= n:
            idx = max(0, min(n - 1, idx))
            self.roll.active_track = idx
            if hasattr(self.tracks_panel, "set_active_index"):
                self.tracks_panel.set_active_index(idx)
        return idx

    def _get_active_track(self):
        idx = self._ensure_valid_active_track()
        if idx < 0:
            return None
        return self.proj.tracks[idx]

    def _update_add_button(self):
        try:
            can_add = len(self.proj.tracks) < MAX_TRACKS
            if hasattr(self.tracks_panel, "add_btn") and self.tracks_panel.add_btn:
                self.tracks_panel.add_btn.setEnabled(can_add)
        except Exception:
            pass

    def _next_free_channel(self) -> int:
        used = {t.channel for t in self.proj.tracks}
        for ch in range(16):
            if ch not in used:
                return ch
        return (max(used) + 1) % 16 if used else 0

    def _add_track(self):
        if len(self.proj.tracks) >= MAX_TRACKS:
            QtWidgets.QMessageBox.information(self, "Add Track", f"已達 {MAX_TRACKS} 條音軌上限，無法再新增。")
            self._update_add_button()
            return

        idx = len(self.proj.tracks) + 1
        color_pool = [QtGui.QColor(*rgb) for rgb in COLOR_POOL]
        tr = Track(
            name=f"Track {idx}",
            channel=self._next_free_channel(),
            program=0,
            volume=100,
            color=color_pool[(idx - 1) % len(color_pool)],
            notes=[],
        )
        self.proj.tracks.append(tr)
        self.tracks_panel._build()
        self.roll.active_track = len(self.proj.tracks) - 1
        self.tracks_panel.set_active_index(self.roll.active_track)
        self._update_visible_tracks()
        self._update_time_label(None)
        self._ensure_valid_active_track()
        self._update_add_button()
        self.proj.mark_dirty()

    def _delete_track(self, idx: int):
        if len(self.proj.tracks) <= 1:
            QtWidgets.QMessageBox.information(self, "Delete Track", "至少需要保留一條音軌。")
            return
        name = self.proj.tracks[idx].name
        if (
            QtWidgets.QMessageBox.question(self, "Delete Track", f"確定刪除音軌「{name}」？此操作無法復原。")
            != QtWidgets.QMessageBox.Yes
        ):
            return

        was_playing = self.trans.playing
        if was_playing:
            self.trans.pause()
        self.trans.wait_idle()
        self.trans.panic()

        del self.proj.tracks[idx]
        self._rekey_freeze_players_after_delete(idx)
        if self.roll.active_track >= len(self.proj.tracks):
            self.roll.active_track = max(0, len(self.proj.tracks) - 1)
        self.tracks_panel._build()
        self.tracks_panel.set_active_index(self.roll.active_track)
        self._update_visible_tracks()
        self._update_time_label(None)
        self._ensure_valid_active_track()
        self._update_add_button()

        if was_playing:
            self.trans.start()
        self.proj.mark_dirty()

    def _bar_beat_from_tick(self, tick: int) -> tuple[int, int]:
        """
        依 timesig_map 計算第幾小節（1-based）與小節內第幾拍（1-based）。
        """
        ppq = max(1, int(self.proj.ppq))
        arr = sorted(getattr(self.proj, "timesig_map", []) or [], key=lambda x: int(getattr(x, "tick", 0)))
        if not arr:
            arr = [type("T", (), {"tick": 0, "num": 4, "den": 4})()]
        bar_count = 1
        last_tick = 0

        for i, ts in enumerate(arr):
            seg_start = int(getattr(ts, "tick", 0))
            num = int(getattr(ts, "num", 4)); den = int(getattr(ts, "den", 4))
            ticks_per_beat = int(round(ppq * (4 / max(1, den))))
            tpb = max(1, ticks_per_beat * max(1, num))

            seg_end = int(getattr(arr[i+1], "tick", tick)) if i+1 < len(arr) else tick
            # 這一段完全在 tick 之前 → 算過去的小節數
            if seg_end <= seg_start:
                continue
            if tick >= seg_end:
                full_bars = max(0, (seg_end - seg_start) // tpb)
                bar_count += full_bars
                last_tick = seg_end
            else:
                # 目標 tick 在此段中
                offset = max(0, tick - seg_start)
                bar_in_seg = offset // tpb
                beat_in_bar = (offset % tpb) // max(1, ticks_per_beat)
                return (bar_count + bar_in_seg, int(beat_in_bar + 1))

        # 若剛好落在段尾
        return (bar_count, 1)

    def _refresh_status_pos(self):
        t = int(getattr(self.trans, "pos", 0))
        bar, beat = self._bar_beat_from_tick(t)
        self.sb_pos.setText(f"Bar {bar} Beat {beat}")

    def _refresh_status_bpm(self):
        """單選音符→用該音符 start；否則→用播放頭位置"""
        sel_note = None
        try:
            for n, it, _ti in getattr(self.roll, "note_items", []):
                if it.isSelected():
                    if sel_note is not None:  # 多選→視為無單一音符
                        sel_note = None
                        break
                    sel_note = n
        except Exception:
            sel_note = None

        tick = int(getattr(sel_note, "start", getattr(self.trans, "pos", 0))) if sel_note is not None else int(getattr(self.trans, "pos", 0))
        self.sb_bpm.setText(f"BPM {self._effective_bpm_at(tick):.2f}")

    def _selection_ticks_span(self) -> tuple[int, int, int]:
        """
        回傳 (count, min_start, max_end)。若沒有選取，count=0。
        以 PianoRoll.note_items 的選取狀態為準。
        """
        cnt = 0
        min_s = None
        max_e = None
        for n, it, _ti in getattr(self.roll, "note_items", []):
            try:
                if it.isSelected():
                    cnt += 1
                    min_s = n.start if (min_s is None or n.start < min_s) else min_s
                    max_e = n.end   if (max_e is None or n.end   > max_e) else max_e
            except RuntimeError:
                pass
        if cnt == 0:
            return 0, 0, 0
        return cnt, int(min_s), int(max_e)

    def _refresh_status_selection(self):
        cnt, s, e = self._selection_ticks_span()
        self.sb_notes.setText(f"Notes {cnt}")

        # Duration
        if cnt <= 0 or e <= s:
            self.sb_duration.setText("Duration 0.0s")
        else:
            sec = self.proj.seconds_between(s, e)
            self.sb_duration.setText(f"Duration {sec:.2f}s")

        # VEL（單選時顯示）
        vel_text = "VEL --"
        try:
            if cnt == 1:
                for n, it, _ti in getattr(self.roll, "note_items", []):
                    if it.isSelected():
                        vel_text = f"VEL {int(getattr(n, 'vel', 0))}"
                        break
        except Exception:
            pass
        self.sb_vel.setText(vel_text)

        # BPM 跟著選取變化
        self._refresh_status_bpm()

    def _refresh_status_loop(self):
        if getattr(self.trans, "loop_on", False):
            a = int(getattr(self.trans, "loop_a", 0))
            b = int(getattr(self.trans, "loop_b", 0))
            if b <= a:
                b = a + self._ticks_per_bar()
            ba, _ = self._bar_beat_from_tick(a)
            bb, _ = self._bar_beat_from_tick(b-1)
            # 顯示區段秒數
            sec = self.proj.seconds_between(a, b)
            self.sb_loop.setText(f"Loop {ba}–{bb} ({sec:.2f}s)")
        else:
            self.sb_loop.setText("Loop Off")

    def _refresh_status_all(self):
        self._refresh_status_pos()
        self._refresh_status_bpm()
        self._refresh_status_selection()
        self._refresh_status_loop()

    # =============================================================================
    #  File: New / Open / Save / Close MIDI
    # =============================================================================
    def _new_midi(self):
        """開新行程，不關閉本視窗。"""
        try:
            proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            debug_runner = os.path.join(proj_root, "debug_runner.py")
            if os.path.isfile(debug_runner):
                argv = [sys.executable, debug_runner]
            else:
                main_py = os.path.join(proj_root, "main.py")
                argv = [sys.executable, main_py]
            subprocess.Popen(argv, cwd=proj_root)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Launch Error", str(e))

    def open_midi(self):
        if not self._maybe_save("即將開啟另一個檔案。"):
            return
        self._clear_all_freeze_players()
        self._cleanup_freeze_cache(keep_in_use=False)
        self._ensure_groups_list()
        self.tracks_panel._build()
        self._sync_bpm_ui_from_model()
        
        if not mido:
            QtWidgets.QMessageBox.warning(self, "mido missing", "請先安裝 mido 才能開啟 MIDI。")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open MIDI", "", "MIDI Files (*.mid *.midi)")
        if not path:
            return
        try:
            was_playing = self.trans.playing
            if was_playing:
                self.trans.pause()
            self.trans.wait_idle()
            self.trans.panic()

            mid = mido.MidiFile(path)
            new_proj = io_midi.from_mido(mid)

            self.proj = new_proj
            import os
            self._current_filename = os.path.basename(path)
            self.proj.clear_dirty()
            self._last_saved_rev = int(getattr(self.proj, "rev", 0))
            self._coerce_track_colors()
            self._ensure_groups_list()

            if hasattr(self.trans, "bind_project") and callable(self.trans.bind_project):
                self.trans.bind_project(self.proj)
            else:
                self.trans.p = self.proj
                if hasattr(self.trans, "reanchor"):
                    try:
                        self.trans.reanchor()
                    except Exception:
                        pass

            self.roll.p = self.proj
            self.tracks_panel.p = self.proj
            self.tracks_panel._build()
            self._update_visible_tracks()
            self.roll.draw_grid()
            self.roll.refresh_notes()
                
            self._refresh_status_bpm()
            self._sync_loop_spins()
            self._update_time_label(None)
            self._ensure_valid_active_track()
            self._update_add_button()
            self.roll.setFocus()

            if was_playing:
                self.trans.start()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Open error", str(e))

    def _filter_tracks_for_export(self, tracks, *, only_solo=False, only_visible=False, ignore_mute=False):
        out = []
        for t in tracks:
            # Solo / Visible / Mute 規則
            if only_solo and not getattr(t, "solo", False):
                continue
            if only_visible and not getattr(t, "view_active", True):
                continue
            if not ignore_mute and getattr(t, "mute", False):
                continue

            # 沒事件就不寫（兼容 notes / events 兩種欄位）
            has_events = (bool(getattr(t, "notes", None)) or bool(getattr(t, "events", None)))
            if not has_events:
                continue

            out.append(t)
        return out

    def save_midi(self):
        if not mido:
            QtWidgets.QMessageBox.warning(self, "mido missing", "請先安裝 mido 才能儲存 MIDI。")
            return

        # 取得所有軌
        tracks_all = list(self.proj.tracks)
        total = len(tracks_all)

        # 1) 顯示過濾選項對話框（即時預覽「將匯出 N 軌」）
        dlg = ExportMidiDialog(self, total_tracks=total)

        def _recompute():
            vals = dlg.values()
            n = len(self._filter_tracks_for_export(tracks_all, **vals))
            dlg.set_preview(n, total)

        dlg.chk_only_solo.toggled.connect(_recompute)
        dlg.chk_only_visible.toggled.connect(_recompute)
        dlg.chk_ignore_mute.toggled.connect(_recompute)
        _recompute()

        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return

        vals = dlg.values()
        export_tracks = self._filter_tracks_for_export(tracks_all, **vals)
        if not export_tracks:
            QtWidgets.QMessageBox.warning(self, "無可匯出軌", "沒有符合條件的軌道可匯出。")
            return

        # 2) 檔名
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save MIDI", "output.mid", "MIDI Files (*.mid *.midi)")
        if not path:
            return

        try:
            # 3) 建一個臨時 Project 只裝被挑中的軌（tempo/ppq 保留）
            tmp = Project()
            tmp.ppq = getattr(self.proj, "ppq", 480)
            tmp.tempo_map = list(getattr(self.proj, "tempo_map", []))  # 仍保留整體速度/拍號資訊
            tmp.tracks = list(export_tracks)  # 淺拷貝即可，不改動原始專案

            mid = io_midi.to_mido(tmp)
            mid.save(path)
            QtWidgets.QMessageBox.information(self, "完成", f"已匯出：{path}")
            import os
            self._current_filename = os.path.basename(path)
            self._on_saved_ok()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save error", str(e))

    def _serialize_track_full(self, t, include_notes=True) -> dict:
        d = dict(
            name=str(getattr(t, "name", "Track")),
            channel=int(getattr(t, "channel", 0)),
            program=int(getattr(t, "program", 0)),
            volume=int(getattr(t, "volume", 100)),
            mute=bool(getattr(t, "mute", False)),
            solo=bool(getattr(t, "solo", False)),
            color=self._qcolor_to_hex(getattr(t, "color", "#888888")),
            view_active=bool(getattr(t, "view_active", True)),
        )
        if include_notes:
            d["notes"] = self._serialize_notes(getattr(t, "notes", []))
        return d

    def _deserialize_track_full(self, d, ppq_scale: float, used_channels: set[int]):
        preferred_ch = d.get("channel", None)
        assigned_ch = self._alloc_channel_preserve(preferred_ch, used_channels)

        tr = Track(
            name=str(d.get("name", "Track")),
            channel=int(assigned_ch),
            program=int(d.get("program", 0)),
            volume=int(d.get("volume", 100)),
            color=self._hex_to_qcolor(d.get("color", "#888888")),
            notes=[],
            mute=bool(d.get("mute", False)),
            solo=bool(d.get("solo", False)),
        )

        # notes
        if isinstance(d.get("notes"), list):
            tr.notes = self._deserialize_notes(d["notes"], ppq_scale=ppq_scale)
            # 無論 JSON 內是否帶 note.channel，統一改成本軌通道
            for n in tr.notes:
                try:
                    n.channel = tr.channel
                except Exception:
                    pass

        try:
            tr.view_active = bool(d.get("view_active", True))
        except Exception:
            pass

        # 記錄已使用通道
        used_channels.add(tr.channel)
        return tr

    def _serialize_project(self) -> dict:
        proj = self.proj

        # tempo_map: 多段 BPM
        tempo_map = [
            {"tick": int(tc.tick), "bpm": float(tc.bpm)}
            for tc in getattr(proj, "tempo_map", [])
        ]
        # fallback: 沒有 tempo_map 時，至少給一個 120
        if not tempo_map:
            tempo_map = [{"tick": 0, "bpm": 120.0}]

        payload = dict(
            schema="daw-project.v1",
            app="Python MIDI DAW — MVP",
            ppq=int(getattr(proj, "ppq", 480)),
            tempo=float(tempo_map[0]["bpm"]),
            tempo_map=tempo_map,
            timesig_map=[
                {"tick": int(getattr(x, "tick", 0)), "num": int(getattr(x, "num", 4)), "den": int(getattr(x, "den", 4))}
                for x in (getattr(self.proj, "timesig_map", []) or [])],
            bars=int(getattr(proj, "bars", 4)),
            tracks=[self._serialize_track_full(t, include_notes=True) for t in proj.tracks],
            
            markers=[{"tick": int(m.tick), "name": str(m.name), "color": getattr(m, "color", None)}
                    for m in getattr(proj, "markers", [])],
            
            ui=dict(
                zoom_x=float(getattr(self.roll, "px_per_tick", 0.08)),
                zoom_y=float(getattr(self.roll, "px_per_note", 12.0)),
                scroll_x=int(getattr(self, "scroll_x", 0)),
                scroll_y=int(getattr(self, "scroll_y", 0)),
            ),
        )
        return payload

    def _apply_project_payload(self, data: dict):
        ppq_src = int(data.get("ppq", getattr(self.proj, "ppq", 480)))
        ppq_dst = int(getattr(self.proj, "ppq", 480))
        ppq_scale = float(ppq_dst) / float(ppq_src) if ppq_src > 0 else 1.0

        # 先清空舊資料
        self.proj.tracks[:] = []
        self.proj.markers = []  # ← 新增：清空 markers

        # ---- tempo_map ----
        tempo_map_data = data.get("tempo_map")
        if isinstance(tempo_map_data, list) and tempo_map_data:
            from modules.core.models import TempoChange
            self.proj.tempo_map = [
                TempoChange(int(tc.get("tick", 0) * ppq_scale), float(tc.get("bpm", 120.0)))
                for tc in tempo_map_data
                if isinstance(tc, dict)
            ]
        else:
            # 向後相容舊格式，只看單一 tempo
            from modules.core.models import TempoChange
            tempo = float(data.get("tempo", 120))
            self.proj.tempo_map = [TempoChange(0, tempo)]

        # ---- bars ----
        if "bars" in data:
            try:
                self.proj.bars = int(data["bars"])
            except Exception:
                pass

        # ---- timesig_map ----
        try:
            arr = data.get("timesig_map", [])
            ts_list = []
            if isinstance(arr, list):
                for d in arr:
                    try:
                        t_src = int(d.get("tick", 0))
                        t = int(round(t_src * ppq_scale))
                        num = int(d.get("num", 4))
                        den = int(d.get("den", 4))
                        ts_list.append(_TimeSigChange(t, num, den))
                    except Exception:
                        pass
            if not ts_list:
                ts_list = [_TimeSigChange(0, 4, 4)]
            ts_list.sort(key=lambda x: x.tick)
            self.proj.timesig_map = ts_list
        except Exception:
            self.proj.timesig_map = [_TimeSigChange(0, 4, 4)]

        # ---- tracks（保留檔內 channel、避免重排）----
        used_channels: set[int] = set()
        for td in data.get("tracks", []):
            try:
                tr = self._deserialize_track_full(td, ppq_scale=ppq_scale, used_channels=used_channels)
                self.proj.tracks.append(tr)
            except Exception:
                pass

        # ---- markers（新功能）----
        try:
            from modules.core.models import Marker
            mks = data.get("markers", [])
            if isinstance(mks, list):
                for d in mks:
                    if not isinstance(d, dict):
                        continue
                    try:
                        t_src = int(d.get("tick", 0))
                        t = int(round(t_src * ppq_scale))
                        name = str(d.get("name", "Marker"))
                        col = d.get("color")  # 可為 None 或 hex/tuple，由 UI 自行處理
                        self.proj.markers.append(Marker(tick=t, name=name, color=col))
                    except Exception:
                        pass
                # 依 tick 排序，確保尺規繪製/跳轉一致
                self.proj.markers.sort(key=lambda m: int(getattr(m, "tick", 0)))
        except Exception:
            # 任何解析錯誤不影響載檔
            self.proj.markers = getattr(self.proj, "markers", [])

        # ---- loop / UI 還原 ----
        try:
            self.loop_a = int(data.get("loop_a", 0) * ppq_scale)
        except Exception:
            self.loop_a = 0
        try:
            self.loop_b = int(data.get("loop_b", 0) * ppq_scale)
        except Exception:
            self.loop_b = 0

        ui = data.get("ui", {}) or {}
        try:
            self.roll.px_per_tick = float(ui.get("zoom_x", self.roll.px_per_tick))
        except Exception:
            pass
        try:
            self.roll.px_per_note = float(ui.get("zoom_y", self.roll.px_per_note))
        except Exception:
            pass

        # ---- 重建畫面 & 同步 BPM 顯示 ----
        self.tracks_panel._build()
        self._update_visible_tracks()
        self.roll.draw_grid()      # 這裡會一起重畫 marker（若已在 draw_grid 實作）
        self.roll.refresh_notes()
        self._update_time_label(None)
        self._ensure_valid_active_track()
        self._update_add_button()
        self._sync_bpm_ui_from_model()

    def save_project_file(self):
        import json, os
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Project", "untitled.json", "JSON Files (*.json)"
        )
        if not path:
            return
        if not (path.lower().endswith(".proj.json") or path.lower().endswith(".json")):
            path += ".proj.json"
        try:
            payload = self._serialize_project()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(self, "完成", f"已儲存專案：\n{os.path.abspath(path)}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "儲存失敗", str(e))

    def load_project_file(self):
        import json
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Project", "", "JSON Files (*.json)"
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "讀取失敗", f"無法讀取檔案：\n{e}")
            return

        # 基本驗證
        if not isinstance(data, dict) or "tracks" not in data:
            QtWidgets.QMessageBox.warning(self, "格式不符", "不是有效的專案文件。")
            return

        try:
            self._apply_project_payload(data)
            QtWidgets.QMessageBox.information(self, "完成", "專案已載入。")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "載入失敗", str(e))

    def _qcolor_to_hex(self, col) -> str:
        try:
            from PySide6 import QtGui
            if isinstance(col, QtGui.QColor):
                return col.name()
            if isinstance(col, (tuple, list)) and len(col) == 3:
                c = QtGui.QColor(int(col[0]), int(col[1]), int(col[2]))
                return c.name()
            c = QtGui.QColor(str(col))
            return c.name() if c.isValid() else "#808080"
        except Exception:
            return "#808080"

    def _hex_to_qcolor(self, s):
        from PySide6 import QtGui
        c = QtGui.QColor(str(s))
        return c if c.isValid() else QtGui.QColor("#808080")

    def _serialize_track_preset(self, t, include_notes: bool = False) -> dict:
            data = dict(
                name=str(getattr(t, "name", "Track")),
                program=int(getattr(t, "program", 0)),
                volume=int(getattr(t, "volume", 100)),
                mute=bool(getattr(t, "mute", False)),
                solo=bool(getattr(t, "solo", False)),
                color=self._qcolor_to_hex(getattr(t, "color", "#888888")),
            )
            if include_notes:
                data["notes"] = self._serialize_notes(getattr(t, "notes", []))
            return data

    def _deserialize_track_preset(self, d) -> "Track":
        name = str(d.get("name", "Track"))
        prog = int(d.get("program", 0))
        vol  = int(d.get("volume", 100))
        mute = bool(d.get("mute", False))
        solo = bool(d.get("solo", False))
        col  = self._hex_to_qcolor(d.get("color", "#888888"))

        notes = self._deserialize_notes(d.get("notes")) if "notes" in d else []
        return Track(
            name=name,
            channel=self._next_free_channel(),
            program=prog,
            volume=vol,
            color=col,
            notes=notes,       # 有 notes 就帶進來，沒有就空
            mute=mute,
            solo=solo,
        )

    def _tracks_chosen_for_preset_export(self):
        """若有任何 View: Active 打勾 → 匯出那幾條；否則匯出全部。"""
        tracks_all = list(self.proj.tracks)
        chosen = [t for t in tracks_all if bool(getattr(t, "view_active", False))]
        return chosen if chosen else tracks_all

    def _serialize_notes(self, notes):
        out = []
        for n in notes or []:
            try:
                start = int(getattr(n, "start", 0))
                # 有 dur 用 dur；沒有就用 end 推回
                if hasattr(n, "dur"):
                    dur = int(getattr(n, "dur"))
                else:
                    end = int(getattr(n, "end", start + 120))
                    dur = max(1, end - start)
                vel = int(getattr(n, "vel", getattr(n, "velocity", 100)))
                out.append(dict(pitch=int(n.pitch), start=start, dur=dur, vel=vel))
            except Exception:
                pass
        return out

    def _deserialize_notes(self, arr, ppq_scale: float = 1.0):
        # 嘗試載入你的核心 Note 類
        NoteCls = None
        try:
            from models import Note as NoteCls
        except Exception:
            try:
                from modules.core.models import Note as NoteCls
            except Exception:
                NoteCls = None

        res = []
        for d in arr or []:
            try:
                pitch_src = int(d.get("pitch", 60))
                start_src = int(d.get("start", 0))
                if "dur" in d:
                    dur_src = int(d["dur"])
                else:
                    # 舊版或其他工具：用 end - start 推回 dur
                    end_src = int(d.get("end", start_src + 120))
                    dur_src = max(1, end_src - start_src)

                # 依 PPQ 縮放到目前專案
                start = max(0, int(round(start_src * ppq_scale)))
                dur   = max(1, int(round(dur_src   * ppq_scale)))
                vel   = int(d.get("vel", d.get("velocity", 100)))

                if NoteCls is not None:
                    note = NoteCls(pitch=pitch_src, vel=vel, start=start, dur=dur, channel=0)
                else:
                    class _N: pass
                    note = _N(); note.pitch=pitch_src; note.vel=vel; note.start=start; note.dur=dur; note.channel=0
                res.append(note)
            except Exception:
                pass
        return res

    def export_track_presets(self):
        import json, os
        tracks = self._tracks_chosen_for_preset_export()
        if not tracks:
            QtWidgets.QMessageBox.information(self, "Export Tracks", "沒有可匯出的軌道。")
            return

        # 詢問是否包含音符
        ret = QtWidgets.QMessageBox.question(
            self, "Export Tracks",
            "Include notes in exported tracks?\n（匯出時包含音符？）",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        include_notes = (ret == QtWidgets.QMessageBox.Yes)

        payload = dict(
            schema="track-presets.v2",
            app="Python MIDI DAW — MVP",
            ppq=int(getattr(self.proj, "ppq", 480)),
            include_notes=include_notes,
            count=len(tracks),
            tracks=[self._serialize_track_preset(t, include_notes) for t in tracks],
        )

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Tracks (Presets)", "tracks.json", "JSON Files (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            QtWidgets.QMessageBox.information(self, "完成", f"已匯出：\n{os.path.abspath(path)}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "匯出失敗", str(e))

    def import_track_presets(self):
        import json
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Tracks (Presets)", "", "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "讀取失敗", f"無法讀取檔案：\n{e}")
            return

        # 取出 notes 陣列；同時抓來源 ppq（若沒有就當作與當前相同）
        if isinstance(data, dict) and "tracks" in data:
            tracks_data = data.get("tracks", [])
            ppq_src = int(data.get("ppq", self.proj.ppq) or self.proj.ppq)
        elif isinstance(data, list):
            tracks_data = data
            ppq_src = self.proj.ppq
        else:
            QtWidgets.QMessageBox.warning(self, "格式不符", "非支援的 Track Presets 格式。")
            return

        if not tracks_data:
            QtWidgets.QMessageBox.information(self, "沒有資料", "檔案內沒有任何軌道。")
            return

        ppq_dst = int(getattr(self.proj, "ppq", 480) or 480)
        ppq_scale = float(ppq_dst) / float(ppq_src) if ppq_src > 0 else 1.0

        created = []
        for d in tracks_data:
            try:
                tr = self._deserialize_track_preset(d)  # 會先建空 notes 的 Track
                # 若 JSON 有 notes，用新版反序列化 + 縮放
                if isinstance(d.get("notes"), list):
                    tr.notes = self._deserialize_notes(d["notes"], ppq_scale=ppq_scale)
                    for n in tr.notes:
                        try: n.channel = tr.channel
                        except Exception: pass
                self.proj.tracks.append(tr)
                created.append(tr)
            except Exception:
                pass

        if not created:
            QtWidgets.QMessageBox.warning(self, "匯入失敗", "未能建立任何軌道。")
            return

        for tr in created:
            try: tr.view_active = True
            except Exception: pass

        self.tracks_panel._build()
        self._update_visible_tracks()
        self.roll.draw_grid()
        self.roll.refresh_notes()
        self._update_time_label(None)
        self._ensure_valid_active_track()
        self._update_add_button()

        QtWidgets.QMessageBox.information(self, "完成", f"已匯入 {len(created)} 條軌道。")

    def export_wav_via_fluidsynth(self):
        """
        使用 Fluidsynth CLI 離線渲染 WAV。
        - 不讀 Settings；所有參數由對話框取得
        - 不再設定 audio.file.channels（聲道以 Fluidsynth 預設為準，通常 2ch）
        - 若版本不支援 audio.file.format，會自動忽略並重試
        """
        import os, sys, subprocess, shutil, tempfile, re

        # 預填目前載入的 sf2（若有）
        preset_sf = ""
        try:
            cur_sf = getattr(getattr(self, "synth", None), "sf2_path", None)
            if cur_sf and os.path.isfile(cur_sf):
                preset_sf = cur_sf
        except Exception:
            pass

        # 參數對話框
        dlg = ExportWavDialog(self, preset_sf=preset_sf)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        opt = dlg.values()

        # 輸出檔名
        default_name = "output.wav"
        out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "儲存 WAV 檔", default_name, "WAV Files (*.wav)"
        )
        if not out_path:
            return
        if not out_path.lower().endswith((".wav", ".wave")):
            out_path += ".wav"
        out_path = os.path.abspath(out_path)

        # 建暫存 MIDI
        tmpdir = tempfile.mkdtemp(prefix="daw_midi_")
        mid_path = os.path.join(tmpdir, "render.mid")
        try:
            mid = io_midi.to_mido(self.proj)
            mid.save(mid_path)
        except Exception as e:
            shutil.rmtree(tmpdir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(self, "匯出失敗", f"建立暫存 MIDI 失敗：\n{e}")
            return

        # 找 fluidsynth
        exe = shutil.which("fluidsynth")
        if not exe:
            shutil.rmtree(tmpdir, ignore_errors=True)
            QtWidgets.QMessageBox.critical(
                self, "找不到 Fluidsynth",
                "系統未找到 fluidsynth 指令。\n請安裝 Fluidsynth 並加入 PATH。"
            )
            return

        # 基礎命令（不含任何 audio.file.channels 設定）
        base = [
            exe, "-ni",
            "-F", out_path,
            "-r", opt["rate"],
            "-o", f"synth.reverb.active={'1' if opt['reverb'] else '0'}",
            "-o", f"synth.chorus.active={'1' if opt['chorus'] else '0'}",
            "-o", f"synth.gain={opt['gain']}",
        ]

        # 嘗試帶上 audio.file.format（若版本不支援就自動移除重試）
        cmd = list(base) + ["-o", f"audio.file.format={opt['fmt']}", opt["sf"], mid_path]

        def _run(cmd_):
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
            return subprocess.run(cmd_, check=True, creationflags=creation, capture_output=True, text=True)

        try:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            _run(cmd)
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.information(self, "完成", f"已匯出：\n{out_path}")
        except subprocess.CalledProcessError as e:
            QtWidgets.QApplication.restoreOverrideCursor()
            err = (e.stderr or "") + "\n" + (e.stdout or "")
            # 偵測 audio.file.format 不支援 → 去掉後重試
            if re.search(r"Setting parameter 'audio\.file\.format' not found", err):
                cmd2 = list(base) + [opt["sf"], mid_path]
                try:
                    QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                    _run(cmd2)
                    QtWidgets.QApplication.restoreOverrideCursor()
                    QtWidgets.QMessageBox.information(
                        self, "完成（已自動相容）",
                        f"已匯出：\n{out_path}\n\n（注意：你的 Fluidsynth 版本不支援 audio.file.format，"
                        f"本次以預設位元格式輸出）"
                    )
                except subprocess.CalledProcessError as e2:
                    QtWidgets.QApplication.restoreOverrideCursor()
                    err2 = (e2.stderr or "") + "\n" + (e2.stdout or "")
                    QtWidgets.QMessageBox.critical(self, "匯出失敗", f"Fluidsynth 執行失敗：\n{err2 or err}")
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
                return
            else:
                QtWidgets.QMessageBox.critical(self, "匯出失敗", f"Fluidsynth 執行失敗：\n{err or str(e)}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def close_midi(self):
        if not self._maybe_save("即將關閉目前的 MIDI。"):
            return
        self._clear_all_freeze_players()
        self._cleanup_freeze_cache(keep_in_use=False)
    
        """關閉當前 MIDI（回到空白專案，不退出程式）。"""
        try:
            was_playing = self.trans.playing
            if was_playing:
                self.trans.pause()
            self.trans.wait_idle()
            self.trans.panic()

            # 換成空白專案並重綁
            self.proj = Project()
            self._current_filename = "未命名"
            self.proj.clear_dirty()
            self._last_saved_rev = int(getattr(self.proj, "rev", 0))
            self._coerce_track_colors()

            if hasattr(self.trans, "bind_project") and callable(self.trans.bind_project):
                self.trans.bind_project(self.proj)
            else:
                self.trans.p = self.proj
                if hasattr(self.trans, "reanchor"):
                    try:
                        self.trans.reanchor()
                    except Exception:
                        pass
                        
            # 在你清理完現有資料後
            try:
                for t in getattr(self.proj, "tracks", []):
                    if hasattr(t, "group_id"):
                        t.group_id = None
                if hasattr(self.proj, "groups"):
                    self.proj.groups.clear()
            except Exception:
                pass

            # 刷新 UI 與控制
            self.roll.p = self.proj
            self.tracks_panel.p = self.proj
            self.tracks_panel._build()
            self._update_visible_tracks()
            self.roll.draw_grid()
            self.roll.refresh_notes()
            self._refresh_status_bpm()

            self._sync_loop_spins()
            self._update_time_label(None)
            self._ensure_valid_active_track()
            self._update_add_button()
            self.roll.setFocus()

        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Close MIDI Error", str(e))

    def closeEvent(self, e: QtGui.QCloseEvent) -> None:
        if not self._maybe_save("關閉程式之前偵測到尚未儲存的變更。"):
            e.ignore()
            return
        # 可選：讓其他 handler 知道正在關閉（若你在 handler 有檢查就更安全）
        self._shutting_down = True
        self._mark_session_clean_exit()
        try:
            # 1) 先停掉 transport，避免清理過程中還在發 tick/loop 事件
            try:
                self.trans.stop()
            except Exception:
                pass

            # 2) 關掉並釋放所有凍結播放器，清空快取
            try:
                self._clear_all_freeze_players()
            except Exception:
                pass
            try:
                self._cleanup_freeze_cache(keep_in_use=False)
            except Exception:
                pass

            # 3) 自動備份（若開啟）
            try:
                if bool(self.cfg.get("auto_backup", False)):
                    self._save_autosave_files()
            except Exception:
                pass

            # 4) 關閉底層 I/O
            try:
                self.synth.close()
            except Exception:
                pass
            try:
                self.midi.close()
            except Exception:
                pass

        finally:
            super().closeEvent(e)

    # =============================================================================
    #  Output（唯讀顯示 + Load/Reset SF2）
    # =============================================================================
    def _set_output_label(self, text: str):
        if hasattr(self, "output_label"):
            self.output_label.setText(text)

    def _apply_initial_output_route(self):
        outs = self.midi.outputs()
        if outs:
            default = next((n for n in outs if "Microsoft GS Wavetable" in n or "Microsoft GS" in n), outs[0])
            if self.midi.open_by_name(default):
                self.synth.enabled = False
                self._set_output_label(default)
                return
        self._set_output_label("No system output")

    def _on_load_sf2(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select SoundFont", "", "SoundFont (*.sf2)")
        if not path:
            return
        try:
            self.synth.load_sf2(path)  # 交給 SynthOut 做所有事
            self.midi.disable()        # 切到內部合成器輸出
            self._set_output_label(f"FluidSynth: {os.path.basename(path)}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load SF2 Error", str(e))

    def _on_reset_output(self):
        try:
            if hasattr(self.synth, "unload_sf2"):
                self.synth.unload_sf2()
            self.synth.enabled = False
        except Exception:
            pass
        self._apply_initial_output_route()

    # =============================================================================
    #  Preview（PianoRoll 左側鍵盤試彈）
    # =============================================================================
    def _preview_note(self, pitch: int, on: bool):
        tr = self._get_active_track()
        if tr is None:
            return
        if on:
            if Message and self.midi.enabled:
                self.midi.send(Message("note_on", note=pitch, velocity=96, channel=tr.channel))
            self.synth.note_on(tr.channel, pitch, 96)
        else:
            if Message and self.midi.enabled:
                self.midi.send(Message("note_off", note=pitch, velocity=0, channel=tr.channel))
            self.synth.note_off(tr.channel, pitch)

    def _apply_defaults_from_config(self) -> float:
        """
        讀取 self.cfg，將 default_bpm 與 default_tracks 套進 self.proj。
        回傳實際套用後的 bpm（方便後面同步到 UI spinner）。
        """
        try:
            # 1) BPM
            bpm = float(self.cfg.get("default_bpm", DEFAULT_BPM))
            if self.proj.tempo_map:
                self.proj.tempo_map[0].bpm = bpm
            else:
                self.proj.tempo_map = [TempoChange(0, bpm)]

            # 2) Tracks 數量（1..MAX_TRACKS）
            target = int(self.cfg.get("default_tracks", 1))
            target = max(1, min(MAX_TRACKS, target))

            cur = len(self.proj.tracks)

            # 若需要增生 tracks，就補到指定數量
            if cur < target:
                from PySide6 import QtGui
                color_pool = [QtGui.QColor(*rgb) for rgb in COLOR_POOL]
                while cur < target:
                    idx = cur + 1
                    self.proj.tracks.append(Track(
                        name=f"Track {idx}",
                        channel=self._next_free_channel() if hasattr(self, "_next_free_channel") else (idx - 1) % 16,
                        program=0,
                        volume=100,
                        color=color_pool[(idx - 1) % len(color_pool)],
                        notes=[],
                    ))
                    cur += 1
            elif cur > target:
                # 若多了就裁到指定數量（至少留 1 條）
                self.proj.tracks = self.proj.tracks[:target]

            return bpm
        except Exception as e:
            import logging
            logging.warning("Failed to apply default bpm/tracks from config: %s", e)
            return DEFAULT_BPM
    
    # =============================================================================
    #  Auto Backup / Restore + Crash Marker
    # =============================================================================
    def _autosave_dir(self):
        import pathlib
        return pathlib.Path(__file__).resolve().parents[2] / "autosave"

    def _autosave_mid_path(self):
        return self._autosave_dir() / "autosave.mid"

    def _autosave_meta_path(self):
        return self._autosave_dir() / "autosave_meta.json"

    def _crash_marker_path(self):
        import pathlib
        return pathlib.Path(__file__).resolve().parents[2] / "autosave" / ".session.lock"

    def _mark_session_start(self):
        p = self._crash_marker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text("running", encoding="utf-8")
        except Exception:
            pass

    def _mark_session_clean_exit(self):
        p = self._crash_marker_path()
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    def _was_last_exit_unclean(self) -> bool:
        try:
            return self._crash_marker_path().exists()
        except Exception:
            return False

    def _auto_backup_init(self):
        """依偏好設定啟用/停用自動備份（每 60 秒）。"""
        try:
            if hasattr(self, "_auto_timer") and self._auto_timer:
                self._auto_timer.stop()
                self._auto_timer.deleteLater()
                self._auto_timer = None
        except Exception:
            pass

        if not bool(self.cfg.get("auto_backup", False)):
            return

        self._auto_timer = QtCore.QTimer(self)
        self._auto_timer.setInterval(60_000)  # 60 秒
        self._auto_timer.timeout.connect(self._auto_backup_tick)
        self._auto_timer.start()

    def _auto_backup_tick(self):
        try:
            self._save_autosave_files()
        except Exception:
            # 靜默失敗，避免干擾編輯
            pass

    def _save_autosave_files(self):
        """把當前 Project 轉成 autosave.mid + autosave_meta.json。"""
        from ..core import io_midi
        import json, time

        d = self._autosave_dir()
        d.mkdir(parents=True, exist_ok=True)

        # 1) MIDI
        mid = io_midi.to_mido(self.proj)
        mid.save(str(self._autosave_mid_path()))

        # 2) Meta（UI/Transport 狀態）
        meta = {
            "ts": time.time(),
            "bpm": float(self.proj.bpm_at(0)),
            "ppq": int(self.proj.ppq),
            "transport": {
                "pos": int(getattr(self.trans, "pos", 0)),
                "loop_on": bool(getattr(self.trans, "loop_on", False)),
                "loop_a": int(getattr(self.trans, "loop_a", 0)),
                "loop_b": int(getattr(self.trans, "loop_b", self.proj.ppq * 16)),
                "speed": float(getattr(self.trans, "speed", 1.0)),
            },
            "ui": {
                "active_track": int(getattr(self.roll, "active_track", 0)),
                "visible_tracks": [i for i, t in enumerate(self.proj.tracks) if getattr(t, "view_active", False)],
                "snap": int(getattr(self.roll, "snap", max(1, self.proj.ppq // 4))),
                "px_per_tick": float(getattr(self.roll, "px_per_tick", 0.08)),
                "px_per_note": float(getattr(self.roll, "px_per_note", 12)),
            },
        }
        with open(self._autosave_meta_path(), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _maybe_restore_autosave(self):
        import json, time
        mid_p = self._autosave_mid_path()
        meta_p = self._autosave_meta_path()

        if not (bool(self.cfg.get("auto_restore", False))
                and mid_p.exists() and meta_p.exists()
                and self._was_last_exit_unclean()):
            return

        # 提示
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mid_p.stat().st_mtime))
        resp = QtWidgets.QMessageBox.question(
            self, "Restore Session?",
            f"Detected an autosave from {mtime}.\nDo you want to restore the last session?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return

        # 實際還原
        try:
            mid = mido.MidiFile(str(mid_p))
            from ..core import io_midi
            new_proj = io_midi.from_mido(mid)

            # 換專案並重綁 Transport / UI
            self.proj = new_proj
            self._coerce_track_colors()
            if hasattr(self.trans, "bind_project"):
                self.trans.bind_project(self.proj)
            else:
                self.trans.p = self.proj
                try: self.trans.reanchor()
                except Exception: pass

            self.roll.p = self.proj
            self.tracks_panel.p = self.proj
            self.tracks_panel._build()
            self._update_visible_tracks()
            self.roll.draw_grid()
            self.roll.refresh_notes()

            # 套用 meta 狀態
            with open(meta_p, "r", encoding="utf-8") as f:
                meta = json.load(f)

            tr = meta.get("transport", {})
            self.trans.loop_on = bool(tr.get("loop_on", False))
            self.trans.loop_a  = int(tr.get("loop_a", 0))
            self.trans.loop_b  = int(tr.get("loop_b", self.proj.ppq * 16))
            self.trans.set_speed(float(tr.get("speed", 1.0)))
            self.trans.set_pos(int(tr.get("pos", 0)))

            ui = meta.get("ui", {})
            self.roll.active_track = int(ui.get("active_track", 0))
            vis = set(ui.get("visible_tracks", []))
            for i, t in enumerate(self.proj.tracks):
                t.view_active = (i in vis)
            self.roll.set_visible_tracks(vis if vis else None)
            try:
                self.roll.snap = int(ui.get("snap", max(1, self.proj.ppq // 4)))
                self.roll.px_per_tick = float(ui.get("px_per_tick", 0.08))
                self.roll.px_per_note = float(ui.get("px_per_note", 12))
            except Exception:
                pass

            # 同步 UI 控制元件
            self._refresh_status_bpm()
            self.tracks_panel.set_active_index(self.roll.active_track)
            self._sync_loop_spins()
            self._update_time_label(None)
            self._ensure_valid_active_track()
            self._update_add_button()
            self.roll.setFocus()

            # （建議）還原成功後刪掉 autosave，避免下次重複提示
            try:
                mid_p.unlink(missing_ok=True)
                meta_p.unlink(missing_ok=True)
            except Exception:
                pass

            QtWidgets.QMessageBox.information(self, "Restore", "Session restored from autosave.")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Restore Failed", str(e))

    def _ensure_timesig_map(self):
        # 若專案沒有 timesig_map，補一個 4/4 @ tick 0
        if not hasattr(self.proj, "timesig_map") or self.proj.timesig_map is None:
            self.proj.timesig_map = []
        if not self.proj.timesig_map:
            self.proj.timesig_map = [_TimeSigChange(0, 4, 4)]
        # 依 tick 排序
        self.proj.timesig_map.sort(key=lambda x: int(getattr(x, "tick", 0)))

    def _open_timesig_tempo_dialog(self):
        self._ensure_timesig_map()
        dlg = _TimesigTempoDialog(self, self.proj)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            data = dlg.values()
            # 1) 寫入 timesig_map
            ts_list = []
            for d in data["timesigs"]:
                ts_list.append(_TimeSigChange(int(d["tick"]), int(d["num"]), int(d["den"])))
            ts_list.sort(key=lambda x: x.tick)
            self.proj.timesig_map = ts_list

            # 2) 寫入 tempo_map（沿用你原本的 TempoChange 結構）
            from ..core.models import TempoChange
            tm_list = []
            for d in data["tempos"]:
                tm_list.append(TempoChange(int(d["tick"]), float(d["bpm"])))
            tm_list.sort(key=lambda x: x.tick)
            self.proj.tempo_map = tm_list if tm_list else [TempoChange(0, self._get_current_bpm())]


            # 重新定位 Transport（時長換算用 tempo_map）
            try:
                self.trans.reanchor()
            except Exception:
                pass

            self.roll.draw_grid()
            self.roll.refresh_notes()
            self._update_time_label(None)
            self._refresh_status_bpm()

    # =============================================================================
    #  Helpers（顏色、主題/輸出套用、偏好設定）
    # =============================================================================
    def _coerce_track_colors(self):
        """把 core 的 Track.color（可能為 (r,g,b) 或 None）轉成 QColor，或用 COLOR_POOL 補色。"""
        color_pool = [QtGui.QColor(*rgb) for rgb in COLOR_POOL]
        for i, tr in enumerate(getattr(self, "proj", Project()).tracks):
            c = getattr(tr, "color", None)
            if isinstance(c, tuple) and len(c) == 3:
                tr.color = QtGui.QColor(*c)
            elif c is None or not isinstance(c, QtGui.QColor):
                tr.color = color_pool[i % len(color_pool)]

    def _apply_theme_from_cfg(self):
        """
        根據設定檔套用主題。
        - 優先使用新版 theme.apply_theme(window, theme, custom=…)
        - 若失敗，退回 dark / light 後援
        """
        theme_name = (self.cfg or {}).get("theme", "dark")
        custom_map = (self.cfg or {}).get("theme_custom")

        # 新版：支援多皮膚與自訂色
        if callable(_apply_theme):
            try:
                _apply_theme(self, theme_name, custom=custom_map)
                return
            except Exception:
                pass  # 後援

        # 後援：舊版僅 dark / light
        if theme_name == "dark":
            # 若你有 legacy apply_dark_theme，可在此呼叫；留白代表沿用現行 palette
            pass
        else:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.setStyle("Fusion")
                app.setPalette(QtGui.QPalette())
            self.setStyleSheet("")  # 清掉可能殘留的深色樣式

    def _apply_output_from_cfg(self):
        """
        依 cfg 選擇輸出：
          - system：打開 preferred_midi_output（若沒設或不存在則 fallback）
          - fluidsynth：若 .sf2 路徑存在則載入並切到內部合成器
        """
        try:
            mode = self.cfg.get("output_device", "system")
            if mode == "fluidsynth":
                sf = str(self.cfg.get("soundfont_path", "")).strip()
                if sf and os.path.isfile(sf):
                    self.synth.load_sf2(sf)
                    self.midi.disable()
                    self._set_output_label(f"FluidSynth: {os.path.basename(sf)}")
                    return
                else:
                    self._set_output_label("FluidSynth (no sf2)")
                    return

            # system（預設）
            outs = self.midi.outputs()
            pref = str(self.cfg.get("preferred_midi_output", "")).strip()
            target = None
            if outs:
                if pref and pref in outs:
                    target = pref
                else:
                    target = next(
                        (n for n in outs if "Microsoft GS Wavetable" in n or "Microsoft GS" in n), outs[0]
                    )

            if target and self.midi.open_by_name(target):
                self.synth.enabled = False
                self._set_output_label(target)
                return

            self._set_output_label("No system output")
        except Exception as e:
            logging.warning("apply output failed: %s", e)
            self._set_output_label("Output init error")

    def _open_preferences(self):
        outs = self.midi.outputs()
        dlg = PreferencesDialog(self, self.cfg, outs)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            # 1) 存新設定
            self.cfg = dlg.result_config()
            save_config(self.cfg)
            # 2) 立即套用主題與輸出
            self._apply_theme_from_cfg()
            self._apply_output_from_cfg()
            self._auto_backup_init()  # 依新設定重啟/停止定時器
            QtWidgets.QMessageBox.information(self, "Preferences", "Settings saved.")

    def _freeze_cache_dir(self):
        import pathlib
        d = pathlib.Path(__file__).resolve().parents[2] / "freeze_cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _render_single_track_to_wav(self, idx: int) -> str:
        """
        將單一軌渲染成 WAV。若相同內容已渲染過，直接用快取，不重算。
        回傳：輸出 WAV 絕對路徑。
        """
        import os, shutil, subprocess, sys, tempfile, hashlib
        from io import BytesIO

        # --- fluidsynth 可執行檢查 ---
        exe = shutil.which("fluidsynth")
        if not exe:
            QtWidgets.QMessageBox.critical(self, "Freeze 失敗", "找不到 fluidsynth；請安裝並加到 PATH。")
            raise RuntimeError("fluidsynth missing")

        # --- 取得 sf2（先用目前載入的） ---
        sf = getattr(getattr(self, "synth", None), "sf2_path", "") or ""
        if not (sf and os.path.isfile(sf)):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "選擇 SoundFont", "", "SoundFont (*.sf2)")
            if not path:
                raise RuntimeError("no sf2 selected")
            sf = path

        # --- 建臨時 Project，只含目標軌，並輸出成 MIDI ---
        from ..core.models import Project, Track
        from ..core import io_midi

        tmp = Project()
        tmp.ppq = getattr(self.proj, "ppq", 480)
        tmp.tempo_map = list(getattr(self.proj, "tempo_map", []))
        tr = self.proj.tracks[idx]
        tr_copy = Track(
            name=tr.name, channel=tr.channel, program=tr.program, volume=tr.volume,
            color=tr.color, notes=list(tr.notes), mute=False, solo=False, view_active=True
        )
        tmp.tracks = [tr_copy]

        # 寫到暫存 MIDI 檔
        tmpdir = tempfile.mkdtemp(prefix="freeze_")
        mid_path = os.path.join(tmpdir, "t.mid")
        mid = io_midi.to_mido(tmp)
        mid.save(mid_path)

        # --- 用「MIDI 位元組 + sf2 路徑 + 渲染參數」算快取鍵 ---
        with open(mid_path, "rb") as f:
            midi_bytes = f.read()
        hasher = hashlib.sha1()
        hasher.update(midi_bytes)
        hasher.update(sf.encode("utf-8"))
        # 把關鍵渲染參數也納入，以免改了參數卻誤用舊檔
        hasher.update(b"rate=48000;format=s24;gain=0.8;rv=0;ch=0")
        key = hasher.hexdigest()

        out_dir = self._freeze_cache_dir()
        out_path = os.path.join(out_dir, f"{key}.wav")

        # 若已有快取，直接用
        if os.path.isfile(out_path):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return out_path

        # --- 沒快取就呼叫 fluidsynth 渲染 ---
        base = [exe, "-ni", "-F", out_path, "-r", "48000",
                "-o", "synth.reverb.active=0",
                "-o", "synth.chorus.active=0",
                "-o", "synth.gain=0.8"]

        cmd = list(base) + ["-o", "audio.file.format=s24", sf, mid_path]
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0
        try:
            subprocess.run(cmd, check=True, creationflags=creation, capture_output=True, text=True)
        except subprocess.CalledProcessError:
            # 某些版本不支援 s24，就退回預設格式
            cmd2 = list(base) + [sf, mid_path]
            subprocess.run(cmd2, check=True, creationflags=creation, capture_output=True, text=True)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return out_path

    def _ensure_player_for_track(self, idx: int, wav_path: str):
        """為凍結軌建立/更新播放器。"""
        # 若已存在就重設 media
        if idx in self._freeze_players:
            player, audio = self._freeze_players[idx]
        else:
            audio = QAudioOutput(self)
            player = QMediaPlayer(self)
            player.setAudioOutput(audio)
            self._freeze_players[idx] = (player, audio)
        # 設定來源
        player.setSource(QUrl.fromLocalFile(wav_path))
        player.setPlaybackRate(float(getattr(self.trans, "speed", 1.0)))
        # 音量用 Track.volume 百分比（簡化）
        vol_pct = max(0, min(100, round(self.proj.tracks[idx].volume * 100 / 127)))
        audio.setVolume(vol_pct / 100.0)

    def _seek_freeze_players_to(self, tick: int):
        """將所有凍結播放器 seek 到對應時間（毫秒）。"""
        try:
            sec = self.proj.seconds_between(0, int(tick))
        except Exception:
            sec = 0.0
        ms = int(max(0.0, sec) * 1000)
        for idx, (player, _audio) in list(self._freeze_players.items()):
            # 媒體必須可用才會 seek；忽略短暫無法 seek 的狀態
            try:
                player.setPosition(ms)
            except Exception:
                pass

    def _on_playing_for_freeze(self, playing: bool):
        """Play/Pause 時控制凍結播放器同步。"""
        if playing:
            # 先對齊目前位置再播放
            self._seek_freeze_players_to(int(getattr(self.trans, "pos", 0)))
            for player, _audio in self._freeze_players.values():
                try: player.play()
                except Exception: pass
        else:
            for player, _audio in self._freeze_players.values():
                try: player.pause()
                except Exception: pass

    def _on_tick_for_freeze(self, tick: int):
        """
        監聽 tick 變化：偵測大跳或 loop 折返時重新對齊播放器。
        規則：tick 比上次小（倒退/折返）或一次跳動超過 ~2拍（ppq*2）就 seek。
        """
        if not getattr(self.trans, "playing", False):
            self._last_tick_for_freeze = tick
            return
        last = self._last_tick_for_freeze
        self._last_tick_for_freeze = tick
        if last is None:
            return
        ppq = max(1, int(self.proj.ppq))
        if tick < last or abs(tick - last) > (ppq * 2):
            self._seek_freeze_players_to(tick)

    def _toggle_track_freeze(self, idx: int, on: bool):
        """按下 F：凍結/解凍該軌。"""
        if not (0 <= idx < len(self.proj.tracks)):
            return
        tr = self.proj.tracks[idx]

        if on:
            # 已有就直接啟用視覺狀態與播放器
            try:
                wav = self._render_single_track_to_wav(idx)
            except Exception as e:
                QtWidgets.QMessageBox.warning(self, "Freeze 失敗", str(e))
                # 回復按鈕狀態
                tr.frozen = False; tr.freeze_path = ""
                self.tracks_panel._build()
                return

            # 記錄與 UI
            tr.freeze_path = wav
            tr.frozen = True
            # 凍結期間把原本的 mute 記起來，並強制靜音，避免 MIDI 與 WAV 疊音
            tr._freeze_prev_mute = tr.mute
            tr.mute = True
            # 建播放器
            self._ensure_player_for_track(idx, wav)
            # 若正在播放，立即加入播放並對齊當前位置
            if getattr(self.trans, "playing", False):
                self._seek_freeze_players_to(int(getattr(self.trans, "pos", 0)))
                try: self._freeze_players[idx][0].play()
                except Exception: pass

        else:
            # 解除
            tr.frozen = False
            # 還原 mute 狀態
            if tr._freeze_prev_mute is not None:
                tr.mute = bool(tr._freeze_prev_mute)
                tr._freeze_prev_mute = None
            # 關掉播放器
            if idx in self._freeze_players:
                try:
                    player, _audio = self._freeze_players.pop(idx)
                    player.stop()
                    player.deleteLater()
                except Exception:
                    pass
            tr.freeze_path = ""

        # 重建一次左面板，刷新「F/❄️」視覺
        self.tracks_panel._build()
        # 也刷新一次狀態列資料（例如 Notes/Duration 等）
        self._refresh_status_all()

    def _apply_speed_to_freeze_players(self):
        rate = float(getattr(self.trans, "speed", 1.0))
        for player, _audio in self._freeze_players.values():
            try:
                player.setPlaybackRate(rate)
            except Exception:
                pass

    def _rekey_freeze_players_after_delete(self, deleted_idx: int):
        # 先處理被刪掉的
        if deleted_idx in self._freeze_players:
            try:
                player, _audio = self._freeze_players.pop(deleted_idx)
                player.stop(); player.deleteLater()
            except Exception:
                pass
        # 其餘索引往前移
        new_map = {}
        for idx, pair in self._freeze_players.items():
            new_idx = idx - 1 if idx > deleted_idx else idx
            new_map[new_idx] = pair
        self._freeze_players = new_map

    def _clear_all_freeze_players(self):
        for idx, (player, _audio) in list(self._freeze_players.items()):
            try:
                player.stop(); player.deleteLater()
            except Exception:
                pass
        self._freeze_players.clear()
        for t in getattr(self.proj, "tracks", []):
            try:
                t.frozen = False
                t.freeze_path = ""
                t._freeze_prev_mute = None
            except Exception:
                pass

    def _toggle_track_lock(self, idx: int, on: bool):
        """按下 L：鎖定/解鎖該軌（僅影響編輯，不影響播放與 M/S/F/View）。"""
        if not (0 <= idx < len(self.proj.tracks)):
            return

        tr = self.proj.tracks[idx]
        tr.locked = bool(on)

        # 狀態列提示
        self.sb_lock.setText("此軌已鎖定 (L 解鎖)" if on else "")

        # 立即重建左側面板（同步 L/🔒 視覺）
        try:
            self.tracks_panel._build()
        except Exception:
            pass

        # ★ 重新繪製 Piano Roll，讓透明度立即生效（不只作用軌）
        try:
            if hasattr(self, "roll") and self.roll is not None:
                self.roll.refresh_notes()
        except Exception:
            pass

    def _effective_bpm_at(self, tick: int) -> float:
        """tempo map 的當前段落 BPM × speed（倍速）"""
        try:
            base = float(self.proj.bpm_at(int(tick)))
        except Exception:
            base = 120.0
        speed = float(getattr(self.trans, "speed", 1.0))
        return base * speed

    def _cleanup_freeze_cache(self, keep_in_use: bool = False):
        """
        清理 freeze_cache。
        keep_in_use=True：保留目前專案仍指向的檔案；False：全部刪除。
        """
        import os, shutil, glob
        d = self._freeze_cache_dir()
        if not os.path.isdir(d):
            return
        in_use = set()
        if keep_in_use:
            for t in getattr(self.proj, "tracks", []):
                p = getattr(t, "freeze_path", "") or ""
                if p and os.path.isfile(p):
                    in_use.add(os.path.abspath(p))

        for p in glob.glob(os.path.join(d, "*.wav")):
            ap = os.path.abspath(p)
            if (not keep_in_use) or (ap not in in_use):
                try: os.remove(ap)
                except Exception: pass

        # 如果整個資料夾空了，可以順手移除
        try:
            if not os.listdir(d):
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

    def _ensure_groups_list(self):
        if not hasattr(self.proj, "groups") or self.proj.groups is None:
            self.proj.groups = []

    def _find_group(self, gid: str):
        for g in getattr(self.proj, "groups", []):
            if getattr(g, "id", None) == gid:
                return g
        return None

    def _add_group(self):
        self._ensure_groups_list()
        g = TrackGroup(name=f"Group {len(self.proj.groups) + 1}")
        self.proj.groups.append(g)
        # 立即刷新左側面板
        self.tracks_panel._build()

    def _on_group_mute(self, gid: str, v: bool):
        g = self._find_group(gid)
        if not g: return
        g.mute = bool(v)
        for i, t in enumerate(self.proj.tracks):
            if getattr(t, "group_id", None) == gid:
                self._set_track_mute(i, v)

    def _on_group_solo(self, gid: str, v: bool):
        g = self._find_group(gid)
        if not g: return
        g.solo = bool(v)
        # MVP：把群組內所有軌的 solo 設為相同值（不做複雜互斥），之後若要真・Solo 邏輯再升級
        for i, t in enumerate(self.proj.tracks):
            if getattr(t, "group_id", None) == gid:
                self._set_track_solo(i, v)

    def _on_group_rename(self, gid: str, name: str):
        g = self._find_group(gid)
        if not g: return
        g.name = name  # 若想立即刷新左面板顯示，可呼叫 self.tracks_panel._build()

    def _on_group_color(self, gid: str, col: QtGui.QColor):
        g = self._find_group(gid)
        if not g: return
        g.color = col

    def _on_group_collapse(self, gid: str, c: bool):
        g = self._find_group(gid)
        if not g: return
        g.collapsed = bool(c)

    def _on_group_delete(self, gid: str):
        g = self._find_group(gid)
        if not g:
            return

        # 可選：確認對話框（不要就移除這段）
        ret = QtWidgets.QMessageBox.question(
            self, "Delete Group",
            f"Delete group “{getattr(g, 'name', 'Group')}”? Tracks will be kept.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )
        if ret != QtWidgets.QMessageBox.Yes:
            return

        # 1) 先把群組內的音軌脫離群組（保留音軌）
        for t in getattr(self.proj, "tracks", []):
            if getattr(t, "group_id", None) == gid:
                t.group_id = None

        # 2) 從專案中移除該群組
        try:
            self.proj.groups = [x for x in getattr(self.proj, "groups", []) if getattr(x, "id", None) != gid]
        except Exception:
            # 如果沒有 groups 屬性也不致錯
            pass

        # 3) 重建左側面板
        self.tracks_panel._build()

    def _get_current_bpm(self) -> float:
        try:
            tm = getattr(self.proj, "tempo_map", None)
            if tm and len(tm) > 0:
                return float(getattr(tm[0], "bpm", 120.0))
        except Exception:
            pass
        # 回退：proj.tempo（若你有此屬性）
        try:
            return float(getattr(self.proj, "tempo", 120.0))
        except Exception:
            return 120.0

    def _sync_bpm_ui_from_model(self):
        bpm = self._get_current_bpm()
        # 把 self.bpm 也納入，並在找到任何一個就更新
        for wname in ("bpm", "bpm_spin", "tempo_spin"):
            w = getattr(self, wname, None)
            if w is not None:
                try:
                    w.blockSignals(True)
                    w.setValue(bpm)   # QDoubleSpinBox / QSpinBox 皆可
                finally:
                    w.blockSignals(False)
                break
        # 更新狀態列顯示
        self._refresh_status_bpm()

    def _alloc_channel_preserve(self, preferred: int | None, used: set[int]) -> int:
        """
        盡量保留檔案內的通道；若衝突才找下一個空位（0..15）。
        盡可能保住 CH9（GM 鼓）。
        """
        ALL = list(range(16))
        if preferred is not None and 0 <= int(preferred) <= 15 and int(preferred) not in used:
            return int(preferred)

        # 若 preferred 衝突：從 0..15 找第一個沒用的；但若 preferred 是 9，先嘗試保留 9
        if preferred == 9 and 9 not in used:
            return 9

        for ch in ALL:
            if ch not in used:
                return ch
        # 實在沒有 → 退回 preferred 或 0
        return int(preferred) if (preferred is not None and 0 <= int(preferred) <= 15) else 0

    def _add_marker_at_playhead(self):
        t = int(getattr(self.trans, "pos", 0))
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Marker", "Name:", text="Section")
        if ok and str(name).strip():
            m = Marker(tick=t, name=str(name).strip())
            self.proj.markers.append(m)
            self.proj.markers.sort(key=lambda x: x.tick)
            self.roll.draw_grid()  # 重新繪製尺規/marker
            self.roll.refresh_notes()

    def _jump_marker(self, dir: int):  # dir = -1 / +1
        pos = int(getattr(self.trans, "pos", 0))
        marks = sorted(getattr(self.proj, "markers", []), key=lambda m: m.tick)
        if not marks:
            return
        if dir > 0:
            # 下一個：第一個 > pos
            for m in marks:
                if int(m.tick) > pos:
                    self.trans.set_pos(int(m.tick))
                    self._update_time_label(None)
                    return
            # 沒有 → 到最後一個
            self.trans.set_pos(int(marks[-1].tick))
        else:
            # 上一個：最後一個 < pos
            prev = None
            for m in marks:
                if int(m.tick) < pos:
                    prev = m
                else:
                    break
            self.trans.set_pos(int(prev.tick) if prev else int(marks[0].tick))
        self._update_time_label(None)
        
    def _on_saved_ok(self):
        """存檔成功後呼叫：清乾淨並刷新基準修訂號。"""
        try:
            self.proj.clear_dirty()
            self._last_saved_rev = int(getattr(self.proj, "rev", 0))
        except Exception:
            pass

    def _maybe_save(self, reason: str = "") -> bool:
        """
        有未儲存變更時詢問是否儲存。
        回傳 True=可以繼續（或已存好）、False=取消動作。
        """
        p = getattr(self, "proj", None)
        if not p:
            return True

        # 沒有變更就放行（雙保險：dirty 或 rev 對比）
        if (not getattr(p, "dirty", False)) or (int(getattr(p, "rev", 0)) == int(self._last_saved_rev)):
            return True

        title = "儲存檔案"
        name = getattr(self, "_current_filename", "未命名")
        msg = f"你要儲存「{name}」嗎？"
        if reason:
            msg = reason + "\n\n" + msg

        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(msg)
        yes = box.addButton("是(&Y)", QtWidgets.QMessageBox.YesRole)
        no  = box.addButton("否(&N)", QtWidgets.QMessageBox.NoRole)
        cancel = box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        box.setDefaultButton(yes)
        box.exec()

        clicked = box.clickedButton()
        if clicked is yes:
            # 直接沿用你既有的「匯出/存檔 MIDI」流程
            try:
                self.save_midi()      # 這個函式目前會彈出另存對話框→存檔
                self._on_saved_ok()
                return True
            except Exception:
                return False
        elif clicked is no:
            return True
        else:
            return False


class _TimesigTempoDialog(QtWidgets.QDialog):
    """
    Unified time map editor (Bar-based):
      Columns: Bar | Num | Den | BPM
      - 'Bar' is 1-based start bar where the change takes effect.
      - Num/Den for time signature; BPM for tempo. Either can be empty to skip.
      - Internally convert back to tick for timesig_map/tempo_map.
    """
    def __init__(self, parent, proj):
        super().__init__(parent)
        self.setWindowTitle("Time Signature & Tempo Map")
        self.setModal(True)
        self.resize(720, 520)
        self.p = proj

        # === widgets ===
        self.grid = QtWidgets.QTableWidget(0, 4, self)
        self.grid.setHorizontalHeaderLabels(["Bar", "Num", "Den", "BPM"])
        self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.grid.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)

        btn_add = QtWidgets.QPushButton("+ Add")
        btn_edit = QtWidgets.QPushButton("Edit")
        btn_del = QtWidgets.QPushButton("Delete")
        btn_ok = QtWidgets.QPushButton("套用")
        btn_cancel = QtWidgets.QPushButton("取消")

        row_btn = QtWidgets.QHBoxLayout()
        for b in (btn_add, btn_edit, btn_del):
            row_btn.addWidget(b)
        row_btn.addStretch(1)

        row_ok = QtWidgets.QHBoxLayout()
        row_ok.addStretch(1); row_ok.addWidget(btn_ok); row_ok.addWidget(btn_cancel)

        lay = QtWidgets.QGridLayout(self)
        lay.addWidget(self.grid, 0, 0)
        lay.addLayout(row_btn, 1, 0)
        lay.addLayout(row_ok, 2, 0)

        # wire
        btn_add.clicked.connect(self._on_add)
        btn_edit.clicked.connect(self._on_edit)
        btn_del.clicked.connect(self._on_delete)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)

        self._populate()

    # ---------- helpers: timing math ----------
    def _tpb(self, num:int, den:int) -> int:
        ppq = max(1, int(getattr(self.p, "ppq", 480)))
        tpbeat = int(round(ppq * (4 / max(1, den))))
        return max(1, tpbeat * max(1, num))

    def _normalize_rows(self):
        """
        Read table rows -> list of (bar:int>=1, num:int|None, den:int|None, bpm:float|None)
        Sorted by bar; dedup by bar (keep last).
        """
        rows = []
        for r in range(self.grid.rowCount()):
            def text(r,c):
                it = self.grid.item(r, c)
                return it.text().strip() if it else ""
            try:
                bar = max(1, int(text(r,0) or "1"))
                num = int(text(r,1)) if text(r,1) else None
                den = int(text(r,2)) if text(r,2) else None
                bpm = float(text(r,3)) if text(r,3) else None
                rows.append((bar, num, den, bpm))
            except Exception:
                pass
        if not rows:
            rows = [(1, 4, 4, float(getattr(self.p, "tempo", 120.0)))]
        rows.sort(key=lambda x: x[0])
        # dedup same bar (keep last)
        ded = []
        for tup in rows:
            if ded and ded[-1][0] == tup[0]:
                ded[-1] = tup
            else:
                ded.append(tup)
        return ded

    def _rows_timesig_stream(self):
        """
        讀出 (bar,num,den) 的拍號變更清單：
        - 只要該列 Num/Den 有值就保留（即使和前段相同也可，避免被「吃掉」）
        - 若整表沒有任何拍號，強制補上 (1, 4, 4)
        - 若同一個 bar 多次設定，保留最後一次
        """
        rows = self._normalize_rows()
        ts_by_bar = {}
        for (bar, num, den, _bpm) in rows:
            if num is not None and den is not None:
                ts_by_bar[int(bar)] = (int(num), int(den))

        if not ts_by_bar:
            ts_by_bar[1] = (4, 4)  # 至少保留一筆

        for b in sorted(ts_by_bar.keys()):
            n, d = ts_by_bar[b]
            yield (b, n, d)

    def _rows_tempo_stream(self):
        """Yield (bar,bpm) from rows; ignore empty bpm."""
        for (bar, _num, _den, bpm) in self._normalize_rows():
            if bpm is None: 
                continue
            yield (bar, float(bpm))

    def _bar_to_tick(self, target_bar:int) -> int:
        """
        Convert 1-based bar to tick, using *the table rows themselves* as the authoritative
        time-signature map (carry-over for missing num/den).
        """
        # Build a clean timesig list first
        ts = list(self._rows_timesig_stream())
        if not ts:
            ts = [(1,4,4)]
        ts.sort(key=lambda x: x[0])

        if target_bar <= 1:
            return 0

        tick = 0
        # walk segments: [bar_i, bar_{i+1})
        for i,(bar_i,num_i,den_i) in enumerate(ts):
            bar_next = ts[i+1][0] if i+1 < len(ts) else None
            tpb_i = self._tpb(num_i, den_i)
            if bar_next is None or target_bar <= bar_next:
                tick += max(0, (target_bar - bar_i)) * tpb_i
                return tick
            else:
                tick += max(0, (bar_next - bar_i)) * tpb_i
        return tick

    # ---------- populate / edit / values ----------
    def _append_row(self, bar:int, num=None, den=None, bpm=None):
        r = self.grid.rowCount(); self.grid.insertRow(r)
        def seti(c, val):
            it = QtWidgets.QTableWidgetItem("" if val is None else str(val))
            it.setTextAlignment(Qt.AlignCenter)
            self.grid.setItem(r, c, it)
        seti(0, bar); seti(1, num); seti(2, den); seti(3, f"{float(bpm):.2f}" if bpm is not None else None)

    def _populate(self):
        self.grid.setRowCount(0)
        # timesigs -> bars
        ts = list(getattr(self.p, "timesig_map", []) or [])
        ts = sorted(ts, key=lambda x: int(getattr(x, "tick", 0)))
        if not ts:
            ts = [type("T", (), {"tick":0, "num":4, "den":4})()]
        # tempos -> bars
        tm = list(getattr(self.p, "tempo_map", []) or [])
        tm = sorted(tm, key=lambda x: int(getattr(x, "tick", 0)))

        # merge two streams by bar index
        # first, convert each to (bar,...) using current rows logic
        def tick_to_bar_local(tick:int) -> int:
            # approximate: accumulate using current known project ts_map
            # (good enough for populate; edits will re-save with our own mapping)
            ts_local = sorted(getattr(self.p, "timesig_map", []) or [], key=lambda z: int(getattr(z, "tick",0)))
            if not ts_local:
                ts_local = [type("T", (), {"tick":0,"num":4,"den":4})()]
            cur_bar = 1; cur_tick = 0
            for i, tsg in enumerate(ts_local):
                num = int(getattr(tsg,"num",4)); den = int(getattr(tsg,"den",4))
                tpb = self._tpb(num, den)
                next_tick = int(getattr(ts_local[i+1],"tick", tick)) if i+1<len(ts_local) else tick
                span = max(0, min(tick, next_tick) - int(getattr(tsg,"tick",0)))
                bars = span // tpb
                if tick < next_tick:
                    return cur_bar + bars
                cur_bar += bars
            return cur_bar

        bars_ts = [(tick_to_bar_local(int(getattr(x,"tick",0))), int(getattr(x,"num",4)), int(getattr(x,"den",4))) for x in ts]
        bars_tm = [(tick_to_bar_local(int(getattr(x,"tick",0))), float(getattr(x,"bpm", float(getattr(self.p,"tempo",120.0))))) for x in tm] if tm else []

        # merge by bar: last one wins per column
        merged = {}
        for b,n,d in bars_ts: merged.setdefault(b, {})["ts"] = (n,d)
        for b,bpm in bars_tm: merged.setdefault(b, {})["bpm"] = bpm

        if not merged:
            merged = {1: {"ts": (4,4), "bpm": float(getattr(self.p,"tempo",120.0))}}

        for bar in sorted(merged.keys()):
            n,d = merged[bar].get("ts", (None,None))
            bpm = merged[bar].get("bpm", None)
            self._append_row(bar, n, d, bpm)

    def _on_add(self):
        # 以最後一列做預設
        last = self.grid.rowCount() - 1
        def val(c, default=""):
            if last >= 0 and self.grid.item(last, c):
                t = self.grid.item(last, c).text().strip()
                return t if t != "" else default
            return default

        bar, ok = QtWidgets.QInputDialog.getInt(self, "Add", "Bar (1-based):", int(val(0, "1")) + 1, 1)
        if not ok:
            return

        num, ok_num = QtWidgets.QInputDialog.getInt(self, "Add", "Numerator (可按取消略過):", int(val(1, "4")), 1, 64)
        num = int(num) if ok_num else None

        den, ok_den = QtWidgets.QInputDialog.getInt(self, "Add", "Denominator (可按取消略過):", int(val(2, "4")), 1, 64)
        den = int(den) if ok_den else None

        bpm, ok_bpm = QtWidgets.QInputDialog.getDouble(self, "Add", "BPM (可按取消略過):", float(val(3, "120") or 120), 20.0, 999.0, 2)
        bpm = float(bpm) if ok_bpm else None

        self._append_row(int(bar), num, den, bpm)

    def _on_edit(self):
        r = self.grid.currentRow()
        if r < 0:
            return

        def geti(c, default=""):
            it = self.grid.item(r, c)
            return (it.text().strip() if it else default)

        # Bar 必填
        bar_old = int(geti(0, "1") or "1")
        bar, ok = QtWidgets.QInputDialog.getInt(self, "Edit", "Bar (1-based):", bar_old, 1)
        if not ok:
            return

        # Num 可取消 → 變 None
        num_text = geti(1, "")
        num_default = int(num_text) if num_text else 4
        num_val, ok_num = QtWidgets.QInputDialog.getInt(self, "Edit", "Numerator (取消＝清空):", num_default, 1, 64)
        num = int(num_val) if ok_num else None

        # Den 可取消 → 變 None
        den_text = geti(2, "")
        den_default = int(den_text) if den_text else 4
        den_val, ok_den = QtWidgets.QInputDialog.getInt(self, "Edit", "Denominator (取消＝清空):", den_default, 1, 64)
        den = int(den_val) if ok_den else None

        # BPM 可取消 → 變 None
        bpm_text = geti(3, "")
        bpm_default = float(bpm_text) if bpm_text else float(getattr(self.p, "tempo", 120.0))
        bpm_val, ok_bpm = QtWidgets.QInputDialog.getDouble(self, "Edit", "BPM (取消＝清空):", bpm_default, 20.0, 999.0, 2)
        bpm = float(bpm_val) if ok_bpm else None

        # 寫回表格
        def seti(c, v):
            it = QtWidgets.QTableWidgetItem("" if v is None else (f"{v:.2f}" if c == 3 and v is not None else str(v)))
            it.setTextAlignment(Qt.AlignCenter)
            self.grid.setItem(r, c, it)

        seti(0, int(bar))
        seti(1, num)
        seti(2, den)
        seti(3, bpm)

    def _on_delete(self):
        r = self.grid.currentRow()
        if r < 0:
            return

        # 刪除後是否還會至少留下一筆「Num 與 Den 皆有值」的列？
        has_other_ts = False
        for i in range(self.grid.rowCount()):
            if i == r:
                continue
            itn = self.grid.item(i, 1)
            itd = self.grid.item(i, 2)
            if itn and itd and itn.text().strip() != "" and itd.text().strip() != "":
                has_other_ts = True
                break

        if not has_other_ts:
            QtWidgets.QMessageBox.warning(self, "Cannot Delete", "必須至少保留一個拍號，不能全部刪除！")
            return

        self.grid.removeRow(r)

    def values(self) -> dict:
        """
        Return {"timesigs":[{"tick", "num","den"},...],
                "tempos":[{"tick","bpm"},...]} with ticks computed from bars.
        """
        # Build streams
        ts_stream = list(self._rows_timesig_stream())
        tm_stream = list(self._rows_tempo_stream())

        # Convert bar -> tick
        timesigs = []
        for (bar, num, den) in ts_stream:
            timesigs.append(dict(tick=int(self._bar_to_tick(bar)), num=int(num), den=int(den)))
        timesigs.sort(key=lambda d: d["tick"])

        tempos = []
        for (bar, bpm) in tm_stream:
            tempos.append(dict(tick=int(self._bar_to_tick(bar)), bpm=float(bpm)))
        tempos.sort(key=lambda d: d["tick"])

        # Ensure at least one of each exists
        if not timesigs:
            timesigs = [dict(tick=0, num=4, den=4)]
        if not tempos:
            tempos = [dict(tick=0, bpm=float(getattr(self.p, "tempo", 120.0)))]

        return dict(timesigs=timesigs, tempos=tempos)

# =============================================================================
#  App Entry
# =============================================================================
def run_app():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
