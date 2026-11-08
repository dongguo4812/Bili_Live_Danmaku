# -*- coding: utf-8 -*-
"""Regression tests for bounded transient per-user and video caches."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import modules.music.service as music_service
import modules.video.service as video_service


class MusicTransientCacheTests(unittest.TestCase):
    def setUp(self):
        self.old_medal_cache = list(music_service.MEDAL_CACHE.items())
        self.old_prune_at = music_service._MEDAL_CACHE_LAST_PRUNE
        music_service.MEDAL_CACHE.clear()
        music_service._MEDAL_CACHE_LAST_PRUNE = 0.0

    def tearDown(self):
        music_service.MEDAL_CACHE.clear()
        music_service.MEDAL_CACHE.update(self.old_medal_cache)
        music_service._MEDAL_CACHE_LAST_PRUNE = self.old_prune_at

    def test_medal_cache_expires_entries_and_enforces_lru_capacity(self):
        with patch.object(music_service, "MEDAL_CACHE_MAX_ENTRIES", 2), patch.object(
            music_service, "MEDAL_CACHE_PRUNE_INTERVAL", 0.0
        ):
            music_service._store_medal_cache(1, (100.0, True, "one"))
            music_service._store_medal_cache(2, (100.0, True, "two"))
            self.assertEqual(music_service._get_medal_cache(1, 101.0)[2], "one")
            music_service._store_medal_cache(3, (101.0, True, "three"))
            self.assertEqual(list(music_service.MEDAL_CACHE), [1, 3])
            music_service._get_medal_cache(4, 200.0)
            self.assertEqual(list(music_service.MEDAL_CACHE), [])

    def test_user_request_locks_are_removed_after_all_callers_release(self):
        module = music_service.MusicModule()
        first = module._acquire_user_request_lock(123)
        second = module._acquire_user_request_lock(123)
        self.assertIs(first, second)
        self.assertEqual(module._user_request_locks[123][1], 2)

        module._release_user_request_lock(123, first)
        self.assertEqual(module._user_request_locks[123][1], 1)
        module._release_user_request_lock(123, second)
        self.assertNotIn(123, module._user_request_locks)

    def test_expired_song_request_cooldowns_are_pruned(self):
        module = music_service.MusicModule()
        module.cooldowns.update({1001: 10.0, 1002: 95.0})
        with patch.object(music_service, "COOLDOWN_SECONDS", 10):
            module._prune_user_cooldowns(100.0)
        self.assertEqual(module.cooldowns, {1002: 95.0})


class VideoTransientCacheTests(unittest.TestCase):
    def setUp(self):
        self.old_state = {
            "titles": dict(video_service.video_titles),
            "durations": dict(video_service.video_durations),
            "requesters": dict(video_service.video_requesters),
            "queue": list(video_service.video_queue),
            "current": video_service.current_bv,
            "pending": set(video_service.pending_video_bvs),
            "cooldowns": dict(video_service.last_order_time),
            "prune_at": video_service._last_order_prune_at,
        }
        video_service.video_titles.clear()
        video_service.video_durations.clear()
        video_service.video_requesters.clear()
        video_service.video_queue.clear()
        video_service.current_bv = None
        video_service.pending_video_bvs.clear()
        video_service.last_order_time.clear()
        video_service._last_order_prune_at = 0.0

    def tearDown(self):
        video_service.video_titles.clear()
        video_service.video_titles.update(self.old_state["titles"])
        video_service.video_durations.clear()
        video_service.video_durations.update(self.old_state["durations"])
        video_service.video_requesters.clear()
        video_service.video_requesters.update(self.old_state["requesters"])
        video_service.video_queue.clear()
        video_service.video_queue.extend(self.old_state["queue"])
        video_service.current_bv = self.old_state["current"]
        video_service.pending_video_bvs.clear()
        video_service.pending_video_bvs.update(self.old_state["pending"])
        video_service.last_order_time.clear()
        video_service.last_order_time.update(self.old_state["cooldowns"])
        video_service._last_order_prune_at = self.old_state["prune_at"]

    def test_video_info_cache_is_capped_without_evicting_active_videos(self):
        for index in range(305):
            bv = f"BV{index:010d}"
            video_service.video_titles[bv] = f"title-{index}"
            video_service.video_durations[bv] = index + 1

        active = ("BV-active", "BV-current", "BV-pending")
        video_service.video_queue.append(active[0])
        video_service.current_bv = active[1]
        video_service.pending_video_bvs.add(active[2])
        for bv in active:
            video_service.video_titles[bv] = bv
            video_service.video_durations[bv] = 120
        video_service.video_requesters.update({
            active[0]: "active viewer",
            "BV-stale": "old viewer",
        })

        video_service._prune_video_info_cache()

        self.assertLessEqual(len(video_service.video_titles), video_service.VIDEO_INFO_CACHE_LIMIT)
        for bv in active:
            self.assertIn(bv, video_service.video_titles)
            self.assertIn(bv, video_service.video_durations)
        self.assertNotIn("BV-stale", video_service.video_requesters)
        self.assertEqual(video_service.video_requesters[active[0]], "active viewer")

    def test_expired_video_request_cooldowns_are_pruned(self):
        video_service.last_order_time.update({2001: 10.0, 2002: 95.0})
        with patch.object(video_service, "COOLDOWN_SEC", 10):
            video_service._prune_last_order_time(100.0)
        self.assertEqual(video_service.last_order_time, {2002: 95.0})


if __name__ == "__main__":
    unittest.main()
