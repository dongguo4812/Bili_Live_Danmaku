# -*- coding: utf-8 -*-
"""Regression tests for bounded application shutdown primitives."""
from __future__ import annotations

import asyncio
import threading
import unittest

from core.application import ApplicationRuntime
from core.async_runtime import AsyncRuntime
from core.blocking import DaemonExecutor, run_blocking
from core.event_bus import EventBus
from core.module_manager import ModuleManager


class DaemonExecutorTests(unittest.TestCase):
    def test_workers_are_daemon_and_submit_returns_result(self):
        executor = DaemonExecutor(max_workers=1, max_pending=2)
        try:
            self.assertTrue(executor._threads[0].daemon)
            self.assertEqual(executor.submit(lambda: 42).result(timeout=1), 42)
        finally:
            executor.shutdown(wait=True, cancel_futures=True, timeout=1)
        self.assertFalse(executor._threads[0].is_alive())

    def test_submit_preserves_exceptions(self):
        executor = DaemonExecutor(max_workers=1, max_pending=2)
        try:
            future = executor.submit(lambda: 1 / 0)
            with self.assertRaises(ZeroDivisionError):
                future.result(timeout=1)
        finally:
            executor.shutdown(wait=True, cancel_futures=True, timeout=1)


class AsyncRuntimeShutdownTests(unittest.TestCase):
    def test_stuck_blocking_call_does_not_hold_runtime_shutdown(self):
        runtime = AsyncRuntime()
        started = threading.Event()
        release = threading.Event()

        async def blocking_call():
            return await run_blocking(lambda: (started.set(), release.wait(15)))

        runtime.start()
        future = runtime.submit(blocking_call())
        try:
            self.assertTrue(started.wait(2))
            runtime.stop(timeout=4)
            self.assertIsNotNone(runtime._thread)
            self.assertFalse(runtime._thread.is_alive())
        finally:
            release.set()
            if runtime._thread and runtime._thread.is_alive():
                runtime.stop(timeout=4)


class EventBusShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_rejects_new_events(self):
        bus = EventBus()
        received = []

        async def handler(event):
            received.append(event["text"])

        bus.subscribe("danmaku", handler)
        await bus.publish({"type": "danmaku", "text": "before"})
        bus.begin_shutdown()
        await bus.publish({"type": "danmaku", "text": "after"})
        await bus.shutdown(timeout=0.1)
        self.assertEqual(received, ["before"])



class ApplicationRuntimeEventBackpressureTests(unittest.TestCase):
    def test_pending_event_dispatches_are_bounded_and_slots_are_released(self):
        runtime = ApplicationRuntime(max_pending_events=1)
        handler_started = threading.Event()
        handler_finished = threading.Event()
        release_holder = []

        async def handler(event):
            if event.get("text") == "hold":
                release = asyncio.Event()
                release_holder.append(release)
                handler_started.set()
                await release.wait()
            handler_finished.set()

        runtime.events.subscribe("danmaku", handler)
        runtime.start()
        try:
            self.assertTrue(runtime.publish_event({"type": "danmaku", "text": "hold"}))
            self.assertTrue(handler_started.wait(2))
            self.assertFalse(runtime.publish_event({"type": "danmaku", "text": "overflow"}))

            runtime.async_runtime.call_soon(release_holder[0].set)
            self.assertTrue(handler_finished.wait(2))
            self.assertTrue(runtime._event_slots.acquire(timeout=2))
            runtime._event_slots.release()

            # The completion callback must return the capacity for later events.
            handler_finished.clear()
            self.assertTrue(runtime.publish_event({"type": "danmaku", "text": "after"}))
            self.assertTrue(handler_finished.wait(2))
        finally:
            if release_holder:
                runtime.async_runtime.call_soon(release_holder[0].set)
            runtime.shutdown(timeout=5)


class ModuleManagerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_begin_shutdown_rejects_queued_manual_close(self):
        manager = ModuleManager()
        closed = []

        class FakeModule:
            async def start(self):
                pass

            async def pause(self):
                pass

            async def resume(self):
                pass

            async def close(self):
                closed.append(True)

        manager.register("music", FakeModule(), initial_state="running")
        manager.begin_shutdown()
        await manager.close("music")
        self.assertEqual(closed, [])

    async def test_one_stuck_module_does_not_block_later_cleanup(self):
        manager = ModuleManager()
        blocker_started = asyncio.Event()
        first_closed = asyncio.Event()

        class FakeModule:
            def __init__(self, shutdown):
                self._shutdown = shutdown

            async def start(self):
                pass

            async def pause(self):
                pass

            async def resume(self):
                pass

            async def close(self):
                pass

            async def shutdown(self):
                await self._shutdown()

        async def close_first():
            first_closed.set()

        async def block_forever():
            blocker_started.set()
            await asyncio.Event().wait()

        manager.register("first", FakeModule(close_first), initial_state="running")
        manager.register("blocker", FakeModule(block_forever), initial_state="running")
        await manager.shutdown_all(timeout=0.5, per_module_timeout=0.1)

        self.assertTrue(blocker_started.is_set())
        self.assertTrue(first_closed.is_set())
        self.assertEqual(manager.list_status()["blocker"]["state"], "error")
        self.assertEqual(manager.list_status()["first"]["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
