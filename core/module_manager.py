# -*- coding: utf-8 -*-
"""独立业务模块生命周期管理与断线状态恢复。"""
from __future__ import annotations

import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("bili_live_danmaku.modules")


@dataclass
class ModuleRecord:
    name: str
    instance: Any
    state: str = "stopped"  # stopped / running / paused / error
    generation: int = 0
    error: str = ""
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ModuleManager:
    """每个模块独立控制；连接恢复只恢复断线前运行且未被用户改动的模块。"""

    def __init__(self) -> None:
        self._modules: dict[str, ModuleRecord] = {}
        self._lock = threading.RLock()
        self._disconnect_snapshot: dict[str, int] | None = None

    def register(self, name: str, module: Any, initial_state: str = "stopped") -> None:
        if not name or module is None:
            raise ValueError("模块名称和实例不能为空。")
        if initial_state not in {"stopped", "running", "paused"}:
            raise ValueError("模块初始状态无效。")
        required = ("start", "pause", "resume", "close")
        missing = [method for method in required if not callable(getattr(module, method, None))]
        if missing:
            raise TypeError(f"模块 {name} 缺少生命周期方法：{', '.join(missing)}")
        with self._lock:
            if name in self._modules:
                raise ValueError(f"模块已注册：{name}")
            self._modules[name] = ModuleRecord(name=name, instance=module, state=initial_state)

    def list_status(self) -> dict[str, dict[str, str]]:
        with self._lock:
            return {
                name: {"state": record.state, "error": record.error}
                for name, record in self._modules.items()
            }

    async def start(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            state = record.state
        if state == "running":
            return
        if state == "paused":
            await self.resume(name)
            return
        await self._operate(name, "start", "running", manual=True)

    async def pause(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            if record.state == "stopped":
                return
            if record.state == "paused":
                # 若模块正等待断线恢复，串行补一次暂停，确保用户手动意图最后生效。
                if self._disconnect_snapshot is not None and name in self._disconnect_snapshot:
                    needs_manual_pause = True
                else:
                    return
            else:
                needs_manual_pause = False
        await self._operate(name, "pause", "paused", manual=True)

    async def resume(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            if record.state in {"stopped", "running"}:
                return
        await self._operate(name, "resume", "running", manual=True)

    async def close(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            if record.state == "stopped":
                return
        await self._operate(name, "close", "stopped", manual=True)

    async def _operate(self, name: str, method: str, target_state: str, manual: bool) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
        # 同一模块的 start/pause/resume/close 串行执行，避免重连恢复与用户操作互相覆盖。
        async with record.operation_lock:
            with self._lock:
                if manual:
                    record.generation += 1
                instance = record.instance
            try:
                result = getattr(instance, method)()
                if inspect.isawaitable(result):
                    await result
                with self._lock:
                    record.state = target_state
                    record.error = ""
            except Exception as exc:
                with self._lock:
                    record.state = "error"
                    record.error = str(exc)[:300]
                logger.exception("模块操作失败：module=%s action=%s", name, method)
                raise

    async def on_connection_lost(self) -> None:
        with self._lock:
            snapshot = {
                name: record.generation
                for name, record in self._modules.items()
                if record.state == "running"
            }
            self._disconnect_snapshot = snapshot
            running = tuple(snapshot)
        for name in running:
            try:
                await self._operate(name, "pause", "paused", manual=False)
            except Exception:
                logger.exception("直播断线时暂停模块失败：%s", name)

    async def on_connection_restored(self) -> None:
        with self._lock:
            snapshot = dict(self._disconnect_snapshot or {})
        for name, generation in snapshot.items():
            with self._lock:
                record = self._modules.get(name)
                should_resume = bool(
                    record is not None
                    and record.generation == generation
                    and record.state == "paused"
                )
            if not should_resume:
                continue
            try:
                await self._operate(name, "resume", "running", manual=False)
            except Exception:
                logger.exception("直播重连后恢复模块失败：%s", name)
        with self._lock:
            self._disconnect_snapshot = None

    async def close_all(self) -> None:
        with self._lock:
            names = tuple(self._modules)
            self._disconnect_snapshot = None
        for name in names:
            with self._lock:
                record = self._modules[name]
                record.generation += 1
                if record.state == "stopped":
                    continue
            try:
                await self._operate(name, "close", "stopped", manual=False)
            except Exception:
                logger.exception("退出时关闭模块失败：%s", name)
