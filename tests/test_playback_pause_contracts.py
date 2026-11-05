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

    def test_music_module_can_pause_and_restore_only_its_video_paused_track(self):
        source = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        self.assertIn("async def pause_for_video(", source)
        self.assertIn("async def resume_after_video(", source)
        self.assertIn("self._video_pause_track", source)
        self.assertIn("actual_track != expected_track", source)
        self.assertIn("self._video_pause_active", source)

    def test_video_playback_controls_music_and_restores_after_last_video(self):
        video = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("async def _pause_music_for_video()", video)
        self.assertIn("async def _resume_music_if_video_finished()", video)
        self.assertIn("if actually_playing:", video)
        self.assertIn("await _resume_music_if_video_finished()", video)
        self.assertIn("music_module=MUSIC_MODULE.bot", main)


if __name__ == "__main__":
    unittest.main()
