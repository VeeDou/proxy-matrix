"""Unit tests for redact and error sanitization utilities."""

from pathlib import Path
import unittest

from proxymatrix.utils.redact import redact_text, sanitize_exception


class TestRedact(unittest.TestCase):
    def test_url_token_redaction(self):
        url = "https://sub.example.com/api/v1/client/subscribe?token=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"  # leak-audit: allow
        redacted = redact_text(url)
        self.assertNotIn("a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6", redacted)
        self.assertIn("token=***", redacted)

    def test_hex64_redaction(self):
        token_64 = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"  # leak-audit: allow
        text = f"Invalid connection token: {token_64}"
        redacted = redact_text(text)
        self.assertNotIn(token_64, redacted)
        self.assertIn("123456...cdef", redacted)

    def test_user_path_redaction(self):
        mac_path = "/Users/secretuser/Projects/proxy-matrix/config.yaml"  # leak-audit: allow
        redacted = redact_text(mac_path)
        self.assertNotIn("secretuser", redacted)
        self.assertIn("/Users/***/Projects", redacted)

        linux_path = "/home/john/projects/config.yaml"  # leak-audit: allow
        redacted_linux = redact_text(linux_path)
        self.assertNotIn("john", redacted_linux)
        self.assertIn("/home/***/projects", redacted_linux)

    def test_sanitize_exception(self):
        try:
            raise ValueError("Failed to fetch https://airport.com/sub?token=secret12345678901234567890")  # leak-audit: allow
        except ValueError as e:
            msg = sanitize_exception(e)
            self.assertIn("ValueError:", msg)
            self.assertNotIn("secret12345678901234567890", msg)
            self.assertIn("token=***", msg)


if __name__ == "__main__":
    unittest.main()
