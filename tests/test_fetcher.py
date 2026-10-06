"""Unit tests for subscription fetcher, URI parsers, and 0600 profile security (S1 verification)."""

import base64
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from proxymatrix.sources.fetcher import (
    fetch_subscription,
    parse_hysteria2_uri,
    parse_proxy_uri,
    parse_ss_uri,
    parse_subscription_nodes,
    parse_trojan_uri,
    parse_vless_uri,
    parse_vmess_uri,
    safe_b64decode,
    save_profile_yaml,
)


class TestFetcher(unittest.TestCase):
    def test_ss_uri_parsing(self):
        # SIP002 format
        # base64("aes-128-gcm:pass123") = YWVzLTEyOC1nY206cGFzczEyMw==
        uri1 = "ss://YWVzLTEyOC1nY206cGFzczEyMw==@192.0.2.1:8388#HongKong-01"  # leak-audit: allow test-fixture
        node1 = parse_ss_uri(uri1)
        self.assertIsNotNone(node1)
        self.assertEqual(node1["name"], "HongKong-01")
        self.assertEqual(node1["type"], "ss")
        self.assertEqual(node1["server"], "192.0.2.1")
        self.assertEqual(node1["port"], 8388)
        self.assertEqual(node1["cipher"], "aes-128-gcm")
        self.assertEqual(node1["password"], "pass123")
        self.assertTrue(node1["udp"])

        # Legacy base64 format: base64("aes-256-gcm:mypass@192.0.2.2:8443")
        legacy_bytes = base64.b64encode(b"aes-256-gcm:mypass@192.0.2.2:8443").decode("ascii")
        uri2 = f"ss://{legacy_bytes}#Tokyo-02"  # leak-audit: allow test-fixture
        node2 = parse_ss_uri(uri2)
        self.assertIsNotNone(node2)
        self.assertEqual(node2["name"], "Tokyo-02")
        self.assertEqual(node2["server"], "192.0.2.2")
        self.assertEqual(node2["port"], 8443)
        self.assertEqual(node2["cipher"], "aes-256-gcm")
        self.assertEqual(node2["password"], "mypass")

    def test_vmess_uri_parsing(self):
        vmess_data = {
            "v": "2",
            "ps": "Singapore-VMess",
            "add": "192.0.2.3",
            "port": "443",
            "id": "b0000000-0000-0000-0000-000000000001",
            "aid": "0",
            "net": "ws",
            "path": "/v2ray",
            "host": "sg.example.com",
            "tls": "tls"
        }
        b64_json = base64.b64encode(json.dumps(vmess_data).encode("utf-8")).decode("ascii")
        uri = f"vmess://{b64_json}"  # leak-audit: allow test-fixture
        node = parse_vmess_uri(uri)
        self.assertIsNotNone(node)
        self.assertEqual(node["name"], "Singapore-VMess")
        self.assertEqual(node["type"], "vmess")
        self.assertEqual(node["server"], "192.0.2.3")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["uuid"], "b0000000-0000-0000-0000-000000000001")
        self.assertTrue(node["tls"])
        self.assertEqual(node["network"], "ws")
        self.assertEqual(node["ws-opts"]["path"], "/v2ray")
        self.assertEqual(node["ws-opts"]["headers"]["Host"], "sg.example.com")

    def test_trojan_uri_parsing(self):
        uri = "trojan://trojanpass@192.0.2.4:443?sni=us.example.com#US-Trojan"  # leak-audit: allow test-fixture
        node = parse_trojan_uri(uri)
        self.assertIsNotNone(node)
        self.assertEqual(node["name"], "US-Trojan")
        self.assertEqual(node["type"], "trojan")
        self.assertEqual(node["server"], "192.0.2.4")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["password"], "trojanpass")
        self.assertEqual(node["sni"], "us.example.com")

    def test_vless_uri_parsing(self):
        # VLESS with Reality
        uri = "vless://c0000000-0000-0000-0000-000000000001@192.0.2.5:443?security=reality&sni=de.example.com&pbk=pubkey123&sid=ab12#Germany-Reality"  # leak-audit: allow test-fixture
        node = parse_vless_uri(uri)
        self.assertIsNotNone(node)
        self.assertEqual(node["name"], "Germany-Reality")
        self.assertEqual(node["type"], "vless")
        self.assertEqual(node["server"], "192.0.2.5")
        self.assertTrue(node["tls"])
        self.assertEqual(node["reality-opts"]["public-key"], "pubkey123")
        self.assertEqual(node["reality-opts"]["short-id"], "ab12")
        self.assertEqual(node["servername"], "de.example.com")

    def test_hysteria2_uri_parsing(self):
        uri = "hysteria2://hypass@192.0.2.6:443?sni=kr.example.com#Korea-Hy2"  # leak-audit: allow test-fixture
        node = parse_hysteria2_uri(uri)
        self.assertIsNotNone(node)
        self.assertEqual(node["name"], "Korea-Hy2")
        self.assertEqual(node["type"], "hysteria2")
        self.assertEqual(node["server"], "192.0.2.6")
        self.assertEqual(node["port"], 443)
        self.assertEqual(node["password"], "hypass")
        self.assertEqual(node["sni"], "kr.example.com")

    def test_base64_encoded_subscription_feed(self):
        """CRITICAL S1 TEST: Decode raw Base64 subscription with multiple node URIs."""
        uris = [
            "trojan://trojanpass@192.0.2.4:443?sni=us.example.com#US-01",  # leak-audit: allow test-fixture
            "hysteria2://hypass@192.0.2.6:443?sni=kr.example.com#KR-01",  # leak-audit: allow test-fixture
        ]
        b64_feed = base64.b64encode("\n".join(uris).encode("utf-8")).decode("ascii")

        nodes = parse_subscription_nodes(b64_feed)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0]["name"], "US-01")
        self.assertEqual(nodes[1]["name"], "KR-01")

    def test_clash_yaml_subscription_feed(self):
        yaml_content = (
            "proxies:\n"
            "  - name: HK-Node-01\n"
            "    type: ss\n"
            "    server: 192.0.2.10\n"
            "    port: 8388\n"
            "    cipher: aes-128-gcm\n"
            "    password: secret\n"
        )
        nodes = parse_subscription_nodes(yaml_content)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["name"], "HK-Node-01")

    def test_save_profile_yaml_strict_0600_permissions(self):
        """CRITICAL S1 SECURITY TEST: Ensure profiles/<name>.yaml is written with mode 0600."""
        with tempfile.TemporaryDirectory() as td:
            target_profile = Path(td) / "test_airport.yaml"
            nodes = [
                {"name": "Node-01", "type": "ss", "server": "192.0.2.1", "port": 8388, "cipher": "aes-128-gcm", "password": "pass"}
            ]
            save_profile_yaml(target_profile, nodes, provider_name="test_airport")
            self.assertTrue(target_profile.is_file())

            # Check file permissions (on POSIX systems: 0o600 = owner read/write only)
            mode = stat.S_IMODE(target_profile.stat().st_mode)
            self.assertEqual(mode, 0o600, f"Profile file permissions must be strictly 0600, got: {oct(mode)}")

    def test_fetch_subscription_error_sanitization(self):
        """CRITICAL S1 SECURITY TEST: Ensure subscription errors redact sensitive tokens from URLs."""
        with tempfile.TemporaryDirectory() as td:
            secret_url = "https://sub.example.com/api/v1/client/subscribe?token=secret_hex_token_12345678"  # leak-audit: allow test-fixture
            # Intentionally mock urlopen to raise an exception containing the URL
            with patch("urllib.request.urlopen", side_effect=Exception(f"Connection refused to {secret_url}")):
                res = fetch_subscription("TestAirport", secret_url, profiles_dir=Path(td))
                self.assertEqual(res["status"], "failed")
                self.assertNotIn("secret_hex_token_12345678", res["error"])
                self.assertNotIn("secret_hex_token_12345678", res["url"])
                self.assertIn("token=***", res["error"])
                self.assertEqual(res["url"], "https://sub.example.com/***")


if __name__ == "__main__":
    unittest.main()
