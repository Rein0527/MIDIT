# modules/ui/theme.py
# =============================================================================
#  Imports
# =============================================================================
from PySide6 import QtCore, QtGui, QtWidgets


# =============================================================================
#  SECTION A: 小工具
# =============================================================================
def _C(x, fallback="#5aa0ff"):
    try:
        if isinstance(x, QtGui.QColor):
            return x
        if isinstance(x, str):
            c = QtGui.QColor(x)
            if c.isValid():
                return c
        if isinstance(x, (tuple, list)) and len(x) == 3:
            r, g, b = map(int, x)
            return QtGui.QColor(r, g, b)
    except Exception:
        pass
    return QtGui.QColor(fallback)

def _hex(c: QtGui.QColor) -> str:
    return c.name()

def _blend(a: QtGui.QColor, b: QtGui.QColor, t: float) -> QtGui.QColor:
    return QtGui.QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
    )

# =============================================================================
#  SECTION B: Palette / Stylesheet 生成
# =============================================================================
def _mk_palette(spec: dict) -> QtGui.QPalette:
    def g(k, d): return _C(spec.get(k, d))
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window,        g("window",        "#2b2d30"))
    pal.setColor(QtGui.QPalette.WindowText,    g("window_text",   "#ebebee"))
    pal.setColor(QtGui.QPalette.Base,          g("base",          "#212427"))
    pal.setColor(QtGui.QPalette.AlternateBase, g("alt_base",      "#303236"))
    pal.setColor(QtGui.QPalette.Text,          g("text",          "#ebebee"))
    pal.setColor(QtGui.QPalette.Button,        g("button",        "#3a3d42"))
    pal.setColor(QtGui.QPalette.ButtonText,    g("button_text",   "#f0f0f4"))
    pal.setColor(QtGui.QPalette.BrightText,    g("bright_text",   "#ff5f5f"))
    pal.setColor(QtGui.QPalette.Highlight,     g("highlight",     "#5aa0ff"))
    pal.setColor(QtGui.QPalette.HighlightedText, g("highlight_text", "#101114"))
    pal.setColor(QtGui.QPalette.ToolTipBase,   g("tooltip_base",  "#303236"))
    pal.setColor(QtGui.QPalette.ToolTipText,   g("tooltip_text",  "#f0f0f4"))
    return pal

def _stylesheet_from_spec(spec: dict) -> str:
    window    = _hex(_C(spec.get("window", "#2b2d30")))
    base      = _hex(_C(spec.get("base", "#212427")))
    alt_base  = _hex(_C(spec.get("alt_base", "#303236")))
    button    = _hex(_C(spec.get("button", "#3a3d42")))
    text      = _hex(_C(spec.get("text", "#ebebee")))
    highlight = _hex(_C(spec.get("highlight", "#5aa0ff")))
    hl_text   = _hex(_C(spec.get("highlight_text", "#101114")))
    border    = _hex(_blend(_C(base), _C(text), 0.35))

    return f"""
    QToolBar {{
        spacing: 6px; background: {window}; border: none;
    }}
    QToolButton, QPushButton {{
        padding: 2px 10px; border: 1px solid {border}; border-radius: 6px;
        background: {button}; color: {text};
    }}
    QToolButton:hover, QPushButton:hover {{ background: {alt_base}; }}
    QToolButton:checked {{ background: {highlight}; border-color: {highlight}; color: {hl_text}; }}

    QLabel {{ color: {text}; }}

    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
        background: {base}; color: {text}; border: 1px solid {border}; border-radius: 6px; padding: 2px 8px;
        selection-background-color: {highlight}; selection-color: {hl_text};
    }}

    QScrollArea {{ background: {window}; border: none; }}
    QFrame[frameShape="5"] {{ border: 1px solid {border}; border-radius: 8px; background: {alt_base}; }}

    QFrame#TrackItem[active="true"] {{ border: 2px solid {highlight}; background: {alt_base}; }}
    QFrame#TrackItem[active="true"] QLabel {{ color: {text}; }}

    QToolBar QToolButton, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height: 14px; }}
    """

# =============================================================================
#  SECTION C: 預設主題色票
# =============================================================================
THEME_PRESETS: dict[str, dict] = {
    # 深色
    "dark": {
        "palette": {
            "window":"#2b2d30","window_text":"#ebebee",
            "base":"#212427","alt_base":"#303236","text":"#ebebee",
            "button":"#3a3d42","button_text":"#f0f0f4",
            "bright_text":"#ff5f5f",
            "highlight":"#5aa0ff","highlight_text":"#101114",
            "tooltip_base":"#303236","tooltip_text":"#f0f0f4",
        },
    },

    # 明亮、偏冷的紙白系
    "light": {
        "palette": {
            "window":"#f7f8fb","window_text":"#1f2430",
            "base":"#ffffff","alt_base":"#eef2f8","text":"#1f2430",
            "button":"#e9edf5","button_text":"#1f2430",
            "bright_text":"#d92d20",
            "highlight":"#2563eb","highlight_text":"#ffffff",
            "tooltip_base":"#ffffff","tooltip_text":"#1f2430",
        },
    },

    # 灰藍岩（更暗、更冷）
    "slate": {
        "palette": {
            "window":"#0f172a","window_text":"#e5e7eb",
            "base":"#0b1220","alt_base":"#111827","text":"#e5e7eb",
            "button":"#1f2937","button_text":"#e5e7eb",
            "bright_text":"#ff6b6b",
            "highlight":"#60a5fa","highlight_text":"#0a0a0a",
            "tooltip_base":"#111827","tooltip_text":"#f3f4f6",
        },
    },

    # 靛紫夜（偏紫藍）
    "indigo": {
        "palette": {
            "window":"#1e1b4b","window_text":"#eae8ff",
            "base":"#151238","alt_base":"#262159","text":"#eae8ff",
            "button":"#2f2a6a","button_text":"#f1f0ff",
            "bright_text":"#ff6b8b",
            "highlight":"#6366f1","highlight_text":"#0b0b14",
            "tooltip_base":"#262159","tooltip_text":"#f1f0ff",
        },
    },

    # 翡翠森（深綠系）
    "emerald": {
        "palette": {
            "window":"#062e24","window_text":"#d1fae5",
            "base":"#05251d","alt_base":"#0a3a2d","text":"#d1fae5",
            "button":"#124737","button_text":"#dcfce7",
            "bright_text":"#ff6a6a",
            "highlight":"#34d399","highlight_text":"#0a0f0d",
            "tooltip_base":"#0a3a2d","tooltip_text":"#dcfce7",
        },
    },

    # 琥珀砂（暖棕系）
    "amber": {
        "palette": {
            "window":"#2c2412","window_text":"#fef3c7",
            "base":"#1e180b","alt_base":"#342a14","text":"#fef3c7",
            "button":"#3b2f14","button_text":"#fff7d6",
            "bright_text":"#ff6a6a",
            "highlight":"#f59e0b","highlight_text":"#0e0b06",
            "tooltip_base":"#342a14","tooltip_text":"#fff7d6",
        },
    },

    # 玫瑰暮（紅粉系）
    "rose": {
        "palette": {
            "window":"#2a0f18","window_text":"#ffe4e6",
            "base":"#1b0a11","alt_base":"#3a1624","text":"#ffe4e6",
            "button":"#4a1d2c","button_text":"#ffe9ee",
            "bright_text":"#ff7d7d",
            "highlight":"#fb7185","highlight_text":"#170b0f",
            "tooltip_base":"#3a1624","tooltip_text":"#ffe9ee",
        },
    },
}

# =============================================================================
#  SECTION D: 主入口 API
# =============================================================================
def apply_theme(window: QtWidgets.QMainWindow, mode: str = "dark", *, custom: dict | None = None, accent=None) -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return

    app.setStyle("Fusion")

    if mode == "custom":
        spec = dict(custom or {})
        pal = _mk_palette(spec)
        app.setPalette(pal); window.setPalette(pal)
        window.setStyleSheet(_stylesheet_from_spec(spec))
        return

    preset = THEME_PRESETS.get(mode, THEME_PRESETS["dark"])

    if mode == "light" and preset.get("palette") == "qt":
        pal = QtWidgets.QApplication.style().standardPalette()
        acc = _C(preset.get("highlight", "#3b82f6"))
        pal.setColor(QtGui.QPalette.Highlight, acc)
        pal.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)
        app.setPalette(pal); window.setPalette(pal)
        window.setStyleSheet(f"""
        QToolButton:checked, QPushButton:checked {{
            background: {acc.name()}; border: 1px solid {acc.name()}; color: white;
        }}
        QProgressBar::chunk {{ background-color: {acc.name()}; }}
        QSlider::groove:horizontal {{ height: 4px; background: {acc.name()}; }}
        QCheckBox::indicator:checked {{ background: {acc.name()}; border: 1px solid {acc.name()}; }}
        """)
        return

    spec = dict(preset.get("palette", {}))
    pal = _mk_palette(spec)
    app.setPalette(pal); window.setPalette(pal)
    window.setStyleSheet(_stylesheet_from_spec(spec))

def apply_dark_theme(window: QtWidgets.QMainWindow) -> None:
    apply_theme(window, "dark")
