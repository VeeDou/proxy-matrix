"""Robust YAML Parser for Clash/Mihomo Subscriptions.

A pure standard-library parser supporting the YAML subset commonly used in proxy subscriptions:
- Flow mappings: {name: "HK", type: ss, port: 8388, password: "pw"}
- Block mappings with nested dictionaries: ws-opts, reality-opts, headers
- Flow and block sequences: alpn: [h2, http/1.1] or block lists
- Strict type preservation: quoted strings ('123456', "0e12") remain str;
  unquoted numbers (443) become int; unquoted booleans (true, false) become bool.

Zero third-party dependencies: 100% Python 3 standard library.
"""

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple, Union


def parse_scalar(s: str) -> Any:
    """Parse a single YAML scalar while strictly respecting quotes."""
    s = s.strip()
    if not s:
        return ""

    # Single-quoted string: strictly str, unescape double single-quotes
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")

    # Double-quoted string: strictly str, decode JSON-style escapes
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        try:
            return json.loads(s)
        except Exception:
            return s[1:-1]

    # Unquoted booleans
    s_lower = s.lower()
    if s_lower in ("true", "yes", "on"):
        return True
    if s_lower in ("false", "no", "off"):
        return False
    if s_lower in ("null", "~"):
        return None

    # Unquoted integers
    if re.fullmatch(r"[-+]?\d+", s):
        try:
            return int(s)
        except ValueError:
            return s

    # Unquoted floats
    if re.fullmatch(r"[-+]?\d*\.\d+(?:[eE][-+]?\d+)?", s):
        try:
            return float(s)
        except ValueError:
            return s

    return s


def split_flow_items(s: str) -> List[str]:
    """Split comma-separated items inside a flow collection ({...} or [...]) at depth 0."""
    items: List[str] = []
    curr: List[str] = []
    in_quote: Optional[str] = None
    depth = 0
    i = 0
    n = len(s)

    while i < n:
        ch = s[i]
        if in_quote:
            curr.append(ch)
            if ch == "\\" and in_quote == '"':
                i += 1
                if i < n:
                    curr.append(s[i])
            elif ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
                curr.append(ch)
            elif ch in ("{", "["):
                depth += 1
                curr.append(ch)
            elif ch in ("}", "]"):
                depth -= 1
                curr.append(ch)
            elif ch == "," and depth == 0:
                item_str = "".join(curr).strip()
                if item_str:
                    items.append(item_str)
                curr = []
                i += 1
                continue
            else:
                curr.append(ch)
        i += 1

    if curr:
        item_str = "".join(curr).strip()
        if item_str:
            items.append(item_str)

    return items


def split_kv_at_depth_zero(s: str) -> Tuple[str, str]:
    """Split a key: value pair on the first colon at depth 0."""
    in_quote: Optional[str] = None
    depth = 0

    for idx, ch in enumerate(s):
        if in_quote:
            if ch == "\\" and in_quote == '"':
                continue
            elif ch == in_quote:
                in_quote = None
        else:
            if ch in ('"', "'"):
                in_quote = ch
            elif ch in ("{", "["):
                depth += 1
            elif ch in ("}", "]"):
                depth -= 1
            elif ch == ":" and depth == 0:
                return s[:idx].strip(), s[idx + 1:].strip()

    return s.strip(), ""


def parse_flow_value(s: str) -> Any:
    """Parse a flow mapping, sequence, or scalar."""
    s = s.strip()
    if s.startswith("{") and s.endswith("}"):
        res_map: Dict[str, Any] = {}
        for item in split_flow_items(s[1:-1]):
            k, v = split_kv_at_depth_zero(item)
            k_parsed = str(parse_scalar(k))
            res_map[k_parsed] = parse_flow_value(v) if (v.startswith("{") or v.startswith("[")) else parse_scalar(v)
        return res_map

    if s.startswith("[") and s.endswith("]"):
        res_list: List[Any] = []
        for item in split_flow_items(s[1:-1]):
            res_list.append(parse_flow_value(item) if (item.startswith("{") or item.startswith("[")) else parse_scalar(item))
        return res_list

    return parse_scalar(s)


def parse_yaml_blocks(
    lines: List[str],
    start_idx: int = 0,
    base_indent: int = 0
) -> Tuple[Any, int]:
    """Recursively parse block YAML lines into nested Python dicts and lists."""
    i = start_idx
    n = len(lines)

    while i < n and (not lines[i].strip() or lines[i].strip().startswith("#")):
        i += 1

    if i >= n:
        return None, i

    line = lines[i]
    indent = len(line) - len(line.lstrip(" "))
    if indent < base_indent:
        return None, i

    stripped = line.strip()

    # List block
    if stripped.startswith("-"):
        result_list: List[Any] = []
        while i < n:
            while i < n and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            if i >= n:
                break

            cur_line = lines[i]
            cur_indent = len(cur_line) - len(cur_line.lstrip(" "))
            if cur_indent < indent:
                break

            cur_stripped = cur_line.strip()
            if not cur_stripped.startswith("-"):
                break

            item_content = cur_stripped[1:].strip()
            i += 1

            if not item_content:
                # Value starts on next indented lines
                val, i = parse_yaml_blocks(lines, i, cur_indent + 1)
                result_list.append(val)
            elif item_content.startswith("{") or item_content.startswith("["):
                result_list.append(parse_flow_value(item_content))
            elif ":" in item_content:
                # "- key: value" inline mapping start
                k, v = split_kv_at_depth_zero(item_content)
                k_val = str(parse_scalar(k))
                item_dict: Dict[str, Any] = {}

                if not v:
                    val, i = parse_yaml_blocks(lines, i, cur_indent + 2)
                    item_dict[k_val] = val
                elif v.startswith("{") or v.startswith("["):
                    item_dict[k_val] = parse_flow_value(v)
                else:
                    item_dict[k_val] = parse_scalar(v)

                # Collect sibling keys under this item
                while i < n:
                    while i < n and (not lines[i].strip() or lines[i].strip().startswith("#")):
                        i += 1
                    if i >= n:
                        break

                    sub_line = lines[i]
                    sub_indent = len(sub_line) - len(sub_line.lstrip(" "))
                    if sub_indent <= cur_indent:
                        break

                    sub_stripped = sub_line.strip()
                    if sub_stripped.startswith("-"):
                        break

                    if ":" in sub_stripped:
                        sk, sv = split_kv_at_depth_zero(sub_stripped)
                        sk_val = str(parse_scalar(sk))
                        i += 1
                        if not sv:
                            sval, i = parse_yaml_blocks(lines, i, sub_indent + 1)
                            item_dict[sk_val] = sval
                        elif sv.startswith("{") or sv.startswith("["):
                            item_dict[sk_val] = parse_flow_value(sv)
                        else:
                            item_dict[sk_val] = parse_scalar(sv)
                    else:
                        break

                result_list.append(item_dict)
            else:
                result_list.append(parse_scalar(item_content))

        return result_list, i

    # Mapping block
    result_dict: Dict[str, Any] = {}
    while i < n:
        while i < n and (not lines[i].strip() or lines[i].strip().startswith("#")):
            i += 1
        if i >= n:
            break

        cur_line = lines[i]
        cur_indent = len(cur_line) - len(cur_line.lstrip(" "))
        if cur_indent < indent:
            break

        cur_stripped = cur_line.strip()
        if cur_stripped.startswith("-"):
            break

        if ":" not in cur_stripped:
            i += 1
            continue

        k, v = split_kv_at_depth_zero(cur_stripped)
        k_val = str(parse_scalar(k))
        i += 1

        if not v:
            val, i = parse_yaml_blocks(lines, i, cur_indent + 1)
            result_dict[k_val] = val
        elif v.startswith("{") or v.startswith("["):
            result_dict[k_val] = parse_flow_value(v)
        else:
            result_dict[k_val] = parse_scalar(v)

    return result_dict, i


def parse_clash_yaml_proxies(yaml_text: str) -> List[Dict[str, Any]]:
    """Extract and parse the 'proxies' sequence from a Clash YAML document.

    Handles flow mappings, block mappings, nested option maps, and type preservation.
    """
    lines = yaml_text.splitlines()
    proxies_start_idx = -1
    proxies_indent = 0

    # Locate the "proxies:" line
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith("proxies:"):
            proxies_start_idx = idx + 1
            proxies_indent = len(line) - len(line.lstrip(" "))
            break

    if proxies_start_idx == -1:
        return []

    # If "proxies: []", return empty list
    content_on_same_line = lines[proxies_start_idx - 1].strip().removeprefix("proxies:").strip()
    if content_on_same_line:
        flow_parsed = parse_flow_value(content_on_same_line)
        if isinstance(flow_parsed, list):
            return [x for x in flow_parsed if isinstance(x, dict)]

    # Collect lines belonging to the proxies section
    proxies_lines: List[str] = []
    for line in lines[proxies_start_idx:]:
        stripped = line.strip()
        if not stripped:
            proxies_lines.append(line)
            continue
        cur_indent = len(line) - len(line.lstrip(" "))
        # Check if we hit another top-level or same-level section
        if cur_indent <= proxies_indent and ":" in stripped and not stripped.startswith("-"):
            break
        proxies_lines.append(line)

    if not proxies_lines:
        return []

    parsed_nodes, _ = parse_yaml_blocks(proxies_lines, start_idx=0, base_indent=proxies_indent)
    if isinstance(parsed_nodes, list):
        return [node for node in parsed_nodes if isinstance(node, dict)]

    return []
