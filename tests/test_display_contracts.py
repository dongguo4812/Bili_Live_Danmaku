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

    def test_moc3_model_directory_is_isolated_from_moc2_runtime(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "models" / "moc3" / ".gitkeep").is_file())
        self.assertTrue((root / "models" / "moc2").is_dir())
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('root = (MODEL_ROOT / version).resolve()', main)
        self.assertNotIn('MODEL_ROOT.iterdir()', main)
        self.assertIn('if version == "moc2" else BASE_DIR / "static" / "live2d" / "moc3" / "Core" / "live2dcubismcore.min.js"', main)


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




class Live2DModelProfileContractTests(unittest.TestCase):
    def test_moc2_model_profiles_are_discovered_and_validated(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('def _model_profiles(model_id: str, version: str = "moc2")', main)
        self.assertIn('directory.glob("model*.json")', main)
        self.assertIn('data.get("textures")', main)
        self.assertIn('"model.default.json"', main)
        self.assertIn('profile: str | None = None', main)
        self.assertIn('_read_model_config(model_id, version, profile)', main)

    def test_live2d_display_can_select_and_persist_a_model_profile(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('"profiles": profiles', main)
        self.assertIn('request.args.get("profile")', main)
        self.assertIn('preferences["profile"] = profile', main)
        self.assertIn('id="model-profile-select"', page)
        self.assertIn('function populateModelProfiles(', script)
        self.assertIn('modelProfileSelect.addEventListener("change", handleModelChange)', script)
        self.assertIn('"&profile=" + encodeURIComponent(modelProfileSelect.value)', script)
        self.assertIn('profile: currentModelProfile', script)
        self.assertIn('?profile=" + encodeURIComponent(profile)', script)


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
        self.assertIn("async function loadMoc3Model(modelId, token = null)", script)
        self.assertIn('typeof runtimeWindow.live2dSetMouthOpen === "function"', script)
        self.assertIn('id="moc3-runtime-frame"', template)


    def test_moc3_status_messages_describe_runtime_file_availability(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        index = (root / "templates" / "index.html").read_text(encoding="utf-8")
        for stale in (
            "Cubism 5 Framework/Core 尚未集成",
            "MOC3 渲染运行时将在下一阶段接入",
            "MOC3 展示暂不可用",
        ):
            with self.subTest(stale=stale):
                self.assertNotIn(stale, main + index)
        self.assertIn("Cubism 5 R5 运行时文件缺失或不完整", main)
        self.assertIn("Cubism 5 R5 运行时文件缺失或不完整", index)





class Live2DOfficialCubism5RuntimeContractTests(unittest.TestCase):
    def test_moc3_runtime_assets_and_renderer_route_are_present(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/live2d/moc3/runtime/<model_id>")', main)
        self.assertIn('@app.get("/api/models")', main)
        self.assertIn('"moc3_runtime":', main)
        self.assertIn('if version == "moc2":', main)
        self.assertIn('MOC3 的缩放和位置由 Cubism 5 Runtime 的模型矩阵实时处理', main)
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

    def test_moc3_size_position_and_crop_use_runtime_model_matrix(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static/js/display.js").read_text(encoding="utf-8")
        self.assertIn('runtimeWindow.live2dSetModelTransform(current.scale, current.x, current.y)', script)
        self.assertNotIn('moc3Frame.style.transform =', script)
        self.assertIn('window.live2dSetModelTransform=', (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8"))
        self.assertIn('if (live2dRuntimeVersion === "moc3") applyLive2DTransform()', script)
        self.assertIn('applyLive2DCrop();\n    applyLive2DTransform();', script)



class Live2DModelLoadFailureContractTests(unittest.TestCase):
    def test_moc3_runtime_reports_failed_model_fetches_and_texture_images(self):
        root = Path(__file__).resolve().parents[1]
        runtime_page = (root / "templates" / "live2d_moc3_runtime.html").read_text(encoding="utf-8")
        self.assertIn('type: "moc3-model-error"', runtime_page)
        self.assertIn('url.pathname.startsWith("/models/moc3/")', runtime_page)
        self.assertIn('if (!response.ok && isModelResource(requestUrl))', runtime_page)
        self.assertIn('target instanceof HTMLImageElement', runtime_page)
        self.assertIn('window.__moc3ModelUrl', runtime_page)

    def test_parent_display_rejects_moc3_resource_error_without_waiting_for_timeout(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('event.data.type === "moc3-model-error"', script)
        self.assertIn('reject(new Error((event.data.message || "MOC3 模型资源加载失败") + resource))', script)
        self.assertIn('event.data.modelUrl !== expectedUrl', script)
        self.assertIn('event.source !== moc3Frame.contentWindow', script)


class Live2DFourthPhaseIntegrationContractTests(unittest.TestCase):
    def test_combined_display_reuses_the_full_live2d_display_page(self):
        root = Path(__file__).resolve().parents[1]
        combined = (root / "templates" / "combined_display.html").read_text(encoding="utf-8")
        display = (root / "templates" / "display.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(combined.count('frame.src="/live2d/display?embedded=1"'), 2)
        self.assertIn('id="moc3-runtime-frame"', display)
        self.assertIn('id="live2d-viewport"', display)

    def test_tts_mouth_sync_bridges_both_runtimes(self):
        root = Path(__file__).resolve().parents[1]
        display_script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        moc3_runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn('runtimeWindow.live2dSetMouthOpen(Math.max(0, Math.min(1, value)))', display_script)
        self.assertIn('model.lipSyncValue = Math.max(0, Math.min(1, value))', display_script)
        self.assertIn('window.live2dSetMouthOpen=t=>e?.setMouthOpen(t)', moc3_runtime)
        self.assertIn("getParameterId(n).getString()", moc3_runtime)
        self.assertIn("i._lipSyncIds", moc3_runtime)
        self.assertIn("getParameterMinimumValue(n)", moc3_runtime)
        self.assertIn("getParameterMaximumValue(n)", moc3_runtime)
        self.assertIn("this._mouthOpenParameterIndices", moc3_runtime)
        self.assertIn('type:"moc3-mouth-sync-status"', moc3_runtime)
        self.assertIn("setParameterValueByIndex(t.index,t.min+(t.max-t.min)*this._mouthOpen)", moc3_runtime)
        self.assertIn('当前模型没有可识别的嘴型参数，TTS 嘴型同步不可用。', moc3_runtime)
        self.assertIn('parameterCount:e.getParameterCount()', moc3_runtime)
        self.assertIn('parameterIds:r.map(e=>e.id)', moc3_runtime)
        self.assertIn('declaredIds:n', moc3_runtime)
        self.assertIn('invalidDeclaredIds:a', moc3_runtime)
        self.assertIn('检测到可用嘴型参数：', moc3_runtime)
        for parameter_id in ('ParamMouthOpenY', 'PARAM_MOUTH_OPEN_Y', 'MouthOpenY'):
            self.assertIn(parameter_id, moc3_runtime)
        self.assertIn('function startMouthSync(analyser)', display_script)
        self.assertIn('analyser.getFloatTimeDomainData(floatSamples)', display_script)
        self.assertIn('const targetLevel = Math.max(0, Math.min(1, (rms - 0.008) * 11.5))', display_script)
        self.assertIn('const smoothing = targetLevel > smoothedLevel ? 0.55 : 0.2', display_script)
        self.assertIn('source.connect(analyser)', display_script)
        self.assertIn('analyser.connect(audioContext.destination)', display_script)

    def test_moc3_model_switch_waits_for_load_and_cancels_stale_requests(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('if (live2dRuntimeVersion === "moc3") {\n        modelSwitchPromise = loadMoc3Model(selectedModelId, token)', script)
        self.assertIn('const isStale = () => token !== null && token !== modelSwitchToken', script)
        self.assertIn('if (!loaded || isStale()) return false', script)
        self.assertIn('await modelSwitchPromise.catch(() => {})', script)
        self.assertIn('loadedModelId = modelId', script)

    def test_moc2_runtime_regression_path_remains_separate(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('if (live2dRuntimeVersion === "moc3") {\n      try {\n        const modelId = await loadDisplaySettings()', script)
        self.assertIn('window.loadlive2d(canvas.id, modelUrl)', script)
        self.assertIn('window.__live2dManager', script)
        self.assertIn('replaceLive2DModel(modelUrl)', script)
        self.assertIn('captureLive2DTransformBase()', script)
        self.assertIn('model.lipSyncValue = Math.max(0, Math.min(1, value))', script)


class Live2DReportedIssueRegressionTests(unittest.TestCase):
    def test_moc3_actions_and_expressions_are_loaded_from_cubism_model3_schema(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('selectedModelConfig.FileReferences', script)
        self.assertIn('moc3References.Motions', script)
        self.assertIn('motion.File', script)
        self.assertIn('moc3References ? moc3References.Expressions', script)
        self.assertIn('await loadModelActionConfig(modelId, token)', script)

    def test_moc3_action_controls_call_the_cubism5_runtime_bridge(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn('runtimeWindow.live2dPlayMotion(group, index)', script)
        self.assertIn('runtimeWindow.live2dSetExpression(name)', script)
        self.assertIn('runtimeWindow.live2dResetExpression()', script)
        self.assertIn('window.live2dPlayMotion=', runtime)
        self.assertIn('window.live2dSetExpression=', runtime)
        self.assertIn('window.live2dResetExpression=', runtime)

    def test_moc3_webgl_canvas_and_iframe_request_alpha_transparency(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        runtime_html = (root / "templates" / "live2d_moc3_runtime.html").read_text(encoding="utf-8")
        display_html = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('getContext(`webgl2`,{alpha:!0,premultipliedAlpha:!0,preserveDrawingBuffer:!1})', runtime)
        self.assertIn('e.clearColor(0,0,0,0)', runtime)
        self.assertIn('blendFunc(e.ONE,e.ONE_MINUS_SRC_ALPHA)', runtime)
        self.assertNotIn("reportAlphaDiagnostic", runtime)
        self.assertNotIn("moc3-alpha-diagnostic", script)
        self.assertIn('<meta name="color-scheme" content="dark">', runtime_html)
        self.assertNotIn('}Aa();var ja=!1;', runtime)
        self.assertIn('canvas { background-color: transparent !important; }', runtime_html)
        self.assertIn('background-color: transparent !important;', display_html)
        self.assertIn('allowtransparency="true"', display_html)


class Live2DMoc3FollowupAuditTests(unittest.TestCase):
    def test_moc3_expression_reset_availability_uses_model3_references(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('selectedModelConfig.FileReferences.Expressions', script)
        self.assertIn('Array.isArray(expressionEntries)', script)

    def test_model_action_config_loading_ignores_stale_model_switch_results(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('async function loadModelActionConfig(modelId, token = null)', script)
        self.assertIn('if (token !== null && token !== modelSwitchToken) return false;', script)
        self.assertIn('loadModelActionConfig(selectedModelId, token)', script)
        self.assertIn('loadModelActionConfig(modelId, token)', script)

    def test_moc3_runtime_html_contains_valid_separate_canvas_css_rule(self):
        root = Path(__file__).resolve().parents[1]
        runtime_html = (root / "templates" / "live2d_moc3_runtime.html").read_text(encoding="utf-8")
        self.assertIn('body { position: relative; background-color: transparent !important; }\n    canvas', runtime_html)
        self.assertNotIn('}\\n    canvas', runtime_html)


class Live2DMoc3MotionPlaybackRegressionTests(unittest.TestCase):
    def test_moc3_motion_bridge_loads_missing_motion_into_runtime_cache(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static" / "js" / "live2d" / "moc3" / "runtime.js").read_text(encoding="utf-8")
        self.assertIn("window.live2dPlayMotion=async", runtime)
        self.assertIn("r._modelSetting.getMotionFileName(t,i)", runtime)
        self.assertIn("r.loadMotion(l,l.byteLength,a,null,null,r._modelSetting,t,i,r._motionConsistency)", runtime)
        self.assertIn("r._motions.set(a,o)", runtime)
        self.assertIn("r.startMotion(t,i,3)!==-1", runtime)

    def test_display_waits_for_async_moc3_motion_playback_result(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn("async function playSelectedModelMotion()", script)
        self.assertIn("await runtimeWindow.live2dPlayMotion(group, index)", script)


class Live2DMoc3MouthSyncSettingsTests(unittest.TestCase):
    def test_display_settings_show_live_moc3_mouth_sync_validation(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static/js/display.js").read_text(encoding="utf-8")
        self.assertIn('id="moc3-mouth-sync-status"', page)
        self.assertIn('type !== "moc3-mouth-sync-status"', script)
        self.assertIn("当前模型无法进行 TTS 嘴型同步。", script)
        self.assertIn("支持 TTS 嘴型同步", script)
        self.assertIn('setMouthSyncStatus("正在检测当前模型的 TTS 嘴型同步参数…", "checking")', script)

    def test_moc3_parameter_validation_prefers_model3_lipsync_group(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn("i._lipSyncIds", runtime)
        self.assertIn('"model3-lipsync"', runtime)
        self.assertIn('"compatibility-after-invalid-lipsync"', runtime)
        self.assertIn('"ParamMouthOpenY"', runtime)
        self.assertIn("getParameterMinimumValue(n)", runtime)
        self.assertIn("getParameterMaximumValue(n)", runtime)
        self.assertIn("invalidDeclaredIds", runtime)



class Live2DMotionShortcutFeatureTests(unittest.TestCase):
    def test_shortcuts_are_persisted_per_model_and_profile(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('"motion_shortcuts_by_profile": raw.get("motion_shortcuts_by_profile")', main)
        self.assertIn('shortcut_profiles[profile] = preferences.pop("motion_shortcuts", {})', main)
        self.assertIn('preferences["motion_shortcuts"] = shortcut_profiles.get(preferences["profile"], {})', main)
        self.assertIn('快捷键不能重复绑定：', main)
        self.assertIn('F(?:[1-9]|1[0-2])', main)

    def test_moc3_shortcuts_accept_default_empty_motion_group(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn('if (not action_group and version != "moc3")', main)
        self.assertIn('action_group, action_index = action.rsplit("::", 1)', main)

    def test_moc3_save_forces_top_level_display_reload(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('if (live2dRuntimeVersion === "moc3")', script)
        self.assertIn('refreshUrl.searchParams.set("_live2d_refresh"', script)
        self.assertIn('window.location.replace(refreshUrl.toString())', script)
        self.assertIn('display.js?v=20261104-moc3-save-refresh', page)

    def test_display_adds_hotkeys_without_replacing_existing_motion_controls(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "templates" / "display.html").read_text(encoding="utf-8")
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('id="model-motion-select"', page)
        self.assertIn('id="model-motion-play"', page)
        self.assertIn('id="model-motion-shortcuts"', page)
        self.assertIn('function renderMotionShortcutEditor()', script)
        self.assertIn('function normalizeMotionShortcut(key)', script)
        self.assertIn('function isShortcutTypingTarget(target)', script)
        self.assertIn('document.addEventListener("keydown"', script)
        self.assertIn('motion_shortcuts: motionShortcutBindings', script)
        self.assertIn('await runtimeWindow.live2dPlayMotion(group, index)', script)

    def test_shortcut_editor_has_clear_and_duplicate_protection(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static" / "js" / "display.js").read_text(encoding="utf-8")
        self.assertIn('clear.textContent = "清除"', script)
        self.assertIn('快捷键 " + key + " 已绑定到其他动作', script)
        self.assertIn('delete motionShortcutBindings[action]', script)
        self.assertIn('event.ctrlKey || event.altKey || event.metaKey', script)


class Live2DHitAreaMotionMappingTests(unittest.TestCase):
    def test_moc3_custom_hit_areas_resolve_motion_group_by_convention(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn("getHitAreasCount()", runtime)
        self.assertIn("getHitAreaName(i)", runtime)
        self.assertIn("n.hitTest(a,e,t)", runtime)
        self.assertIn("a.startsWith(`Touch`)?`Tap${a}`", runtime)
        self.assertIn("getMotionCount(o)>0", runtime)
        self.assertIn("n.startRandomMotion(o,2,this.finishedMotion,this.beganMotion)", runtime)

    def test_standard_head_and_body_tap_fallbacks_remain(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn('if(a===`Head`)', runtime)
        self.assertIn('n.setRandomExpression()', runtime)
        self.assertIn('if(a===`Body`)', runtime)


class Live2DModelCapabilityDiagnosticsTests(unittest.TestCase):
    def test_capability_panel_reports_runtime_features_and_invalid_resources(self):
        root = Path(__file__).resolve().parents[1]
        page = (root / "templates/display.html").read_text(encoding="utf-8")
        script = (root / "static/js/display.js").read_text(encoding="utf-8")
        self.assertIn('id="model-capabilities"', page)
        self.assertIn('async function diagnoseModelCapabilities(config, modelId, token = null)', script)
        for label in ("Idle 动作", "可播放动作", "表情", "物理配置", "触摸区域", "TTS 嘴型同步", "无效资源"):
            self.assertIn(label, script)
        self.assertIn('Array.isArray(data.PhysicsSettings) && data.PhysicsSettings.length > 0', script)
        self.assertIn('getParamIndex(id) >= 0', script)
        self.assertIn('event.data.supported', script)

    def test_capability_checks_ignore_stale_model_switches(self):
        root = Path(__file__).resolve().parents[1]
        script = (root / "static/js/display.js").read_text(encoding="utf-8")
        self.assertIn('const isStale = () => token !== null && token !== modelSwitchToken', script)
        self.assertIn('await diagnoseModelCapabilities(config, modelId, token)', script)
        self.assertIn('if (isStale()) return;', script)


class Live2DMoc3MotionMetadataRecoveryTests(unittest.TestCase):
    def test_motion_loader_repairs_exported_metadata_counts_before_consistency_check(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn("function normalizeMotion3Consistency(buffer,byteLength)", runtime)
        self.assertIn("meta.TotalSegmentCount=segmentTotal", runtime)
        self.assertIn("meta.TotalPointCount=pointTotal", runtime)
        self.assertIn("meta.CurveCount=curves.length", runtime)
        self.assertIn("if(c){let n=normalizeMotion3Consistency(e,t);e=n.buffer,t=n.byteLength}", runtime)
        self.assertIn("_n.create(e,t,r,i,c)", runtime)

    def test_motion_metadata_recovery_validates_segment_encoding_before_rewriting(self):
        root = Path(__file__).resolve().parents[1]
        runtime = (root / "static/js/live2d/moc3/runtime.js").read_text(encoding="utf-8")
        self.assertIn("type===0||type===2||type===3", runtime)
        self.assertIn("type===1", runtime)
        self.assertIn("index+6>=segments.length", runtime)
        self.assertIn("return{buffer,byteLength}", runtime)

class BilibiliStartupAutoConnectTests(unittest.TestCase):
    def test_startup_reuses_saved_room_authenticated_connection_flow(self):
        root = Path(__file__).resolve().parents[1]
        main = (root / "main.py").read_text(encoding="utf-8")
        start = main.index("def _restore_saved_live_connection() -> None:")
        end = main.index("\n\n@app.get(\"/\")", start)
        startup = main[start:end]

        self.assertIn('_connect_saved_room("系统启动")', startup)
        self.assertIn("已保存房间号", startup)
        self.assertNotIn("不会在程序启动时自动连接", startup)

        connect_start = main.index("def _connect_saved_room(source: str)")
        connect_end = main.index("\n\ndef _restore_saved_live_connection", connect_start)
        connect_flow = main[connect_start:connect_end]
        self.assertIn("validate_cookie_file", main)
        self.assertIn("LIVE_CLIENT.start(room_id, cookies)", connect_flow)
        self.assertIn("if room_id <= 0:", connect_flow)
        self.assertIn('current_state in {"connected", "connecting", "reconnecting"}', connect_flow)


if __name__ == "__main__":
    unittest.main()
