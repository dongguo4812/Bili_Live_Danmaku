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
            "chrome_service": video_service.chrome_service,
            "shutdown_requested": video_service.VIDEO_SHUTDOWN_REQUESTED.is_set(),
            "current_bv": video_service.current_bv,
            "current_video_position": video_service.current_video_position,
            "is_playing": video_service.is_playing,
            "video_queue": list(video_service.video_queue),
            "monitor": video_service.playback_monitor_task,
            "last_order_time": dict(video_service.last_order_time),
            "pending_uids": set(video_service.pending_video_uids),
            "pending_bvs": set(video_service.pending_video_bvs),
            "require_medal": video_service.REQUIRE_FAN_MEDAL,
            "cooldown": video_service.COOLDOWN_SEC,
            "max_queue": video_service.MAX_QUEUE_SIZE,
            "session_generation": video_service.video_session_generation,
            "request_generation": video_service.video_request_generation,
        }
        video_service.module_enabled = False
        video_service.module_paused = False
        video_service.driver = None
        video_service.chrome_service = None
        video_service.VIDEO_SHUTDOWN_REQUESTED.clear()
        video_service.current_bv = None
        video_service.current_video_position = 0.0
        video_service.is_playing = False
        video_service.video_queue.clear()
        video_service.playback_monitor_task = None
        video_service.last_order_time.clear()
        video_service.pending_video_uids.clear()
        video_service.pending_video_bvs.clear()
        video_service.REQUIRE_FAN_MEDAL = False
        video_service.COOLDOWN_SEC = 60
        self.module = object.__new__(video_service.VideoModuleFacade)
        self.module.config = {}
        self.module.cookie_provider = lambda: {}
        self.module.room_id = 123

    def tearDown(self):
        video_service.module_enabled = self.old_state["module_enabled"]
        video_service.module_paused = self.old_state["module_paused"]
        video_service.driver = self.old_state["driver"]
        video_service.chrome_service = self.old_state["chrome_service"]
        if self.old_state["shutdown_requested"]:
            video_service.VIDEO_SHUTDOWN_REQUESTED.set()
        else:
            video_service.VIDEO_SHUTDOWN_REQUESTED.clear()
        video_service.current_bv = self.old_state["current_bv"]
        video_service.current_video_position = self.old_state["current_video_position"]
        video_service.is_playing = self.old_state["is_playing"]
        video_service.video_queue.clear()
        video_service.video_queue.extend(self.old_state["video_queue"])
        video_service.playback_monitor_task = self.old_state["monitor"]
        video_service.last_order_time.clear()
        video_service.last_order_time.update(self.old_state["last_order_time"])
        video_service.pending_video_uids.clear()
        video_service.pending_video_uids.update(self.old_state["pending_uids"])
        video_service.pending_video_bvs.clear()
        video_service.pending_video_bvs.update(self.old_state["pending_bvs"])
        video_service.REQUIRE_FAN_MEDAL = self.old_state["require_medal"]
        video_service.COOLDOWN_SEC = self.old_state["cooldown"]
        video_service.MAX_QUEUE_SIZE = self.old_state["max_queue"]
        video_service.video_session_generation = self.old_state["session_generation"]
        video_service.video_request_generation = self.old_state["request_generation"]

    async def test_create_browser_registers_service_before_webdriver_creation(self):
        class FakeService:
            process = None

        class FakeBrowser:
            def __init__(self, service):
                self.service = service
                self.timeout = None

            def set_page_load_timeout(self, timeout):
                self.timeout = timeout

        fake_service = FakeService()
        fake_browser = FakeBrowser(fake_service)
        original_driver = video_service.driver
        original_service = video_service.chrome_service
        try:
            def create_driver(*, service, options):
                self.assertIs(video_service.chrome_service, service)
                return fake_browser

            with patch.object(video_service, "ManagedChromeService", return_value=fake_service), \
                 patch.object(video_service.webdriver, "Chrome", side_effect=create_driver):
                result = video_service.create_browser()

            self.assertIs(result, fake_browser)
            self.assertIs(video_service.driver, fake_browser)
            self.assertIs(video_service.chrome_service, fake_service)
            self.assertEqual(fake_browser.timeout, video_service.PAGE_LOAD_TIMEOUT)
        finally:
            video_service.driver = original_driver
            video_service.chrome_service = original_service

    async def test_create_browser_has_30_second_hard_startup_timeout(self):
        import time

        class FakeService:
            process = None

        class FakeBrowser:
            def set_page_load_timeout(self, timeout):
                pass

        fake_service = FakeService()
        original_driver = video_service.driver
        original_service = video_service.chrome_service

        def delayed_create_driver(*, service, options):
            time.sleep(0.05)
            return FakeBrowser()

        try:
            with patch.object(video_service, "BROWSER_STARTUP_TIMEOUT", 0.01), \
                 patch.object(video_service, "ManagedChromeService", return_value=fake_service), \
                 patch.object(video_service.webdriver, "Chrome", side_effect=delayed_create_driver), \
                 patch.object(video_service, "_force_terminate_service_sync", return_value=True) as kill, \
                 patch.object(video_service, "_terminate_chrome_profile_processes_sync", return_value=([], [])):
                with self.assertRaisesRegex(TimeoutError, "超过 0.01 秒"):
                    video_service.create_browser()
                self.assertGreaterEqual(kill.call_count, 1)
                self.assertIsNone(video_service.driver)
                self.assertIsNone(video_service.chrome_service)
        finally:
            video_service.driver = original_driver
            video_service.chrome_service = original_service

    async def test_emergency_cleanup_can_find_service_before_driver_exists(self):
        class FakeProcess:
            pid = 43210

        class FakeService:
            process = FakeProcess()

        original_driver = video_service.driver
        original_service = video_service.chrome_service
        video_service.driver = None
        video_service.chrome_service = FakeService()
        with patch.object(video_service, "_force_terminate_service_sync", return_value=True) as kill, \
             patch.object(video_service, "_terminate_chrome_profile_processes_sync", return_value=([], [])):
            try:
                self.assertTrue(video_service.emergency_close_browser_sync())
                kill.assert_called_once()
                self.assertIsNone(video_service.chrome_service)
            finally:
                video_service.driver = original_driver
                video_service.chrome_service = original_service

    async def test_disabled_module_ignores_danmaku(self):
        await video_service.on_dan_mu({
            "type": "danmaku",
            "uid": 123,
            "nickname": "viewer",
            "text": "点播 BV1xx411c7mD",
            "timestamp": 1,
        })
        self.assertEqual(video_service.video_queue, [])

    async def test_first_viewer_request_is_not_blocked_by_process_uptime(self):
        video_service.module_enabled = True
        video_service.is_playing = True
        event = {
            "type": "danmaku", "uid": 1001, "nickname": "new-viewer",
            "text": "点播 BV1xx411c7mD", "timestamp": 1,
        }
        with patch.object(video_service.time, "monotonic", return_value=10), \
             patch.object(video_service, "ensure_video_info", new=AsyncMock(return_value=("title", 120))):
            await video_service.on_dan_mu(event)

        self.assertEqual(video_service.video_queue, ["BV1xx411c7mD"])
        self.assertEqual(video_service.last_order_time[1001], 10)

    async def test_open_video_page_restores_last_known_playback_position(self):
        class FakeDriver:
            def __init__(self):
                self.scripts = []
                self.opened_url = None

            def get(self, url):
                self.opened_url = url

            def execute_script(self, script, *args):
                self.scripts.append((script, args))
                return {"ok": True, "currentTime": args[0], "duration": 120}

        fake_driver = FakeDriver()
        original_driver = video_service.driver
        try:
            video_service.driver = fake_driver
            with patch.object(video_service, "ensure_browser_window"):
                video_service.open_video_page(
                    "BV1xx411c7mD",
                    "https://www.bilibili.com/video/BV1xx411c7mD",
                    37.5,
                )
            self.assertEqual(fake_driver.opened_url, "https://www.bilibili.com/video/BV1xx411c7mD")
            self.assertEqual(fake_driver.scripts[0][1], (37.5,))
        finally:
            video_service.driver = original_driver

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

    async def test_same_viewer_parallel_different_bv_requests_obey_cooldown(self):
        import asyncio
        video_service.module_enabled = True
        video_service.is_playing = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_info(bv):
            entered.set()
            await release.wait()
            return (f"title-{bv}", 120)

        first = {
            "type": "danmaku", "uid": 1001, "nickname": "viewer",
            "text": "点播 BV1xx411c7mD", "timestamp": 1,
        }
        second = {
            "type": "danmaku", "uid": 1001, "nickname": "viewer",
            "text": "点播 BV1yy411c7mE", "timestamp": 2,
        }
        with patch.object(video_service, "ensure_video_info", side_effect=delayed_info) as info:
            first_task = asyncio.create_task(video_service.on_dan_mu(first))
            await asyncio.wait_for(entered.wait(), timeout=3)
            await video_service.on_dan_mu(second)
            release.set()
            await asyncio.wait_for(first_task, timeout=5)

        self.assertEqual(info.await_count, 1)
        self.assertEqual(video_service.video_queue, ["BV1xx411c7mD"])
        self.assertNotIn(1001, video_service.pending_video_uids)

    async def test_parallel_same_bv_from_different_viewers_is_reserved_once(self):
        import asyncio
        video_service.module_enabled = True
        video_service.is_playing = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_info(bv):
            entered.set()
            await release.wait()
            return ("title", 120)

        event_a = {
            "type": "danmaku", "uid": 1001, "nickname": "viewer-a",
            "text": "点播 BV1xx411c7mD", "timestamp": 1,
        }
        event_b = {
            "type": "danmaku", "uid": 1002, "nickname": "viewer-b",
            "text": "点播 BV1xx411c7mD", "timestamp": 2,
        }
        with patch.object(video_service, "ensure_video_info", side_effect=delayed_info) as info:
            first_task = asyncio.create_task(video_service.on_dan_mu(event_a))
            await asyncio.wait_for(entered.wait(), timeout=3)
            await video_service.on_dan_mu(event_b)
            release.set()
            await asyncio.wait_for(first_task, timeout=5)

        self.assertEqual(info.await_count, 1)
        self.assertEqual(video_service.video_queue, ["BV1xx411c7mD"])
        self.assertFalse(video_service.pending_video_bvs)

    async def test_pending_requests_reserve_queue_capacity(self):
        import asyncio
        video_service.module_enabled = True
        video_service.is_playing = True
        video_service.MAX_QUEUE_SIZE = 1
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_info(bv):
            entered.set()
            await release.wait()
            return ("title", 120)

        event_a = {
            "type": "danmaku", "uid": 1001, "nickname": "viewer-a",
            "text": "点播 BV1xx411c7mD", "timestamp": 1,
        }
        event_b = {
            "type": "danmaku", "uid": 1002, "nickname": "viewer-b",
            "text": "点播 BV1yy411c7mE", "timestamp": 2,
        }
        with patch.object(video_service, "ensure_video_info", side_effect=delayed_info) as info:
            first_task = asyncio.create_task(video_service.on_dan_mu(event_a))
            await asyncio.wait_for(entered.wait(), timeout=3)
            await video_service.on_dan_mu(event_b)
            release.set()
            await asyncio.wait_for(first_task, timeout=5)

        self.assertEqual(info.await_count, 1)
        self.assertEqual(video_service.video_queue, ["BV1xx411c7mD"])

    async def test_queue_snapshot_is_async_and_contains_current_queue_state(self):
        video_service.module_enabled = True
        video_service.module_paused = True
        video_service.current_bv = "BV1xx411c7mD"
        video_service.video_queue.append("BV1yy411c7mE")
        snapshot = await self.module.queue_snapshot()
        self.assertTrue(snapshot["enabled"])
        self.assertTrue(snapshot["paused"])
        self.assertEqual(snapshot["current"]["bvid"], "BV1xx411c7mD")
        self.assertEqual(snapshot["queue"][0]["bvid"], "BV1yy411c7mE")

    async def test_configure_uses_unified_lowercase_cookie_fields(self):
        import tempfile
        from pathlib import Path
        old = {
            "cookie_values": dict(video_service.COOKIE_VALUES),
            "config": dict(video_service.VIDEO_CONFIG),
            "roomid": video_service.roomid,
            "profile": video_service.CHROME_PROFILE,
            "admin_uids": set(video_service.ADMIN_UIDS),
            "obs": dict(video_service.OBS_CONFIG),
        }
        try:
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(video_service, "BASE_DIR", Path(tmp)), \
                 patch.object(video_service, "load_obs_config", return_value={"enabled": False}):
                (Path(tmp) / "legacy-uid.txt").write_text("999999\\n", encoding="utf-8")
                video_service.configure_video_module(
                    {"chrome": {"profile_dir": "profile"}, "requests": {},
                     "admin_uids": [12345], "admins_file": "legacy-uid.txt"},
                    lambda: {
                        "sessdata": "session-value",
                        "bili_jct": "csrf-value",
                        "buvid3": "buvid-value",
                        "dedeuserid": "12345",
                    },
                    room_id=123,
                )
                self.assertEqual(video_service.COOKIE_VALUES["SESSDATA"], "session-value")
                self.assertEqual(video_service.COOKIE_VALUES["bili_jct"], "csrf-value")
                self.assertEqual(video_service.COOKIE_VALUES["buvid3"], "buvid-value")
                self.assertEqual(video_service.COOKIE_VALUES["DedeUserID"], "12345")
                self.assertEqual(video_service.ADMIN_UIDS, {12345})
                self.assertFalse(video_service.REQUIRE_FAN_MEDAL)
        finally:
            video_service.COOKIE_VALUES = old["cookie_values"]
            video_service.VIDEO_CONFIG = old["config"]
            video_service.roomid = old["roomid"]
            video_service.CHROME_PROFILE = old["profile"]
            video_service.ADMIN_UIDS = old["admin_uids"]
            video_service.OBS_CONFIG = old["obs"]

    async def test_inflight_order_is_discarded_after_pause_and_resume(self):
        import asyncio
        video_service.module_enabled = True
        video_service.is_playing = True
        entered = asyncio.Event()
        release = asyncio.Event()

        async def delayed_info(bv):
            entered.set()
            await release.wait()
            return ("title", 120)

        event = {
            "type": "danmaku", "uid": 1001, "nickname": "viewer",
            "text": "点播 BV1xx411c7mD", "timestamp": 1,
        }
        with patch.object(video_service, "ensure_video_info", side_effect=delayed_info), \
             patch.object(video_service, "configure_video_module"):
            task = asyncio.create_task(video_service.on_dan_mu(event))
            await asyncio.wait_for(entered.wait(), timeout=3)
            await self.module.pause()
            await self.module.resume()
            release.set()
            await asyncio.wait_for(task, timeout=5)

        self.assertEqual(video_service.video_queue, [])
        self.assertFalse(video_service.pending_video_uids)
        self.assertFalse(video_service.pending_video_bvs)

    async def test_stale_monitor_exits_before_touching_obs_or_new_browser(self):
        video_service.module_enabled = True
        video_service.video_session_generation = 8
        with patch.object(video_service, "set_obs_visibility_async", new=AsyncMock()) as obs:
            await video_service.monitor_video_end("BV1xx411c7mD", session_generation=7)
        obs.assert_not_awaited()





if __name__ == "__main__":
    unittest.main()
