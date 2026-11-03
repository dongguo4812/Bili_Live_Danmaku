# -*- coding: utf-8 -*-
"""核心基础设施的轻量回归测试。"""
from __future__ import annotations

import asyncio
import os
import base64
import json
import tempfile
import unittest
from pathlib import Path

from core.config_manager import ConfigError, ConfigManager
import core.config_manager as config_manager_module
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
    def test_rejects_secret_fields_and_common_casing_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            secret_payloads = [
                {"lx_url": "http://127.0.0.1", "api_key": "secret"},
                {"apiKey": "secret"},
                {"APIKey": "secret"},
                {"accessToken": "secret"},
                {"Authorization": "Bearer secret"},
                {"SESSDATA": "secret"},
                {"biliJct": "secret"},
                {"sessData": "secret"},
                {"acTimeValue": "secret"},
                {"buvid3": "secret"},
                {"buvid4": "secret"},
                {"cookieValue": "secret"},
                {"nested": [{"refresh_token": "secret"}]},
            ]
            for payload in secret_payloads:
                with self.subTest(payload=payload):
                    with self.assertRaises(ConfigError):
                        manager.save("music", payload)

    def test_round_trip_plain_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory))
            manager.save("live", {"room_id": 123456, "reconnect": True})
            self.assertEqual(manager.load("live")["room_id"], 123456)


    def test_default_and_local_configuration_are_separated_and_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "default"
            local = root / "local"
            legacy = root / "config"
            defaults.mkdir()
            local.mkdir()
            legacy.mkdir()
            (defaults / "music.json").write_text(
                json.dumps({
                    "requests": {
                        "cooldown_seconds": 10,
                        "queue_limit": 100,
                        "require_fans_medal": False,
                    }
                }),
                encoding="utf-8",
            )
            # 模拟升级前的旧点歌配置：0 表示要求粉丝牌。
            (legacy / "music.json").write_text(
                json.dumps({"requests": {"cooldown_seconds": 25, "require_fans_medal": 0}}),
                encoding="utf-8",
            )
            with (
                unittest.mock.patch.object(config_manager_module, "LOCAL_CONFIG_DIR", local),
                unittest.mock.patch.object(config_manager_module, "DEFAULT_CONFIG_DIR", defaults),
                unittest.mock.patch.object(config_manager_module, "CONFIG_ROOT", legacy),
            ):
                manager = ConfigManager()
                loaded = manager.load("music")
                self.assertEqual(loaded["requests"]["cooldown_seconds"], 25)
                self.assertEqual(loaded["requests"]["queue_limit"], 100)
                self.assertIs(loaded["requests"]["require_fans_medal"], True)
                saved = json.loads((local / "music.json").read_text(encoding="utf-8"))
                self.assertIs(saved["requests"]["require_fans_medal"], True)

    def test_new_local_configuration_uses_boolean_default_without_medal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            defaults = root / "default"
            local = root / "local"
            legacy = root / "config"
            defaults.mkdir()
            (defaults / "music.json").write_text(
                json.dumps({"requests": {"require_fans_medal": False}}),
                encoding="utf-8",
            )
            with (
                unittest.mock.patch.object(config_manager_module, "LOCAL_CONFIG_DIR", local),
                unittest.mock.patch.object(config_manager_module, "DEFAULT_CONFIG_DIR", defaults),
                unittest.mock.patch.object(config_manager_module, "CONFIG_ROOT", legacy),
            ):
                loaded = ConfigManager().load("music")
                self.assertIs(loaded["requests"]["require_fans_medal"], False)
                self.assertTrue((local / "music.json").is_file())

    def test_legacy_config_migration_support_is_retired(self):
        self.assertFalse(hasattr(ConfigManager, "migrate_legacy_config"))


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

    def test_credential_name_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory))
            for name in ("../outside", "a/b", "a\\\\b", ""):
                with self.subTest(name=name):
                    with self.assertRaises(Exception):
                        store.save(name, {"secret": "must-not-escape"})

    def test_corrupt_credential_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory))
            path = store.save("obs", {"password": "test-password"})
            path.write_text('{"format":"unknown-v99","payload":"AAAA"}', encoding="utf-8")
            with self.assertRaises(Exception):
                store.load("obs")

    @unittest.skipUnless(os.name == "nt", "真实 DPAPI 测试仅在 Windows CI 运行")
    def test_windows_dpapi_file_is_encrypted_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(Path(directory))
            secret = "windows-dpapi-regression-secret"
            path = store.save("obs", {"password": secret})
            raw = path.read_bytes()
            stored = json.loads(raw.decode("utf-8"))
            self.assertEqual(stored.get("format"), "dpapi-v1")
            encrypted = base64.b64decode(stored["payload"], validate=True)
            self.assertNotIn(secret.encode("utf-8"), raw)
            self.assertNotIn(secret.encode("utf-8"), encrypted)
            self.assertEqual(store.load("obs"), {"password": secret})


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

        await manager.close("video")
        await manager.on_connection_restored()

        self.assertEqual(manager.list_status()["music"]["state"], "running")
        self.assertEqual(manager.list_status()["video"]["state"], "stopped")
        self.assertNotIn("resume", video.calls)


if __name__ == "__main__":
    unittest.main()
