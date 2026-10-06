"""Golden tests and compiler verification for Clash, Shadowrocket CONF, and Shadowrocket YAML."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from proxymatrix.targets.clash import ClashCompiler, dict_to_yaml
from proxymatrix.targets.shadowrocket_conf import compile_shadowrocket_conf
from proxymatrix.targets.shadowrocket_yaml import (
    compile_shadowrocket_yaml,
    filter_and_format_proxies,
    load_subscription_nodes,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
import manage


class TestCompilers(unittest.TestCase):
    def setUp(self):
        self.compiler = ClashCompiler(REPO_ROOT)
        self.sample_urls = {
            "Airport-A": "https://airport-a.example.com/api/v1/client/subscribe?token=EXAMPLE_TOKEN_AAA",
            "Airport-B": "https://airport-b.example.com/api/v1/client/subscribe?token=EXAMPLE_TOKEN_BBB",
        }
        self.sample_sites = [
            {"domain": "custom-work.internal", "type": "DOMAIN-SUFFIX", "target": "DIRECT"},
            {"domain": "special-proxy.dev", "type": "DOMAIN-SUFFIX", "target": "@proxy"},
        ]

    def test_clash_compiler_output(self):
        """Verify Clash / Mihomo compiler produces clean YAML with all policy groups and DoH policy."""
        yaml_text = self.compiler.compile(
            urls=self.sample_urls,
            providers_meta={"interval": 86400, "health_check_interval": 300},
            custom_sites=self.sample_sites,
        )

        self.assertIn('mode: "rule"', yaml_text)
        self.assertIn('"proxy-providers":', yaml_text)
        self.assertIn('"Airport-A":', yaml_text)
        self.assertIn('"Airport-B":', yaml_text)
        self.assertIn('"proxy-groups":', yaml_text)
        self.assertIn("🚀 节点选择", yaml_text)
        self.assertIn("🤖 AI 服务", yaml_text)
        self.assertIn("🌐 谷歌服务", yaml_text)
        self.assertIn('"nameserver-policy":', yaml_text)
        self.assertIn("rules:", yaml_text)
        self.assertIn("DOMAIN-SUFFIX,custom-work.internal,DIRECT", yaml_text)
        self.assertIn("DOMAIN-SUFFIX,special-proxy.dev,🚀 节点选择", yaml_text)

    def test_shadowrocket_conf_compiler_output(self):
        """Verify Shadowrocket INI compiler output adheres to strict security defaults and has 0 @ residue."""
        conf_text = compile_shadowrocket_conf(
            root_dir=REPO_ROOT,
            custom_sites=self.sample_sites,
            dns_preset="cn-mainland",
        )

        # 1. Strict UDP REJECT verification (prevents real IP leaks)
        self.assertIn("udp-policy-not-supported-behaviour = REJECT", conf_text)

        # 2. Prepend QUIC block rule
        self.assertIn("AND,((PROTOCOL,UDP),(DEST-PORT,443)),REJECT", conf_text)

        # 3. DNS settings
        self.assertIn("dns-server = 223.5.5.5, 119.29.29.29", conf_text)
        self.assertIn("dns-direct-system = true", conf_text)

        # 4. Sections
        self.assertIn("[General]", conf_text)
        self.assertIn("[Rule]", conf_text)
        self.assertIn("[Proxy Group]", conf_text)

        # 5. Dynamic Region Groups with include-all-proxies=true
        self.assertIn("Hong Kong = select, include-all-proxies=true, policy-regex-filter=", conf_text)
        self.assertIn("Japan = select, include-all-proxies=true, policy-regex-filter=", conf_text)
        self.assertIn("Singapore = select, include-all-proxies=true, policy-regex-filter=", conf_text)

        # 6. Zero unresolved @ residue in Shadowrocket rules
        self.assertNotIn("@", conf_text, "All logical IDs must be fully resolved with no '@' remaining!")

        # 7. Domestic and private routing rules (B2 verification)
        self.assertIn("DOMAIN-SUFFIX,cn,DIRECT", conf_text, "Must have DOMAIN-SUFFIX,cn,DIRECT for domestic sites")
        self.assertIn("GEOIP,CN,DIRECT", conf_text, "Must have GEOIP,CN,DIRECT")
        self.assertNotIn("GEOIP,CN,DIRECT,no-resolve", conf_text, "GEOIP,CN must NOT have no-resolve (must resolve unlisted domains)")
        self.assertNotIn("GEOIP,private", conf_text, "GEOIP,private must be expanded into concrete CIDRs")
        self.assertIn("IP-CIDR,10.0.0.0/8,DIRECT,no-resolve", conf_text)
        self.assertIn("IP-CIDR,172.16.0.0/12,DIRECT,no-resolve", conf_text)
        self.assertIn("IP-CIDR,192.168.0.0/16,DIRECT,no-resolve", conf_text)

    def test_shadowrocket_yaml_compiler_output(self):
        """Verify Shadowrocket proxies YAML output properly formats and disambiguates nodes."""
        # 1. Configured URLs without cache raises FileNotFoundError unless fallback explicitly requested
        with self.assertRaises(FileNotFoundError):
            load_subscription_nodes(REPO_ROOT, self.sample_urls, allow_sample_fallback=False)

        # 2. Configured URLs with explicit fallback allowed
        nodes, is_sample = load_subscription_nodes(REPO_ROOT, self.sample_urls, allow_sample_fallback=True)
        self.assertTrue(is_sample)
        yaml_text = compile_shadowrocket_yaml(nodes, is_sample=is_sample)

        self.assertIn("[WARNING: DEMO MODE]", yaml_text)
        self.assertIn("proxies:", yaml_text)
        self.assertIn("[Airport-A] 🇭🇰 香港 BGP 01", yaml_text)
        self.assertIn("[Airport-A] 🇯🇵 东京 CN2 01", yaml_text)
        self.assertIn("[Airport-B] 🇸🇬 新加坡 01", yaml_text)

        # 3. Pure demo mode (no URLs configured)
        demo_nodes, demo_sample = load_subscription_nodes(REPO_ROOT, urls={})
        self.assertTrue(demo_sample)
        self.assertIn("SampleAirport", demo_nodes)

    def test_shadowrocket_yaml_filtering_and_deduplication(self):
        """Verify traffic info nodes are excluded and duplicate names are disambiguated."""
        raw_nodes = [
            {"name": "剩余流量 500GB", "type": "ss", "server": "192.0.2.1", "port": 80},
            {"name": "套餐到期 2026-12-31", "type": "ss", "server": "192.0.2.1", "port": 80},
            {"name": "HK Premium", "type": "ss", "server": "192.0.2.1", "port": 80},
            {"name": "HK Premium", "type": "ss", "server": "192.0.2.2", "port": 80},
        ]
        clean = filter_and_format_proxies("TestAirport", raw_nodes)

        self.assertEqual(len(clean), 2, "Traffic and expiration nodes must be stripped")
        self.assertEqual(clean[0]["name"], "[TestAirport] HK Premium")
        self.assertEqual(clean[1]["name"], "[TestAirport] HK Premium (2)")

    def test_golden_clash_yaml(self):
        """Verify compiled Clash YAML matches golden fixture byte-for-byte."""
        golden_file = REPO_ROOT / "tests/fixtures/golden/golden_clash.yaml"
        self.assertTrue(golden_file.is_file(), "Golden Clash YAML fixture must exist")
        expected = golden_file.read_text(encoding="utf-8")
        actual_raw = self.compiler.compile(
            urls=manage.load_urls(),
            providers_meta=manage.load_json("subscriptions/providers.json", "subscriptions/providers.example.json"),
            custom_sites=manage.load_sites(),
        )
        marker = "# proxy-matrix-managed: v1\n"
        actual = marker + actual_raw if not actual_raw.startswith(marker) else actual_raw
        self.assertEqual(actual, expected)

    def test_golden_shadowrocket_conf(self):
        """Verify compiled Shadowrocket CONF matches golden fixture."""
        golden_file = REPO_ROOT / "tests/fixtures/golden/golden_shadowrocket.conf"
        self.assertTrue(golden_file.is_file(), "Golden Shadowrocket CONF fixture must exist")
        expected = golden_file.read_text(encoding="utf-8")
        actual = compile_shadowrocket_conf(
            root_dir=REPO_ROOT,
            custom_sites=manage.load_sites(),
            dns_preset="cn-mainland",
        )
        self.assertEqual(actual, expected)

    def test_golden_shadowrocket_yaml(self):
        """Verify compiled Shadowrocket YAML matches golden fixture."""
        golden_file = REPO_ROOT / "tests/fixtures/golden/golden_shadowrocket.yaml"
        self.assertTrue(golden_file.is_file(), "Golden Shadowrocket YAML fixture must exist")
        expected = golden_file.read_text(encoding="utf-8")
        nodes, is_sample = load_subscription_nodes(REPO_ROOT, manage.load_urls(), allow_sample_fallback=True)
        actual = compile_shadowrocket_yaml(nodes, is_sample=is_sample)
        self.assertEqual(actual, expected)

    @unittest.skipUnless(
        shutil.which("verge-mihomo") or Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo").is_file(),
        "Mihomo / Clash Verge core not installed on system"
    )
    def test_golden_clash_syntax_with_mihomo_core(self):
        """Validate golden Clash configuration directly with real local verge-mihomo core."""
        core_path = shutil.which("verge-mihomo") or "/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo"
        golden_file = REPO_ROOT / "tests/fixtures/golden/golden_clash.yaml"
        with tempfile.TemporaryDirectory() as td:
            res = subprocess.run([str(core_path), "-t", "-f", str(golden_file), "-d", td], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Mihomo validation error:\n{res.stdout}\n{res.stderr}")
            self.assertIn("test is successful", res.stdout + res.stderr)


    def test_yaml_boundary_cases(self):
        """Verify YAML serializer handles regex backslashes, short-id, numeric strings, and wildcard keys."""
        test_data = {
            "mode": "rule",
            "nameserver-policy": {
                "*.example.com": "223.5.5.5",
                "+.google.com": "https://dns.google/dns-query"
            },
            "proxy-groups": [
                {
                    "name": "RegexGroup",
                    "type": "select",
                    "filter": r"(?:HK|\d{2})",
                    "proxies": ["DIRECT"]
                }
            ],
            "proxies": [
                {
                    "name": "SpecialNode",
                    "type": "vless",
                    "server": "192.0.2.1",
                    "port": 443,
                    "uuid": "00000000-0000-0000-0000-000000000000",
                    "short-id": "0e12",
                    "password": "123456",
                    "sni": "yes",
                    "custom-header": "  spaced  ",
                }
            ]
        }
        yaml_out = dict_to_yaml(test_data)

        # 1. Assert short-id is quoted as string (not parsed as float 0)
        self.assertIn('"short-id": "0e12"', yaml_out)
        # 2. Assert numeric password is quoted as string (not parsed as int)
        self.assertIn('password: "123456"', yaml_out)
        # 3. Assert YAML 1.1 boolean keyword string is quoted (not boolean)
        self.assertIn('sni: "yes"', yaml_out)
        # 4. Assert regex backslash is escaped properly
        self.assertIn('filter: "(?:HK|\\\\d{2})"', yaml_out)
        # 5. Assert wildcard key is quoted
        self.assertIn('"*.example.com": "223.5.5.5"', yaml_out)

        # 6. If local Mihomo kernel is installed, validate directly against kernel
        core_path = Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo")
        if core_path.is_file():
            with tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / "config.yaml"
                cfg.write_text(yaml_out, encoding="utf-8")
                res = subprocess.run([str(core_path), "-t", "-f", str(cfg), "-d", td], capture_output=True, text=True)
                self.assertEqual(res.returncode, 0, f"Mihomo validation failed on boundary YAML:\n{res.stdout}\n{res.stderr}")


if __name__ == "__main__":
    unittest.main()
