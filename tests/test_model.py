"""Unit tests for Intermediate Representation (IR) models and region mapping."""

import json
from pathlib import Path
import unittest

from proxymatrix.model import (
    GROUP_AI,
    GROUP_DIRECT,
    GROUP_GOOGLE,
    GROUP_MEDIA,
    GROUP_PROXY,
    GROUP_REJECT,
    LogicalGroup,
    ProxyNode,
    RegionDefinition,
    Rule,
    load_regions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestModel(unittest.TestCase):
    def test_proxy_node_creation(self):
        node = ProxyNode(
            name="HK 01",
            type="ss",
            server="hk.example.com",
            port=8388,
            raw_config={"cipher": "aes-128-gcm", "password": "pass"}
        )
        self.assertEqual(node.name, "HK 01")
        self.assertEqual(node.type, "ss")
        self.assertEqual(node.port, 8388)
        clash_dict = node.to_clash_dict()
        self.assertEqual(clash_dict["name"], "HK 01")
        self.assertEqual(clash_dict["cipher"], "aes-128-gcm")

    def test_rule_formatting(self):
        rule = Rule(
            type="DOMAIN-SUFFIX",
            payload="example.com",
            target_id=GROUP_PROXY,
            no_resolve=False
        )
        id_map = {GROUP_PROXY: "🚀 节点选择"}
        self.assertEqual(rule.to_clash_rule(id_map), "DOMAIN-SUFFIX,example.com,🚀 节点选择")

        rule_nr = Rule(
            type="IP-CIDR",
            payload="91.108.0.0/16",
            target_id=GROUP_PROXY,
            no_resolve=True
        )
        self.assertEqual(rule_nr.to_clash_rule(id_map), "IP-CIDR,91.108.0.0/16,🚀 节点选择,no-resolve")

    def test_region_definition_matching(self):
        regions = load_regions(REPO_ROOT / "config/regions.json")
        self.assertTrue(len(regions) >= 5, "Expected at least 5 standard regions")

        hk_region = next((r for r in regions if r.id == "@region_hk"), None)
        self.assertIsNotNone(hk_region)
        self.assertTrue(hk_region.matches("🇭🇰 香港 BGP 01"))
        self.assertTrue(hk_region.matches("HK-IPL-100M"))
        self.assertFalse(hk_region.matches("🇯🇵 东京 01"))

        jp_region = next((r for r in regions if r.id == "@region_jp"), None)
        self.assertIsNotNone(jp_region)
        self.assertTrue(jp_region.matches("🇯🇵 东京 CN2 01"))
        self.assertTrue(jp_region.matches("Japan Tokyo Premium"))
        self.assertFalse(jp_region.matches("US Los Angeles"))

        us_region = next((r for r in regions if r.id == "@region_us"), None)
        self.assertIsNotNone(us_region)
        self.assertTrue(us_region.matches("🇺🇸 美国 洛杉矶 01"))
        self.assertTrue(us_region.matches("US Silicon Valley"))
        self.assertFalse(us_region.matches("SG Cloud 01"))


if __name__ == "__main__":
    unittest.main()
