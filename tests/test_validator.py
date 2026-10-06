"""Unit tests for input validation (P2-1 verification)."""

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from proxymatrix.utils.validator import (
    validate_site_rule,
    validate_subscription_entry,
)


class TestValidator(unittest.TestCase):
    def test_valid_site_rules(self):
        """Verify standard valid routing rules pass validation."""
        valid_cases = [
            {"type": "DOMAIN-SUFFIX", "domain": "example.com", "target": "@proxy"},
            {"type": "DOMAIN", "domain": "api.openai.com", "target": "@ai"},
            {"type": "DOMAIN-KEYWORD", "domain": "google", "target": "@google"},
            {"type": "IP-CIDR", "domain": "192.0.2.0/24", "target": "DIRECT"},
            {"type": "IP-CIDR6", "domain": "2001:db8::/32", "target": "REJECT"},
            {"type": "DOMAIN-SUFFIX", "domain": "hk-stream.dev", "target": "@region_hk"},
            {"type": "DOMAIN-SUFFIX", "domain": "netflix.com", "target": "@media", "name": "Custom Netflix", "enabled": False},
            {"type": "DOMAIN-SUFFIX", "domain": "str-false.com", "target": "@proxy", "enabled": "false"},
            {"type": "DOMAIN-SUFFIX", "domain": "str-true.com", "target": "@proxy", "enabled": "true"},
            {
                "type": "DOMAIN-SUFFIX",
                "domain": "long-name.com",
                "target": "@proxy",
                "name": "A" * 100 + "\n\r\tCleaned",
                "enabled": True
            },
        ]
        for item in valid_cases:
            res = validate_site_rule(item)
            self.assertEqual(res["domain"], item["domain"].lower())
            self.assertEqual(res["type"], item["type"].upper())
            if "name" in item:
                self.assertLessEqual(len(res["name"]), 64)
                self.assertNotIn("\n", res["name"])
                self.assertNotIn("\r", res["name"])
                self.assertNotIn("\t", res["name"])
            if item.get("domain") == "str-false.com":
                self.assertIs(res["enabled"], False)
            elif item.get("domain") == "str-true.com":
                self.assertIs(res["enabled"], True)
            elif "enabled" in item:
                self.assertEqual(res["enabled"], item["enabled"])

    def test_reject_injected_site_rules(self):
        """CRITICAL P2-1 TEST: Ensure malicious rule inputs (newlines, delimiters) are blocked."""
        malicious_cases = [
            # 1. Comma injection (trying to set target directly in domain string)
            {"type": "DOMAIN-SUFFIX", "domain": "example.com,REJECT", "target": "@proxy"},
            # 2. INI section injection via newline
            {"type": "DOMAIN-SUFFIX", "domain": "example.com\n[NewSection]", "target": "@proxy"},
            # 3. Carriage return injection
            {"type": "DOMAIN-SUFFIX", "domain": "example.com\r\nmalicious", "target": "@proxy"},
            # 4. Unknown rule type
            {"type": "CUSTOM-BAD-TYPE", "domain": "example.com", "target": "@proxy"},
            # 5. Invalid CIDR format
            {"type": "IP-CIDR", "domain": "999.999.999.999/24", "target": "DIRECT"},
            # 6. Unknown target group (preventing Clash kernel errors)
            {"type": "DOMAIN-SUFFIX", "domain": "example.com", "target": "@nonexistent_group"},
            # 7. Target injection with newline
            {"type": "DOMAIN-SUFFIX", "domain": "example.com", "target": "@proxy\nREJECT"},
            # 8. Obsolete/wrong group @stream (must be @media)
            {"type": "DOMAIN-SUFFIX", "domain": "example.com", "target": "@stream"},
            # 9. Non-existent region @region_xx
            {"type": "DOMAIN-SUFFIX", "domain": "example.com", "target": "@region_xx"},
        ]
        for item in malicious_cases:
            with self.assertRaises(ValueError, msg=f"Should reject: {item}"):
                validate_site_rule(item)

    def test_valid_subscription_entries(self):
        """Verify standard valid subscription names and URLs."""
        validate_subscription_entry("主力机场", "https://sub.example.com/api/v1/client/subscribe?token=abc")
        validate_subscription_entry("Airport-01_Backup", "http://sub-backup.internal/subscribe")

    def test_reject_malicious_subscriptions(self):
        """CRITICAL P2-1 TEST: Prevent path traversal and control characters in subscriptions."""
        bad_entries = [
            # Path traversal
            ("../../evil_escape", "https://example.com/sub"),
            ("sub/nested", "https://example.com/sub"),
            ("sub\\windows_slash", "https://example.com/sub"),
            # Newline injection into YAML keys
            ("sub\nkey: value", "https://example.com/sub"),
            # Non-http schemes
            ("NormalAirport", "file:///etc/passwd"),
            ("NormalAirport", "javascript:alert(1)"),
            # Control characters in URL
            ("NormalAirport", "https://example.com/sub\nmalicious_header: foo"),
        ]
        for name, url in bad_entries:
            with self.assertRaises(ValueError, msg=f"Should reject subscription ({name}, {url})"):
                validate_subscription_entry(name, url)

    def test_reject_subscription_error_redaction(self):
        """CRITICAL SECURITY TEST: Ensure invalid scheme error redacts path tokens and userinfo."""
        bad_url = "ftp://adminuser:supersecretpass@example.com/link/token12345678"  # leak-audit: allow test-fixture
        try:
            validate_subscription_entry("TestAirport", bad_url)
            self.fail("Should have raised ValueError")
        except ValueError as e:
            err_msg = str(e)
            self.assertNotIn("adminuser", err_msg)
            self.assertNotIn("supersecretpass", err_msg)
            self.assertNotIn("token12345678", err_msg)
            self.assertIn("ftp://example.com/***", err_msg)


if __name__ == "__main__":
    unittest.main()
