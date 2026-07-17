#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Apply the Freeze/Mute separation and bar-aligned page scrolling fixes to the
single-file MIDIT build supplied by the user.

Usage:
    python apply_freeze_autoscroll_fix.py "main(2).py"

If no path is given, the script tries main(2).py and then main.py in the
current directory. It writes main_freeze_autoscroll_fixed.py beside the input
and verifies the generated file with py_compile.
"""
from __future__ import annotations

import argparse
import py_compile
import sys
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one matching block, found {count}. "
            "Make sure you are patching the same main(2).py version."
        )
    return text.replace(old, new, 1)


def choose_source(arg: str | None) -> Path:
    if arg:
        p = Path(arg).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"Source file not found: {p}")
        return p

    for name in ("main(2).py", "main.py"):
        p = Path.cwd() / name
        if p.is_file():
            return p.resolve()
    raise FileNotFoundError(
        "Could not find main(2).py or main.py in the current directory."
    )


def patch(source: Path, output: Path) -> None:
    text = source.read_text(encoding="utf-8")

    # 1. Frozen tracks are rendered by WAV, so Transport must skip their MIDI
    #    without borrowing the user's Mute state.
    text = replace_once(
        text,
        '''            tr = tracks[ev.track_index]\n            solo_indices = [i for i, t in enumerate(tracks) if getattr(t, "solo", False)]\n            muted = getattr(tr, "mute", False)\n        if solo_indices and ev.track_index not in solo_indices:\n            return\n        if muted:\n            return\n''',
        '''            tr = tracks[ev.track_index]\n            solo_indices = [i for i, t in enumerate(tracks) if getattr(t, "solo", False)]\n            muted = bool(getattr(tr, "mute", False))\n            frozen = bool(getattr(tr, "frozen", False))\n        if solo_indices and ev.track_index not in solo_indices:\n            return\n        # Frozen tracks are played by their rendered WAV. Skip their original\n        # MIDI independently from the user-facing Mute switch.\n        if muted or frozen:\n            return\n''',
        "Transport frozen MIDI suppression",
    )

    # 2. Mute/Solo changes must also control rendered Frozen audio.
    text = replace_once(
        text,
        '''    def _set_track_mute(self, idx: int, v: bool):\n        self.proj.tracks[idx].mute = v\n        self.proj.mark_dirty()\n\n    def _set_track_solo(self, idx: int, v: bool):\n        self.proj.tracks[idx].solo = v\n        self.proj.mark_dirty()\n''',
        '''    def _set_track_mute(self, idx: int, v: bool):\n        self.proj.tracks[idx].mute = bool(v)\n        self._sync_freeze_player_mutes()\n        self.proj.mark_dirty()\n\n    def _set_track_solo(self, idx: int, v: bool):\n        self.proj.tracks[idx].solo = bool(v)\n        self._sync_freeze_player_mutes()\n        self.proj.mark_dirty()\n''',
        "Track Mute/Solo synchronization",
    )

    # 3. Add effective Mute/Solo handling for QMediaPlayer/QAudioOutput.
    text = replace_once(
        text,
        '''        # 音量用 Track.volume 百分比（簡化）\n        vol_pct = max(0, min(100, round(self.proj.tracks[idx].volume * 100 / 127)))\n        audio.setVolume(vol_pct / 100.0)\n\n    def _seek_freeze_players_to(self, tick: int):\n''',
        '''        # 音量用 Track.volume 百分比（簡化）\n        vol_pct = max(0, min(100, round(self.proj.tracks[idx].volume * 100 / 127)))\n        audio.setVolume(vol_pct / 100.0)\n        self._sync_freeze_player_mutes()\n\n    def _sync_freeze_player_mutes(self):\n        """Apply the same Mute/Solo rules to rendered Frozen WAV players."""\n        tracks = list(getattr(self.proj, "tracks", []))\n        solo_indices = {\n            i for i, tr in enumerate(tracks)\n            if bool(getattr(tr, "solo", False))\n        }\n\n        for idx, (_player, audio) in list(self._freeze_players.items()):\n            if not (0 <= idx < len(tracks)):\n                continue\n            tr = tracks[idx]\n            effective_mute = bool(getattr(tr, "mute", False))\n            if solo_indices and idx not in solo_indices:\n                effective_mute = True\n            try:\n                audio.setMuted(effective_mute)\n            except Exception:\n                pass\n\n    def _seek_freeze_players_to(self, tick: int):\n''',
        "Frozen WAV Mute/Solo helper",
    )

    # Keep Frozen outputs synchronized immediately before playback begins.
    text = replace_once(
        text,
        '''        if playing:\n            # 先對齊目前位置再播放\n            self._seek_freeze_players_to(int(getattr(self.trans, "pos", 0)))\n            for player, _audio in self._freeze_players.values():\n''',
        '''        if playing:\n            self._sync_freeze_player_mutes()\n            # 先對齊目前位置再播放\n            self._seek_freeze_players_to(int(getattr(self.trans, "pos", 0)))\n            for player, _audio in self._freeze_players.values():\n''',
        "Frozen playback synchronization",
    )

    # 4. Freeze no longer checks the M button. The MIDI engine uses frozen=True
    #    to suppress original events, while the WAV observes the real Mute/Solo.
    text = replace_once(
        text,
        '''            # 記錄與 UI\n            tr.freeze_path = wav\n            tr.frozen = True\n            # 凍結期間把原本的 mute 記起來，並強制靜音，避免 MIDI 與 WAV 疊音\n            tr._freeze_prev_mute = tr.mute\n            tr.mute = True\n            # 建播放器\n            self._ensure_player_for_track(idx, wav)\n''',
        '''            # 記錄與 UI\n            tr.freeze_path = wav\n            tr.frozen = True\n            tr._freeze_prev_mute = None\n\n            # Stop any MIDI note that was already sounding. Future events for\n            # this track are skipped by Transport because frozen=True.\n            try:\n                self.trans.panic()\n            except Exception:\n                pass\n\n            # Build the WAV player without changing the user's Mute state.\n            self._ensure_player_for_track(idx, wav)\n''',
        "Freeze activation without forced Mute",
    )

    text = replace_once(
        text,
        '''        else:\n            # 解除\n            tr.frozen = False\n            # 還原 mute 狀態\n            if tr._freeze_prev_mute is not None:\n                tr.mute = bool(tr._freeze_prev_mute)\n                tr._freeze_prev_mute = None\n            # 關掉播放器\n''',
        '''        else:\n            # 解除\n            tr.frozen = False\n            tr._freeze_prev_mute = None\n            # 關掉播放器\n''',
        "Freeze deactivation",
    )

    text = replace_once(
        text,
        '''        # 重建一次左面板，刷新「F/❄️」視覺\n        self.tracks_panel._build()\n        # 也刷新一次狀態列資料（例如 Notes/Duration 等）\n        self._refresh_status_all()\n''',
        '''        self._sync_freeze_player_mutes()\n\n        # 重建一次左面板，刷新「F/❄️」視覺；M 維持使用者原始設定。\n        self.tracks_panel._build()\n        # 也刷新一次狀態列資料（例如 Notes/Duration 等）\n        self._refresh_status_all()\n''',
        "Freeze UI refresh",
    )

    # Restore Mute correctly when clearing a session created by the old Freeze
    # implementation, which stored the previous state in _freeze_prev_mute.
    text = replace_once(
        text,
        '''        for t in getattr(self.proj, "tracks", []):\n            try:\n                t.frozen = False\n                t.freeze_path = ""\n                t._freeze_prev_mute = None\n            except Exception:\n                pass\n''',
        '''        for t in getattr(self.proj, "tracks", []):\n            try:\n                # Compatibility with sessions frozen by the previous version.\n                if getattr(t, "_freeze_prev_mute", None) is not None:\n                    t.mute = bool(t._freeze_prev_mute)\n                t.frozen = False\n                t.freeze_path = ""\n                t._freeze_prev_mute = None\n            except Exception:\n                pass\n''',
        "Legacy Freeze Mute restoration",
    )

    # 5. Replace centerOn() following with bar-aligned page scrolling. When the
    #    playhead leaves the right edge, its current bar begins at the left.
    text = replace_once(
        text,
        '''    def _on_tick(self, tick: int):\n        self._ensure_playhead()\n        x = self.tick_to_x(tick)\n        self.playhead.setLine(x, 0, x, (self.high - self.low + 1) * self.px_per_note)\n\n        if getattr(self, "follow_playhead", True):\n            if getattr(self, "follow_playhead", True):\n                vis = self.mapToScene(self.viewport().rect()).boundingRect()\n                margin = 120\n                if tick + self.p.ppq // 8 < getattr(self, "_last_tick", 0):\n                    self.centerOn(x, vis.center().y())\n                else:\n                    if x < vis.left() + margin or x > vis.right() - margin:\n                        self.centerOn(x, vis.center().y())\n\n        self._last_tick = tick\n        self._invalidate_gutter()\n''',
        '''    def _follow_bar_start_tick(self, tick: int) -> int:\n        """Return the start tick of the bar containing tick."""\n        tick = max(0, int(tick))\n        ppq = max(1, int(getattr(self.p, "ppq", 480)))\n\n        seg_start = 0\n        num, den = 4, 4\n        try:\n            ts_map = sorted(\n                list(getattr(self.p, "timesig_map", []) or []),\n                key=lambda ts: int(getattr(ts, "tick", 0)),\n            )\n            for ts in ts_map:\n                ts_tick = int(getattr(ts, "tick", 0))\n                if ts_tick > tick:\n                    break\n                seg_start = max(0, ts_tick)\n                num = max(1, int(getattr(ts, "num", 4)))\n                den = max(1, int(getattr(ts, "den", 4)))\n        except Exception:\n            pass\n\n        ticks_per_beat = max(1, int(round(ppq * (4.0 / den))))\n        ticks_per_bar = max(1, ticks_per_beat * num)\n        return seg_start + ((tick - seg_start) // ticks_per_bar) * ticks_per_bar\n\n    def _align_follow_tick_to_left(self, tick: int) -> None:\n        """Align the current bar to the left edge of the editable area."""\n        bar_tick = self._follow_bar_start_tick(tick)\n        bar_x = self.tick_to_x(bar_tick)\n        bar_view_x = self.mapFromScene(QtCore.QPointF(bar_x, 0.0)).x()\n\n        hbar = self.horizontalScrollBar()\n        delta = int(round(bar_view_x - self.GUTTER_W))\n        if delta:\n            hbar.setValue(hbar.value() + delta)\n\n    def _on_tick(self, tick: int):\n        self._ensure_playhead()\n        x = self.tick_to_x(tick)\n        self.playhead.setLine(x, 0, x, (self.high - self.low + 1) * self.px_per_note)\n\n        if getattr(self, "follow_playhead", True):\n            vp = self.viewport().rect()\n            playhead_view_x = self.mapFromScene(QtCore.QPointF(x, 0.0)).x()\n            last_tick = int(getattr(self, "_last_tick", 0))\n            jumped_back = int(tick) + self.p.ppq // 8 < last_tick\n\n            # Page-style following: scroll only after crossing the right edge.\n            # The current bar then starts at the left of the piano-roll area.\n            work_left = self.GUTTER_W\n            work_right = max(work_left + 1, vp.right() - 2)\n            if jumped_back or playhead_view_x < work_left or playhead_view_x > work_right:\n                self._align_follow_tick_to_left(int(tick))\n\n        self._last_tick = tick\n        self._invalidate_gutter()\n''',
        "Playhead page scrolling",
    )

    output.write_text(text, encoding="utf-8")
    py_compile.compile(str(output), doraise=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", nargs="?", help="Path to main(2).py or main.py")
    parser.add_argument(
        "-o", "--output",
        help="Output path; default: main_freeze_autoscroll_fixed.py beside source",
    )
    args = parser.parse_args()

    try:
        source = choose_source(args.source)
        output = (
            Path(args.output).expanduser().resolve()
            if args.output
            else source.with_name("main_freeze_autoscroll_fixed.py")
        )
        if output == source:
            raise RuntimeError("Output must not overwrite the source file.")
        patch(source, output)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {output}")
    print("Syntax check: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
