# -*- coding: utf-8 -*-
"""核心基础设施的轻量回归测试。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.config_manager import ConfigError, ConfigManager
from core.module_manager import ModuleManager


class FakeModule:
    def __init__(self):
        self.calls = []

    async def start(self):
        self.calls.append("start")

    async def pause(self):
        self.calls.append("pause")

    async def resume(self):
        self.calls.append("resume")

    async def close(self):
        self.calls.append("close")


class ConfigManagerTests(unittest.TestCase):
    def test_rejects_secret_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            with self.assertRaises(ConfigError):
                manager.save("music", {"lx_url": "http://127.0.0.1", "api_key": "must-not-be-saved"})

    def test_round_trip_plain_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            manager.save("live", {"room_id": 123456, "reconnect": True})
            self.assertEqual(manager.load("live")["room_id"], 123456)


class ModuleManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_authenticated_connection_starts_only_opted_in_modules(self):
        manager = ModuleManager()
        live2d = FakeModule()
        music = FakeModule()
        manager.register("live2d", live2d, auto_start_on_connect=True)
        manager.register("music", music)
        await manager.on_connection_restored()
        self.assertEqual(manager.list_status()["live2d"]["state"], "running")
        self.assertEqual(manager.list_status()["music"]["state"], "stopped")
        self.assertEqual(live2d.calls, ["start"])
        self.assertEqual(music.calls, [])

    async def test_modules_are_independent_and_start_stopped(self):
        manager = ModuleManager()
        music = FakeModule()
        video = FakeModule()
        manager.register("music", music)
        manager.register("video", video)
        await manager.on_connection_restored()

        self.assertEqual(manager.list_status()["music"]["state"], "stopped")
        self.assertEqual(manager.list_status()["video"]["state"], "stopped")

        await manager.start("music")
        self.assertEqual(manager.list_status()["music"]["state"], "running")
        self.assertEqual(manager.list_status()["video"]["state"], "stopped")
        self.assertEqual(video.calls, [])

    async def test_manual_pause_during_disconnect_overrides_auto_resume(self):
        manager = ModuleManager()
        music = FakeModule()
        manager.register("music", music)
        await manager.on_connection_restored()
        await manager.start("music")
        await manager.on_connection_lost()
        self.assertEqual(manager.list_status()["music"]["state"], "paused")

        # 模块已因断线暂停；用户再次点击暂停，应阻止重连自动恢复。
        await manager.pause("music")
        await manager.on_connection_restored()
        self.assertEqual(manager.list_status()["music"]["state"], "paused")
        self.assertNotIn("resume", music.calls)

    async def test_reconnect_restores_only_unchanged_running_modules(self):
        manager = ModuleManager()
        music = FakeModule()
        video = FakeModule()
        manager.register("music", music)
        manager.register("video", video)
        await manager.on_connection_restored()

        await manager.start("music")
        await manager.start("video")
        await manager.pause("video")
        await manager.on_connection_lost()

        self.assertEqual(manager.list_status()["music"]["state"], "paused")
        self.assertEqual(manager.list_status()["video"]["state"], "paused")

        # 断线期间用户手动关闭视频；重连不得覆盖用户操作。
        await manager.close("video")
        await manager.on_connection_restored()

        self.assertEqual(manager.list_status()["music"]["state"], "running")
        self.assertEqual(manager.list_status()["video"]["state"], "stopped")
        self.assertNotIn("resume", video.calls)


if __name__ == "__main__":
    unittest.main()
