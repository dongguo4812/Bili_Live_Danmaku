# -*- coding: utf-8 -*-
"""Offline regression tests for migrated music matching and module gating."""
from __future__ import annotations

import unittest

from modules.music.service import MusicModule, match_candidates, parse_request


class MusicMatchingTests(unittest.TestCase):
    def test_parse_song_and_artist(self):
        self.assertEqual(parse_request("点歌 晴天-周杰伦"), ("晴天", "周杰伦"))
        self.assertEqual(parse_request("点歌 夜曲"), ("夜曲", ""))

    def test_exact_artist_match_precedes_contains_fallback(self):
        candidates = [
            {"title": "晴天", "artist": "周杰伦、乐队"},
            {"title": "晴天", "artist": "周杰伦"},
        ]
        exact, fallback = match_candidates(candidates, "晴天", "周杰伦")
        self.assertEqual(exact[0]["artist"], "周杰伦")
        self.assertEqual(fallback[0]["artist"], "周杰伦、合唱团")

    def test_bad_versions_are_filtered(self):
        candidates = [
            {"title": "晴天", "artist": "周杰伦"},
            {"title": "晴天 Live", "artist": "周杰伦"},
            {"title": "晴天", "artist": "周杰伦 Cover"},
        ]
        exact, fallback = match_candidates(candidates, "晴天", "")
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["title"], "晴天")
        self.assertEqual(fallback, [])


class FanMedalParsingTests(unittest.TestCase):
    def test_finds_nested_medal_list(self):
        from modules.music.service import _find_medal_list
        medals = [{"room_id": 123, "is_light": 1}]
        self.assertIs(_find_medal_list({"data": {"medal_list": medals}}), medals)

    def test_requires_matching_room_and_explicit_light(self):
        from modules.music.service import _medal_matches_room
        self.assertEqual(_medal_matches_room({"room_id": 123, "is_light": 1}, 123), (True, True))
        self.assertEqual(_medal_matches_room({"room_id": 123, "is_light": 0}, 123), (True, False))
        self.assertEqual(_medal_matches_room({"room_id": 456, "is_light": 1}, 123), (False, True))
        self.assertEqual(_medal_matches_room({"room_id": 123}, 123), (True, None))


class MusicModuleLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_module_starts_disabled_and_ignores_danmaku(self):
        module = MusicModule(config={}, admin_uids=set())
        self.assertFalse(module.enabled)
        await module.on_danmu({
            "type": "danmaku",
            "uid": 123,
            "nickname": "viewer",
            "text": "点歌 晴天-周杰伦",
            "timestamp": 1,
        })
        self.assertEqual(len(module.queue), 0)
        self.assertIsNone(module.current)


if __name__ == "__main__":
    unittest.main()
