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
        self.assertIn('frame.src="/live2d/moc2/display?embedded=1"', combined)
        self.assertIn('/api/voice/tts/settings', combined)
        self.assertNotIn('/api/tts/settings', combined)
        for mode in ('data-display-mode="live2d"', 'data-display-mode="danmaku"', 'data-display-mode="tts"'):
            self.assertIn(mode, template)
        self.assertIn('const showLive2D =', script)
        self.assertIn('const showDanmaku =', script)
        self.assertIn('const showTts =', script)

    def test_danmaku_settings_page_and_config_ownership(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "danmaku.html").read_text(encoding="utf-8")
        display = (root / "templates" / "display.html").read_text(encoding="utf-8")
        live2d_defaults = json.loads((root / "config" / "default" / "live2d.json").read_text(encoding="utf-8"))
        danmaku_defaults = json.loads((root / "config" / "default" / "danmaku.json").read_text(encoding="utf-8"))
        self.assertIn('@app.get("/danmaku")', main)
        self.assertIn('return render_template("danmaku.html")', main)
        self.assertIn('/api/danmaku/idle-tips/settings', page)
        self.assertIn('/api/status', page)
        self.assertNotIn("空闲随机提示", display)
        self.assertNotIn("Edge TTS 语音播报", display)
        self.assertIn("bubble-x-slider", display)
        voice_page = (root / "templates" / "voice.html").read_text(encoding="utf-8")
        self.assertIn("/api/voice/tts/settings", voice_page)
        self.assertNotIn("idle_tips", live2d_defaults)
        self.assertIn("idle_tips", danmaku_defaults)
        self.assertIn('manager.save("danmaku", danmaku)', main)
        self.assertIn('legacy_idle_tips', main)

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

    def test_combined_live2d_editor_uses_model_specific_static_preview(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('api("/api/live2d/settings")', template)
        self.assertIn('/static/live2d_preview/"+encodeURIComponent(modelId)+".png', template)
        self.assertIn('renderLive2DPreview(body)', template)
        self.assertIn('模型“"+modelId+"”暂无静态预览图。', template)
        self.assertIn('frame.src="/live2d/moc2/display?embedded=1"', template)
        self.assertIn('.display-mode .widget.live2d .widget-header{display:none}', template)
        self.assertIn('.display-mode .widget.live2d .widget-body{flex:1;min-height:0;padding:0;overflow:hidden;background:transparent}', template)
        display_page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        self.assertIn('new URLSearchParams(window.location.search).get("embedded") === "1"', display_page)
        preview = (root / "static" / "live2d_preview" / "README.md").read_text(encoding="utf-8")
        self.assertIn('static/live2d_preview/<model_id>.png', preview)

    def test_live2d_preview_export_uses_the_active_canvas(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/live2d/preview")', main)
        self.assertIn('static" / "live2d_preview', main)
        self.assertIn('id="live2d-preview-export"', page)
        self.assertIn('captureLive2DCanvasPng()', script)
        self.assertIn('gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)', script)
        self.assertIn('fetch("/api/live2d/preview"', script)
        self.assertIn('loadedModelId', script)
        self.assertNotIn('loadlive2d(canvas.id', script[script.index('function captureLive2DCanvasPng'):script.index('function waitForRenderedPixels')])

    def test_combined_layout_supports_independent_content_scales_and_edge_resize(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        media_css = (root / "static" / "display_media.css").read_text(encoding="utf-8")
        music_widget = (root / "templates" / "music_widget.html").read_text(encoding="utf-8")
        self.assertIn('"font_scale": round(font_scale, 1)', main)
        self.assertIn('"content_scale": round(content_scale, 1)', main)
        for direction in ('"n"', '"s"', '"e"', '"w"', '"ne"', '"nw"', '"se"', '"sw"'):
            self.assertIn(direction, combined)
        self.assertIn('id="font-scale"', combined)
        self.assertIn('id="content-scale"', combined)
        self.assertIn('.editor-control[hidden]{display:none!important}', combined)
        self.assertIn('font_scale', combined)
        self.assertIn('content_scale', combined)
        self.assertIn('var(--content-scale, 1)', media_css)
        self.assertIn('var(--widget-scale, 1)', music_widget)

    def test_combined_tts_exposes_current_and_pending_speech(self):
        root = Path(__file__).resolve().parents[1]
        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        display_script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('type: "combined-tts-state"', display_script)
        self.assertIn('speechQueue.map(entry => entry.text)', display_script)
        self.assertIn('正在播报', combined)
        self.assertIn('等待播报（', combined)
        self.assertIn('combined-tts-state', combined)

    def test_live2d_preview_metadata_tracks_crop_and_model_transform(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        self.assertIn('metadata_target = preview_dir / (model_id + ".json")', main)
        self.assertIn('"canvas_width": canvas_width', main)
        self.assertIn('form.append("frame", JSON.stringify(capture.frame))', script)
        self.assertIn('form.append("preferences"', script)
        self.assertIn('/static/live2d_preview/"+encodeURIComponent(modelId)+".json', combined)
        self.assertIn('image.style.transformOrigin=', combined)
        self.assertIn('const scale=(Number(preferences.scale)||1)/(Number(baseline.scale)||1)', combined)

    def test_video_widget_does_not_embed_video_player(self):
        source = (Path(__file__).resolve().parents[1] / "templates" / "combined_display.html").read_text(encoding="utf-8")
        start = source.index("function renderVideo(")
        end = source.index("\n  function renderDanmaku(", start)
        video_renderer = source[start:end]
        self.assertNotIn("<video", video_renderer.lower())
        self.assertNotIn("<iframe", video_renderer.lower())


    def test_live2d_custom_crop_is_independent_from_danmaku(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        css = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")

        for side in ("top", "right", "bottom", "left"):
            self.assertIn(f'id="crop-{side}-slider"', page)
            self.assertIn(f'"crop_{side}": number("crop_{side}", 0.0, 0.0, 45.0)', main)
            self.assertIn(f'"crop_{side}": bounded_number("crop_{side}", 0.0, 0.0, 45.0)', main)
            self.assertIn(f'controls["crop-{side}"]', script)

        self.assertIn('id="live2d-viewport"', page)
        self.assertIn('#live2d-viewport {', css)
        self.assertIn('target.style.clipPath = clip', script)
        self.assertIn('target.style.webkitClipPath = clip', script)
        self.assertIn('viewport.style.clipPath="inset("+crop.join(" ")+")"', combined)
        self.assertIn('const bubble = document.getElementById("danmaku-bubble")', script)
        self.assertNotIn('bubble.style.clipPath', script)


if __name__ == "__main__":
    unittest.main()
