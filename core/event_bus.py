# -*- coding: utf-8 -*-
"""统一直播事件总线，业务模块只接收标准化事件。"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("bili_live_danmaku.events")


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[[dict[str, Any]], Any]]] = defaultdict(list)
        self._lock = threading.RLock()
        self._accepting = True
        self._active_publish_tasks: set[asyncio.Task[Any]] = set()
        self._drained = asyncio.Event()
        self._drained.set()

    def subscribe(self, event_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not event_type or not callable(handler):
            raise ValueError("事件类型和处理函数不能为空。")
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def begin_shutdown(self) -> None:
        """封锁新事件；已开始的事件由 drain 等待或取消。"""
        with self._lock:
            self._accepting = False

    async def shutdown(self, timeout: float = 3.0) -> None:
        """限时等待已接收事件；超时后取消未完成的事件处理协程。"""
        self.begin_shutdown()
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=max(0.0, timeout))
            return
        except asyncio.TimeoutError:
            pass

        with self._lock:
            tasks = tuple(
                task for task in self._active_publish_tasks
                if task is not asyncio.current_task() and not task.done()
            )
        if tasks:
            logger.warning("退出时仍有 %s 条直播事件正在处理，已请求取消。", len(tasks))
            for task in tasks:
                task.cancel()
            await asyncio.wait(tasks, timeout=0.5)

    async def publish(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        task = asyncio.current_task()
        with self._lock:
            if not self._accepting:
                return
            if task is not None:
                self._active_publish_tasks.add(task)
                self._drained.clear()

        try:
            normalized = {
                "type": str(event.get("type") or "unknown"),
                "uid": self._safe_int(event.get("uid")),
                "nickname": str(event.get("nickname") or "观众")[:80],
                "text": str(event.get("text") or "")[:2000],
                "timestamp": self._safe_timestamp(event.get("timestamp")),
                "raw": event.get("raw"),
            }
            with self._lock:
                handlers = tuple(self._handlers.get(normalized["type"], ())) + tuple(self._handlers.get("*", ()))

            async def invoke(handler: Callable[[dict[str, Any]], Any]) -> None:
                try:
                    result = handler(normalized)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("直播事件处理器执行失败：type=%s", normalized["type"])

            # 各业务模块并行处理同一条弹幕，避免歌曲搜索/网络校验拖慢视频点播。
            if handlers:
                await asyncio.gather(*(invoke(handler) for handler in handlers))
        finally:
            if task is not None:
                with self._lock:
                    self._active_publish_tasks.discard(task)
                    drained = not self._active_publish_tasks
                if drained:
                    self._drained.set()

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        with self._lock:
            accepting = self._accepting
        if loop.is_closed() or not accepting:
            return
        asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _safe_timestamp(cls, value: Any) -> int:
        parsed = cls._safe_int(value)
        return parsed or int(time.time() * 1000)
