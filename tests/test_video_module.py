# -*- coding: utf-8 -*-
"""Offline regression tests for migrated video module gating and BV parsing."""
from __future__ import annotations

import unittest

from modules.video.service import ORDER_PATTERN, build_queue_snapshot, on_dan_mu


class VideoModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_video_module_starts_disabled_and_ignores_danmaku(self):
        from modules.video import service
        self.assertFalse(service.module_enabled)
        await on_dan_mu({
            "type": "danmaku",
            "uid": 123,
            "nickname": "viewer",
            "text": "点播 BV1xx411c7mD",
            "timestamp": 1,
        })
        self.assertEqual(service.video_queue, [])
        self.assertIsNone(service.current_bv)

    def test_bv_command_pattern(self):
        self.assertIsNotNone(ORDER_PATTERN.fullmatch("点播 BV1xx411c7mD"))
        self.assertIsNone(ORDER_PATTERN.fullmatch("点播 https://www.bilibili.com/video/BV1xx411c7mD"))

    def test_queue_snapshot_is_independent(self):
        from modules.video import service
        service.video_queue.clear()
        service.current_bv = None
        snapshot = build_queue_snapshot()
        self.assertIsNone(snapshot["current"])
        self.assertEqual(snapshot["queue"], [])
        self.assertEqual(snapshot["queue_length"], 0)


if __name__ == "__main__":
    unittest.main()
