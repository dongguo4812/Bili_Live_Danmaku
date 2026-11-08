# -*- coding: utf-8 -*-
"""可控的守护阻塞任务执行器。

asyncio 默认线程池的 worker 会被解释器退出钩子等待；对 Selenium/网络等
无法安全强杀的同步调用，使用 daemon worker，避免单个卡住的调用拖住进程退出。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("bili_live_danmaku.blocking")


class DaemonExecutor(concurrent.futures.Executor):
    """固定数量 daemon worker；排队数量由调用侧 semaphore 限制。"""

    def __init__(self, max_workers: int = 4, max_pending: int = 32) -> None:
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_pending)
        self._shutdown = False
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"bili-blocking-{index + 1}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> concurrent.futures.Future[Any]:
        future: concurrent.futures.Future[Any] = concurrent.futures.Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("阻塞任务执行器已经关闭。")
            try:
                self._queue.put_nowait((future, fn, args, kwargs))
            except queue.Full as exc:
                raise RuntimeError("阻塞任务队列已满，拒绝继续堆积任务。") from exc
        return future

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                future, fn, args, kwargs = item
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    future.set_exception(exc)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False, timeout: float = 1.0) -> None:
        with self._lock:
            if self._shutdown:
                threads = tuple(self._threads)
            else:
                self._shutdown = True
                if cancel_futures:
                    while True:
                        try:
                            item = self._queue.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            if item is not None:
                                item[0].cancel()
                        finally:
                            self._queue.task_done()
                for _ in self._threads:
                    self._queue.put_nowait(None)
                threads = tuple(self._threads)
        if wait:
            for thread in threads:
                thread.join(max(0.0, timeout))


_EXECUTOR_CONTEXT: contextvars.ContextVar[DaemonExecutor | None] = contextvars.ContextVar(
    "bili_blocking_executor", default=None
)
_SEMAPHORE_CONTEXT: contextvars.ContextVar[asyncio.Semaphore | None] = contextvars.ContextVar(
    "bili_blocking_semaphore", default=None
)


def install_blocking_executor(executor: DaemonExecutor, max_pending: int = 32):
    """在运行时事件循环线程中安装执行器上下文。"""
    return (
        _EXECUTOR_CONTEXT.set(executor),
        _SEMAPHORE_CONTEXT.set(asyncio.Semaphore(max_pending)),
    )


def reset_blocking_executor(tokens) -> None:
    _EXECUTOR_CONTEXT.reset(tokens[0])
    _SEMAPHORE_CONTEXT.reset(tokens[1])


async def run_blocking(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """在线程中运行同步函数；取消等待不会伪装成同步函数已停止。"""
    executor = _EXECUTOR_CONTEXT.get()
    semaphore = _SEMAPHORE_CONTEXT.get()
    if executor is None or semaphore is None:
        # 支持独立 asyncio 测试环境；主程序运行时始终安装守护执行器。
        return await asyncio.to_thread(fn, *args, **kwargs)

    loop = asyncio.get_running_loop()
    await semaphore.acquire()
    context = contextvars.copy_context()
    try:
        future = executor.submit(context.run, fn, *args, **kwargs)
    except BaseException:
        semaphore.release()
        raise

    def release_slot(_future: concurrent.futures.Future[Any]) -> None:
        try:
            loop.call_soon_threadsafe(semaphore.release)
        except RuntimeError:
            # 事件循环已经结束；worker 仍是 daemon，不会阻止进程退出。
            pass

    future.add_done_callback(release_slot)
    wrapped = asyncio.wrap_future(future, loop=loop)
    # shield 保证取消调用方协程时不会错误地把仍在运行的同步任务标记为已完成。
    return await asyncio.shield(wrapped)
