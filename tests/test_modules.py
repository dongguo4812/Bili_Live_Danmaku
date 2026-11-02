# -*- coding: utf-8 -*-
"""Offline tests for simple display-driven module lifecycles and event routing."""
from __future__ import annotations

import unittest

from core.event_bus import EventBus
from modules.live2d.service import Live2DModule
from modules.voice.service import VoiceModule


class DisplayModuleTests(unittest.IsolatedAsyncioTestCase):
    async def test_live2d_and_voice_lifecycle_are_independent(self):
        live2d = Live2DModule()
        voice = VoiceModule()
        await live2d.pause()
        self.assertTrue(live2d.paused)
        self.assertFalse(voice.paused)
        await voice.close()
        self.assertFalse(voice.enabled)
        self.assertTrue(live2d.enabled)
        await live2d.resume()
        self.assertFalse(live2d.paused)


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_danmaku_event_reaches_subscriber(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event)

        bus.subscribe("danmaku", handler)
        await bus.publish({
            "type": "danmaku",
            "uid": "123",
            "nickname": "viewer",
            "text": "hello",
            "timestamp": 1234,
        })
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["uid"], 123)
        self.assertEqual(received[0]["nickname"], "viewer")
        self.assertEqual(received[0]["text"], "hello")


if __name__ == "__main__":
    unittest.main()
