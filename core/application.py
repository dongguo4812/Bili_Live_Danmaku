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

    def start(self) -> None:
        if self._started:
            return
        self.async_runtime.start()
        self._started = True
        logger.info("统一应用异步运行时已启动。")

    def register_module(self, name: str, module: Any, initial_state: str = "stopped") -> None:
        self.modules.register(name, module, initial_state=initial_state)

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

    def shutdown(self, timeout: float = 15.0) -> None:
        if not self._started:
            return
        try:
            self.async_runtime.submit(self.modules.close_all()).result(timeout=timeout)
        except Exception:
            logger.exception("统一关闭业务模块时发生错误。")
        finally:
            self.async_runtime.stop(timeout=timeout)
            self._started = False
            logger.info("统一应用运行时已关闭。")
