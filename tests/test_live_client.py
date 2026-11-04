# -*- coding: utf-8 -*-
"""B站直播连接工作线程生命周期回归测试。"""
from __future__ import annotations

import threading
import unittest

from bilibili_client import BilibiliClientError, LiveRoomClient


class LiveRoomClientLifecycleTests(unittest.TestCase):
    def make_client(self):
        states = []
        client = LiveRoomClient(
            on_event=lambda _event: None,
            on_state=lambda state, message: states.append((state, message)),
        )
        return client, states

    def test_stop_waits_for_worker_and_reports_disconnected_only_after_exit(self):
        client, states = self.make_client()

        def worker():
            client.stop_event.wait()

        thread = threading.Thread(target=worker, daemon=True)
        client.thread = thread
        thread.start()

        self.assertTrue(client.stop(timeout=1))
        self.assertFalse(thread.is_alive())
        self.assertEqual([state for state, _ in states], ["stopping", "disconnected"])
        self.assertIn("正在断开", states[0][1])

    def test_stop_timeout_keeps_stopping_state_without_false_success(self):
        client, states = self.make_client()
        release_worker = threading.Event()
        thread = threading.Thread(target=release_worker.wait, daemon=True)
        client.thread = thread
        thread.start()

        try:
            self.assertFalse(client.stop(timeout=0.01))
            self.assertTrue(thread.is_alive())
            self.assertEqual([state for state, _ in states], ["stopping"])
        finally:
            release_worker.set()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())

    def test_start_rejects_a_worker_that_has_not_finished_stopping(self):
        client, _states = self.make_client()
        release_worker = threading.Event()
        thread = threading.Thread(target=release_worker.wait, daemon=True)
        client.thread = thread
        thread.start()

        try:
            with self.assertRaisesRegex(BilibiliClientError, "已经在运行"):
                client.start(123456, {"sessdata": "test"})
        finally:
            release_worker.set()
            thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
