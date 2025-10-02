# modules/utils/config.py
# =============================================================================
#  Imports / Typing / Path
# =============================================================================
from __future__ import annotations
import os
import json
from typing import Any, Dict

# 設定檔路徑：存放在專案的 modules/utils/config.json
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# =============================================================================
#  Constants (單一真源：供其它模組引用)
# =============================================================================
MAX_TRACKS = 16
DEFAULT_BPM = 120.0

# UI 顏色池（TracksPanel / PianoRoll 會用）
COLOR_POOL = [
    (120, 180, 255),
    (140, 255, 180),
    (255, 180, 120),
    (200, 160, 255),
    (255, 140, 200),
]

# General MIDI Program Names（128 筆）
GM_NAMES = [
  "Acoustic Grand","Bright Acoustic","Electric Grand","Honky-tonk","Electric Piano 1","Electric Piano 2","Harpsichord","Clavinet",
  "Celesta","Glockenspiel","Music Box","Vibraphone","Marimba","Xylophone","Tubular Bells","Dulcimer",
  "Drawbar Organ","Percussive Organ","Rock Organ","Church Organ","Reed Organ","Accordion","Harmonica","Tango Accordion",
  "Nylon Guitar","Steel Guitar","Jazz Guitar","Clean Guitar","Muted Guitar","Overdrive Guitar","Distortion Guitar","Guitar Harmonics",
  "Acoustic Bass","Fingered Bass","Picked Bass","Fretless Bass","Slap Bass 1","Slap Bass 2","Synth Bass 1","Synth Bass 2",
  "Violin","Viola","Cello","Contrabass","Tremolo Strings","Pizzicato","Orchestral Harp","Timpani",
  "String Ensemble 1","String Ensemble 2","SynthStrings 1","SynthStrings 2","Choir Aahs","Voice Oohs","Synth Voice","Orch Hit",
  "Trumpet","Trombone","Tuba","Muted Trumpet","French Horn","Brass Section","Synth Brass 1","Synth Brass 2",
  "Soprano Sax","Alto Sax","Tenor Sax","Baritone Sax","Oboe","English Horn","Bassoon","Clarinet",
  "Piccolo","Flute","Recorder","Pan Flute","Blown Bottle","Shakuhachi","Whistle","Ocarina",
  "Square Lead","Saw Lead","Calliope Lead","Chiff Lead","Charang","Voice Lead","Fifths Lead","Bass+Lead",
  "New Age Pad","Warm Pad","Polysynth Pad","Choir Pad","Bowed Pad","Metallic Pad","Halo Pad","Sweep Pad",
  "Rain","Soundtrack","Crystal","Atmosphere","Brightness","Goblins","Echoes","Sci-Fi",
  "Sitar","Banjo","Shamisen","Koto","Kalimba","Bagpipe","Fiddle","Shanai",
  "Tinkle Bell","Agogo","Steel Drums","Woodblock","Taiko Drum","Melodic Tom","Synth Drum","Reverse Cymbal",
  "Guitar Fret Noise","Breath Noise","Seashore","Bird Tweet","Telephone","Helicopter","Applause","Gunshot"
]
assert len(GM_NAMES) == 128, f"GM_NAMES length must be 128, got {len(GM_NAMES)}"

# 播放延遲預設檔
LATENCY_PRESETS: Dict[str, Dict[str, Any]] = {
    # 低CPU / 高延遲（適合筆電省電）
    "low":    {"WAKE_HZ": 200, "SAFETY_BACKTRACK_SEC": 0.004, "UI_THROTTLE_MS": 30},
    # 預設（均衡）
    "medium": {"WAKE_HZ": 400, "SAFETY_BACKTRACK_SEC": 0.006, "UI_THROTTLE_MS": 20},
    # 高CPU / 低延遲（適合即時鍵盤演奏）
    "high":   {"WAKE_HZ": 800, "SAFETY_BACKTRACK_SEC": 0.008, "UI_THROTTLE_MS": 12},
}
DEFAULT_LATENCY_PROFILE = "high"

# =============================================================================
#  Defaults（組態預設值）
# =============================================================================
DEFAULTS: Dict[str, Any] = {
    "theme": "dark",
    "theme_custom": {
        "accent": "#5aa0ff"
    },

    "default_bpm": DEFAULT_BPM,
    "default_tracks": 1,

    # 輸出模式
    "output_device": "system",
    "preferred_midi_output": "",
    "soundfont_path": "",

    # 備份/回復
    "auto_backup": True,
    "auto_restore": False,

    # 播放延遲參數
    "latency_profile": DEFAULT_LATENCY_PROFILE,

    # 快捷鍵
    "shortcuts": {},

    # 檢視設定
    "view": {
        "auto_scroll": True,
        "show_note_labels": False,
        "snap": "1/16",
    },

    # 開發者模式
    "developer": {
        "debug_log": False,
        "log_to_file": True,
    }
}

# =============================================================================
#  Internal Helpers
# =============================================================================
def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """遞迴合併 dict（對齊結構，不做型別驗證）。"""
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore
        else:
            out[k] = v
    return out

# =============================================================================
#  Public API
# =============================================================================
def load_config() -> Dict[str, Any]:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)

    if not os.path.isfile(CONFIG_PATH):
        save_config(dict(DEFAULTS))
        return dict(DEFAULTS)

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user = json.load(f) or {}
    except Exception:
        user = {}

    merged = _deep_merge(DEFAULTS, user)

    if merged.get("latency_profile") not in LATENCY_PRESETS:
        merged["latency_profile"] = DEFAULT_LATENCY_PROFILE
    if not isinstance(merged.get("default_bpm", DEFAULT_BPM), (int, float)):
        merged["default_bpm"] = DEFAULT_BPM

    if merged != user:
        try:
            save_config(merged)
        except Exception:
            pass

    return merged

def save_config(cfg: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_latency_params(profile: str | None) -> Dict[str, Any]:
    if not profile:
        profile = DEFAULT_LATENCY_PROFILE
    return LATENCY_PRESETS.get(profile, LATENCY_PRESETS[DEFAULT_LATENCY_PROFILE])
