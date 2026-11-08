# -*- coding: utf-8 -*-
"""可复用的专用 asyncio 运行时，供统一事件总线和业务模块使用。"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any

from core.blocking import DaemonExecutor, install_blocking_executor, reset_blocking_executor

logger = logging.getLogger("bili_live_danmaku.runtime")


class AsyncRuntime:
    def __init__(self, name: str = "bili-async-runtime") -> None:
        self.name = name
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.RLock()
        self._blocking_executor: DaemonExecutor | None = None

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def start(self, timeout: float = 10.0) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._stopped.clear()
            self._blocking_executor = DaemonExecutor(max_workers=4, max_pending=32)
            self._thread = threading.Thread(target=self._thread_main, name=self.name, daemon=True)
            self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("等待统一异步运行时启动超时。")
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("统一异步运行时未能创建事件循环。")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        executor = self._blocking_executor
        if executor is None:
            raise RuntimeError("阻塞任务执行器尚未初始化。")
        tokens = install_blocking_executor(executor)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                # 最终兜底取消也必须有上限；阻塞同步函数由 daemon worker 承载，
                # 不允许默认线程池 shutdown 无限等待。
                try:
                    _, unfinished = loop.run_until_complete(
                        asyncio.wait(pending, timeout=2.0)
                    )
                    if unfinished:
                        logger.warning(
                            "异步运行时关闭时仍有 %s 个任务未完成：%s",
                            len(unfinished),
                            [task.get_name() for task in unfinished],
                        )
                except Exception:
                    logger.exception("等待异步任务取消时发生异常。")
            try:
                loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), timeout=2.0))
            except Exception:
                logger.warning("异步生成器清理超时或失败。", exc_info=True)
            finally:
                executor.shutdown(wait=True, cancel_futures=True, timeout=1.0)
                reset_blocking_executor(tokens)
                loop.close()
                self._loop = None
                self._stopped.set()

    def submit(self, coroutine: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise RuntimeError("统一异步运行时尚未启动或已经关闭。")
        return asyncio.run_coroutine_threadsafe(coroutine, loop)

    def call_soon(self, callback: Any, *args: Any) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError("统一异步运行时尚未启动或已经关闭。")
        loop.call_soon_threadsafe(callback, *args)

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            loop = self._loop
            thread = self._thread
            if loop is None or thread is None:
                return
            if not loop.is_closed():
                loop.call_soon_threadsafe(loop.stop)
        if thread.is_alive():
            thread.join(timeout)
        if thread.is_alive():
            logger.warning("统一异步运行时未能在 %.1f 秒内退出。", timeout)
