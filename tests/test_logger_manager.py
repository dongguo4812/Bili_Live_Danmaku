# -*- coding: utf-8 -*-
import unittest

from core.logger_manager import _redact


class LoggerRedactionTests(unittest.TestCase):
    def test_secret_fields_are_redacted(self):
        value = _redact('SESSDATA="cookie-value" password=obs-pass api_key=api-value')
        self.assertNotIn("cookie-value", value)
        self.assertNotIn("obs-pass", value)
        self.assertNotIn("api-value", value)
        self.assertIn("SESSDATA=***", value)

    def test_cookie_and_authorization_headers_are_redacted(self):
        value = _redact("Cookie: SESSDATA=secret; bili_jct=secret2\nAuthorization: Bearer access-token")
        self.assertNotIn("secret", value)
        self.assertNotIn("access-token", value)
        self.assertIn("Cookie: ***", value)
        self.assertIn("Authorization: ***", value)


if __name__ == "__main__":
    unittest.main()
