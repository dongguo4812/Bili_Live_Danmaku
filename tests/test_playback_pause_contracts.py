# -*- coding: utf-8 -*-
"""Regression contracts for real playback pause and paused request feedback."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlaybackPauseContractTests(unittest.TestCase):
    def test_music_snapshot_exposes_player_pause_separately_from_module_pause(self):
        source = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        self.assertIn("def get_lx_playback_state(", source)
        self.assertIn('return "paused"', source)
        self.assertIn('"playback_state": playback_state', source)
        self.assertIn('"module_paused" if current and self.paused', source)
        self.assertIn("歌曲点播当前已暂停", source)

    def test_video_snapshot_tracks_html5_paused_state_and_module_pause_separately(self):
        source = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        self.assertIn('else "paused" if bool(status.get("paused"))', source)
        self.assertIn('"playback_state": "module_paused" if current and module_paused', source)
        self.assertIn("视频点播当前已暂停", source)
        self.assertIn('add_operation_message(message, "failure")', source)

    def test_shared_display_shows_player_pause_without_replacing_module_pause_notice(self):
        script = (ROOT / "static" / "display_media.js").read_text(encoding="utf-8")
        self.assertIn('playbackPaused?"已暂停播放 ⏸":"正在播放 ▶"', script)
        self.assertNotIn("md-playback-paused-status", script)
        self.assertIn('options.moduleState==="paused"', script)
        self.assertIn("🚫 歌曲点播已暂停", script)
        self.assertIn("🚫 视频点播已暂停", script)

    def test_music_module_can_pause_and_restore_only_its_video_paused_track(self):
        source = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        self.assertIn("async def pause_for_video(", source)
        self.assertIn("async def resume_after_video(", source)
        self.assertIn("self._video_pause_track", source)
        self.assertIn("actual_track != expected_track", source)
        self.assertIn("self._video_pause_active", source)

    def test_video_playback_controls_music_and_restores_after_last_video(self):
        video = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("async def _pause_music_for_video()", video)
        self.assertIn("async def _resume_music_if_video_finished()", video)
        self.assertIn("if actually_playing:", video)
        self.assertIn("await _resume_music_if_video_finished()", video)
        self.assertIn("music_module=MUSIC_MODULE.bot", main)

    def test_application_shutdown_closes_video_browser_without_stopping_lx_music(self):
        application = (ROOT / "core" / "application.py").read_text(encoding="utf-8")
        manager = (ROOT / "core" / "module_manager.py").read_text(encoding="utf-8")
        music = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        video = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        self.assertIn("self.modules.shutdown_all(timeout=module_timeout)", application)
        self.assertIn('getattr(instance, "shutdown", None)', manager)
        self.assertIn("async def shutdown(self):", music)
        self.assertIn("await self.bot.close(stop_playback=False)", music)
        self.assertIn("if had_current_song and stop_playback:", music)
        self.assertIn("async def shutdown(self):", video)
        self.assertIn("await self.close(restore_music=False, cleanup_browser=False)", video)
        self.assertIn('if not callable(shutdown) and record.state == "stopped":', manager)
        self.assertIn("def begin_shutdown(self)", manager)
        self.assertIn("if self._shutting_down:", manager)
        app_shutdown = application[application.index("def shutdown("):]
        self.assertLess(app_shutdown.index("self._started = False"), app_shutdown.index("self.async_runtime.submit"))
        self.assertNotIn("await self.bot.resume_after_video(force=True)", music)
        music_shutdown = music[music.index("async def shutdown(self):", music.index("class MusicModuleFacade")):]
        music_shutdown = music_shutdown[:music_shutdown.index("\n    def update_room_id", 1)]
        self.assertIn("await self.bot.close(stop_playback=False)", music_shutdown)
        self.assertNotIn("_lx_control", music_shutdown)
        self.assertNotIn("emergency_resume_video_paused_music_sync", music)
        video_close = video[video.index("async def close(self, restore_music: bool = True, cleanup_browser: bool = True):", video.index("class VideoModuleFacade")):]
        self.assertIn("await self.close(restore_music=False, cleanup_browser=False)", video)
        self.assertIn("async def close(self, restore_music: bool = True, cleanup_browser: bool = True):", video)
        self.assertIn("if restore_music:", video_close)
        self.assertIn("await _resume_music_if_video_finished()", video_close)
        self.assertIn("def _browser_process_alive_sync(browser)", video)
        self.assertIn("def _force_terminate_browser_sync(browser)", video)
        self.assertIn("ROOTS=", video)
        self.assertIn("REMAINING=", video)
        self.assertIn("taskkill.exe /PID $id /T /F", video)
        self.assertIn("Win32_Process", video)
        self.assertIn("--user-data-dir=", video)
        self.assertIn("def _terminate_chrome_profile_processes_sync()", video)
        self.assertIn("$pattern = '(?i)--user-data-dir=", video)
        self.assertIn("$_.CommandLine -match $pattern", video)
        self.assertIn("$leftIds", video)
        self.assertIn("await run_blocking(_terminate_chrome_profile_processes_sync)", video)
        self.assertIn("run_blocking(emergency_close_browser_sync)", video)
        self.assertIn("timeout=9.0", video)
        video_begin_shutdown_body = video[video.index("def begin_shutdown(self):", video.index("class VideoModuleFacade")):]
        video_begin_shutdown_body = video_begin_shutdown_body[:video_begin_shutdown_body.index("\n    async def shutdown", 1)]
        self.assertLess(video_begin_shutdown_body.index("VIDEO_SHUTDOWN_REQUESTED.set()"), video_begin_shutdown_body.index("target=emergency_close_browser_sync"))
        self.assertIn("module_enabled = False", video_begin_shutdown_body)
        video_shutdown_body = video[video.index("async def shutdown(self):", video.index("class VideoModuleFacade")):]
        video_shutdown_body = video_shutdown_body[:video_shutdown_body.index("\n    async def close", 1)]
        self.assertIn("cleanup_thread.join, 9.0", video_shutdown_body)
        self.assertIn("await self.close(restore_music=False, cleanup_browser=False)", video_shutdown_body)
        self.assertIn("if cleanup_browser else ([], [])", video)
        video_close_body = video[video.index("async def close(self, restore_music: bool = True, cleanup_browser: bool = True):", video.index("class VideoModuleFacade")):]
        video_close_body = video_close_body[:video_close_body.index("\n    def update_room_id", 1)]
        self.assertLess(video_close_body.index("_quit_browser_sync"), video_close_body.index("_terminate_chrome_profile_processes_sync"))
        # 即使 WebDriver 引用已不存在，也仍通过专用 Profile 清理流程处理残留进程。
        self.assertIn("if cleanup_browser and driver is not None:", video_close_body)
        self.assertIn("if cleanup_browser else ([], [])", video_close_body)
        self.assertIn("Chrome 专用 Profile 仍有残留进程，PID=%s", video)
        self.assertIn("timeout=4.0", video)
        self.assertIn("await asyncio.wait_for(", video)
        self.assertIn("timeout=1.5", video)
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def _install_console_close_handler()", main)
        self.assertIn("SetConsoleCtrlHandler", main)
        self.assertNotIn("emergency_close_browser_sync", main)
        self.assertNotIn("emergency_resume_video_paused_music_sync", main)
        console_handler = main[main.index("def _install_console_close_handler()"):]
        console_handler = console_handler[:console_handler.index('\n\nif __name__ == "__main__"')]
        self.assertIn("_shutdown_application_and_refresh_obs", console_handler)
        self.assertNotIn("emergency_close_browser_sync", console_handler)
        self.assertIn("task.join(40.0)", console_handler)
        self.assertIn("def _shutdown_application_and_refresh_obs()", main)
        shutdown_flow = main[main.index("def _shutdown_application_and_refresh_obs()"):]
        shutdown_flow = shutdown_flow[:shutdown_flow.index("\ndef _available_models", 1)]
        self.assertLess(shutdown_flow.index("APPLICATION_RUNTIME.shutdown(timeout=37.0)"),
                        shutdown_flow.index("ensure_voice_engine=False"))
        self.assertLess(shutdown_flow.index("LIVE_CLIENT.stop(timeout=6.0)"),
                        shutdown_flow.index("ensure_voice_engine=False"))
        self.assertEqual(shutdown_flow.count("ensure_voice_engine=False"), 1)
        self.assertIn("不再重试", shutdown_flow)
        self.assertNotIn("for attempt in range", shutdown_flow)
        self.assertIn("OBS_COMBINED_REFRESH_STOP.set()", shutdown_flow)
        self.assertIn("_shutdown_application_and_refresh_obs()", main[main.index("finally:", main.index("if __name__ == \"__main__\"")):])


    def test_module_errors_are_explained_to_users_without_exposing_exception_names(self):
        main = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertIn("def _friendly_module_exception_message(", main)
        self.assertIn("Chrome 无法创建新的浏览器会话", main)
        self.assertIn("专用浏览器配置目录正在被占用", main)
        self.assertIn("详细错误已记录到程序日志", main)
        self.assertIn("_friendly_module_exception_message(exc, module_name, action)", main)
        self.assertIn('_friendly_module_exception_message(exc, "video", "action")', main)
        self.assertNotIn('f"模块操作失败：{type(exc).__name__}"', main)
        self.assertNotIn('f"操作失败：{type(exc).__name__}"', main)

    def test_kuwo_search_uses_current_signed_web_api(self):
        source = (ROOT / "modules" / "music" / "service.py").read_text(encoding="utf-8")
        self.assertIn('_KUWO_SEARCH_URL = "https://www.kuwo.cn/search/searchMusicBykeyWord"', source)
        self.assertIn('_KUWO_SESSION_COOKIE = "Hm_Iuvt_cdb524f42f23cer9b268564v7y735ewrq2324"', source)
        self.assertIn("def _kuwo_build_secret(cookie: str, nonce: int)", source)
        self.assertIn('"Secret": _kuwo_build_secret(_KUWO_SESSION_VALUE, nonce)', source)
        self.assertIn('"reqId": str(uuid.uuid4())', source)
        self.assertIn('rows = data_section.get("list") or data.get("abslist") or []', source)
        self.assertIn('row.get("rid") or row.get("MUSICRID") or row.get("id")', source)
        self.assertNotIn('url = "https://search.kuwo.cn/r.s?"', source)
        self.assertNotIn("_music_get_json(url, allow_python_literal=True)", source)

    def test_chrome_session_creation_failure_cleans_only_the_dedicated_profile(self):
        video = (ROOT / "modules" / "video" / "service.py").read_text(encoding="utf-8")
        create_body = video[video.index("def create_browser():"):]
        create_body = create_body[:create_body.index("\n\n@selenium_locked", 1)]
        self.assertIn("logger.exception(\"创建视频点播 Chrome 会话失败", create_body)
        self.assertIn("_terminate_chrome_profile_processes_sync()", create_body)
        self.assertIn("raise", create_body)
        self.assertNotIn("taskkill.exe /IM chrome.exe", create_body)


if __name__ == "__main__":
    unittest.main()
