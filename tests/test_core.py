# -*- coding: utf-8 -*-
"""核心基础设施的轻量回归测试。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from core.config_manager import ConfigError, ConfigManager
from core.credential_store import CredentialStore
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
            with self.assertRaises(ConfigError):
                manager.save("music", {"apiKey": "must-not-be-saved"})
            with self.assertRaises(ConfigError):
                manager.save("music", {"APIKey": "must-not-be-saved"})
            with self.assertRaises(ConfigError):
                manager.save("music", {"accessToken": "must-not-be-saved"})
            with self.assertRaises(ConfigError):
                manager.save("music", {"Authorization": "Bearer must-not-be-saved"})

    def test_round_trip_plain_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            manager.save("live", {"room_id": 123456, "reconnect": True})
            self.assertEqual(manager.load("live")["room_id"], 123456)


    def test_last_saved_user_room_takes_precedence_over_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            legacy = root / "config.json"
            legacy.write_text(
                __import__("json").dumps({"bilibili": {"room_id": 111}}),
                encoding="utf-8",
            )
            (root / "user_settings.json").write_text(
                __import__("json").dumps({"bilibili": {"room_id": 222}}),
                encoding="utf-8",
            )

            manager.migrate_legacy_config(legacy)
            self.assertEqual(manager.load("live")["room_id"], 0)

    def test_legacy_config_migrates_once_without_plain_obs_password(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            manager.save("app", {"host": "127.0.0.1", "port": 8768, "debug": False})
            manager.save("live", {"room_id": 22851631, "connect_on_start": False})
            manager.save("voice", {"voice": "zh-CN-XiaoxiaoNeural"})

            legacy = {
                "app": {"host": "127.0.0.2", "port": 9000},
                "bilibili": {"room_id": 777, "reconnect": True},
                "tts": {"voice": "zh-CN-YunxiNeural", "rate": 12},
                "obs_websocket": {
                    "host": "192.168.1.3",
                    "port": 4455,
                    "password": "must-not-be-copied",
                    "password_file": "obs_config.txt",
                },
            }
            legacy_path = root / "config.json"
            legacy_path.write_text(__import__("json").dumps(legacy), encoding="utf-8")

            migrated = manager.migrate_legacy_config(legacy_path)
            self.assertIn("app", migrated)
            self.assertEqual(manager.load("app")["host"], "127.0.0.2")
            self.assertEqual(manager.load("live")["room_id"], 777)
            self.assertEqual(manager.load("voice")["voice"], "zh-CN-YunxiNeural")
            obs = manager.load("obs")
            self.assertEqual(obs["password_file"], "credentials/obs.json")
            self.assertNotIn("password", obs)

            manager.save("app", {"host": "127.0.0.9", "port": 9001})
            manager.migrate_legacy_config(legacy_path)
            self.assertEqual(manager.load("app")["host"], "127.0.0.9")


class CredentialStoreTests(unittest.TestCase):
    def test_round_trip_and_status_never_returns_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory))
            store.save("obs", {"password": "test-password"})
            self.assertEqual(store.load("obs"), {"password": "test-password"})
            self.assertEqual(
                store.status("obs"),
                {"configured": True, "file": "obs.json"},
            )


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

    async def test_manual_close_before_first_connection_suppresses_auto_start(self):
        manager = ModuleManager()
        live2d = FakeModule()
        manager.register("live2d", live2d, auto_start_on_connect=True)
        await manager.close("live2d")
        await manager.on_connection_restored()
        self.assertEqual(manager.list_status()["live2d"]["state"], "stopped")
        self.assertEqual(live2d.calls, [])

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
