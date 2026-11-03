# -*- coding: utf-8 -*-
"""核心基础设施的轻量回归测试。"""
from __future__ import annotations

import asyncio
import json
import os
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_last_saved_user_room_takes_precedence_over_legacy_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            legacy = root / "config.json"
            legacy.write_text(json.dumps({"bilibili": {"room_id": 111}}), encoding="utf-8")
            (root / "user_settings.json").write_text(
                json.dumps({"bilibili": {"room_id": 222}}), encoding="utf-8"
            )

            manager.migrate_legacy_config(legacy)
            self.assertEqual(manager.load("live")["room_id"], 0)

    def test_legacy_defaults_are_imported_but_user_edits_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            manager.save("app", {
                "host": "127.0.0.9", "port": 8768, "debug": False,
                "custom_option": "keep",
            })
            manager.save("live", {
                "room_id": 22851631, "reconnect": True,
                "reconnect_interval_seconds": 5, "connect_on_start": False,
            })
            manager.save("voice", {
                "enabled": True, "voice": "zh-CN-XiaoyiNeural",
                "rate": "+0%", "volume": "+0%",
            })

            legacy = {
                "app": {"host": "127.0.0.2", "port": 9000},
                "bilibili": {"room_id": 777, "reconnect": False},
                "tts": {"voice": "zh-CN-YunxiNeural", "rate": "+10%"},
                "obs_websocket": {
                    "host": "192.168.1.3", "port": 4455,
                    "password": "must-not-be-copied",
                    "password_file": "obs_config.txt",
                },
            }
            legacy_path = root / "config.json"
            legacy_path.write_text(json.dumps(legacy), encoding="utf-8")

            migrated = manager.migrate_legacy_config(legacy_path)
            self.assertIn("app", migrated)
            app = manager.load("app")
            self.assertEqual(app["host"], "127.0.0.9")
            self.assertEqual(app["port"], 9000)
            self.assertEqual(app["custom_option"], "keep")
            live = manager.load("live")
            self.assertEqual(live["room_id"], 22851631)
            self.assertFalse(live["reconnect"])
            voice = manager.load("voice")
            self.assertEqual(voice["voice"], "zh-CN-XiaoyiNeural")
            self.assertEqual(voice["rate"], "+10%")
            obs = manager.load("obs")
            self.assertEqual(obs["password_file"], "credentials/obs.json")
            self.assertNotIn("password", obs)

            manager.save("app", {**app, "host": "127.0.0.8"})
            manager.migrate_legacy_config(legacy_path)
            self.assertEqual(manager.load("app")["host"], "127.0.0.8")

    def test_corrupt_legacy_json_is_preserved_and_not_marked_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            legacy = root / "config.json"
            original = b'{"app": {"host": '
            legacy.write_bytes(original)

            with self.assertRaises(ConfigError):
                manager.migrate_legacy_config(legacy)

            self.assertEqual(legacy.read_bytes(), original)
            self.assertFalse((config_dir / ".legacy_config_migrated_v1.json").exists())
            self.assertFalse((config_dir / ".legacy_config_migration_v1.json").exists())

    def test_interrupted_migration_retry_preserves_edits_made_after_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            legacy = root / "config.json"
            legacy.write_text(json.dumps({
                "app": {"host": "127.0.0.2", "port": 9000},
                "tts": {"voice": "zh-CN-YunxiNeural"},
            }), encoding="utf-8")

            original_save = manager.save
            calls = {"count": 0}

            def fail_on_second_save(name, data):
                calls["count"] += 1
                if calls["count"] == 2:
                    raise OSError("simulated interruption")
                return original_save(name, data)

            with patch.object(manager, "save", side_effect=fail_on_second_save):
                with self.assertRaises(OSError):
                    manager.migrate_legacy_config(legacy)

            app = manager.load("app")
            self.assertEqual(app["host"], "127.0.0.2")
            manager.save("app", {**app, "host": "127.0.0.8"})

            manager.migrate_legacy_config(legacy)
            self.assertEqual(manager.load("app")["host"], "127.0.0.8")
            self.assertEqual(manager.load("app")["port"], 9000)
            self.assertEqual(manager.load("voice")["voice"], "zh-CN-YunxiNeural")
            self.assertTrue((config_dir / ".legacy_config_migrated_v1.json").is_file())
            self.assertFalse((config_dir / ".legacy_config_migration_v1.json").exists())

    def test_legacy_sensitive_values_never_enter_plain_config_or_migration_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            manager = ConfigManager(config_dir)
            legacy = root / "config.json"
            legacy.write_text(json.dumps({
                "app": {"host": "127.0.0.2", "nested": {"SESSDATA": "cookie-secret"}},
                "obs_websocket": {"host": "192.168.1.3", "password": "password-secret"},
            }), encoding="utf-8")

            with self.assertRaises(ConfigError):
                manager.migrate_legacy_config(legacy)

            for path in config_dir.glob("*.json*"):
                self.assertNotIn("cookie-secret", path.read_text(encoding="utf-8"))
                self.assertNotIn("password-secret", path.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["obs_websocket"]["password"], "password-secret")


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
