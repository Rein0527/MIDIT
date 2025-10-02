# modules/core/models.py
# =============================================================================
#  Imports / Typing / Constants
# =============================================================================
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, List, Optional
import uuid
import bisect

# -- Optional deps（缺少時以 None 代替，讓 core 不依賴外部 I/O 層） --------------------
try:
    import mido as _mido
    from mido import Message as _Message, MetaMessage as _MetaMessage
except Exception:
    _mido = None
    _Message = None
    _MetaMessage = None

# 對外輸出（供其它模組統一引用）
mido = _mido
Message = _Message
MetaMessage = _MetaMessage

PPQ_DEFAULT = 960


# =============================================================================
#  Core Data Classes
# =============================================================================
@dataclass(slots=True)
class Note:
    pitch: int
    vel: int
    start: int
    dur: int
    channel: int = 0

    @property
    def end(self) -> int:
        return self.start + self.dur

@dataclass(slots=True)
class Track:
    name: str = "Track"
    channel: int = 0
    program: int = 0
    volume: int = 100
    notes: List[Note] = field(default_factory=list)
    mute: bool = False
    solo: bool = False
    locked: bool = False
    color: Any = None
    view_active: bool = False
    collapsed: bool = False
    frozen: bool = False 
    freeze_path: str = ""
    _freeze_prev_mute: bool | None = None
    group_id: Optional[str] = None

@dataclass(slots=True)
class TrackGroup:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = "Group"
    color: Any = None
    mute: bool = False
    solo: bool = False
    collapsed: bool = False
    locked: bool = False
    frozen: bool = False

@dataclass(slots=True)
class TempoChange:
    tick: int
    bpm: float

@dataclass(slots=True)
class TimeSigChange:
    tick: int
    num: int
    den: int

@dataclass(slots=True)
class Marker:
    tick: int
    name: str
    color: Any = None

@dataclass(slots=True)
class Project:
    ppq: int = PPQ_DEFAULT
    tempo_map: List[TempoChange] = field(default_factory=lambda: [TempoChange(0, 120.0)])
    timesigs: List[TimeSigChange] = field(default_factory=lambda: [TimeSigChange(0, 4, 4)])
    tracks: List[Track] = field(default_factory=list)
    groups: List[TrackGroup] = field(default_factory=list)
    markers: List[Marker] = field(default_factory=list)
    master_volume: int = 100
    dirty: bool = False
    rev: int = 0

    _max_tick_cached: Optional[int] = field(default=None, init=False, repr=False)
    _ti_ticks: Optional[List[int]] = field(default=None, init=False, repr=False)
    _ti_secs: Optional[List[float]] = field(default=None, init=False, repr=False)
    _ti_tm: Optional[List[TempoChange]] = field(default=None, init=False, repr=False)
    _ti_ppq: Optional[int] = field(default=None, init=False, repr=False)

    def on_notes_changed(self) -> None:
        try:
            for tr in self.tracks:
                if tr.notes:
                    tr.notes.sort(key=lambda n: n.start)
        except Exception:
            pass
        self.recompute_max_tick()
        self.mark_dirty()

    def on_tempo_changed(self) -> None:
        self._build_time_index()

    def recompute_max_tick(self) -> int:
        mt = 0
        try:
            for tr in self.tracks:
                for n in tr.notes:
                    if n.end > mt:
                        mt = n.end
        except Exception:
            mt = 0
        self._max_tick_cached = mt
        return mt

    def max_tick(self) -> int:
        if self._max_tick_cached is None:
            return self.recompute_max_tick()
        return int(self._max_tick_cached)

    def _effective_ppq(self) -> int:
        try:
            v = int(self.ppq)
            return v if v > 0 else PPQ_DEFAULT
        except Exception:
            return PPQ_DEFAULT

    def _build_time_index(self) -> None:
        tm = sorted(self.tempo_map or [TempoChange(0, 120.0)], key=lambda t: int(getattr(t, "tick", 0)))
        ppq = self._effective_ppq()

        ticks = [int(getattr(t, "tick", 0)) for t in tm]
        if not ticks or ticks[0] != 0:
            tm = [TempoChange(0, float(tm[0].bpm if tm else 120.0))] + tm
            ticks = [0] + ticks

        secs = [0.0] * len(ticks)
        for i in range(1, len(ticks)):
            t0 = ticks[i - 1]
            t1 = ticks[i]
            bpm = float(getattr(tm[i - 1], "bpm", 120.0)) or 120.0
            if bpm <= 0:
                bpm = 120.0
            sec_per_tick = (60.0 / bpm) / ppq
            secs[i] = secs[i - 1] + (t1 - t0) * sec_per_tick

        self._ti_ticks = ticks
        self._ti_secs = secs
        self._ti_tm = tm
        self._ti_ppq = ppq

    def _ensure_time_index(self) -> None:
        if self._ti_ticks is None or self._ti_secs is None or self._ti_tm is None or self._ti_ppq is None:
            self._build_time_index()

    def bpm_at(self, tick: int) -> float:
        if not self.tempo_map:
            return 120.0
        self._ensure_time_index()
        i = bisect.bisect_right(self._ti_ticks, int(tick)) - 1
        i = 0 if i < 0 else i
        bpm = float(getattr(self._ti_tm[i], "bpm", 120.0)) if self._ti_tm else 120.0
        return 120.0 if bpm <= 0 else bpm

    def _seconds_at_tick(self, tick: int) -> float:
        self._ensure_time_index()
        i = bisect.bisect_right(self._ti_ticks, int(tick)) - 1
        if i < 0:
            i = 0
        base_sec = self._ti_secs[i]
        base_tick = self._ti_ticks[i]
        bpm = float(getattr(self._ti_tm[i], "bpm", 120.0))
        if bpm <= 0:
            bpm = 120.0
        sec_per_tick = (60.0 / bpm) / self._ti_ppq
        return base_sec + max(0, tick - base_tick) * sec_per_tick

    def seconds_between(self, t0: int, t1: int) -> float:
        if t0 == t1:
            return 0.0
        if t1 < t0:
            t0, t1 = t1, t0
        try:
            return self._seconds_at_tick(t1) - self._seconds_at_tick(t0)
        except Exception:
            ppq = self._effective_ppq()
            return ((t1 - t0) / ppq) * (60.0 / 120.0)

    def tick_at_seconds(self, sec: float, start_tick: int = 0) -> int:
        if sec <= 0.0:
            return max(0, int(start_tick) if start_tick >= 0 else 0)

        self._ensure_time_index()
        i = bisect.bisect_right(self._ti_secs, float(sec)) - 1
        if i < 0:
            i = 0
        base_sec = self._ti_secs[i]
        base_tick = self._ti_ticks[i]
        bpm = float(getattr(self._ti_tm[i], "bpm", 120.0))
        if bpm <= 0:
            bpm = 120.0
        sec_per_tick = (60.0 / bpm) / self._ti_ppq
        est = base_tick + int(round((sec - base_sec) / max(sec_per_tick, 1e-12)))

        mt = self.max_tick()
        if est > mt:
            est = mt
        if est < 0:
            est = 0
        return est

    @property
    def timesig_map(self):
        return self.timesigs

    @timesig_map.setter
    def timesig_map(self, value):
        self.timesigs = value or []

    def mark_dirty(self) -> None:
        self.dirty = True
        self.rev += 1

    def clear_dirty(self) -> None:
        self.dirty = False