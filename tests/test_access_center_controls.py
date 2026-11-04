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

    def test_each_lifecycle_pair_uses_one_toggle_button(self):
        for module in ("music", "video", "voice"):
            with self.subTest(module=module):
                self.assertEqual(self.page.count(f'id="{module}-toggle-lifecycle"'), 1)
                self.assertEqual(self.page.count(f'id="{module}-toggle-pause"'), 1)
                self.assertNotIn(f'id="{module}-start"', self.page)
                self.assertNotIn(f'id="{module}-close"', self.page)
                self.assertNotIn(f'id="{module}-pause"', self.page)
                self.assertNotIn(f'id="{module}-resume"', self.page)

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

    def test_control_panel_has_no_duplicate_element_ids(self):
        ids = re.findall(r'\\bid="([^"]+)"', self.page)
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
