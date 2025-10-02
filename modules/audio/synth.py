# modules/audio/synth.py
# =============================================================================
#  Imports / Optional Dependencies / Logger
# =============================================================================
import sys
import logging
from contextlib import suppress
from typing import Optional

try:
    import mido
    from mido import Message
except Exception:
    mido = None
    Message = None

try:
    import fluidsynth
except Exception:
    fluidsynth = None

logger = logging.getLogger(__name__)

def _safe(fn, *a, _label: str = "", **kw):
    try:
        return fn(*a, **kw)
    except Exception as e:
        logger.warning("[synth] %s failed: %s", (_label or fn.__name__), e)
        return None

def _safe_close(obj, method: str):
    with suppress(Exception):
        getattr(obj, method)()

def _pick_driver() -> str:
    return (
        "dsound" if sys.platform.startswith("win")
        else "coreaudio" if sys.platform == "darwin"
        else "alsa"
    )

class SynthOut:
    def __init__(self):
        self.enabled: bool = False
        self.fs: Optional["fluidsynth.Synth"] = None
        self._sfid: Optional[int] = None

    @property
    def is_loaded(self) -> bool:
        return bool(self.fs and self._sfid is not None)

    def _ready(self) -> bool:
        return bool(self.enabled and self.fs)

    def load_sf2(self, path: str, *, gain: float = 0.5):
        if fluidsynth is None:
            raise RuntimeError("fluidsynth is not installed")

        self.unload_sf2()

        fs = fluidsynth.Synth()
        _safe(fs.setting, "synth.gain", gain, _label="fs.setting(gain)")
        _safe(fs.start, _pick_driver(), _label="fs.start")

        sfid = _safe(fs.sfload, path, _label="fs.sfload")
        if sfid is None:
            _safe_close(fs, "delete")
            raise RuntimeError(f"Failed to load SoundFont: {path}")

        for ch in range(16):
            _safe(
                fs.program_select,
                ch & 0x0F, int(sfid), 0, 0,
                _label=f"fs.program_select[ch={ch}]",
            )

        self.fs = fs
        self._sfid = int(sfid)
        self.enabled = True

    def unload_sf2(self):
        fs = self.fs
        if not fs:
            self.enabled = False
            return
        if self._sfid is not None:
            _safe(fs.sfunload, self._sfid, True, _label="fs.sfunload")
            self._sfid = None
        self.enabled = False

    def disable(self):
        self.enabled = False

    def close(self):
        fs = self.fs
        if fs is None:
            return
        with suppress(Exception):
            fs.system_reset()
        with suppress(Exception):
            fs.stop()
        with suppress(Exception):
            fs.delete()
        self.fs = None
        self._sfid = None
        self.enabled = False

    def note_on(self, ch: int, key: int, vel: int):
        if not self._ready():
            return
        _safe(self.fs.noteon, ch & 0x0F, int(key), int(vel), _label="fs.noteon")

    def note_off(self, ch: int, key: int):
        if not self._ready():
            return
        _safe(self.fs.noteoff, ch & 0x0F, int(key), _label="fs.noteoff")

    def cc(self, ch: int, control: int, value: int):
        if not self._ready():
            return
        _safe(self.fs.cc, ch & 0x0F, int(control), int(value), _label="fs.cc")

    def program(self, ch: int, bank: int, program: int):
        if not (self._ready() and self._sfid is not None):
            return
        _safe(
            self.fs.program_select,
            ch & 0x0F, int(self._sfid), int(bank), int(program),
            _label="fs.program_select",
        )

class MidiOut:
    def __init__(self):
        self.enabled: bool = False
        self._port = None

    def _ready(self) -> bool:
        return bool(self.enabled and self._port)

    def outputs(self) -> list[str]:
        if not mido:
            return []
        try:
            return list(mido.get_output_names())
        except Exception:
            return []

    def open_by_name(self, name: str) -> bool:
        if not mido:
            return False
        self.close()
        port = _safe(mido.open_output, name, autoreset=True, _label="mido.open_output")
        if port is None:
            self.enabled = False
            return False
        self._port = port
        self.enabled = True
        return True

    def send(self, msg: "Message") -> None:
        if not (self._ready() and msg is not None):
            return
        with suppress(Exception):
            self._port.send(msg)

    def disable(self):
        self.enabled = False

    def close(self):
        port = self._port
        if port is None:
            return
        _safe_close(port, "reset")
        _safe_close(port, "close")
        self._port = None
        self.enabled = False
