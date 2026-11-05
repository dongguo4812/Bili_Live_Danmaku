import re
import unittest
from pathlib import Path


class AccessCenterModuleControlTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.page = (root / "templates" / "index.html").read_text(encoding="utf-8")

    def test_unified_control_panel_is_below_bilibili_connection(self):
        connection = self.page.index("<h2>B站登录与直播间连接</h2>")
        controls = self.page.index('id="module-controls"')
        music_details = self.page.index("<h2>歌曲点播模块</h2>")
        self.assertLess(connection, controls)
        self.assertLess(controls, music_details)

    def test_bilibili_connection_uses_one_connect_disconnect_toggle(self):
        self.assertEqual(self.page.count('id="connection-toggle"'), 1)
        self.assertNotIn('id="connect-live"', self.page)
        self.assertNotIn('id="disconnect-live"', self.page)
        self.assertIn('connectionActive ? "断开连接" : "连接直播间"', self.page)
        self.assertIn('["connecting", "reconnecting", "stopping"].includes(data.bilibili.connection_state)', self.page)
        self.assertIn('stopping: "断开连接中"', self.page)
        self.assertRegex(self.page, r'connectionStopping\s*\? "断开连接中…"', self.page)
        self.assertIn('button.dataset.active === "true"', self.page)
        self.assertIn('api("/api/bilibili/connect"', self.page)
        self.assertIn('api("/api/bilibili/disconnect"', self.page)

    def test_each_lifecycle_pair_uses_one_toggle_button(self):
        for module in ("music", "video", "voice"):
            with self.subTest(module=module):
                self.assertEqual(self.page.count(f'id="{module}-toggle-lifecycle"'), 1)
                self.assertEqual(self.page.count(f'id="{module}-toggle-pause"'), 1)
                self.assertNotIn(f'id="{module}-start"', self.page)
                self.assertNotIn(f'id="{module}-close"', self.page)
                self.assertNotIn(f'id="{module}-pause"', self.page)
                self.assertNotIn(f'id="{module}-resume"', self.page)

    def test_runtime_status_notice_excludes_live2d_messages(self):
        self.assertIn('$("status-message").textContent = data.bilibili.message;', self.page)
        self.assertNotIn('$("status-message").textContent = data.bilibili.message + " " + data.live2d.message;', self.page)

    def test_live2d_has_a_single_enable_disable_toggle(self):
        self.assertEqual(self.page.count('id="live2d-toggle"'), 1)
        self.assertIn('setLive2DEnabled(!currentlyEnabled)', self.page)
        self.assertIn('id="live2d-control-state"', self.page)

    def test_toggle_actions_follow_actual_module_state(self):
        self.assertIn('(["stopped", "error"].includes(state) ? "start" : "close")', self.page)
        self.assertIn('(state === "running" ? "pause" : state === "paused" ? "resume" : null)', self.page)
        self.assertIn('moduleStates.music = music.state || "stopped"', self.page)
        self.assertIn('moduleStates.video = video.state || "stopped"', self.page)
        self.assertIn('moduleStates.voice = voice.state || "stopped"', self.page)
        self.assertIn('lifecycle.disabled = busy', self.page)
        self.assertIn('pause.disabled = busy || !["running", "paused"].includes(state)', self.page)


    def test_module_statuses_have_distinct_color_states(self):
        root = Path(__file__).resolve().parents[1]
        css = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for state in ("running", "enabled", "paused", "stopped", "disabled", "error", "starting", "unknown"):
            with self.subTest(state=state):
                self.assertIn(f'data-state="{state}"', self.page + css)
                self.assertIn(f'.status-pill[data-state="{state}"]', css)

    def test_login_and_connection_statuses_have_distinct_colors(self):
        self.assertIn('id="login-state" data-state="unknown"', self.page)
        self.assertIn('id="connection-state" data-state="unknown"', self.page)
        # The former top-level OBS/Live2D runtime status was intentionally removed;
        # Live2D runtime state is shown in its own module section instead.
        self.assertNotIn('id="runtime-state"', self.page)
        self.assertIn('dataset.state = data.bilibili.logged_in ? "logged_in" : "logged_out"', self.page)
        self.assertIn('data.bilibili.connected ? "connected"', self.page)
        root = Path(__file__).resolve().parents[1]
        css = (root / "static" / "css" / "style.css").read_text(encoding="utf-8")
        for state in ("logged_in", "logged_out", "connected", "disconnected",
                      "connecting", "reconnecting", "stopping"):
            with self.subTest(state=state):
                self.assertIn(f'.status-grid strong[data-state="{state}"]', css)

    def test_detailed_module_sections_do_not_duplicate_lifecycle_controls(self):
        details = self.page[self.page.index("<h2>歌曲点播模块</h2>"):]
        for module in ("music", "video", "voice"):
            with self.subTest(module=module):
                self.assertNotIn(f'id="{module}-toggle-lifecycle"', details)
                self.assertNotIn(f'id="{module}-toggle-pause"', details)

    def test_voice_detail_page_has_no_duplicate_lifecycle_controls(self):
        root = Path(__file__).resolve().parents[1]
        voice_page = (root / "templates" / "voice.html").read_text(encoding="utf-8")
        self.assertNotIn("data-module-action", voice_page)
        self.assertNotIn("/api/modules/voice/", voice_page)
        self.assertIn("功能启停控制", voice_page)
        self.assertIn("/api/voice/tts/settings", voice_page)
        self.assertIn("/api/voice/tts/synthesize", voice_page)

    def test_access_center_has_no_obsolete_lifecycle_handlers(self):
        for obsolete in ("videoModuleAction", "musicModuleAction", "window.moduleAction", "async function moduleAction"):
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, self.page)

    def test_unused_legacy_live2d_preview_assets_are_removed(self):
        root = Path(__file__).resolve().parents[1]
        preview = root / "static" / "live2d_preview"
        self.assertFalse((preview / "chiaki_kitty.json").exists())
        self.assertFalse((preview / "chiaki_kitty.png").exists())
        for folder in (root / "templates", root / "static", root / "main.py"):
            files = [folder] if folder.is_file() else folder.rglob("*")
            for path in files:
                if path.is_file() and path.suffix.lower() in {".html", ".js", ".css", ".py"}:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    self.assertNotIn("chiaki_kitty", content)

    def test_gitignore_keeps_secret_rules_without_duplicate_entries(self):
        root = Path(__file__).resolve().parents[1]
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
        entries = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual(len(entries), len(set(entries)))
        for entry in ("credentials/", "bili_cookie.txt", "obs_config.txt", "uid.txt", "chrome_profile/"):
            with self.subTest(entry=entry):
                self.assertIn(entry, entries)

    def test_control_panel_has_no_duplicate_element_ids(self):
        ids = re.findall(r'\bid="([^"]+)"', self.page)
        self.assertEqual(len(ids), len(set(ids)))


    def test_access_center_hides_obsolete_danmaku_display_link(self):
        connection = self.page[self.page.index("<h2>B站登录与直播间连接</h2>"):self.page.index('<section class="panel module-control-panel"')]
        self.assertNotIn("/danmaku/display", connection)
        self.assertNotIn("弹幕 OBS 展示页", connection)

    def test_access_center_removes_redundant_music_and_video_controls(self):
        music = self.page[self.page.index("<h2>歌曲点播模块</h2>"):self.page.index("<h2>视频点播模块</h2>")]
        video = self.page[self.page.index("<h2>视频点播模块</h2>"):self.page.index('<section class="panel" id="live2d-settings">')]
        self.assertNotIn("music-toggle-request", music)
        self.assertNotIn("music-request-state", music)
        self.assertNotIn("关闭观众点歌", music)
        self.assertNotIn("点歌开关：", music)
        self.assertNotIn("video-queue-count", video)
        self.assertIn('id="video-queue"', video)
        self.assertNotIn('$("video-queue-count")', self.page)


    def test_music_and_video_sidebar_links_open_in_new_tabs(self):
        self.assertIn('<a href="/music" target="_blank" rel="noopener noreferrer">歌曲点播</a>', self.page)
        self.assertIn('<a href="/video" target="_blank" rel="noopener noreferrer">视频点播</a>', self.page)


if __name__ == "__main__":
    unittest.main()
