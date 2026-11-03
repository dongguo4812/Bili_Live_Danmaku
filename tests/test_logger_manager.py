# -*- coding: utf-8 -*-
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from core.logger_manager import _cleanup_old_logs, _redact


class LoggerRedactionTests(unittest.TestCase):
    def test_secret_fields_are_redacted(self):
        value = _redact('SESSDATA="cookie-value" password=obs-pass api_key=api-value')
        self.assertNotIn("cookie-value", value)
        self.assertNotIn("obs-pass", value)
        self.assertNotIn("api-value", value)
        self.assertIn("SESSDATA=***", value)

    def test_log_retention_removes_only_expired_date_named_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            today = date.today()
            expired = directory / f"{(today - timedelta(days=3)).isoformat()}.log"
            recent = directory / f"{today.isoformat()}.log"
            unrelated = directory / "legacy-output.log"
            for path in (expired, recent, unrelated):
                path.write_text("test", encoding="utf-8")

            with patch("core.logger_manager.LOG_DIR", directory):
                _cleanup_old_logs(2)

            self.assertFalse(expired.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())

    def test_zero_log_retention_keeps_existing_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            old_log = directory / "2000-01-01.log"
            old_log.write_text("test", encoding="utf-8")
            with patch("core.logger_manager.LOG_DIR", directory):
                _cleanup_old_logs(0)
            self.assertTrue(old_log.exists())

    def test_cookie_and_authorization_headers_are_redacted(self):
        value = _redact("Cookie: SESSDATA=secret; bili_jct=secret2\nAuthorization: Bearer access-token")
        self.assertNotIn("secret", value)
        self.assertNotIn("access-token", value)
        self.assertIn("Cookie: ***", value)
        self.assertIn("Authorization: ***", value)


if __name__ == "__main__":
    unittest.main()
