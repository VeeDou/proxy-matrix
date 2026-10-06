"""Unit tests for Shadowrocket lossy conversion report and dropped rules tracking."""

from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from proxymatrix.targets.shadowrocket_conf import (
    LossyConversionReport,
    compile_shadowrocket_conf,
)


class TestLossyReport(unittest.TestCase):
    def test_lossy_report_tracking_and_markdown_generation(self):
        """CRITICAL TEST: Ensure dropped GEOSITE rules and converted rules are faithfully tracked."""
        report = LossyConversionReport()
        custom_sites = [
            {"domain": "custom-active.com", "type": "DOMAIN-SUFFIX", "group": "@proxy", "enabled": True},
            {"domain": "custom-disabled.com", "type": "DOMAIN-SUFFIX", "group": "@media", "enabled": False},
        ]
        conf_text = compile_shadowrocket_conf(
            root_dir=REPO_ROOT,
            custom_sites=custom_sites,
            dns_preset="cn-mainland",
            report=report
        )

        self.assertTrue(len(conf_text) > 0)
        self.assertTrue(report.total_rules > 0)
        self.assertTrue(report.emitted_rules > 0)

        # 1. Verify GEOSITE,private and GEOSITE,cn are recorded in dropped_rules
        dropped_rule_names = [d["rule"] for d in report.dropped_rules]
        self.assertIn("GEOSITE,private,DIRECT", dropped_rule_names)
        self.assertIn("GEOSITE,cn,DIRECT", dropped_rule_names)

        # 2. Verify disabled custom site is recorded as dropped
        self.assertIn("DOMAIN-SUFFIX,custom-disabled.com", dropped_rule_names)

        # 3. Verify GEOSITE,google and MATCH,@proxy are recorded in converted_rules
        converted_rule_names = [c["original"] for c in report.converted_rules]
        self.assertIn("GEOSITE,google,@google", converted_rule_names)
        self.assertIn("MATCH,@proxy", converted_rule_names)

        # 4. Verify Markdown report output
        md_text = report.to_markdown()
        self.assertIn("# Shadowrocket 规则转换与有损降级报告", md_text)
        self.assertIn("GEOSITE,private,DIRECT", md_text)
        self.assertIn("GEOSITE,cn,DIRECT", md_text)
        self.assertIn("GEOSITE,google,@google", md_text)
        self.assertIn("MATCH,@proxy", md_text)
        self.assertIn("udp-policy-not-supported-behaviour = REJECT", md_text)


if __name__ == "__main__":
    unittest.main()
