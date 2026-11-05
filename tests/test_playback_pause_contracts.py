# -*- coding: utf-8 -*-
"""Regression contracts for real playback pause and paused request feedback."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlaybackPauseContractTests(unittest.TestCase):
    def test_music_snapshot_exposes_player_pause_separately_from_module_pause(self):
        source = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        self.assertIn("def get_lx_playback_state(", source)
        self.assertIn('return "paused"', source)
        self.assertIn('"playback_state": playback_state', source)
        self.assertIn('"module_paused" if current and self.paused', source)
        self.assertIn("歌曲点播当前已暂停", source)

    def test_video_snapshot_tracks_html5_paused_state_and_module_pause_separately(self):
        source = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        self.assertIn('else "paused" if bool(status.get("paused"))', source)
        self.assertIn('"playback_state": "module_paused" if current and module_paused', source)
        self.assertIn("视频点播当前已暂停", source)
        self.assertIn('add_operation_message(message, "failure")', source)

    def test_shared_display_shows_player_pause_without_replacing_module_pause_notice(self):
        script = (ROOT / "static" / "display_media.js").read_text(encoding="utf-8")
        self.assertIn('playbackPaused?"已暂停播放 ⏸":"正在播放 ▶"', script)
        self.assertNotIn("md-playback-paused-status", script)
        self.assertIn('options.moduleState==="paused"', script)
        self.assertIn("🚫 歌曲点播已暂停", script)
        self.assertIn("🚫 视频点播已暂停", script)


if __name__ == "__main__":
    unittest.main()
