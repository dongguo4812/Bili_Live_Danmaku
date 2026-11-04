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
        live2d_mode_css = template.split('html[data-display-mode="live2d"]', 1)[1].split('html[data-display-mode="danmaku"]', 1)[0]
        self.assertIn("#display-settings-toggle", live2d_mode_css)
        self.assertIn("#display-settings-panel", live2d_mode_css)
        self.assertNotIn("#danmaku-bubble", live2d_mode_css)
        self.assertNotIn("#danmaku-preview", live2d_mode_css)
        self.assertIn('displayMode === "live2d" || displayMode === "danmaku"', script)
        self.assertIn('return render_template("display.html", display_mode="all", live2d_enabled=_live2d_enabled(), live2d_version=_live2d_runtime_version())', main)
        self.assertIn('live2d_version="moc2"', main)
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

    def test_combined_live2d_editor_uses_full_display_preview_without_silent_toggle(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        display_script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        main = (root / "main.py").read_text(encoding="utf-8")

        self.assertIn('frame.src="/live2d/display?embedded=1"', template)
        self.assertIn('return render_template("combined_display.html", display_mode=False)', main)
        self.assertIn("function syncLive2DFrame(widget)", template)
        self.assertIn("frame.style.width=stage.clientWidth+\"px\"", template)
        self.assertIn("frame.style.height=stage.clientHeight+\"px\"", template)
        self.assertIn('frame.style.left=((body.clientWidth-stage.clientWidth)/2)+"px"', template)
        self.assertIn('frame.style.top=((body.clientHeight-stage.clientHeight)/2)+"px"', template)
        self.assertNotIn("bodyRect.left-stageRect.left", template)
        self.assertNotIn("bodyRect.top-stageRect.top", template)
        self.assertIn("new ResizeObserver(syncAllLive2DFrames)", template)
        self.assertNotIn('silent=1', template)
        self.assertNotIn('live2d-preview-toggle', template)
        self.assertNotIn('live2dPreviewEnabled', template)
        self.assertNotIn('silentPreview', display_script)
        self.assertIn('const showTts = displayMode === "all" || displayMode === "tts";', display_script)
        self.assertNotIn("live2d-preview-export", display_script)
        self.assertNotIn('/api/live2d/preview', main)
        self.assertNotIn('static/live2d_preview', template + display_script + main)

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
        # The model viewport crop stays inside Live2D; the combined widget clips the full iframe (model + bubble).
        self.assertIn('.widget{position:absolute;min-width:10%;min-height:10%;overflow:hidden;', combined)
        self.assertIn('.widget.live2d iframe,.widget.music_widget iframe{width:100%;height:100%;border:0;background:transparent}', combined)
        self.assertIn('frame.src="/live2d/display?embedded=1"', combined)
        self.assertNotIn('frame.src="/live2d/display?embedded=1&silent=1"', combined)
        self.assertIn('const bubble = document.getElementById("danmaku-bubble")', script)
        self.assertNotIn('bubble.style.clipPath', script)

    def test_live2d_transform_updates_model_matrix_in_realtime(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn("function captureLive2DTransformBase(", script)
        self.assertIn("function applyLive2DTransform(", script)
        # Cubism 2 stores its transform in Float32Array, not a plain JavaScript Array.
        self.assertIn("ArrayBuffer.isView(matrix.tr)", script)
        self.assertNotIn("Array.isArray(matrix.tr)", script)
        # The server has already applied saved layout preferences in model.json.
        # Live preview must apply only the delta from that saved baseline.
        self.assertIn("preferences: transformSnapshot()", script)
        self.assertIn("const saved = base.preferences || { scale: 1, x: 0, y: 0 }", script)
        self.assertIn("const scaleRatio = current.scale / saved.scale", script)
        self.assertIn("base.matrix.scale(base.scaleX * scaleRatio, base.scaleY * scaleRatio)", script)
        self.assertIn("base.matrix.centerX(base.centerX + current.x - saved.x)", script)
        self.assertIn("base.matrix.centerY(base.centerY + current.y - saved.y)", script)
        self.assertIn("base.matrix.centerX(", script)
        self.assertIn("base.matrix.centerY(", script)
        self.assertIn('if (key === "scale" || key === "x" || key === "y") applyLive2DTransform();', script)
        self.assertNotIn("transformChangedSinceLoad", script)
        self.assertNotIn("canvas.style.transform", script)
        self.assertIn("保存只负责持久化", script)
        self.assertIn("window.location.reload()", script)


class Live2DModelDirectoryContractTests(unittest.TestCase):
    def test_live2d_runtime_is_an_optional_single_integration(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        center = (root / "templates" / "index.html").read_text(encoding="utf-8")
        display = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        defaults = json.loads((root / "config" / "default" / "live2d.json").read_text(encoding="utf-8"))

        self.assertIs(defaults["enabled"], True)
        self.assertIn('@app.post("/api/modules/live2d/enabled")', main)
        self.assertIn('requires_connection=False', main)
        self.assertIn('data-live2d-enabled=', display)
        self.assertIn('{% if live2d_enabled|default(true) %}', display)
        self.assertIn('<script src="/static/js/live2d.min.js?', display)
        self.assertIn('{% endif %}', display)
        self.assertIn('dataset.live2dEnabled !== "0"', script)
        self.assertIn('Boolean(data.live2d.enabled) !== live2dEnabledAtLoad', script)
        self.assertIn('id="live2d-toggle"', center)
        self.assertIn('/api/modules/live2d/enabled', center)
        self.assertNotIn("moduleAction('live2d'", center)
        self.assertIn('if not _live2d_enabled():', main)
        self.assertIn('"enabled": enabled', main)

    def test_model_scan_uses_only_versioned_moc2_directory(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('root = (MODEL_ROOT / version).resolve()', main)
        self.assertIn('version_root = MODEL_ROOT / version', main)
        self.assertIn('for directory in sorted(version_root.iterdir()', main)
        self.assertNotIn('roots = (MODEL_ROOT / "moc2", MODEL_ROOT)', main)
        self.assertNotIn('MODEL_ROOT.iterdir()', main)
        self.assertIn('"version": version', main)
        self.assertIn('models.get(f"{model_version}:{model_id}"', main)
        self.assertIn('[f"{version}:{model_id}"] = preferences', main)

    def test_versioned_model_assets_are_served_from_their_new_directory(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('def _model_asset_prefix(directory: Path, version: str = "moc2") -> str:', main)
        self.assertIn('f"/models/{version}/" + relative.as_posix().rstrip("/") + "/"', main)
        self.assertIn('relative_prefix = "../" + prefix.removeprefix("/models/")', main)
        self.assertIn('return relative_prefix + value.lstrip("./")', main)
        self.assertIn('data = _prefix_model_asset_paths(data, _model_asset_prefix(directory, version))', main)
        self.assertIn('@app.get("/models/<path:filename>")', main)

    def test_moc3_directory_is_reserved_without_changing_moc2_runtime(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "models" / "moc3" / ".gitkeep").is_file())
        self.assertTrue((root / "models" / "moc2").is_dir())
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('root = (MODEL_ROOT / version).resolve()', main)
        self.assertNotIn('MODEL_ROOT.iterdir()', main)
        self.assertIn('runtime_file = BASE_DIR / "static" / "js" / "live2d.min.js"', main)


class Live2DVersionIsolationContractTests(unittest.TestCase):
    def test_live2d_versions_use_separate_model_roots_and_config_formats(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('def _normalize_live2d_version(', source)
        self.assertIn('(MODEL_ROOT / version).resolve()', source)
        self.assertIn('f"{model_id}.model3.json"', source)
        self.assertIn('refs.get("Moc")', source)
        self.assertIn('@app.get("/models/<version>/<model_id>/model.json")', source)

    def test_live2d_model_selection_and_preferences_are_version_scoped(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn('"model_by_version"', source)
        self.assertIn('settings.setdefault("models", {})[f"{version}:{model_id}"]', source)
        self.assertIn('models.get(model_id, {}) if model_version == "moc2" else {}', source)
        self.assertIn('_available_models(version)', source)

    def test_live2d_runtime_defaults_to_moc2(self):
        defaults = json.loads((Path(__file__).resolve().parents[1] / "config" / "default" / "live2d.json").read_text(encoding="utf-8"))
        self.assertEqual(defaults["runtime_version"], "moc2")




class Live2DRuntimeSelectionContractTests(unittest.TestCase):
    def test_runtime_version_has_a_persistent_api_and_manager_selector(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('@app.post("/api/modules/live2d/version")', main)
        self.assertIn('config["runtime_version"] = version', main)
        self.assertIn('id="live2d-version"', page)
        self.assertIn('"/api/modules/live2d/version"', page)

    def test_moc3_never_falls_back_to_cubism2_loader(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn("live2d_version|default('moc2') == 'moc2'", template)
        self.assertIn('if (live2dRuntimeVersion === "moc3")', script)
        self.assertIn("loadMoc3Model(modelId)", script)
        self.assertIn('typeof runtimeWindow.live2dSetMouthOpen === "function"', script)
        self.assertIn('id="moc3-runtime-frame"', template)


if __name__ == "__main__":
    unittest.main()


class Live2DOfficialCubism5RuntimeContractTests(unittest.TestCase):
    def test_moc3_runtime_assets_and_renderer_route_are_present(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/live2d/moc3/runtime/<model_id>")', main)
        self.assertIn('@app.get("/api/models")', main)
        self.assertIn('if version == "moc2":', main)
        self.assertIn('MOC3 的缩放和位置由统一展示页实时变换 iframe', main)
        self.assertTrue((root / "static/js/live2d/moc3/runtime.js").is_file())
        self.assertTrue((root / "static/js/live2d/moc3/Core/live2dcubismcore.min.js").is_file())
        self.assertTrue((root / "static/js/live2d/moc3/Framework/Shaders/WebGL/vertshadersrc.vert").is_file())
        self.assertTrue((root / "static/js/live2d/moc3/Framework/LICENSE.md").is_file())
        self.assertTrue((root / "static/js/live2d/moc3/Core/LICENSE.md").is_file())

    def test_moc3_runtime_bundle_uses_local_shader_paths(self):
        root = Path(__file__).resolve().parents[1]
        bundle = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn("/static/js/live2d/moc3/Framework/Shaders/WebGL/", bundle)
        self.assertIn('type:"moc3-model-loaded"', bundle)
        self.assertNotIn("window.setInterval(()=>{Ma()},700);", bundle)

