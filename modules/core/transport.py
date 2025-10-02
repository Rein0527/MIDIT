# modules/core/transport.py
# =============================================================================
#  Imports / Typing
# =============================================================================
from __future__ import annotations
import sys, threading
from time import perf_counter, sleep
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple, Iterable

from PySide6 import QtCore
from ..core.models import mido, Message
from ..utils.config import LATENCY_PRESETS, DEFAULT_LATENCY_PROFILE

# =============================================================================
#  Internal Event Model
# =============================================================================
@dataclass
class _Evt:
    tsec: float
    pitch: int
    vel: int
    chan: int
    on: bool
    track_index: int


# =============================================================================
#  Transport
#  - 高精度播放排程（依 latency_profile 可調醒頻）
#  - 事件索引：僅掃描時間窗附近的音符
#  - UI 節流：tickChanged 預設 20ms 發射一次
#  - reanchor：BPM / tempo_map 變更時重對齊秒->tick
# =============================================================================
class Transport(QtCore.QObject):
    playingChanged = QtCore.Signal(bool)
    tickChanged = QtCore.Signal(int)

    def __init__(self, project, synth, midi, *, latency_profile: str = DEFAULT_LATENCY_PROFILE):
        super().__init__()
        self.p = project
        self.synth = synth
        self.midi = midi
        self.pos: int = 0
        self.loop_on: bool = False
        self.loop_a: int = 0
        self.loop_b: int = self.p.ppq * 16
        self.playing: bool = False
        self.speed: float = 1.0
        self._thr: Optional[threading.Thread] = None
        self._runner_lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._idle_evt = threading.Event()
        self._idle_evt.set()
        self._proj_lock = threading.RLock()
        self._anchor_wall: float = 0.0
        self._anchor_song_sec: float = 0.0
        self._latency_profile: Optional[str] = None
        self.WAKE_HZ: int = 400
        self.SAFETY_BACKTRACK_SEC: float = 0.006
        self.UI_THROTTLE_MS: int = 20
        self._ui_emit_interval: float = 0.020
        self._last_ui_emit_wall: float = 0.0
        self.set_latency_profile(latency_profile)
        self._stats_on = 0
        self._stats_off = 0
        self._note_index: List[Dict[str, List]] = []
        self._note_index_hash: Tuple[int, int] = (-1, -1)

    def set_latency_profile(self, profile: str) -> None:
        cfg = LATENCY_PRESETS.get(profile) or LATENCY_PRESETS.get(DEFAULT_LATENCY_PROFILE)
        self._latency_profile = profile
        self.WAKE_HZ = int(cfg["WAKE_HZ"])
        self.SAFETY_BACKTRACK_SEC = float(cfg["SAFETY_BACKTRACK_SEC"])
        self.UI_THROTTLE_MS = int(cfg["UI_THROTTLE_MS"])
        self._ui_emit_interval = max(0.001, self.UI_THROTTLE_MS / 1000.0)

    def reanchor(self):
        with self._proj_lock:
            self._anchor_song_sec = self._sec_at_tick(self.pos)
        self._anchor_wall = perf_counter()

    def set_speed(self, s: float):
        if s <= 0:
            s = 0.0001
        with self._proj_lock:
            cur_song_sec = self._sec_at_tick(self.pos)
        self._anchor_song_sec = cur_song_sec
        self._anchor_wall = perf_counter()
        self.speed = float(s)

    def set_pos(self, tick: int):
        with self._proj_lock:
            self.pos = max(0, int(tick))
            self._anchor_song_sec = self._sec_at_tick(self.pos)
        self._anchor_wall = perf_counter()
        self._emit_tick_throttled(self.pos)

    def start(self):
        if self.playing:
            return
        self.playing = True
        self.playingChanged.emit(True)

        with self._proj_lock:
            self._anchor_song_sec = self._sec_at_tick(self.pos)
        self._anchor_wall = perf_counter()

        with self._runner_lock:
            if self._thr is not None and self._thr.is_alive():
                return
            self._stop_flag.clear()
            self._thr = threading.Thread(target=self._runner, name="TransportRunner", daemon=True)
            self._thr.start()

    def pause(self):
        if not self.playing:
            return
        self.playing = False
        self.playingChanged.emit(False)
        self._panic()

    def stop(self):
        self.playing = False
        self.playingChanged.emit(False)
        self._panic()
        with self._runner_lock:
            if self._thr and self._thr.is_alive():
                self._stop_flag.set()
                try:
                    self._thr.join(timeout=1.0)
                except Exception:
                    pass
                self._thr = None
            else:
                self._stop_flag.set()
        self.set_pos(0)

    def wait_idle(self, timeout: float = 0.8) -> bool:
        return self._idle_evt.wait(timeout=timeout)

    def bind_project(self, project) -> None:
        with self._proj_lock:
            self.p = project
            self.loop_a = 0
            self.loop_b = max(self.p.ppq * 16, getattr(self, "loop_b", self.p.ppq * 16))
            self.pos = 0
            self._anchor_song_sec = self._sec_at_tick(self.pos)
            self._anchor_wall = perf_counter()
            self._rebuild_note_index_locked()
        self._emit_tick_throttled(self.pos)

    def panic(self):
        self._panic()

    def _runner(self):
        if sys.platform.startswith("win"):
            try:
                import ctypes, atexit
                ctypes.windll.winmm.timeBeginPeriod(1)
                atexit.register(lambda: ctypes.windll.winmm.timeEndPeriod(1))
            except Exception:
                pass

        last_win_end = perf_counter()

        while not self._stop_flag.is_set():
            if not self.playing:
                self._idle_evt.set()
                sleep(0.01)
                # 維持錨點新鮮
                with self._proj_lock:
                    self._anchor_song_sec = self._sec_at_tick(self.pos)
                self._anchor_wall = perf_counter()
                last_win_end = self._anchor_wall
                continue

            self._idle_evt.clear()
            now = perf_counter()

            # 牆鐘 → 歌曲秒數
            now_song_sec = self._anchor_song_sec + (now - self._anchor_wall) * self.speed

            # 反推 tick（tempo map 友好）
            with self._proj_lock:
                new_pos = self._tick_at_sec(now_song_sec)
                if self.loop_on and new_pos >= self.loop_b:
                    over = new_pos - self.loop_b
                    new_pos = self.loop_a + over
                    self._anchor_song_sec = self._sec_at_tick(new_pos)
                    self._anchor_wall = now
                self.pos = new_pos
            self._emit_tick_throttled(self.pos)

            # 當輪「牆鐘」窗口
            WAKE = 1.0 / max(1, self.WAKE_HZ)
            win_start = last_win_end - float(self.SAFETY_BACKTRACK_SEC)
            win_end   = now + (WAKE * 0.5)

            # 轉成歌曲秒數窗口
            song_start = self._anchor_song_sec + (win_start - self._anchor_wall) * self.speed
            song_end   = self._anchor_song_sec + (win_end   - self._anchor_wall) * self.speed
            if song_end < song_start:
                song_start, song_end = song_end, song_start

            # 對 Project 做快照取事件（使用索引）
            with self._proj_lock:
                self._ensure_note_index_locked()
                try:
                    events = list(self._gather_events_in_sec_window(song_start, song_end))
                except Exception:
                    events = []

            if events:
                events.sort(key=lambda e: e.tsec)
                for ev in events:
                    try:
                        tfire_wall = self._anchor_wall + (ev.tsec - self._anchor_song_sec) / max(self.speed, 1e-6)
                        # 精準等待：遠→近→觸發
                        lead = 0.0003  # 0.3 ms
                        while self.playing:
                            now2 = perf_counter()
                            dt = tfire_wall - now2
                            if dt <= 0:
                                break
                            if dt > 0.001:
                                sleep(0.0005)
                            elif dt > lead:
                                sleep(dt - lead)
                            else:
                                break
                        if not self.playing or self._stop_flag.is_set():
                            break
                        self._emit(ev)
                    except Exception:
                        pass

            last_win_end = win_end
            self._idle_evt.set()
            sleep(WAKE)

        # 離開前標記 idle
        self._idle_evt.set()

    def _emit_tick_throttled(self, tick: int):
        now = perf_counter()
        if now - self._last_ui_emit_wall >= self._ui_emit_interval:
            self._last_ui_emit_wall = now
            self.tickChanged.emit(int(tick))

    def _emit(self, ev: _Evt):
        with self._proj_lock:
            tracks = getattr(self.p, "tracks", [])
            if not (0 <= ev.track_index < len(tracks)):
                return
            tr = tracks[ev.track_index]
            solo_indices = [i for i, t in enumerate(tracks) if getattr(t, "solo", False)]
            muted = getattr(tr, "mute", False)
        if solo_indices and ev.track_index not in solo_indices:
            return
        if muted:
            return

        if ev.on:
            try:
                tr = self.p.tracks[ev.track_index]
                track_vol = getattr(tr, "volume", 100)
            except Exception:
                track_vol = 100

            master_vol = int(getattr(self.p, "master_volume", 100))
            scale = max(0.0, min(1.0, (track_vol / 100.0) * (master_vol / 100.0)))
            final_vel = max(1, min(127, int(round(ev.vel * scale))))

            if getattr(self.synth, "enabled", False):
                try: self.synth.note_on(ev.chan, ev.pitch, final_vel)
                except Exception: pass
            if getattr(self.midi, "enabled", False) and Message:
                try: self.midi.send(Message('note_on', note=ev.pitch, velocity=final_vel, channel=ev.chan))
                except Exception: pass
        else:
            if getattr(self.synth, "enabled", False):
                try: self.synth.note_off(ev.chan, ev.pitch)
                except Exception: pass
            if getattr(self.midi, "enabled", False) and Message:
                try: self.midi.send(Message('note_off', note=ev.pitch, velocity=0, channel=ev.chan))
                except Exception: pass
            self._stats_off += 1

    def _panic(self):
        for ch in range(16):
            if getattr(self.synth, "enabled", False):
                try:
                    self.synth.cc(ch, 64, 0)
                    self.synth.cc(ch, 121, 0)
                    self.synth.cc(ch, 123, 0)
                    self.synth.cc(ch, 120, 0)
                except Exception: pass
            if getattr(self.midi, "enabled", False) and Message:
                try:
                    self.midi.send(Message('control_change', control=64, value=0, channel=ch))
                    self.midi.send(Message('control_change', control=121, value=0, channel=ch))
                    self.midi.send(Message('control_change', control=123, value=0, channel=ch))
                    self.midi.send(Message('control_change', control=120, value=0, channel=ch))
                except Exception: pass
        try:
            fs = getattr(self.synth, "fs", None)
            if fs and hasattr(fs, "system_reset"):
                try: fs.system_reset()
                except Exception: pass
            if fs and hasattr(fs, "panic"):
                try: fs.panic()
                except Exception: pass
        except Exception:
            pass

    def _sec_at_tick(self, tick: int) -> float:
        try:
            return float(self.p.seconds_between(0, max(0, int(tick))))
        except Exception:
            bpm = 120.0
            try: bpm = float(self.p.bpm_at(0))
            except Exception: pass
            ppq = max(1, int(getattr(self.p, "ppq", 480)))
            return max(0.0, tick) * (60.0 / (bpm * ppq))

    def _tick_at_sec(self, target_sec: float) -> int:
        if target_sec <= 0:
            return 0
        try:
            start_guess = getattr(self, "pos", 0)
            return int(self.p.tick_at_seconds(float(target_sec), int(start_guess)))
        except Exception:
            hi = max(self.loop_b, self._max_tick() + max(1, int(getattr(self.p, "ppq", 480))) * 4)
            lo = 0
            for _ in range(40):
                mid = (lo + hi) // 2
                s = self._sec_at_tick(mid)
                if s < target_sec: lo = mid + 1
                else:              hi = mid - 1
            lo_s = self._sec_at_tick(lo)
            hi_s = self._sec_at_tick(hi)
            return int(lo if abs(lo_s - target_sec) <= abs(hi_s - target_sec) else hi)

    def _max_tick(self) -> int:
        try:
            return int(self.p.max_tick())
        except Exception:
            mt = 0
            for tr in getattr(self.p, "tracks", []):
                for n in getattr(tr, "notes", []):
                    end = getattr(n, "end", getattr(n, "start", 0) + getattr(n, "dur", 0))
                    if end > mt: mt = end
            return mt

    def _calc_notes_hash_locked(self) -> Tuple[int, int]:
        tracks = getattr(self.p, "tracks", [])
        total = sum(len(getattr(tr, "notes", [])) for tr in tracks)
        return (len(tracks), total)

    def _rebuild_note_index_locked(self) -> None:
        self._note_index = []
        tracks = list(getattr(self.p, "tracks", []))
        for ti, tr in enumerate(tracks):
            notes = list(getattr(tr, "notes", []))
            items = [(int(n.start), int(getattr(n, "end", n.start + n.dur)),
                      int(n.pitch), int(n.vel), int(tr.channel), ti)
                     for n in notes]
            items.sort(key=lambda x: x[0])
            ends = [it[1] for it in items]
            self._note_index.append({"ends": ends, "items": items})
        self._note_index_hash = self._calc_notes_hash_locked()

    def _ensure_note_index_locked(self) -> None:
        h = self._calc_notes_hash_locked()
        if h != self._note_index_hash or not self._note_index:
            self._rebuild_note_index_locked()

    def _gather_events_in_sec_window(self, sec_a: float, sec_b: float) -> Iterable[_Evt]:
        if sec_b < sec_a:
            sec_a, sec_b = sec_b, sec_a

        if self.loop_on:
            loop_a_s = self._sec_at_tick(self.loop_a)
            loop_b_s = self._sec_at_tick(self.loop_b)
            sec_a = max(sec_a, loop_a_s - 0.5)
            sec_b = min(sec_b, loop_b_s + 0.5)

        tick_a = max(0, self._tick_at_sec(sec_a) - self.p.ppq // 8)
        tick_b = max(tick_a + 1, self._tick_at_sec(sec_b) + self.p.ppq // 8)

        from bisect import bisect_left
        for ti, idx in enumerate(self._note_index):
            ends:  List[int] = idx["ends"]
            items: List[Tuple[int,int,int,int,int,int]] = idx["items"]
            if not items:
                continue
            i = bisect_left(ends, tick_a)
            while i < len(items):
                st, ed, pitch, vel, ch, track_index = items[i]
                if st > tick_b:
                    break
                if ed >= tick_a and st <= tick_b:
                    on_sec  = self._sec_at_tick(st)
                    off_sec = self._sec_at_tick(ed)
                    if sec_a <= on_sec <= sec_b:
                        yield _Evt(on_sec,  pitch, vel, ch, True,  track_index)
                    if sec_a <= off_sec <= sec_b:
                        yield _Evt(off_sec, pitch, 0,   ch, False, track_index)
                i += 1
