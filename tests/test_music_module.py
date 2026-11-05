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
    def test_stops_after_first_platform_match(self):
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

        self.assertEqual(calls, ["tx"])
        self.assertEqual([item["source"] for item in matches], ["tx"])
        self.assertEqual([item["songmid"] for item in matches], ["tx-id"])

    def test_disabled_providers_are_skipped(self):
        from unittest.mock import patch
        import modules.music.service as service

        calls = []
        def provider(source):
            def search(keyword):
                calls.append(source)
                return [{
                    "title": "晴天", "artist": "周杰伦", "source": source,
                    "songmid": source + "-id",
                }]
            return search

        providers = (
            ("QQ音乐", "tx", provider("tx")),
            ("酷狗音乐", "kg", provider("kg")),
            ("网易云音乐", "wy", provider("wy")),
            ("酷我音乐", "kw", provider("kw")),
        )
        with patch.object(service, "PROVIDERS", providers), patch.object(service, "ENABLED_PROVIDERS", ("kg", "kw")):
            matches, _ = service.find_matching_songs("晴天", "周杰伦")

        self.assertEqual(calls, ["kg"])
        self.assertEqual([item["source"] for item in matches], ["kg"])

    def test_trailing_space_artist_fallback_matches_song_and_artist(self):
        from unittest.mock import patch
        import modules.music.service as service

        calls = []

        def search(keyword):
            calls.append(keyword)
            return [{
                "title": "稻香",
                "artist": "周杰伦",
                "source": "tx",
                "songmid": "tx-dao-xiang-id",
                "types": [{"type": "128k", "size": None}],
            }]

        providers = (
            ("QQ音乐", "tx", search),
            ("酷狗音乐", "kg", lambda keyword: []),
        )
        with patch.object(service, "PROVIDERS", providers):
            matches, message = service.find_matching_songs("稻香 周杰伦", "")

        self.assertEqual(calls, ["稻香 周杰伦"])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "稻香")
        self.assertEqual(matches[0]["artist"], "周杰伦")
        self.assertEqual(matches[0]["songmid"], "tx-dao-xiang-id")
        self.assertEqual(matches[0]["source"], "tx")
        self.assertIn("QQ音乐", message)

    def test_first_platform_match_wins(self):
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

        self.assertEqual([item["source"] for item in matches], ["tx"])


    def test_fallback_search_starts_after_failed_provider(self):
        from unittest.mock import patch
        import modules.music.service as service

        calls = []
        def make_provider(source):
            def search(keyword):
                calls.append(source)
                return [{"title": "晴天", "artist": "周杰伦", "source": source, "songmid": source + "-id"}]
            return search

        providers = (
            ("QQ音乐", "tx", make_provider("tx")),
            ("酷狗音乐", "kg", make_provider("kg")),
            ("网易云音乐", "wy", make_provider("wy")),
        )
        with patch.object(service, "PROVIDERS", providers):
            matches, _ = service.find_matching_songs("晴天", "周杰伦", after_source="tx")

        self.assertEqual(calls, ["kg"])
        self.assertEqual([item["source"] for item in matches], ["kg"])

    def test_search_moves_to_next_platform_when_current_has_no_match(self):
        from unittest.mock import patch
        import modules.music.service as service

        calls = []
        def no_match(keyword):
            calls.append("tx")
            return []
        def match(keyword):
            calls.append("kg")
            return [{"title": "晴天", "artist": "周杰伦", "source": "kg", "songmid": "kg-id"}]

        providers = (("QQ音乐", "tx", no_match), ("酷狗音乐", "kg", match))
        with patch.object(service, "PROVIDERS", providers):
            matches, _ = service.find_matching_songs("晴天", "周杰伦")

        self.assertEqual(calls, ["tx", "kg"])
        self.assertEqual([item["source"] for item in matches], ["kg"])


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

    def test_medal_check_uses_shared_login_cookie(self):
        from unittest.mock import MagicMock, patch
        import modules.music.service as service

        old_cookie = service.COOKIE_DICT
        old_room = service.ROOM_ID
        service.COOKIE_DICT = {"SESSDATA": "test-session"}
        service.ROOM_ID = 123
        service.MEDAL_CACHE.clear()
        session = MagicMock()
        session.__enter__.return_value = session
        response = MagicMock()
        response.json.return_value = {
            "code": 0,
            "data": {"list": [{"room_id": 123, "is_light": 1}]},
        }
        session.get.return_value = response
        try:
            with patch("modules.music.service.requests.Session", return_value=session):
                ok, reason = service.check_fan_medal_sync(999)
            self.assertTrue(ok, reason)
            self.assertIn("已点亮", reason)
            self.assertEqual(session.cookies.update.call_args.args[0]["SESSDATA"], "test-session")
        finally:
            service.COOKIE_DICT = old_cookie
            service.ROOM_ID = old_room
            service.MEDAL_CACHE.clear()


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

    async def test_close_pauses_confirmed_bot_playback(self):
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        module.current = {"title": "晴天", "artist": "周杰伦"}
        module._lx_playback_active = True
        with patch("modules.music.service._lx_control", return_value=(True, "paused")) as lx_control:
            await module.close()
            lx_control.assert_called_once_with("stop")
            self.assertIsNone(module.current)
            self.assertFalse(module._lx_playback_active)

    async def test_active_song_pause_and_resume_control_only_owned_lx_track(self):
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        module.current = {"title": "晴天", "artist": "周杰伦"}
        module._lx_playback_active = True
        with patch("modules.music.service._lx_control", return_value=(True, "ok")) as lx_control:
            await module.pause()
            await module.resume()
        self.assertEqual(
            [call.args[0] for call in lx_control.call_args_list],
            ["pause", "resume"],
        )

    async def test_parallel_requests_from_same_uid_obey_cooldown(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        module._medal_enabled = True
        module.enqueue = AsyncMock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def medal_check(uid):
            entered.set()
            await release.wait()
            return True, "ok"

        event = {
            "type": "danmaku", "uid": 456, "nickname": "viewer",
            "text": "点歌 晴天-周杰伦", "timestamp": 1,
        }
        with patch("modules.music.service.check_fan_medal", side_effect=medal_check):
            first = asyncio.create_task(module.on_danmu(event))
            await entered.wait()
            second = asyncio.create_task(module.on_danmu(event))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(first, second)

        self.assertEqual(module.enqueue.await_count, 1)

    async def test_inflight_search_cannot_enqueue_after_module_close(self):
        import asyncio
        import threading
        from unittest.mock import patch
        module = MusicModule(config={}, admin_uids=set())
        module.enabled = True
        status_entered = threading.Event()
        release_status = threading.Event()

        def delayed_status():
            status_entered.set()
            release_status.wait(timeout=3)
            return None

        candidate = {
            "title": "晴天", "artist": "周杰伦", "source": "tx",
            "songmid": "tx-id", "types": [{"type": "128k", "size": None}],
        }
        with patch("modules.music.service.find_matching_songs", return_value=([candidate], "ok")), \
             patch("modules.music.service.get_lx_status", side_effect=delayed_status):
            task = asyncio.create_task(module.enqueue("晴天", "周杰伦", "viewer", 789))
            self.assertTrue(await asyncio.to_thread(status_entered.wait, 2))
            await module.close()
            release_status.set()
            await task

        self.assertFalse(module.enabled)
        self.assertEqual(len(module.queue), 0)





if __name__ == "__main__":
    unittest.main()
