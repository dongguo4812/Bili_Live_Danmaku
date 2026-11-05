# -*- coding: utf-8 -*-
"""独立业务模块生命周期管理、断线恢复和手动操作优先级。"""
from __future__ import annotations

import asyncio
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
    state: str = "stopped"
    generation: int = 0
    error: str = ""
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    auto_start_on_connect: bool = False
    requires_connection: bool = True


class ModuleManager:
    """每个模块独立控制；断线恢复只恢复断线前运行且未被用户改动的模块。"""

    _TRANSITIONS = {
        "start": "starting",
        "pause": "pausing",
        "resume": "resuming",
        "close": "closing",
    }

    def __init__(self) -> None:
        self._modules: dict[str, ModuleRecord] = {}
        self._lock = threading.RLock()
        self._disconnect_snapshot: dict[str, int] | None = None
        self._connection_active = False
        self._shutting_down = False

    def register(
        self,
        name: str,
        module: Any,
        initial_state: str = "stopped",
        auto_start_on_connect: bool = False,
        requires_connection: bool = True,
    ) -> None:
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
            self._modules[name] = ModuleRecord(
                name=name,
                instance=module,
                state=initial_state,
                auto_start_on_connect=bool(auto_start_on_connect),
                requires_connection=bool(requires_connection),
            )

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
            if record.requires_connection and not self._connection_active:
                raise RuntimeError("B站直播尚未完成 WebSocket 认证，不能启动模块。")
        if state in {"running", "starting", "resuming"}:
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
            if record.state in {"stopped", "closing"}:
                return
            if record.state in {"paused", "pausing"}:
                # 断线自动暂停过程中/完成后用户再次点击暂停，仍需排队执行手动暂停。
                if self._disconnect_snapshot is None or name not in self._disconnect_snapshot:
                    return
        await self._operate(name, "pause", "paused", manual=True)

    async def resume(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            if record.state in {"stopped", "running", "starting", "resuming", "closing"}:
                return
            if record.requires_connection and not self._connection_active:
                raise RuntimeError("B站直播尚未完成 WebSocket 认证，不能恢复模块。")
        await self._operate(name, "resume", "running", manual=True)

    async def close(self, name: str) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")
            if record.state == "stopped":
                # 即使模块尚未启动，用户明确点击“关闭”也应覆盖首次连接自动启动。
                record.generation += 1
                return
            if record.state == "closing":
                return
        await self._operate(name, "close", "stopped", manual=True)

    async def _operate(
        self,
        name: str,
        method: str,
        target_state: str,
        manual: bool,
        expected_generation: int | None = None,
        expected_states: tuple[str, ...] | None = None,
    ) -> None:
        with self._lock:
            record = self._modules.get(name)
            if record is None:
                raise KeyError(f"未知模块：{name}")

        # 同一模块的生命周期操作串行执行，避免 Chrome/洛雪资源竞争和断线恢复覆盖用户操作。
        async with record.operation_lock:
            with self._lock:
                if expected_generation is not None and record.generation != expected_generation:
                    return
                if expected_states is not None and record.state not in expected_states:
                    return
                if manual:
                    record.generation += 1
                record.state = self._TRANSITIONS[method]
                instance = record.instance

            try:
                result = getattr(instance, method)()
                if inspect.isawaitable(result):
                    await result

                final_state = target_state
                # 启动/恢复期间如果连接已经断开，资源启动完成后立即暂停。
                if method in {"start", "resume"}:
                    with self._lock:
                        connection_active = self._connection_active
                        requires_connection = record.requires_connection
                    if requires_connection and not connection_active:
                        pause_result = instance.pause()
                        if inspect.isawaitable(pause_result):
                            await pause_result
                        final_state = "paused"

                with self._lock:
                    record.state = final_state
                    record.error = ""
            except Exception as exc:
                with self._lock:
                    record.state = "error"
                    record.error = str(exc)[:300]
                logger.exception("模块操作失败：module=%s action=%s", name, method)
                raise

    def begin_shutdown(self) -> None:
        """同步封锁新的连接状态回调，避免退出过程中再次暂停业务模块。"""
        with self._lock:
            self._shutting_down = True
            self._connection_active = False
            self._disconnect_snapshot = None

    async def on_connection_lost(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._connection_active = False
            snapshot = {
                name: record.generation
                for name, record in self._modules.items()
                if record.requires_connection and record.state in {"running", "starting", "resuming"}
            }
            self._disconnect_snapshot = snapshot
            names = tuple(snapshot)

        for name in names:
            try:
                await self._operate(
                    name,
                    "pause",
                    "paused",
                    manual=False,
                    expected_generation=snapshot[name],
                    expected_states=("running", "starting", "resuming"),
                )
            except Exception:
                logger.exception("直播断线时暂停模块失败：%s", name)

    async def on_connection_restored(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._connection_active = True
            first_connection = self._disconnect_snapshot is None
            snapshot = dict(self._disconnect_snapshot or {})
            auto_start_names = [
                name for name, record in self._modules.items()
                if first_connection and record.auto_start_on_connect and record.state == "stopped" and record.generation == 0
            ]

        if first_connection:
            # 首次完成真实 WebSocket 认证后才启动配置为自动启动的展示模块。
            for name in auto_start_names:
                try:
                    await self._operate(name, "start", "running", manual=False)
                except Exception:
                    logger.exception("首次连接后启动模块失败：%s", name)
            return

        for name, generation in snapshot.items():
            try:
                await self._operate(
                    name,
                    "resume",
                    "running",
                    manual=False,
                    expected_generation=generation,
                    expected_states=("paused",),
                )
            except Exception:
                logger.exception("直播重连后恢复模块失败：%s", name)

        with self._lock:
            self._disconnect_snapshot = None

    async def shutdown_all(self) -> None:
        """程序退出专用清理：优先调用模块的 shutdown，避免复用手动关闭语义。"""
        self.begin_shutdown()
        with self._lock:
            # 按注册顺序逆序清理：后注册的依赖模块（如视频依赖歌曲模块）
            # 先释放资源并完成必要恢复，再关闭其依赖模块。
            records = tuple(reversed(tuple(self._modules.values())))

        for record in records:
            async with record.operation_lock:
                with self._lock:
                    instance = record.instance
                    shutdown = getattr(instance, "shutdown", None)
                    # shutdown 是资源兜底清理入口，即使管理器状态已经是 stopped
                    # 也必须执行（例如状态不同步但 Selenium driver 仍然存在）。
                    # 没有专用 shutdown 的普通模块则沿用 stopped 跳过行为。
                    if not callable(shutdown) and record.state == "stopped":
                        continue
                    record.state = "closing"
                try:
                    result = shutdown() if callable(shutdown) else instance.close()
                    if inspect.isawaitable(result):
                        await result
                    with self._lock:
                        record.state = "stopped"
                        record.error = ""
                except Exception as exc:
                    with self._lock:
                        record.state = "error"
                        record.error = str(exc)[:300]
                    logger.exception("程序退出时清理模块失败：%s", record.name)

    async def close_all(self) -> None:
        with self._lock:
            self._connection_active = False
            self._disconnect_snapshot = None
            names = tuple(self._modules)

        for name in names:
            with self._lock:
                record = self._modules[name]
                if record.state == "stopped":
                    continue
            try:
                await self._operate(name, "close", "stopped", manual=False)
            except Exception:
                logger.exception("退出时关闭模块失败：%s", name)
