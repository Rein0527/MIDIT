# modules/ui/preferences_dialog.py
# =============================================================================
#  Imports / Constants
# =============================================================================
from __future__ import annotations
from typing import Dict, Any, List
from PySide6 import QtCore, QtGui, QtWidgets

THEME_CHOICES = ["dark", "light", "slate", "indigo", "emerald", "amber", "rose", "custom"]

CUSTOM_KEYS = [
    "window", "window_text",
    "base", "alt_base", "text",
    "button", "button_text",
    "bright_text",
    "highlight", "highlight_text",
    "tooltip_base", "tooltip_text",
]

DEFAULT_CUSTOM_THEME: Dict[str, str] = {
    "window": "#2b2d30", "window_text": "#ebebee",
    "base": "#212427", "alt_base": "#303236", "text": "#ebebee",
    "button": "#3a3d42", "button_text": "#f0f0f4",
    "bright_text": "#ff5f5f",
    "highlight": "#5aa0ff", "highlight_text": "#101114",
    "tooltip_base": "#303236", "tooltip_text": "#f0f0f4",
}

# =============================================================================
#  Widget: ColorButton
# =============================================================================
class ColorButton(QtWidgets.QPushButton):
    """可點擊的色塊按鈕，顯示 hex；按下可挑顏色。"""
    colorChanged = QtCore.Signal(str)

    def __init__(self, hex_color: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._apply_style(hex_color)
        self.clicked.connect(self._pick)

    def _apply_style(self, hex_color: str):
        c = QtGui.QColor(hex_color)
        if not c.isValid():
            c = QtGui.QColor("#808080")
            hex_color = c.name()

        luminance = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255.0
        text_col = "#000000" if luminance > 0.6 else "#ffffff"
        self.setStyleSheet(
            f"QPushButton{{background:{hex_color};color:{text_col};"
            f"border:1px solid #333;border-radius:6px;padding:4px 10px;}}"
        )
        self.setText(hex_color)

    def _pick(self):
        init = QtGui.QColor(self.text())
        col = QtWidgets.QColorDialog.getColor(init, self, "Pick Color")
        if col.isValid():
            hex_color = col.name()
            self._apply_style(hex_color)
            self.colorChanged.emit(hex_color)

    def value(self) -> str:
        return self.text()


# =============================================================================
#  Dialog: CustomThemeDialog（編輯完整 custom 色表）
# =============================================================================
class CustomThemeDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, base: Dict[str, Any] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Customize Theme Colors")
        self.setModal(True)
        self.resize(560, 420)

        base = dict(DEFAULT_CUSTOM_THEME) | dict(base or {})

        grid = QtWidgets.QGridLayout(self)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self._btns: Dict[str, ColorButton] = {}
        for i, key in enumerate(CUSTOM_KEYS):
            r = i // 2
            c = (i % 2) * 2
            label = QtWidgets.QLabel(key.replace("_", " ").title() + ":")
            btn = ColorButton(base.get(key, DEFAULT_CUSTOM_THEME[key]))
            self._btns[key] = btn
            grid.addWidget(label, r, c, 1, 1)
            grid.addWidget(btn,   r, c + 1, 1, 1)

        grid.setRowStretch((len(CUSTOM_KEYS) // 2) + 1, 1)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        grid.addWidget(btns, grid.rowCount(), 0, 1, 4)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

    def result_theme(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, btn in self._btns.items():
            out[k] = btn.value()
        return out


# =============================================================================
#  Dialog: PreferencesDialog（整體偏好設定）
# =============================================================================
class PreferencesDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget, cfg: Dict[str, Any], midi_outputs: List[str]):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(560, 520)

        self._cfg = dict(cfg)
        self._midi_outputs = list(midi_outputs or [])

        tc = dict(cfg.get("theme_custom") or {})
        if "window" not in tc and "accent" in tc:
            tmp = dict(DEFAULT_CUSTOM_THEME)
            tmp["highlight"] = tc.get("accent")
            tc = tmp
        self._custom_theme = tc or dict(DEFAULT_CUSTOM_THEME)

        root = QtWidgets.QVBoxLayout(self)

        # ============ Theme ============
        g_theme = QtWidgets.QGroupBox("UI Theme")
        f_theme = QtWidgets.QFormLayout(g_theme)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(THEME_CHOICES)
        self.theme_combo.setCurrentText(str(cfg.get("theme", "dark")))
        self.theme_combo.currentTextChanged.connect(self._update_theme_controls)
        f_theme.addRow("Theme:", self.theme_combo)

        self.btn_customize = QtWidgets.QPushButton("Customize colors…")
        self.btn_customize.clicked.connect(self._open_custom_editor)
        f_theme.addRow("", self.btn_customize)

        root.addWidget(g_theme)

        # ============ Defaults ============
        g_def = QtWidgets.QGroupBox("Defaults")
        f_def = QtWidgets.QFormLayout(g_def)

        self.bpm = QtWidgets.QDoubleSpinBox()
        self.bpm.setRange(20, 300)
        self.bpm.setDecimals(2)
        self.bpm.setValue(float(cfg.get("default_bpm", 120.0)))
        f_def.addRow("Default BPM:", self.bpm)

        self.tracks = QtWidgets.QSpinBox()
        self.tracks.setRange(1, 16)
        self.tracks.setValue(int(cfg.get("default_tracks", 1)))
        f_def.addRow("Default Tracks:", self.tracks)

        root.addWidget(g_def)

        # ============ Output ============
        g_out = QtWidgets.QGroupBox("Default Output")
        f_out = QtWidgets.QFormLayout(g_out)

        self.output_mode = QtWidgets.QComboBox()
        self.output_mode.addItems(["system", "fluidsynth"])
        self.output_mode.setCurrentText(str(cfg.get("output_device", "system")))
        self.output_mode.currentTextChanged.connect(self._update_output_mode_enabled)
        f_out.addRow("Mode:", self.output_mode)

        # system: MIDI port list
        self.midi_combo = QtWidgets.QComboBox()
        if self._midi_outputs:
            self.midi_combo.addItems(self._midi_outputs)
            pref_name = cfg.get("preferred_midi_output", "")
            if pref_name in self._midi_outputs:
                self.midi_combo.setCurrentText(pref_name)
        else:
            self.midi_combo.addItem("(no MIDI outputs found)")
            self.midi_combo.setEnabled(False)
        f_out.addRow("System MIDI:", self.midi_combo)

        # fluidsynth: sf2 picker（永遠可填，先存路徑）
        w_sf = QtWidgets.QWidget()
        h_sf = QtWidgets.QHBoxLayout(w_sf); h_sf.setContentsMargins(0, 0, 0, 0)
        self.sf_path = QtWidgets.QLineEdit(str(cfg.get("soundfont_path", "")))
        self.sf_browse = QtWidgets.QToolButton(); self.sf_browse.setText("Browse")
        self.sf_browse.clicked.connect(self._browse_sf2)
        h_sf.addWidget(self.sf_path, 1); h_sf.addWidget(self.sf_browse, 0)
        f_out.addRow("SoundFont (.sf2):", w_sf)

        root.addWidget(g_out)

        # ============ Storage ============
        g_st = QtWidgets.QGroupBox("Storage / Recovery")
        h_st = QtWidgets.QHBoxLayout(g_st)
        self.auto_backup = QtWidgets.QCheckBox("Enable auto backup")
        self.auto_backup.setChecked(bool(cfg.get("auto_backup", False)))
        self.auto_restore = QtWidgets.QCheckBox("Enable auto restore")
        self.auto_restore.setChecked(bool(cfg.get("auto_restore", False)))
        h_st.addWidget(self.auto_backup)
        h_st.addWidget(self.auto_restore)
        h_st.addStretch(1)
        root.addWidget(g_st)

        # Buttons
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

        self._update_theme_controls(self.theme_combo.currentText())
        self._update_output_mode_enabled(self.output_mode.currentText())

    # ---------- Theme helpers ----------
    def _open_custom_editor(self):
        dlg = CustomThemeDialog(self, self._custom_theme)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._custom_theme = dlg.result_theme()

    def _update_theme_controls(self, mode: str):
        self.btn_customize.setEnabled(mode == "custom")

    # ---------- Output helpers ----------
    def _browse_sf2(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select SoundFont", "", "SoundFont (*.sf2)")
        if path:
            self.sf_path.setText(path)

    def _update_output_mode_enabled(self, mode: str):
        is_system = (mode == "system")
        self.midi_combo.setEnabled(is_system and bool(self._midi_outputs))
        self.sf_path.setEnabled(True)
        self.sf_browse.setEnabled(True)

    # ---------- Output ----------
    def result_config(self) -> Dict[str, Any]:
        out = dict(self._cfg)
        out["theme"] = self.theme_combo.currentText()
        if out["theme"] == "custom":
            out["theme_custom"] = dict(self._custom_theme)
        else:
            out["theme_custom"] = dict(self._custom_theme or DEFAULT_CUSTOM_THEME)
        out["default_bpm"] = float(self.bpm.value())
        out["default_tracks"] = int(self.tracks.value())
        out["output_device"] = self.output_mode.currentText()
        out["preferred_midi_output"] = self.midi_combo.currentText() if self._midi_outputs else ""
        out["soundfont_path"] = self.sf_path.text().strip()
        out["auto_backup"] = bool(self.auto_backup.isChecked())
        out["auto_restore"] = bool(self.auto_restore.isChecked())
        return out
