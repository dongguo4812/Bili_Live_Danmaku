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
        self.assertEqual(fallback[0]["artist"], "周杰伦、乐队")

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


class MusicFallbackSearchTests(unittest.TestCase):
    def test_collects_ordered_source_candidates(self):
        from unittest.mock import patch
        import modules.music.service as service

        calls = []
        def provider(source):
            def search(keyword):
                calls.append(source)
                return [{
                    "title": "晴天",
                    "artist": "周杰伦",
                    "source": source,
                    "songmid": source + "-id",
                    "types": [{"type": "128k", "size": None}],
                }]
            return search

        ordered = (
            ("QQ音乐", "tx", provider("tx")),
            ("酷狗音乐", "kg", provider("kg")),
            ("网易云音乐", "wy", provider("wy")),
            ("酷我音乐", "kw", provider("kw")),
        )
        with patch.object(service, "PROVIDERS", ordered):
            matches, message = service.find_matching_songs("晴天", "周杰伦")

        self.assertEqual(calls, ["tx", "kg", "wy", "kw"])
        self.assertEqual([item["source"] for item in matches], ["tx", "kg", "wy", "kw"])
        self.assertEqual([item["songmid"] for item in matches], ["tx-id", "kg-id", "wy-id", "kw-id"])

    def test_exact_artist_from_later_source_precedes_earlier_contains_match(self):
        from unittest.mock import patch
        import modules.music.service as service

        providers = (
            ("QQ音乐", "tx", lambda keyword: [{"title": "晴天", "artist": "周杰伦、乐队", "source": "tx", "songmid": "tx-id"}]),
            ("酷狗音乐", "kg", lambda keyword: [{"title": "晴天", "artist": "周杰伦", "source": "kg", "songmid": "kg-id"}]),
            ("网易云音乐", "wy", lambda keyword: []),
            ("酷我音乐", "kw", lambda keyword: []),
        )
        with patch.object(service, "PROVIDERS", providers):
            matches, _ = service.find_matching_songs("晴天", "周杰伦")

        self.assertEqual([item["source"] for item in matches], ["kg", "tx"])


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

    async def test_idle_pause_and_resume_do_not_control_external_lx_song(self):
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        with patch("modules.music.service._lx_control") as lx_control:
            await module.pause()
            self.assertTrue(module.paused)
            await module.resume()
            self.assertFalse(module.paused)
            lx_control.assert_not_called()

    async def test_pending_request_does_not_pause_external_lx_song(self):
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        module.current = {"title": "晴天", "artist": "周杰伦"}
        with patch("modules.music.service._lx_control") as lx_control:
            await module.pause()
            await module.resume()
            lx_control.assert_not_called()

    async def test_close_does_not_pause_external_song_without_confirmed_bot_playback(self):
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        module.current = {"title": "晴天", "artist": "周杰伦"}
        with patch("modules.music.service._lx_control") as lx_control:
            await module.close()
            lx_control.assert_not_called()
            self.assertIsNone(module.current)



if __name__ == "__main__":
    unittest.main()
