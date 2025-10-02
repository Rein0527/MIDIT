# modules/core/io_midi.py
# =============================================================================
#  Imports & Typing
# =============================================================================
from __future__ import annotations
from collections import Counter, defaultdict
from typing import List, Optional, Tuple

from .models import (
    Project,
    Track,
    Note,
    Marker,
    TempoChange,
    TimeSigChange,
    mido,
    Message,
    MetaMessage,
)

# =============================================================================
#  Helpers: 去重 / 事件輸出（delta-time）/ 小工具
# =============================================================================
def _dedup_tempos(tempos_raw: List[TempoChange]) -> List[TempoChange]:
    if not tempos_raw:
        return [TempoChange(0, 120.0)]
    by_tick: dict[int, float] = {}
    for t in tempos_raw:
        by_tick[int(t.tick)] = float(t.bpm)
    return [TempoChange(tk, bpm) for tk, bpm in sorted(by_tick.items())]

def _dedup_timesigs(times_raw: List[TimeSigChange]) -> List[TimeSigChange]:
    if not times_raw:
        times_raw = [TimeSigChange(0, 4, 4)]
    by_tick: dict[int, Tuple[int, int]] = {}
    for ts in times_raw:
        by_tick[int(ts.tick)] = (int(ts.num), int(ts.den))
    return [TimeSigChange(tk, nd[0], nd[1]) for tk, nd in sorted(by_tick.items())]

def _append_delta(track: "mido.MidiTrack", events: List[tuple[int, "Message" | "MetaMessage"]]) -> None:
    last = 0
    for tick, msg in events:
        dt = int(tick) - last
        last = int(tick)
        msg.time = int(dt)
        track.append(msg)


# =============================================================================
#  SECTION A: mido.MidiFile -> Project
#  - 僅保留含 note 的軌
#  - 支援同音高重疊（以堆疊處理）
#  - tempo / time_signature 去重（同 tick 留最後）
#  - 每軌以最常見 channel 作為 Track.channel
#  - 讀取 meta "marker" → Project.markers
# =============================================================================
def from_mido(mid: "mido.MidiFile") -> Project:
    ppq = int(mid.ticks_per_beat)

    tempos_raw: List[TempoChange] = []
    times_raw: List[TimeSigChange] = [TimeSigChange(0, 4, 4)]
    tracks: List[Track] = []
    markers_raw: List[Marker] = []

    for ti, mtr in enumerate(mid.tracks):
        abs_tick = 0

        on_notes: dict[tuple[int, int], List[tuple[int, int]]] = defaultdict(list)

        program = 0
        vol = 100
        tr_name: Optional[str] = None
        notes: List[Note] = []

        for msg in mtr:
            abs_tick += int(msg.time)

            if msg.is_meta:
                if ti == 0 and msg.type == "set_tempo":
                    tempos_raw.append(TempoChange(abs_tick, float(mido.tempo2bpm(msg.tempo))))
                elif ti == 0 and msg.type == "time_signature":
                    times_raw.append(TimeSigChange(abs_tick, int(msg.numerator), int(msg.denominator)))
                elif msg.type == "track_name":
                    tr_name = msg.name or tr_name
                elif msg.type == "marker":
                    try:
                        markers_raw.append(Marker(tick=int(abs_tick), name=str(msg.text)))
                    except Exception:
                        pass
                continue

            ch = getattr(msg, "channel", None)

            if msg.type == "program_change" and ch is not None:
                program = int(msg.program)

            elif msg.type == "control_change" and ch is not None and int(msg.control) == 7:
                vol = int(msg.value)

            elif msg.type == "note_on" and ch is not None:
                if int(msg.velocity) > 0:
                    on_notes[(int(msg.note), int(ch))].append((abs_tick, int(msg.velocity)))
                else:
                    lst = on_notes.get((int(msg.note), int(ch)))
                    if lst:
                        st, vel = lst.pop()
                        dur = max(1, abs_tick - st)
                        notes.append(Note(int(msg.note), int(vel), int(st), int(dur), int(ch)))

            elif msg.type == "note_off" and ch is not None:
                lst = on_notes.get((int(msg.note), int(ch)))
                if lst:
                    st, vel = lst.pop()
                    dur = max(1, abs_tick - st)
                    notes.append(Note(int(msg.note), int(vel), int(st), int(dur), int(ch)))

        if on_notes:
            for (pitch, ch), lst in list(on_notes.items()):
                while lst:
                    st, vel = lst.pop()
                    dur = max(1, abs_tick - st)
                    notes.append(Note(int(pitch), int(vel), int(st), int(dur), int(ch)))

        if not notes:
            continue

        ch_mode = max(Counter(n.channel for n in notes).items(), key=lambda kv: kv[1])[0]
        for n in notes:
            n.channel = int(ch_mode)

        name = tr_name or f"Track {len(tracks) + 1}"

        tracks.append(
            Track(
                name=name,
                channel=int(ch_mode),
                program=int(program),
                volume=int(vol),
                notes=notes,
            )
        )

    tempos = _dedup_tempos(tempos_raw)
    times = _dedup_timesigs(times_raw)

    if not tracks:
        tracks = [Track(name="Empty")]

    proj = Project(ppq=ppq, tempo_map=tempos, timesigs=times, tracks=tracks)
    proj.markers = sorted(markers_raw, key=lambda m: int(m.tick)) if markers_raw else []
    return proj


# =============================================================================
#  SECTION B: Project -> mido.MidiFile
#  - type=1（多軌）
#  - 產生 meta 軌（tempo / time_signature / marker）
#  - 每個資料軌結尾補 end_of_track
# =============================================================================
def to_mido(p: Project) -> "mido.MidiFile":
    if not mido:
        raise RuntimeError("mido is not available; cannot export MIDI")

    mid = mido.MidiFile(type=1, ticks_per_beat=int(p.ppq))
    meta = mido.MidiTrack()
    mid.tracks.append(meta)
    ev_meta: List[tuple[int, MetaMessage]] = []

    for t in sorted(p.tempo_map, key=lambda x: int(x.tick)):
        ev_meta.append(
            (
                int(t.tick),
                MetaMessage("set_tempo", tempo=int(mido.bpm2tempo(float(t.bpm))), time=0),
            )
        )

    for ts in sorted(p.timesigs, key=lambda x: int(x.tick)):
        ev_meta.append(
            (
                int(ts.tick),
                MetaMessage(
                    "time_signature",
                    numerator=int(ts.num),
                    denominator=int(ts.den),
                    time=0,
                ),
            )
        )

    for mk in sorted(getattr(p, "markers", []), key=lambda x: int(x.tick)):
        ev_meta.append(
            (int(mk.tick), MetaMessage("marker", text=str(mk.name), time=0))
        )

    ev_meta.sort(key=lambda x: x[0])
    _append_delta(meta, ev_meta)

    for tr in p.tracks:
        mt = mido.MidiTrack()
        mid.tracks.append(mt)
        mt.append(MetaMessage("track_name", name=str(tr.name), time=0))
        ev: List[tuple[int, Message]] = []
        ev.append((0, Message("program_change", program=int(tr.program), channel=int(tr.channel), time=0)))
        ev.append((0, Message("control_change", control=7, value=int(tr.volume), channel=int(tr.channel), time=0)))

        for n in tr.notes:
            st = int(getattr(n, "start", 0))
            ed = int(getattr(n, "end", st + int(getattr(n, "dur", 0))))
            if ed < st:
                ed = st

            ev.append((st, Message("note_on", note=int(n.pitch), velocity=int(n.vel), channel=int(tr.channel), time=0)))
            ev.append((ed, Message("note_off", note=int(n.pitch), velocity=0, channel=int(tr.channel), time=0)))

        ev.sort(key=lambda x: x[0])

        _append_delta(mt, ev)

        mt.append(MetaMessage("end_of_track", time=0))

    return mid