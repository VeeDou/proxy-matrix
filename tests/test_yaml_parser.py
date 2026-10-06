"""Unit tests for the custom YAML subscription parser (R2 verification)."""

import json
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from proxymatrix.utils.yaml_parser import (
    parse_clash_yaml_proxies,
    parse_flow_value,
    parse_scalar,
)


class TestYamlParser(unittest.TestCase):
    def test_scalar_type_preservation(self):
        """Verify strict scalar parsing: quoted values remain str; unquoted are cast properly."""
        # 1. Quoted digits remain string
        self.assertIsInstance(parse_scalar("'123456'"), str)
        self.assertEqual(parse_scalar("'123456'"), "123456")
        self.assertIsInstance(parse_scalar('"123456"'), str)
        self.assertEqual(parse_scalar('"123456"'), "123456")

        # 2. Hex / short-id strings remain string
        self.assertEqual(parse_scalar('"0e12"'), "0e12")
        self.assertEqual(parse_scalar("'0e12'"), "0e12")

        # 3. Quoted booleans remain string
        self.assertIsInstance(parse_scalar('"true"'), str)
        self.assertEqual(parse_scalar('"true"'), "true")
        self.assertIsInstance(parse_scalar("'no'"), str)
        self.assertEqual(parse_scalar("'no'"), "no")

        # 4. Unquoted numbers and booleans are converted
        self.assertEqual(parse_scalar("443"), 443)
        self.assertIsInstance(parse_scalar("443"), int)
        self.assertEqual(parse_scalar("true"), True)
        self.assertEqual(parse_scalar("false"), False)
        self.assertIs(parse_scalar("null"), None)

    def test_flow_mapping_and_sequence(self):
        """Verify flow mappings {...} and flow sequences [...] parsing."""
        raw_flow = '{name: "HK Inline 01", type: ss, port: 8388, password: "sample-password", alpn: [h2, http/1.1], udp: true}'
        res = parse_flow_value(raw_flow)

        self.assertIsInstance(res, dict)
        self.assertEqual(res["name"], "HK Inline 01")
        self.assertEqual(res["type"], "ss")
        self.assertEqual(res["port"], 8388)
        self.assertIsInstance(res["port"], int)
        self.assertEqual(res["udp"], True)
        self.assertEqual(res["alpn"], ["h2", "http/1.1"])

    def test_nested_options_reality_and_ws(self):
        """Verify complex block-style proxies with nested reality-opts and ws-opts headers."""
        yaml_content = """
mode: rule
proxies:
  - name: "HK Reality"
    type: vless
    server: 192.0.2.1
    port: 443
    uuid: "00000000-0000-0000-0000-000000000000"
    password: '123456'
    network: tcp
    tls: true
    reality-opts:
      public-key: "sample-reality-pubkey"
      short-id: "0e12"
    client-fingerprint: chrome
  - name: "HK WebSocket"
    type: vmess
    server: 192.0.2.2
    port: 8443
    uuid: "11111111-1111-1111-1111-111111111111"
    alterId: 0
    cipher: auto
    network: ws
    ws-opts:
      path: "/chat"
      headers:
        Host: "example.com"
        User-Agent: "CustomUA"
    alpn:
      - h2
      - http/1.1
"""
        nodes = parse_clash_yaml_proxies(yaml_content)
        self.assertEqual(len(nodes), 2)

        # Node 1: Reality
        n1 = nodes[0]
        self.assertEqual(n1["name"], "HK Reality")
        self.assertEqual(n1["type"], "vless")
        self.assertEqual(n1["password"], "123456")
        self.assertIsInstance(n1["password"], str, "Quoted numeric password MUST stay str")
        self.assertIsInstance(n1["reality-opts"], dict)
        self.assertEqual(n1["reality-opts"]["public-key"], "sample-reality-pubkey")
        self.assertEqual(n1["reality-opts"]["short-id"], "0e12")

        # Node 2: WS
        n2 = nodes[1]
        self.assertEqual(n2["name"], "HK WebSocket")
        self.assertEqual(n2["type"], "vmess")
        self.assertIsInstance(n2["ws-opts"], dict)
        self.assertEqual(n2["ws-opts"]["path"], "/chat")
        self.assertIsInstance(n2["ws-opts"]["headers"], dict)
        self.assertEqual(n2["ws-opts"]["headers"]["Host"], "example.com")
        self.assertEqual(n2["ws-opts"]["headers"]["User-Agent"], "CustomUA")
        self.assertEqual(n2["alpn"], ["h2", "http/1.1"])

    def test_mixed_flow_and_block_subscriptions(self):
        """Verify subscriptions mixing inline flow nodes and standard block nodes."""
        yaml_content = """
proxies:
  - {name: "Node 1", type: ss, server: 192.0.2.10, port: 8388, cipher: aes-128-gcm, password: "pass"}
  - {name: "Node 2", type: trojan, server: 192.0.2.11, port: 443, password: "pass", sni: "test.com"}
  - name: "Node 3"
    type: hysteria2
    server: 192.0.2.12
    port: 443
    password: "pass"
"""
        nodes = parse_clash_yaml_proxies(yaml_content)
        self.assertEqual(len(nodes), 3)
        self.assertEqual(nodes[0]["name"], "Node 1")
        self.assertEqual(nodes[1]["name"], "Node 2")
        self.assertEqual(nodes[2]["name"], "Node 3")


    def test_trailing_comment_outside_quotes(self):
        """CRITICAL P3 TEST: Ensure quoted values with trailing comment (#) parse cleanly."""
        yaml_content = """
proxies:
  - name: "Node # 1" # trailing comment
    type: ss # shadowsocks cipher
    server: 192.0.2.10 # ip
    port: 8388 # port
    password: "pass#word" # comment
    cipher: aes-128-gcm
"""
        nodes = parse_clash_yaml_proxies(yaml_content)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["name"], "Node # 1")
        self.assertEqual(nodes[0]["type"], "ss")
        self.assertEqual(nodes[0]["port"], 8388)
        self.assertEqual(nodes[0]["password"], "pass#word")


if __name__ == "__main__":
    unittest.main()
