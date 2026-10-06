"""Core Intermediate Representation (IR) data models for ProxyMatrix.

Defines client-agnostic representations of:
- Proxy Nodes
- Logical Proxy Groups (@proxy, @ai, @google, etc.)
- Routing Rules (with logical target IDs)
- Regional Definitions
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# Standard Logical Group IDs
GROUP_PROXY = "@proxy"      # Primary manual proxy selection
GROUP_AUTO = "@auto"        # Auto latency selection
GROUP_AI = "@ai"            # Dedicated AI services (OpenAI, Claude, Perplexity, etc.)
GROUP_GOOGLE = "@google"    # Google infrastructure & accounts
GROUP_MEDIA = "@media"      # Streaming services (Netflix, YouTube, Spotify, etc.)
GROUP_DIRECT = "@direct"    # Direct connect
GROUP_REJECT = "@reject"    # Blocked / Ad / Leak prevention


@dataclass
class ProxyNode:
    """Standard proxy node representation."""
    name: str
    type: str  # ss, vmess, vless, trojan, hysteria2, etc.
    server: str
    port: int
    raw_config: Dict[str, Any] = field(default_factory=dict)

    def to_clash_dict(self) -> Dict[str, Any]:
        """Return dict suitable for Clash / Mihomo proxies section."""
        d = dict(self.raw_config)
        d["name"] = self.name
        d["type"] = self.type
        d["server"] = self.server
        d["port"] = self.port
        return d


@dataclass
class LogicalGroup:
    """Logical proxy group representation."""
    id: str                         # e.g. '@proxy', '@ai', '@region_hk'
    display_name: str               # e.g. '🚀 节点选择', '🤖 AI 服务'
    type: str = "select"            # select, url-test, fallback, load-balance
    proxies: List[str] = field(default_factory=list)  # list of proxy names or child group IDs
    url: Optional[str] = None       # Health check URL
    interval: Optional[int] = None  # Health check interval in seconds
    tolerance: Optional[int] = None # Tolerance in ms for url-test
    filter: Optional[str] = None    # Regex filter for dynamic node matching
    empty_fallback: Optional[str] = "REJECT" # Mihomo fallback on 0 nodes


@dataclass
class Rule:
    """Routing rule with logical target ID."""
    type: str                       # DOMAIN, DOMAIN-SUFFIX, IP-CIDR, GEOIP, GEOSITE, AND, FINAL
    payload: str                    # e.g. 'google.com', 'cn', '192.168.0.0/16'
    target_id: str                  # e.g. '@google', '@ai', '@direct', '@proxy'
    no_resolve: bool = False
    raw_params: List[str] = field(default_factory=list)

    def to_clash_rule(self, id_to_name_map: Dict[str, str]) -> str:
        """Render standard Clash rule string with resolved display name."""
        if self.target_id in (GROUP_DIRECT, "DIRECT", "@direct"):
            target_name = "DIRECT"
        elif self.target_id in (GROUP_REJECT, "REJECT", "@reject"):
            target_name = "REJECT"
        else:
            target_name = id_to_name_map.get(self.target_id, self.target_id)

        if self.type == "FINAL":
            return f"MATCH,{target_name}"

        parts = [self.type, self.payload, target_name]
        if self.no_resolve:
            parts.append("no-resolve")
        return ",".join(parts)

    def to_shadowrocket_rule(self, id_to_name_map: Dict[str, str]) -> Optional[str]:
        """Render standard Shadowrocket rule string."""
        if self.target_id in (GROUP_DIRECT, "DIRECT", "@direct"):
            target_name = "DIRECT"
        elif self.target_id in (GROUP_REJECT, "REJECT", "@reject"):
            target_name = "REJECT"
        elif self.target_id in (GROUP_PROXY, "PROXY", "@proxy"):
            target_name = "PROXY"
        else:
            target_name = id_to_name_map.get(self.target_id, self.target_id)

        if self.type == "FINAL":
            return f"FINAL,{target_name}"
        if self.type == "AND":
            return f"AND,{self.payload},{target_name}"
        if self.type == "GEOSITE":
            if self.payload == "google":
                return f"DOMAIN-SUFFIX,google.com,{target_name}\nDOMAIN-SUFFIX,googleapis.com,{target_name}"
            # Shadowrocket does not natively support GEOSITE tags; drop gracefully
            return None
        if self.type == "GEOIP":
            payload_upper = self.payload.upper()
            if payload_upper == "PRIVATE":
                # Shadowrocket does not support GEOIP,private; expand to RFC 1918 / loopback CIDRs
                return (
                    f"IP-CIDR,127.0.0.0/8,{target_name},no-resolve\n"
                    f"IP-CIDR,172.16.0.0/12,{target_name},no-resolve\n"
                    f"IP-CIDR,192.168.0.0/16,{target_name},no-resolve\n"
                    f"IP-CIDR,10.0.0.0/8,{target_name},no-resolve\n"
                    f"IP-CIDR,100.64.0.0/10,{target_name},no-resolve\n"
                    f"IP-CIDR6,fc00::/7,{target_name},no-resolve\n"
                    f"IP-CIDR6,fe80::/10,{target_name},no-resolve\n"
                    f"IP-CIDR6,::1/128,{target_name},no-resolve"
                )
            if payload_upper == "CN":
                # Crucial domestic routing fix (Decision M3):
                # 1. Direct-route all .cn top-level domains.
                # 2. Drop no-resolve for GEOIP,CN in Shadowrocket so unlisted domestic domains
                #    are resolved by local domestic DNS (223.5.5.5) and matched to DIRECT instead of leaking to PROXY.
                return f"DOMAIN-SUFFIX,cn,{target_name}\nGEOIP,CN,{target_name}"

        parts = [self.type, self.payload, target_name]
        if self.no_resolve:
            parts.append("no-resolve")
        return ",".join(parts)


def parse_rule_string(r_str: str) -> Rule:
    """Parse raw comma-separated rule string into intermediate representation Rule."""
    parts = [p.strip() for p in r_str.split(",")]
    if not parts:
        raise ValueError("Empty rule string")
    rule_type = parts[0]

    if rule_type in ("MATCH", "FINAL"):
        target = parts[1] if len(parts) > 1 else GROUP_PROXY
        return Rule(type="FINAL", payload="", target_id=target)

    if rule_type == "AND":
        # e.g. AND,((PROTOCOL,UDP),(DEST-PORT,443)),REJECT
        payload = parts[1] if len(parts) > 1 else ""
        target = parts[2] if len(parts) > 2 else GROUP_REJECT
        return Rule(type="AND", payload=payload, target_id=target)

    payload = parts[1] if len(parts) > 1 else ""
    # In standard Clash rule: TYPE,PAYLOAD,TARGET[,no-resolve]
    no_resolve = any(p.lower() == "no-resolve" for p in parts[2:])

    # Find the target: first element after payload that is not "no-resolve"
    target_id = GROUP_PROXY
    for p in parts[2:]:
        if p.lower() != "no-resolve":
            target_id = p
            break

    return Rule(type=rule_type, payload=payload, target_id=target_id, no_resolve=no_resolve)


@dataclass
class RegionDefinition:
    """Regional matching definition."""
    id: str                         # e.g. '@region_hk'
    display_name: str               # e.g. '🇭🇰 香港节点'
    keywords: List[str]             # ['香港', 'HK', 'Hong Kong']
    regex_pattern: str              # regex matching node names
    compiled_re: re.Pattern = field(init=False)

    def __post_init__(self):
        self.compiled_re = re.compile(self.regex_pattern, re.IGNORECASE)

    def matches(self, node_name: str) -> bool:
        """Check if proxy node name belongs to this region."""
        return bool(self.compiled_re.search(node_name))


def load_regions(config_path: Path) -> List[RegionDefinition]:
    """Load regional definitions from JSON file."""
    if not config_path.is_file():
        raise FileNotFoundError(f"Regions configuration not found at {config_path}")
    raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    return [
        RegionDefinition(
            id=item["id"],
            display_name=item["display_name"],
            keywords=item.get("keywords", []),
            regex_pattern=item["regex_pattern"],
        )
        for item in raw_data
    ]
