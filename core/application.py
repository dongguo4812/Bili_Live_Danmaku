# -*- coding: utf-8 -*-
"""统一应用运行时：集中承载事件总线和模块生命周期管理。"""
from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Any

from core.async_runtime import AsyncRuntime
from core.event_bus import EventBus
from core.module_manager import ModuleManager

logger = logging.getLogger("bili_live_danmaku.application")


class ApplicationRuntime:
    def __init__(self, max_pending_events: int = 256) -> None:
        self.async_runtime = AsyncRuntime()
        self.events = EventBus()
        self.modules = ModuleManager()
        self._max_pending_events = max(1, int(max_pending_events))
        self._event_slots = threading.BoundedSemaphore(self._max_pending_events)
        self._event_overflow_count = 0
        self._event_overflow_lock = threading.Lock()
        self._started = False
        self._shutting_down = False
        self._shutdown_complete = False
        self._shutdown_lock = threading.RLock()

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

    def publish_event(self, event: dict[str, Any]) -> bool:
        """有界投递直播事件，避免突发弹幕无限创建待执行协程。"""
        if not isinstance(event, dict):
            return False
        if not self._started or self._shutting_down:
            logger.warning("统一运行时尚未启动或正在关闭，忽略直播事件：%s", event.get("type", "unknown"))
            return False
        if not self._event_slots.acquire(blocking=False):
            with self._event_overflow_lock:
                self._event_overflow_count += 1
                dropped = self._event_overflow_count
            if dropped == 1 or dropped % 100 == 0:
                logger.warning(
                    "直播事件待处理数量达到上限 %s，已拒绝新事件；累计拒绝 %s 条。",
                    self._max_pending_events,
                    dropped,
                )
            return False
        try:
            future = self.async_runtime.submit(self.events.publish(dict(event)))
        except Exception:
            self._event_slots.release()
            raise
        future.add_done_callback(lambda _future: self._event_slots.release())
        return True

    def connection_lost(self) -> None:
        if self._started and not self._shutting_down:
            self.async_runtime.submit(self.modules.on_connection_lost())

    def connection_restored(self) -> None:
        if self._started and not self._shutting_down:
            self.async_runtime.submit(self.modules.on_connection_restored())

    def module_action(self, name: str, action: str):
        if not self._started or self._shutting_down:
            raise RuntimeError("统一应用运行时尚未启动或正在关闭。")
        actions = {
            "start": self.modules.start,
            "pause": self.modules.pause,
            "resume": self.modules.resume,
            "close": self.modules.close,
        }
        if action not in actions:
            raise ValueError(f"不支持的模块操作：{action}")
        return self.async_runtime.submit(actions[action](name))

    def begin_shutdown(self) -> None:
        """立即封锁新事件、连接状态回调和模块操作；可从任意线程同步调用。"""
        with self._shutdown_lock:
            if self._shutdown_complete or self._shutting_down:
                return
            self._shutting_down = True
            self.events.begin_shutdown()
            self.modules.begin_shutdown()

    async def _shutdown_async(self, module_timeout: float) -> None:
        """先排空已接收事件，再按依赖逆序关闭业务模块。"""
        await self.events.shutdown(timeout=min(3.0, max(0.1, module_timeout)))
        await self.modules.shutdown_all(timeout=module_timeout)

    def shutdown(self, timeout: float = 40.0) -> None:
        """关闭模块后再停止事件循环；整个过程有明确的总等待上限。"""
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutting_down = True
            self._started = False
            self.events.begin_shutdown()
            self.modules.begin_shutdown()

            try:
                module_timeout = min(32.0, max(0.1, float(timeout) - 8.0))
                future = self.async_runtime.submit(self._shutdown_async(module_timeout))
                try:
                    future.result(timeout=module_timeout + 4.0)
                except concurrent.futures.TimeoutError:
                    logger.error(
                        "业务模块清理超过 %.1f 秒；请求取消清理协程后进入运行时兜底关闭。",
                        module_timeout,
                    )
                    future.cancel()
                except Exception:
                    logger.exception("统一关闭业务模块时发生错误。")
            except Exception:
                logger.exception("提交业务模块关闭任务失败。")
            finally:
                self.async_runtime.stop(timeout=max(0.1, float(timeout) - module_timeout - 4.0))
                self._shutdown_complete = True
                logger.info("统一应用运行时关闭流程结束。")
