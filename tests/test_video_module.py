# -*- coding: utf-8 -*-
"""Offline regression tests for independent video module lifecycle."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import modules.video.service as video_service
from core.config_manager import ConfigManager


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
        video_service.current_bv = None
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
        video_service.current_bv = self.old_state["current_bv"]
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
                video_service.configure_video_module(
                    {"chrome": {"profile_dir": "profile"}, "requests": {}},
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
        finally:
            video_service.COOKIE_VALUES = old["cookie_values"]
            video_service.VIDEO_CONFIG = old["config"]
            video_service.roomid = old["roomid"]
            video_service.CHROME_PROFILE = old["profile"]
            video_service.ADMIN_UIDS = old["admin_uids"]
            video_service.OBS_CONFIG = old["obs"]

    async def test_legacy_room_config_migrates_limits_once_without_changing_room(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_dir = root / "config"
            manager = ConfigManager(config_dir)
            manager.save("video", {
                "requests": {
                    "require_fans_medal": False,
                    "cooldown_seconds": 60,
                    "queue_limit": 30,
                    "max_video_duration_seconds": 300,
                }
            })
            (root / "room_config.txt").write_text(
                "ROOM_ID=22851631\n"
                "REQUIRE_FAN_MEDAL=1\n"
                "MAX_VIDEO_DURATION_SEC=240\n"
                "COOLDOWN_SEC=90\n"
                "MAX_QUEUE_SIZE=20\n",
                encoding="utf-8",
            )
            migrated = video_service.migrate_legacy_video_room_config(root, manager)
            config = manager.load("video", {})
            self.assertEqual(migrated["require_fans_medal"], True)
            self.assertEqual(config["requests"]["require_fans_medal"], True)
            self.assertEqual(config["requests"]["max_video_duration_seconds"], 240)
            self.assertEqual(config["requests"]["cooldown_seconds"], 90)
            self.assertEqual(config["requests"]["queue_limit"], 20)
            self.assertNotIn("room_id", config)
            (root / "room_config.txt").write_text("REQUIRE_FAN_MEDAL=0\n", encoding="utf-8")
            self.assertEqual(video_service.migrate_legacy_video_room_config(root, manager), {})
            self.assertTrue(manager.load("video", {})["requests"]["require_fans_medal"])

    async def test_legacy_obs_migration_keeps_password_out_of_plain_config(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = ConfigManager(root / "config")
            (root / "obs_config.txt").write_text(
                "host=192.168.1.3\n"
                "port=4455\n"
                "password=must-not-be-migrated\n"
                "source_name=b站视频播放器\n",
                encoding="utf-8",
            )
            migrated = video_service.migrate_legacy_video_obs_config(root, manager)
            config = manager.load("obs", {})
            self.assertEqual(migrated["host"], "192.168.1.3")
            self.assertEqual(migrated["port"], 4455)
            self.assertEqual(config["source_name"], "b站视频播放器")
            self.assertNotIn("password", config)
            (root / "obs_config.txt").write_text("host=127.0.0.1\n", encoding="utf-8")
            self.assertEqual(video_service.migrate_legacy_video_obs_config(root, manager), {})
            self.assertEqual(manager.load("obs", {})["host"], "192.168.1.3")

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
