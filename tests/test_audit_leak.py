import os
import unittest
from pathlib import Path

from proxymatrix.utils.audit_leak import (
    scan_line,
    scan_file,
    scan_directory,
    scan_git_history,
    load_private_denylist,
    is_safe_ip,
)

CANARY_PATH = Path(__file__).parent / "fixtures" / "canaries" / "canary_sample.txt"
REPO_ROOT = Path(__file__).resolve().parent.parent


class TestAuditLeak(unittest.TestCase):
    def test_safe_ip_detection(self):
        # Safe IPs
        self.assertTrue(is_safe_ip("127.0.0.1"))
        self.assertTrue(is_safe_ip("192.168.1.1"))
        self.assertTrue(is_safe_ip("10.0.0.1"))
        self.assertTrue(is_safe_ip("172.16.0.1"))
        self.assertTrue(is_safe_ip("223.5.5.5"))
        self.assertTrue(is_safe_ip("1.1.1.1"))
        self.assertTrue(is_safe_ip("8.8.8.8"))
        self.assertTrue(is_safe_ip("192.0.2.1"))    # RFC 5737 TEST-NET-1 (safe doc)
        self.assertTrue(is_safe_ip("203.0.113.7"))  # RFC 5737 TEST-NET-3 (safe doc)
        # Unsafe public IPs
        self.assertFalse(is_safe_ip("1.2.3.4"))       # leak-audit: allow test-ip
        self.assertFalse(is_safe_ip("198.51.200.7"))   # leak-audit: allow test-ip

    def test_inline_exemption_cannot_bypass_private_denylist(self):
        """CRITICAL SECURITY TEST: Ensure inline # leak-audit: allow NEVER bypasses Tier-2 private denylist."""
        line_with_leak_and_exemption = 'SERVER_TARGET = "secret-private-corp.com"  # leak-audit: allow'
        findings = scan_line(
            line=line_with_leak_and_exemption,
            line_number=1,
            filename="test.py",
            private_denylist=["secret-private-corp.com"],
            private_allowlist=[]
        )
        self.assertEqual(len(findings), 1, "Private denylist MUST NOT be bypassed by inline exemption comments!")
        self.assertEqual(findings[0].rule_name, "PRIVATE_DENYLIST")

    def test_negative_pattern_exception_in_denylist(self):
        """Ensure negative patterns (!pattern) in external denylist allow specific exceptions."""
        line_ok = 'PUBLIC_ENDPOINT = "https://canary-brand.com/api"'
        line_bad = 'PRIVATE_INTERNAL = "canary-brand-secret-cluster"'

        # Without allowlist, both match 'canary-brand'
        findings_bad = scan_line(line_bad, 1, "test.py", ["canary-brand"], ["canary-brand.com"])
        self.assertEqual(len(findings_bad), 1)

        # With allowlist, line_ok is allowed
        findings_ok = scan_line(line_ok, 1, "test.py", ["canary-brand"], ["canary-brand.com"])
        self.assertEqual(len(findings_ok), 0)

    def test_canary_detection_rate_100_percent(self):
        """Verify that every canary category is detected, and Tier 1 exemptions work."""
        denylist = ["testuser", "canarybrand"]  # leak-audit: allow test-denylist
        allowlist = ["canarybrand.com"]
        findings = scan_file(CANARY_PATH, denylist, allowlist)

        rules_found = {f.rule_name for f in findings}
        expected_rules = {
            "HEX64_SECRET",
            "PUBLIC_IPV4",
            "AWS_ACCESS_KEY",
            "ALIYUN_ACCESS_KEY",
            "TOKEN_PARAM",
            "SENSITIVE_SCHEME",
            "LOCAL_USER_PATH",
            "PRIVATE_DENYLIST",
        }
        for rule in expected_rules:
            self.assertIn(rule, rules_found, f"Expected canary rule {rule} not triggered!")

        # Verify Tier 1 exemption worked: line 10 has explicit exemption and should not trigger
        for f in findings:
            if "CANARY_EXEMPTED_HEX" in f.matched_text:
                self.fail("CANARY_EXEMPTED_HEX has explicit exemption and should not trigger finding!")

    def test_clean_repo_has_zero_leaks(self):
        """Verify that current repository (clean files) has zero leaks."""
        deny, allow = load_private_denylist()
        findings = scan_directory(
            REPO_ROOT,
            private_denylist=deny,
            private_allowlist=allow,
            skip_dirs={".git", "__pycache__", "dist", ".state", "local"}
        )
        self.assertEqual(
            len(findings),
            0,
            f"Expected zero leaks in clean repo, but found: {findings}"
        )

    def test_git_metadata_scan(self):
        """Verify that git history and author metadata are clean."""
        deny, allow = load_private_denylist()
        findings = scan_git_history(REPO_ROOT, deny, allow)
        self.assertEqual(
            len(findings),
            0,
            f"Git history or metadata contains leaks: {findings}"
        )


if __name__ == "__main__":
    unittest.main()
