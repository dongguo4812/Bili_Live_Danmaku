# -*- coding: utf-8 -*-
"""Static contracts for the unified OBS display page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


class CombinedDisplayContractTests(unittest.TestCase):
    def test_live2d_api_has_canonical_prefix_and_legacy_compatibility(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/live2d/settings")', source)
        self.assertIn('@app.post("/api/live2d/settings")', source)
        self.assertIn('@app.get("/api/live2d/status")', source)
        self.assertIn('@app.post("/api/live2d/status")', source)
        self.assertIn('@app.get("/api/display/settings")', source)
        self.assertIn('@app.post("/api/display/settings")', source)
        self.assertIn('fetch("/api/live2d/settings', script)
        self.assertNotIn('fetch("/api/display/settings', script)

    def test_each_display_module_has_a_dedicated_route_and_mode(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        template = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        for route in ("/display/live2d", "/display/danmaku", "/display/tts",
                      "/display/music", "/display/video", "/display/music-widget"):
            self.assertIn(f'@app.get("{route}")', main)
        for mode in ('data-display-mode="live2d"', 'data-display-mode="danmaku"', 'data-display-mode="tts"'):
            self.assertIn(mode, template)
        self.assertIn('const showLive2D =', script)
        self.assertIn('const showDanmaku =', script)
        self.assertIn('const showTts =', script)

    def test_video_fan_medal_setting_uses_one_persisted_config_key(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        service = (root / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        self.assertIn('"require_fan_medal": bool(req.get("require_fans_medal", False))', main)
        self.assertIn('"require_fans_medal": bool(require_medal)', main)
        self.assertIn('requests_config.get("require_fans_medal", False)', service)

    def test_shipped_config_templates_exclude_unused_settings(self):
        root = Path(__file__).resolve().parents[1]
        read = lambda name: json.loads((root / "config" / "default" / name).read_text(encoding="utf-8"))
        live = read("live.json")
        live2d = read("live2d.json")
        voice = read("voice.json")
        obs = read("obs.json")
        logging = read("logging.json")
        self.assertNotIn("connect_on_start", live)
        for key in ("model_dir", "model_config", "canvas_width", "canvas_height", "max_device_pixel_ratio"):
            self.assertNotIn(key, live2d)
        for key in ("enabled", "rate", "volume"):
            self.assertNotIn(key, voice)
        self.assertNotIn("password_file", obs)
        self.assertNotIn("daily_rotation", logging)
        music = read("music.json")
        video = read("video.json")
        self.assertNotIn("enabled_on_start", music)
        self.assertNotIn("enabled_on_start", video)
        self.assertNotIn("platform_order", music.get("search", {}))
        self.assertIs(music["requests"]["require_fans_medal"], False)
        self.assertIs(video["requests"]["require_fans_medal"], False)

    def test_obs_password_is_read_only_from_credential_store(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("password_file", main)
        self.assertIn("def _read_obs_password() -> str:", main)

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
