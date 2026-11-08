# -*- coding: utf-8 -*-
"""可控的守护阻塞任务执行器。

asyncio 默认线程池的 worker 会被解释器退出钩子等待；对无法安全强杀的同步调用，
使用 daemon worker，避免单个卡住的调用拖住进程退出。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import logging
import queue
import threading
import weakref
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
            if not self._shutdown:
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


_EXECUTORS: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[DaemonExecutor, asyncio.Semaphore]] = weakref.WeakKeyDictionary()
_EXECUTORS_LOCK = threading.RLock()


def install_blocking_executor(loop: asyncio.AbstractEventLoop, executor: DaemonExecutor, max_pending: int = 32) -> None:
    """将执行器注册到目标事件循环；不依赖跨线程 ContextVar 传播。"""
    with _EXECUTORS_LOCK:
        _EXECUTORS[loop] = (executor, asyncio.Semaphore(max_pending))


def reset_blocking_executor(loop: asyncio.AbstractEventLoop) -> None:
    with _EXECUTORS_LOCK:
        _EXECUTORS.pop(loop, None)


async def run_blocking(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """在线程中运行同步函数；取消等待不会伪装成同步函数已停止。"""
    loop = asyncio.get_running_loop()
    with _EXECUTORS_LOCK:
        registration = _EXECUTORS.get(loop)
    if registration is None:
        # 支持独立 asyncio 测试环境；主程序运行时始终安装守护执行器。
        return await asyncio.to_thread(fn, *args, **kwargs)

    executor, semaphore = registration
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
    return await asyncio.shield(wrapped)
