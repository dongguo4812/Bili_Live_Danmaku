# -*- coding: utf-8 -*-
"""Regression tests for the temporary Edge TTS audio cache."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


class TtsCacheLimitTests(unittest.TestCase):
    def test_synthesis_timeout_skips_request_and_removes_partial_audio(self):
        class SlowCommunicate:
            async def save(self, path):
                Path(path).write_bytes(b"partial")
                await asyncio.sleep(1)

        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with patch.object(main, "TTS_CACHE_DIR", cache_dir), patch.object(
                main, "TTS_SYNTHESIS_TIMEOUT_SECONDS", 0.01
            ), patch.object(main, "_load_user_settings", return_value={}), patch.object(
                main, "_tts_preferences",
                return_value={"voice": "zh-CN-XiaoxiaoNeural", "rate": 0, "volume": 0},
            ), patch("edge_tts.Communicate", return_value=SlowCommunicate()):
                response = main.app.test_client().post(
                    "/api/voice/tts/synthesize", json={"text": "测试超时"}
                )

            self.assertEqual(response.status_code, 504)
            self.assertIn("超时", response.get_json()["error"])
            self.assertEqual(list(cache_dir.glob("*.mp3")), [])
            self.assertTrue(main.TTS_CACHE_LOCK.acquire(blocking=False))
            main.TTS_CACHE_LOCK.release()

    def test_successful_synthesis_keeps_audio_in_cache(self):
        class ImmediateCommunicate:
            async def save(self, path):
                Path(path).write_bytes(b"audio")

        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with patch.object(main, "TTS_CACHE_DIR", cache_dir), patch.object(
                main, "_load_user_settings", return_value={}
            ), patch.object(
                main, "_tts_preferences",
                return_value={"voice": "zh-CN-XiaoxiaoNeural", "rate": 0, "volume": 0},
            ), patch("edge_tts.Communicate", return_value=ImmediateCommunicate()):
                response = main.app.test_client().post(
                    "/api/voice/tts/synthesize", json={"text": "测试成功"}
                )

            self.assertEqual(response.status_code, 200)
            filename = response.get_json()["url"].rsplit("/", 1)[-1]
            self.assertEqual((cache_dir / filename).read_bytes(), b"audio")

    def test_cleanup_applies_age_and_file_count_limits_with_reserved_slot(self):
        now = 10000.0
        with tempfile.TemporaryDirectory() as directory:
            cache_dir = Path(directory)
            with patch.object(main, "TTS_CACHE_DIR", cache_dir), patch.object(
                main, "TTS_CACHE_MAX_FILES", 3
            ), patch.object(main, "TTS_CACHE_MAX_AGE_SECONDS", 3600), patch.object(
                main.time, "time", return_value=now
            ):
                expired = cache_dir / "expired.mp3"
                expired.write_bytes(b"expired")
                os.utime(expired, (now - 4000, now - 4000))

                for index in range(5):
                    path = cache_dir / f"{index:032x}.mp3"
                    path.write_bytes(str(index).encode("ascii"))
                    modified = now - 100 + index
                    os.utime(path, (modified, modified))

                main._cleanup_tts_cache(reserve_slots=1)

                remaining = sorted(path.name for path in cache_dir.glob("*.mp3"))
                self.assertEqual(len(remaining), 2)
                self.assertNotIn("expired.mp3", remaining)
                self.assertIn(f"{4:032x}.mp3", remaining)
                self.assertIn(f"{3:032x}.mp3", remaining)

                # The reserved slot is consumed by the newly synthesized file.
                (cache_dir / "new-audio.mp3").write_bytes(b"new")
                self.assertEqual(len(list(cache_dir.glob("*.mp3"))), 3)


if __name__ == "__main__":
    unittest.main()
