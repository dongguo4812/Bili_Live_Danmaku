# -*- coding: utf-8 -*-
"""Static contracts for the unified OBS display page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path


class CombinedDisplayContractTests(unittest.TestCase):
    def test_live2d_api_uses_only_canonical_prefix(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/live2d/settings")', source)
        self.assertIn('@app.post("/api/live2d/settings")', source)
        self.assertIn('@app.get("/api/live2d/status")', source)
        self.assertIn('@app.post("/api/live2d/status")', source)
        self.assertNotIn('@app.get("/api/display/settings")', source)
        self.assertNotIn('@app.post("/api/display/settings")', source)
        self.assertIn('fetch("/api/live2d/settings', script)
        self.assertNotIn('fetch("/api/display/settings', script)

    def test_module_api_routes_use_only_canonical_prefixes(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        display_script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        voice_page = (root / "templates" / "voice.html").read_text(encoding="utf-8")
        music_page = (root / "templates" / "music_settings.html").read_text(encoding="utf-8")
        video_page = (root / "templates" / "video_settings.html").read_text(encoding="utf-8")

        canonical_routes = (
            "/api/danmaku/idle-tips/settings",
            "/api/voice/tts/settings",
            "/api/voice/tts/synthesize",
            "/api/voice/tts/audio/<filename>",
            "/api/music/settings",
            "/api/video/settings",
        )
        for route in canonical_routes:
            self.assertIn(route, main)

        legacy_routes = (
            "/api/idle-tips/settings",
            "/api/tts/settings",
            "/api/tts/synthesize",
            "/api/tts/audio/<filename>",
            "/api/settings/music",
            "/api/settings/video",
        )
        for route in legacy_routes:
            self.assertNotIn(route, main)

        self.assertIn('/api/voice/tts/audio/{filename}', main)
        self.assertIn('/api/danmaku/idle-tips/settings', display_script)
        self.assertIn('/api/voice/tts/settings', display_script)
        self.assertIn('/api/voice/tts/synthesize', display_script)
        self.assertIn('/api/voice/tts/settings', voice_page)
        self.assertIn('/api/voice/tts/synthesize', voice_page)
        self.assertIn('/api/music/settings', music_page)
        self.assertIn('/api/video/settings', video_page)

        # Browser code must call only the canonical routes.
        for legacy in legacy_routes:
            self.assertNotIn(legacy, display_script + voice_page + music_page + video_page)

    def test_each_display_module_has_a_dedicated_route_and_mode(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        template = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        canonical_routes = (
            "/live2d/display", "/live2d/moc2/display",
            "/danmaku/display", "/voice/display",
            "/music/display", "/video/display", "/music/widget",
        )
        for route in canonical_routes:
            self.assertIn(f'@app.get("{route}")', main)

        legacy_display_routes = (
            "/display", "/display/live2d", "/display/danmaku",
            "/display/tts", "/display/music", "/display/music-widget",
            "/display/video",
        )
        for route in legacy_display_routes:
            self.assertNotIn(f'@app.get("{route}")', main)

        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('frame.src="/live2d/display?embedded=1"', combined)
        self.assertIn('/api/voice/tts/settings', combined)
        self.assertNotIn('/api/tts/settings', combined)
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

    def test_combined_display_uses_canonical_tts_settings_api(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('/api/voice/tts/settings', source)
        self.assertNotIn('/api/tts/settings', source)

    def test_video_widget_does_not_embed_video_player(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "combined_display.html").read_text(encoding="utf-8")
        start = source.index("function renderVideo(")
        end = source.index("\n  function renderDanmaku(", start)
        video_renderer = source[start:end]
        self.assertNotIn("<video", video_renderer.lower())
        self.assertNotIn("<iframe", video_renderer.lower())


if __name__ == "__main__":
    unittest.main()
