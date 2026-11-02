# -*- coding: utf-8 -*-
"""Static contracts for the unified OBS display page."""
from __future__ import annotations

import unittest
from pathlib import Path


class CombinedDisplayContractTests(unittest.TestCase):
    def test_danmaku_cursor_prevents_replaying_cached_messages(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('/api/danmaku?latest=1', source)
        self.assertIn('/api/danmaku?after="+encodeURIComponent(danmakuAfter)', source)
        self.assertNotIn('/api/danmaku?after=0', source)

    def test_video_widget_does_not_embed_video_player(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "combined_display.html").read_text(encoding="utf-8")
        start = source.index("function renderVideo(")
        end = source.index("\n  function renderDanmaku(", start)
        video_renderer = source[start:end]
        self.assertNotIn("<video", video_renderer.lower())
        self.assertNotIn("<iframe", video_renderer.lower())


if __name__ == "__main__":
    unittest.main()
