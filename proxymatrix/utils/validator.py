"""Input Validation Utilities for ProxyMatrix Web Console and Configurations.

Prevents rule injection, INI newline tampering, YAML key corruption, and path traversal.
Zero third-party dependencies: 100% Python 3 standard library.
"""

import ipaddress
from pathlib import Path
import re
from typing import Any, Dict, Set
import urllib.parse

ALLOWED_RULE_TYPES: Set[str] = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "IP-CIDR",
    "IP-CIDR6",
    "GEOSITE",
    "GEOIP",
}

ALLOWED_TARGET_GROUPS: Set[str] = {
    "@proxy",
    "@ai",
    "@google",
    "@stream",
    "@direct",
    "@reject",
    "@auto",
    "DIRECT",
    "REJECT",
    "PROXY",
}

DOMAIN_REGEX = re.compile(r"^[a-zA-Z0-9-_.]+$")
KEYWORD_REGEX = re.compile(r"^[a-zA-Z0-9-_.]+$")
SUBSCRIPTION_NAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\u4e00-\u9fa5]{1,64}$")


def validate_subscription_entry(name: str, url: str) -> None:
    """Validate subscription provider name and URL."""
    name_clean = (name or "").strip()
    if not SUBSCRIPTION_NAME_REGEX.fullmatch(name_clean):
        raise ValueError(
            f"订阅名称 [{name}] 非法: 仅允许中文、英文字母、数字、下划线及横杠 (长度 1-64)，禁止包含路径分隔符或换行。"
        )

    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"订阅 [{name}] 的 URL 地址不能为空。")

    url_clean = url.strip()
    if any(c in url_clean for c in ("\r", "\n", "\0")):
        raise ValueError(f"订阅 [{name}] 的 URL 包含非法控制字符。")

    parsed = urllib.parse.urlsplit(url_clean)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"订阅 [{name}] 的 URL 必须是有效的 HTTP/HTTPS 地址: {url}")


def validate_site_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a custom site routing rule dictionary."""
    if not isinstance(rule, dict):
        raise ValueError("规则必须是 JSON 对象。")

    rule_type = str(rule.get("type", "DOMAIN-SUFFIX")).strip().upper()
    if rule_type not in ALLOWED_RULE_TYPES:
        raise ValueError(f"不支持的规则类型: {rule_type}。允许类型: {sorted(ALLOWED_RULE_TYPES)}")

    domain = str(rule.get("domain", "")).strip().lower()
    if not domain or any(c in domain for c in ("\r", "\n", ",", " ", "\t")):
        raise ValueError(f"规则域名/特征 [{domain}] 包含非法字符或换行符。")

    if rule_type in ("DOMAIN", "DOMAIN-SUFFIX"):
        if not DOMAIN_REGEX.fullmatch(domain):
            raise ValueError(f"非法域名格式: {domain}")
    elif rule_type == "DOMAIN-KEYWORD":
        if not KEYWORD_REGEX.fullmatch(domain):
            raise ValueError(f"非法关键字格式: {domain}")
    elif rule_type in ("IP-CIDR", "IP-CIDR6"):
        try:
            ipaddress.ip_network(domain, strict=False)
        except ValueError as e:
            raise ValueError(f"非法 CIDR 网络地址 [{domain}]: {e}")

    target = str(rule.get("target", rule.get("group", "@proxy"))).strip()
    if any(c in target for c in ("\r", "\n", ",", " ", "\t")):
        raise ValueError(f"规则目标策略组 [{target}] 包含非法字符。")

    if target not in ALLOWED_TARGET_GROUPS and not target.startswith("@region_"):
        raise ValueError(f"目标策略组 [{target}] 未定义或未知。")

    res = {
        "type": rule_type,
        "domain": domain,
        "target": target,
    }
    if "id" in rule and rule["id"]:
        res["id"] = str(rule["id"])
    return res
