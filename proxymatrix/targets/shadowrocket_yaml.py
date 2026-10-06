"""Shadowrocket Node Subscription Compiler (shadowrocket.yaml).

Produces a clean, proxies-only YAML subscription suitable for iOS Shadowrocket.
Strips rules, routing, and groups, preserving full node protocol parameters.
Zero third-party dependencies: 100% Python 3 standard library.
"""

from collections import Counter
from pathlib import Path
import re
from typing import Any, Dict, List, Optional
from proxymatrix.targets.clash import dict_to_yaml

DEFAULT_EXCLUDE_FILTER = re.compile(
    r'(?i)剩余流量|套餐到期|到期时间|官网|官方网站|重置时间|^Auto\s+[0-9.]+\s*(GB|MB|TB)'
)


def parse_clash_yaml_proxies(yaml_text: str) -> List[Dict[str, Any]]:
    """Parse proxies list from standard Clash/Mihomo YAML without pyyaml."""
    lines = yaml_text.splitlines()
    in_proxies = False
    proxies: List[Dict[str, Any]] = []
    current_node: Optional[Dict[str, Any]] = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Check section boundaries
        if not line.startswith(" ") and not line.startswith("\t"):
            if stripped.startswith("proxies:"):
                in_proxies = True
                continue
            elif in_proxies and ":" in stripped and not stripped.startswith("-"):
                in_proxies = False
                break

        if not in_proxies:
            continue

        # Inside proxies section
        if stripped.startswith("-"):
            if current_node:
                proxies.append(current_node)
            current_node = {}
            item_content = stripped[1:].strip()
            if ":" in item_content:
                k, v = item_content.split(":", 1)
                k = k.strip().strip("'\"")
                v = v.strip().strip("'\"")
                if v.lower() == "true":
                    v = True
                elif v.lower() == "false":
                    v = False
                elif v.isdigit():
                    v = int(v)
                current_node[k] = v
        elif current_node is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            k = k.strip().strip("'\"")
            v = v.strip().strip("'\"")
            if v.lower() == "true":
                v = True
            elif v.lower() == "false":
                v = False
            elif v.isdigit():
                v = int(v)
            current_node[k] = v

    if current_node:
        proxies.append(current_node)

    return proxies


def load_proxies_from_file(file_path: Path) -> List[Dict[str, Any]]:
    """Load and parse proxies from local Clash YAML subscription file."""
    if not file_path.is_file():
        return []
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    return parse_clash_yaml_proxies(content)


def filter_and_format_proxies(
    provider_name: str,
    raw_nodes: List[Dict[str, Any]],
    exclude_pattern: Optional[re.Pattern] = DEFAULT_EXCLUDE_FILTER
) -> List[Dict[str, Any]]:
    """Clean and disambiguate proxies for a provider."""
    formatted = []
    name_counts: Counter = Counter()

    for node in raw_nodes:
        if not isinstance(node, dict):
            continue
        raw_name = str(node.get("name", "Node")).strip()
        if exclude_pattern and exclude_pattern.search(raw_name):
            continue

        clean_node = dict(node)
        # Add provider prefix if not already present
        prefix = f"[{provider_name}] "
        if not raw_name.startswith(prefix):
            target_name = f"{prefix}{raw_name}"
        else:
            target_name = raw_name

        # Disambiguate duplicate names
        name_counts[target_name] += 1
        if name_counts[target_name] > 1:
            target_name = f"{target_name} ({name_counts[target_name]})"

        clean_node["name"] = target_name
        formatted.append(clean_node)

    return formatted


def compile_shadowrocket_yaml(
    nodes_by_provider: Dict[str, List[Dict[str, Any]]],
    is_sample: bool = False
) -> str:
    """Compile grouped nodes into a single clean proxies YAML."""
    all_proxies = []

    for provider_name, nodes in nodes_by_provider.items():
        clean_nodes = filter_and_format_proxies(provider_name, nodes)
        all_proxies.extend(clean_nodes)

    output_dict = {
        "proxies": all_proxies
    }

    if is_sample:
        header = (
            "# managed-by-proxy-matrix - Shadowrocket Node Subscription\n"
            "# [WARNING: DEMO MODE] This file contains sample offline nodes for demonstration only.\n"
            "# Configure real subscription URLs in local/urls.json and run 'python3 manage.py update' to generate real nodes.\n"
        )
    else:
        header = (
            "# managed-by-proxy-matrix - Shadowrocket Node Subscription\n"
            "# [REAL SUBSCRIPTION] Generated from verified local subscription cache.\n"
        )
    return header + dict_to_yaml(output_dict)


def load_subscription_nodes(
    root_dir: Path,
    urls: Optional[Dict[str, str]] = None,
    allow_sample_fallback: bool = False
) -> Tuple[Dict[str, List[Dict[str, Any]]], bool]:
    """Load proxy nodes from cached profiles or fallback fixture.

    Returns:
        Tuple of (nodes_by_provider, is_sample)

    Raises:
        FileNotFoundError: If subscriptions are configured but no cached profile exists,
                           unless allow_sample_fallback=True is explicitly passed.
    """
    nodes_by_provider: Dict[str, List[Dict[str, Any]]] = {}
    target_urls = urls or {}
    profiles_dir = root_dir / "profiles"

    # Check for downloaded / cached profiles first
    for name in target_urls.keys():
        profile_path = profiles_dir / f"{name}.yaml"
        if profile_path.is_file():
            parsed = load_proxies_from_file(profile_path)
            if parsed:
                nodes_by_provider[name] = parsed

    if target_urls:
        if nodes_by_provider:
            return nodes_by_provider, False

        # Configured real subscriptions, but no local profile cache exists
        if not allow_sample_fallback:
            missing_names = list(target_urls.keys())
            raise FileNotFoundError(
                f"未找到订阅缓存文件 (预期位置: {profiles_dir}/<机场名>.yaml)。"
                f"已配置真实订阅 ({missing_names})，但本地尚无节点缓存。"
                f"请先运行 'python3 manage.py update' 拉取真实节点缓存，或清空订阅 URL 进入示例演示模式。"
            )

        # Explicit test / offline fallback allowed
        fixture_path = root_dir / "subscriptions/sample_subscription.yaml"
        sample_nodes = load_proxies_from_file(fixture_path)
        for name in target_urls.keys():
            nodes_by_provider[name] = list(sample_nodes)
        return nodes_by_provider, True

    # Pure sample demo mode (no URLs configured)
    fixture_path = root_dir / "subscriptions/sample_subscription.yaml"
    sample_nodes = load_proxies_from_file(fixture_path)
    nodes_by_provider["SampleAirport"] = sample_nodes
    return nodes_by_provider, True
