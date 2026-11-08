# -*- coding: utf-8 -*-
"""Regression tests for QR login polling concurrency."""
from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bilibili_client import QRLogin


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}
        self.history = []

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class BlockingPollSession:
    def __init__(self, response):
        self.response = response
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.close_calls = 0

    def get(self, *_args, **_kwargs):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        return self.response

    def close(self):
        self.close_calls += 1


class FakeImage:
    def save(self, buffer, format):
        buffer.write(b"fake-png")


class QRLoginConcurrencyTests(unittest.TestCase):
    def make_waiting_login(self, session):
        login = QRLogin(Path("credentials/bilibili.json"))
        with login.lock:
            login.session = session
            login.qrcode_key = "old-key"
            login.state = "waiting"
            login.expires_at = time.time() + 60
        return login

    def test_overlapping_polls_do_not_share_one_requests_session(self):
        session = BlockingPollSession(FakeResponse({"data": {"code": 86101}}))
        login = self.make_waiting_login(session)
        first_result = []
        worker = threading.Thread(target=lambda: first_result.append(login.poll()), daemon=True)
        worker.start()
        try:
            self.assertTrue(session.entered.wait(1))
            second_result = login.poll()
            self.assertEqual(session.calls, 1)
            self.assertEqual(second_result["state"], "waiting")
        finally:
            session.release.set()
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(first_result[0]["state"], "waiting")

    def test_expired_qr_closes_its_http_session(self):
        session = BlockingPollSession(FakeResponse({"data": {"code": 86101}}))
        login = self.make_waiting_login(session)
        login.expires_at = time.time() - 1

        result = login.poll()

        self.assertEqual(result["state"], "expired")
        self.assertIsNone(login.session)
        self.assertEqual(session.close_calls, 1)

    def test_failed_qr_start_closes_new_http_session(self):
        class FailingSession:
            def __init__(self):
                self.headers = {}
                self.close_calls = 0

            def get(self, *_args, **_kwargs):
                raise RuntimeError("network failure")

            def close(self):
                self.close_calls += 1

        session = FailingSession()
        login = QRLogin(Path("credentials/bilibili.json"))
        with patch("bilibili_client.requests.Session", return_value=session):
            with self.assertRaisesRegex(RuntimeError, "network failure"):
                login.start()

        self.assertEqual(session.close_calls, 1)
        self.assertEqual(login.status()["state"], "error")

    def test_old_poll_cannot_overwrite_new_qr_state(self):
        old_session = BlockingPollSession(FakeResponse({"data": {"code": 86090}}))
        login = self.make_waiting_login(old_session)
        worker = threading.Thread(target=login.poll, daemon=True)
        worker.start()
        self.assertTrue(old_session.entered.wait(1))

        new_session = type("NewSession", (), {
            "headers": {},
            "get": lambda self, *_args, **_kwargs: FakeResponse({
                "code": 0,
                "data": {"qrcode_key": "new-key", "url": "https://qr.example/"},
            }),
        })()
        try:
            with patch("bilibili_client.requests.Session", return_value=new_session), patch(
                "bilibili_client.qrcode.make", return_value=FakeImage()
            ):
                result = login.start()
            self.assertTrue(result["ok"])
            self.assertEqual(login.status()["state"], "waiting")
            self.assertEqual(login.qrcode_key, "new-key")
        finally:
            old_session.release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(login.status()["state"], "waiting")
        self.assertEqual(login.qrcode_key, "new-key")
        self.assertEqual(old_session.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
