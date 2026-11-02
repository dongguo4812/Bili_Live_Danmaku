# -*- coding: utf-8 -*-
"""Offline regression tests for independent video module lifecycle."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import modules.video.service as video_service


class VideoModuleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_state = {
            "module_enabled": video_service.module_enabled,
            "module_paused": video_service.module_paused,
            "driver": video_service.driver,
            "current_bv": video_service.current_bv,
            "is_playing": video_service.is_playing,
            "video_queue": list(video_service.video_queue),
            "monitor": video_service.playback_monitor_task,
        }
        video_service.module_enabled = False
        video_service.module_paused = False
        video_service.driver = None
        video_service.current_bv = None
        video_service.is_playing = False
        video_service.video_queue.clear()
        video_service.playback_monitor_task = None
        self.module = object.__new__(video_service.VideoModuleFacade)
        self.module.config = {}
        self.module.cookie_provider = lambda: {}
        self.module.room_id = 123

    def tearDown(self):
        video_service.module_enabled = self.old_state["module_enabled"]
        video_service.module_paused = self.old_state["module_paused"]
        video_service.driver = self.old_state["driver"]
        video_service.current_bv = self.old_state["current_bv"]
        video_service.is_playing = self.old_state["is_playing"]
        video_service.video_queue.clear()
        video_service.video_queue.extend(self.old_state["video_queue"])
        video_service.playback_monitor_task = self.old_state["monitor"]

    async def test_disabled_module_ignores_danmaku(self):
        await video_service.on_dan_mu({
            "type": "danmaku",
            "uid": 123,
            "nickname": "viewer",
            "text": "点播 BV1xx411c7mD",
            "timestamp": 1,
        })
        self.assertEqual(video_service.video_queue, [])

    async def test_bv_command_pattern(self):
        self.assertIsNotNone(video_service.ORDER_PATTERN.fullmatch("点播 BV1xx411c7mD"))
        self.assertIsNone(video_service.ORDER_PATTERN.fullmatch("点播 https://www.bilibili.com/video/BV1xx411c7mD"))

    async def test_pause_and_resume_preserve_current_and_queue(self):
        video_service.module_enabled = True
        video_service.current_bv = "BV1xx411c7mD"
        video_service.is_playing = True
        video_service.driver = object()
        video_service.video_queue.append("BV1yy411c7mE")

        with patch.object(video_service, "configure_video_module"), \
             patch.object(video_service, "execute_script_sync", return_value=True):
            await self.module.pause()
            self.assertTrue(video_service.module_paused)
            self.assertEqual(video_service.current_bv, "BV1xx411c7mD")
            self.assertEqual(video_service.video_queue, ["BV1yy411c7mE"])

            await self.module.resume()
            self.assertFalse(video_service.module_paused)
            self.assertEqual(video_service.current_bv, "BV1xx411c7mD")
            self.assertEqual(video_service.video_queue, ["BV1yy411c7mE"])

    async def test_close_clears_queue_and_releases_browser_reference(self):
        video_service.module_enabled = True
        video_service.current_bv = "BV1xx411c7mD"
        video_service.is_playing = True
        video_service.driver = object()
        video_service.video_queue.append("BV1yy411c7mE")

        with patch.object(video_service, "_pause_video_for_close_sync", return_value=True), \
             patch.object(video_service, "_quit_browser_sync", return_value=None), \
             patch.object(video_service, "set_obs_visibility_async", new=AsyncMock(return_value=True)):
            await self.module.close()

        self.assertFalse(video_service.module_enabled)
        self.assertIsNone(video_service.driver)
        self.assertIsNone(video_service.current_bv)
        self.assertEqual(video_service.video_queue, [])


if __name__ == "__main__":
    unittest.main()
