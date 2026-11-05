# -*- coding: utf-8 -*-
"""统一应用运行时：集中承载事件总线和模块生命周期管理。"""
from __future__ import annotations

import logging
from typing import Any

from core.async_runtime import AsyncRuntime
from core.event_bus import EventBus
from core.module_manager import ModuleManager

logger = logging.getLogger("bili_live_danmaku.application")


class ApplicationRuntime:
    def __init__(self) -> None:
        self.async_runtime = AsyncRuntime()
        self.events = EventBus()
        self.modules = ModuleManager()
        self._started = False
        self._shutting_down = False

    def start(self) -> None:
        if self._started:
            return
        self.async_runtime.start()
        self._started = True
        logger.info("统一应用异步运行时已启动。")

    def register_module(
        self, name: str, module: Any, initial_state: str = "stopped",
        auto_start_on_connect: bool = False,
        requires_connection: bool = True,
    ) -> None:
        self.modules.register(
            name, module, initial_state=initial_state,
            auto_start_on_connect=auto_start_on_connect,
            requires_connection=requires_connection,
        )

    def publish_event(self, event: dict[str, Any]) -> None:
        if not self._started:
            logger.warning("统一运行时尚未启动，忽略直播事件：%s", event.get("type", "unknown"))
            return
        self.async_runtime.submit(self.events.publish(event))

    def connection_lost(self) -> None:
        if self._started:
            self.async_runtime.submit(self.modules.on_connection_lost())

    def connection_restored(self) -> None:
        if self._started:
            self.async_runtime.submit(self.modules.on_connection_restored())

    def module_action(self, name: str, action: str):
        if not self._started:
            raise RuntimeError("统一应用运行时尚未启动。")
        actions = {
            "start": self.modules.start,
            "pause": self.modules.pause,
            "resume": self.modules.resume,
            "close": self.modules.close,
        }
        if action not in actions:
            raise ValueError(f"不支持的模块操作：{action}")
        return self.async_runtime.submit(actions[action](name))

    def shutdown(self, timeout: float = 45.0) -> None:
        if not self._started or self._shutting_down:
            return
        # 先同步封锁断线/重连回调，再安排模块清理，避免退出时的断线回调
        # 把歌曲模块重新置为 paused，导致视频自动暂停的歌曲无法恢复。
        self._shutting_down = True
        self._started = False
        self.modules.begin_shutdown()
        try:
            self.async_runtime.submit(self.modules.shutdown_all()).result(timeout=timeout)
        except Exception:
            logger.exception("统一关闭业务模块时发生错误。")
        finally:
            self.async_runtime.stop(timeout=timeout)
            logger.info("统一应用运行时已关闭。")
