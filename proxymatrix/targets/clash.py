"""Clash Verge Rev (Mihomo) Profile Compiler.

Assembles base kernel settings, proxy providers, dynamic proxy groups,
and routing rules into standard Mihomo YAML.
Zero third-party dependencies: 100% Python 3 standard library.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from proxymatrix.model import (
    GROUP_AUTO,
    GROUP_DIRECT,
    GROUP_PROXY,
    GROUP_REJECT,
    LogicalGroup,
    RegionDefinition,
    Rule,
    load_regions,
    parse_rule_string,
)


def format_yaml_key(k: str) -> str:
    """Format YAML dictionary key, quoting if needed."""
    k_str = str(k)
    # Quote if contains special characters or starts with punctuation/wildcard
    if any(c in k_str for c in [":", "{", "}", "[", "]", ",", "&", "*", "#", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`", " ", "'", '"', "+"]):
        return json.dumps(k_str, ensure_ascii=False)
    return k_str


def format_yaml_scalar(val: Any) -> str:
    """Safely format a scalar value for YAML without third-party pyyaml.

    Strings are formatted via json.dumps (valid YAML 1.2 double-quoted strings),
    handling backslash escaping, regex patterns, spaces, and type preservation.
    """
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return "null"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return json.dumps(val, ensure_ascii=False)
    return json.dumps(str(val), ensure_ascii=False)


def dict_to_yaml(data: Any, indent: int = 0) -> str:
    """Simple, pure-Python hierarchical YAML dumper (zero pip dependencies)."""
    spaces = "  " * indent
    lines = []

    if isinstance(data, dict):
        for k, v in data.items():
            formatted_k = format_yaml_key(k)
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{spaces}{formatted_k}:")
                lines.append(dict_to_yaml(v, indent + 1))
            elif isinstance(v, list) and not v:
                lines.append(f"{spaces}{formatted_k}: []")
            elif isinstance(v, dict) and not v:
                lines.append(f"{spaces}{formatted_k}: {{}}")
            else:
                lines.append(f"{spaces}{formatted_k}: {format_yaml_scalar(v)}")
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{spaces}-")
                lines.append(dict_to_yaml(item, indent + 1))
            else:
                lines.append(f"{spaces}- {format_yaml_scalar(item)}")
    else:
        lines.append(f"{spaces}{format_yaml_scalar(data)}")

    return "\n".join(lines)


class ClashCompiler:
    """Compiles ProxyMatrix IR to Clash Verge Rev (Mihomo) YAML."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.config_dir = root_dir / "config"
        self.rules_dir = root_dir / "rules"

    def load_base_config(self) -> Dict[str, Any]:
        path = self.config_dir / "base.json"
        if not path.is_file():
            raise FileNotFoundError(f"Base config not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_regions(self) -> List[RegionDefinition]:
        return load_regions(self.config_dir / "regions.json")

    def load_groups_template(self) -> List[Dict[str, Any]]:
        path = self.config_dir / "groups.json"
        if not path.is_file():
            raise FileNotFoundError(f"Groups template not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_rules(self) -> List[str]:
        path = self.rules_dir / "rules.json"
        if not path.is_file():
            raise FileNotFoundError(f"Rules not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def build_id_to_name_map(
        self,
        groups: List[Dict[str, Any]],
        regions: List[RegionDefinition]
    ) -> Dict[str, str]:
        """Build mapping from logical ID (@proxy, @region_hk) to display names."""
        mapping = {
            GROUP_DIRECT: "DIRECT",
            GROUP_REJECT: "REJECT",
        }
        for g in groups:
            mapping[g["id"]] = g["display_name"]
        for r in regions:
            mapping[r.id] = r.display_name
        return mapping

    def compile(
        self,
        urls: Dict[str, str],
        providers_meta: Optional[Dict[str, Any]] = None,
        custom_sites: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Compile complete Clash profile YAML."""
        base_config = self.load_base_config()
        regions = self.load_regions()
        groups_tmpl = self.load_groups_template()
        id_to_name = self.build_id_to_name_map(groups_tmpl, regions)

        # 1. Build proxy-providers section
        providers = {}
        provider_names = list(urls.keys())
        for name, url in urls.items():
            meta = (providers_meta or {}).get(name, {})
            providers[name] = {
                "type": meta.get("type", "http"),
                "url": url,
                "interval": meta.get("interval", 86400),
                "path": f"./profiles/{name}.yaml",
                "health-check": meta.get("health-check", {
                    "enable": True,
                    "url": "http://cp.cloudflare.com/generate_204",
                    "interval": 300
                })
            }

        # 2. Build proxy-groups
        proxy_groups = []

        # 2a. Add defined groups from template
        for g in groups_tmpl:
            group_dict = {
                "name": g["display_name"],
                "type": g.get("type", "select"),
            }
            # Resolve proxies list
            resolved_proxies = []
            for p in g.get("proxies", []):
                if p in id_to_name:
                    resolved_proxies.append(id_to_name[p])
                else:
                    resolved_proxies.append(p)
            if resolved_proxies:
                group_dict["proxies"] = resolved_proxies

            if g.get("type") in ("url-test", "fallback", "load-balance"):
                group_dict["url"] = g.get("url", "http://cp.cloudflare.com/generate_204")
                group_dict["interval"] = g.get("interval", 300)
                if "tolerance" in g:
                    group_dict["tolerance"] = g["tolerance"]
                # For auto groups, use all providers
                if not resolved_proxies and provider_names:
                    group_dict["use"] = provider_names

            if "empty-fallback" in g:
                group_dict["empty-fallback"] = g["empty-fallback"]

            if "use" in g:
                group_dict["use"] = g["use"]
            elif g.get("id") == GROUP_PROXY and provider_names and "proxies" not in group_dict:
                group_dict["use"] = provider_names

            proxy_groups.append(group_dict)

        # 2b. Add regional groups dynamically from regions.json
        for r in regions:
            reg_group = {
                "name": r.display_name,
                "type": "url-test",
                "url": "http://cp.cloudflare.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "filter": r.regex_pattern,
                "empty-fallback": "REJECT",
            }
            if provider_names:
                reg_group["use"] = provider_names
            proxy_groups.append(reg_group)

        # 3. Resolve nameserver-policy in base config DNS
        dns_config = base_config.get("dns", {})
        if "nameserver-policy" in dns_config:
            new_nsp = {}
            for pattern, target in dns_config["nameserver-policy"].items():
                if isinstance(target, str) and "#" in target:
                    url_part, group_id = target.split("#", 1)
                    resolved_group = id_to_name.get(group_id, group_id)
                    new_nsp[pattern] = f"{url_part}#{resolved_group}"
                else:
                    new_nsp[pattern] = target
            dns_config["nameserver-policy"] = new_nsp

        # 4. Build rules list using Rule IR
        rendered_rules = []

        # 4a. User custom sites (highest priority)
        if custom_sites:
            for site in custom_sites:
                if not site.get("enabled", True):
                    continue
                domain = site.get("domain")
                rule_type = site.get("type", "DOMAIN-SUFFIX")
                target_id = site.get("group") or site.get("target") or GROUP_PROXY
                r = Rule(type=rule_type, payload=domain, target_id=target_id)
                rendered_rules.append(r.to_clash_rule(id_to_name))

        # 4b. Core rules via Rule IR
        for r_str in self.load_rules():
            rule = parse_rule_string(r_str)
            rendered_rules.append(rule.to_clash_rule(id_to_name))

        # Assemble full dict
        full_config = dict(base_config)
        full_config["proxy-providers"] = providers
        full_config["proxy-groups"] = proxy_groups
        full_config["rules"] = rendered_rules

        # Output with managed header
        header = "# managed-by-proxy-matrix\n"
        return header + dict_to_yaml(full_config)
