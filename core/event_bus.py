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

    async def publish(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
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
        for handler in handlers:
            try:
                result = handler(normalized)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("直播事件处理器执行失败：type=%s", normalized["type"])

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: dict[str, Any]) -> None:
        if loop.is_closed():
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
