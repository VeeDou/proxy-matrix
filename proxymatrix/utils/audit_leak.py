#!/usr/bin/env python3
"""ProxyMatrix Two-Tier Leak Scanner.

Tier 1 (Public): Generic regex patterns (Hex-64, public IPv4, cloud AKs, user home paths).
Tier 2 (Private): External local denylist (~/.config/proxy-matrix/denylist.txt).
CRITICAL SECURITY INVARIANT:
- Tier 2 private denylist is NEVER exempted by inline `# leak-audit: allow` comments.
- Inline exemptions ONLY apply to Tier 1 generic heuristics.
- Negative exceptions (allowlists) are exclusively managed via `!pattern` in the external denylist.
"""

import argparse
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import List, NamedTuple, Optional, Set, Tuple

# Default path for maintainer's external private denylist
DEFAULT_DENYLIST_PATH = Path.home() / ".config/proxy-matrix/denylist.txt"

# Inline exemption pattern: `# leak-audit: allow` or `// leak-audit: allow` (Tier 1 only!)
EXEMPTION_PATTERN = re.compile(r"(?:#|//|--)\s*leak-audit:\s*allow", re.IGNORECASE)

# Tier 1 Regex Patterns
HEX64_PATTERN = re.compile(r"\b[0-9a-fA-F]{64}\b")
IPV4_PATTERN = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
AWS_AK_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
ALIYUN_AK_PATTERN = re.compile(r"\bLTAI[0-9A-Za-z]{12,24}\b")
TOKEN_PARAM_PATTERN = re.compile(r"[?&](?:token|auth|key|secret)=([a-zA-Z0-9_\-]{16,})", re.IGNORECASE)
SENSITIVE_SCHEMES_PATTERN = re.compile(r"\b(?:trojan|vmess|vless|ss|hysteria2?)://[^\s\"']+", re.IGNORECASE)
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
USER_PATH_PATTERN = re.compile(r"/(?:Users|home)/([a-zA-Z0-9_\-]+)/")

# Standard documentation networks (RFC 5737 / RFC 3849)
DOC_NETWORKS = [
    ipaddress.ip_network("192.0.2.0/24"),    # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3
    ipaddress.ip_network("100.64.0.0/10"),    # Shared address space / CGNAT (RFC 6598)
    ipaddress.ip_network("198.18.0.0/15"),    # Benchmark testing
    ipaddress.ip_network("240.0.0.0/4"),      # Future/reserved
]

# Common safe public DNS/Anycast IPs and well-known public ASN network seeds
SAFE_PUBLIC_IPS = {
    "1.1.1.1", "1.0.0.1",
    "8.8.8.8", "8.8.4.4",
    "9.9.9.9", "149.112.112.112",
    "223.5.5.5", "223.6.6.6",
    "119.29.29.29", "182.254.116.116",
    "114.114.114.114", "114.114.115.115",
    "0.0.0.0", "255.255.255.255",
    # Telegram well-known CIDR network seeds and 6to4 anycast in standard routing rules
    "91.108.0.0", "149.154.160.0", "192.88.99.0",
}


class LeakFinding(NamedTuple):
    file: str
    line_number: int
    rule_name: str
    matched_text: str


def is_safe_ip(ip_str: str) -> bool:
    """Check if IPv4 address is safe (private, loopback, doc, non-global, multicast, or known public DNS/ASN)."""
    if ip_str in SAFE_PUBLIC_IPS:
        return True
    try:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_multicast or ip.is_reserved or ip.is_private or ip.is_loopback or ip.is_link_local or not ip.is_global:
            return True
        for net in DOC_NETWORKS:
            if ip in net:
                return True
        return False
    except ValueError:
        return True


def load_private_denylist(path: Optional[Path] = None) -> Tuple[List[str], List[str]]:
    """Load private keywords and negative exceptions from external denylist.

    Returns:
        (deny_patterns, allow_patterns)
    """
    denylist_file = path or Path(os.getenv("PROXY_MATRIX_DENYLIST", str(DEFAULT_DENYLIST_PATH)))
    if not denylist_file.is_file():
        return [], []
    deny = []
    allow = []
    with open(denylist_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("!"):
                allow.append(stripped[1:].strip())
            else:
                deny.append(stripped)
    return deny, allow


def scan_line(
    line: str,
    line_number: int,
    filename: str,
    private_denylist: List[str],
    private_allowlist: Optional[List[str]] = None
) -> List[LeakFinding]:
    """Scan a single line of text for leaks."""
    findings: List[LeakFinding] = []
    allows = private_allowlist or []

    # 1. Tier 2: Check Private Denylist FIRST (NEVER bypassed by inline comments!)
    # Mask allowed negative pattern substrings first so they only exempt their exact occurrences
    sanitized_line_lower = line.lower()
    for allow in allows:
        sanitized_line_lower = sanitized_line_lower.replace(allow.lower(), " " * len(allow))

    for item in private_denylist:
        if item.lower() in sanitized_line_lower:
            findings.append(LeakFinding(filename, line_number, "PRIVATE_DENYLIST", item))

    # Inline exemptions ONLY apply to Tier 1 generic heuristics
    if EXEMPTION_PATTERN.search(line):
        return findings

    # 2. Tier 1: Public Generic Patterns
    # Hex-64
    for match in HEX64_PATTERN.finditer(line):
        findings.append(LeakFinding(filename, line_number, "HEX64_SECRET", match.group()[:16] + "..."))

    # Public IP
    for match in IPV4_PATTERN.finditer(line):
        ip_cand = match.group()
        if not is_safe_ip(ip_cand):
            findings.append(LeakFinding(filename, line_number, "PUBLIC_IPV4", ip_cand))

    # Cloud AK
    for match in AWS_AK_PATTERN.finditer(line):
        findings.append(LeakFinding(filename, line_number, "AWS_ACCESS_KEY", match.group()))
    for match in ALIYUN_AK_PATTERN.finditer(line):
        findings.append(LeakFinding(filename, line_number, "ALIYUN_ACCESS_KEY", match.group()))

    # Token param (exempt explicit placeholder/example tokens)
    for match in TOKEN_PARAM_PATTERN.finditer(line):
        tok_val = match.group(1)
        if any(ph in tok_val.upper() for ph in ("EXAMPLE", "PLACEHOLDER", "SAMPLE", "TEST", "MOCK", "YOUR_TOKEN")):
            continue
        findings.append(LeakFinding(filename, line_number, "TOKEN_PARAM", tok_val[:12] + "..."))

    # Sensitive schemes
    for match in SENSITIVE_SCHEMES_PATTERN.finditer(line):
        findings.append(LeakFinding(filename, line_number, "SENSITIVE_SCHEME", match.group()[:20] + "..."))

    # Private key
    if PRIVATE_KEY_PATTERN.search(line):
        findings.append(LeakFinding(filename, line_number, "PRIVATE_KEY_HEADER", "PRIVATE KEY"))

    # User home path
    for match in USER_PATH_PATTERN.finditer(line):
        findings.append(LeakFinding(filename, line_number, "LOCAL_USER_PATH", match.group()))

    return findings


def scan_file(
    filepath: Path,
    private_denylist: List[str],
    private_allowlist: Optional[List[str]] = None
) -> List[LeakFinding]:
    """Scan a single file (fail-closed on IO/read errors)."""
    findings: List[LeakFinding] = []
    filename_str = str(filepath)
    allows = private_allowlist or []

    # Check filename itself against denylist
    sanitized_name_lower = filepath.name.lower()
    for allow in allows:
        sanitized_name_lower = sanitized_name_lower.replace(allow.lower(), " " * len(allow))
    for item in private_denylist:
        if item.lower() in sanitized_name_lower:
            findings.append(LeakFinding(filename_str, 0, "FILENAME_DENYLIST", item))

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            for idx, line in enumerate(f, start=1):
                findings.extend(scan_line(line, idx, filename_str, private_denylist, allows))
    except Exception as e:
        raise RuntimeError(f"Failed to scan file '{filepath}': {e}") from e

    return findings


def scan_directory(
    root: Path,
    private_denylist: List[str],
    private_allowlist: Optional[List[str]] = None,
    skip_dirs: Optional[Set[str]] = None
) -> List[LeakFinding]:
    """Scan all tracked or relevant files in a directory."""
    if skip_dirs is None:
        skip_dirs = {".git", ".state", "__pycache__", "dist", "local"}

    findings: List[LeakFinding] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for f in filenames:
            if f.endswith((".pyc", ".png", ".jpg", ".jpeg", ".ico", ".DS_Store")):
                continue
            path = Path(dirpath) / f
            # Only skip the synthetic canary samples fixture
            if "tests/fixtures/canaries" in str(path):
                continue
            findings.extend(scan_file(path, private_denylist, private_allowlist))
    return findings


def scan_git_history(
    repo_dir: Path,
    private_denylist: List[str],
    private_allowlist: Optional[List[str]] = None
) -> List[LeakFinding]:
    """Scan git log diffs, commit messages, and author metadata (fail-closed)."""
    findings: List[LeakFinding] = []
    if not (repo_dir / ".git").is_dir():
        return findings

    # Return empty findings if repository has no commits yet (e.g. initial commit)
    res = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--verify", "HEAD"],
        capture_output=True
    )
    if res.returncode != 0:
        return findings

    allows = private_allowlist or []

    # 1. Check commit author and committer metadata
    try:
        authors = subprocess.check_output(
            ["git", "-C", str(repo_dir), "log", "--format=%an %ae %cn %ce"],
            text=True,
            stderr=subprocess.PIPE
        )
        for line in authors.splitlines():
            sanitized_line_lower = line.lower()
            for a in allows:
                sanitized_line_lower = sanitized_line_lower.replace(a.lower(), " " * len(a))
            for item in private_denylist:
                if item.lower() in sanitized_line_lower:
                    findings.append(LeakFinding("git-metadata", 0, "AUTHOR_METADATA_LEAK", item))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git author scan failed: {e.stderr}") from e

    # 2. Check commit messages (subject and body)
    try:
        commit_msgs = subprocess.check_output(
            ["git", "-C", str(repo_dir), "log", "--format=%B"],
            text=True,
            stderr=subprocess.PIPE
        )
        for idx, line in enumerate(commit_msgs.splitlines(), start=1):
            sanitized_line_lower = line.lower()
            for a in allows:
                sanitized_line_lower = sanitized_line_lower.replace(a.lower(), " " * len(a))
            for item in private_denylist:
                if item.lower() in sanitized_line_lower:
                    findings.append(LeakFinding("git-commit-message", idx, "COMMIT_MSG_LEAK", item))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git commit message scan failed: {e.stderr}") from e

    # 3. Check git diffs
    try:
        diffs = subprocess.check_output(
            ["git", "-C", str(repo_dir), "log", "-p"],
            text=True,
            stderr=subprocess.PIPE
        )
        current_file = ""
        is_canary = False
        for idx, line in enumerate(diffs.splitlines(), start=1):
            if line.startswith("diff --git a/"):
                parts = line.split()
                if len(parts) >= 3:
                    current_file = parts[2].removeprefix("a/")
                    is_canary = "tests/fixtures/canaries" in current_file
            elif line.startswith("+") and not line.startswith("+++"):
                if not is_canary:
                    content = line[1:]
                    findings.extend(scan_line(content, idx, f"git-history:{current_file}", private_denylist, allows))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Git diff scan failed: {e.stderr}") from e

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="ProxyMatrix Leak Audit Tool")
    parser.add_argument("--dir", type=Path, default=Path(__file__).resolve().parents[2], help="Target directory to scan")
    parser.add_argument("--check-git", action="store_true", help="Scan git history diffs and metadata")
    parser.add_argument("--denylist", type=Path, default=None, help="Path to custom denylist file")
    args = parser.parse_args()

    target_dir = args.dir.resolve()
    print(f"[*] Running leak audit on {target_dir}...")

    deny, allow = load_private_denylist(args.denylist)
    print(f"[*] Private denylist rules loaded: {len(deny)} (exceptions: {len(allow)})")

    findings: List[LeakFinding] = []
    try:
        findings.extend(scan_directory(target_dir, deny, allow))
        if args.check_git:
            findings.extend(scan_git_history(target_dir, deny, allow))
    except Exception as e:
        print(f"[!] Scan error: {e}", file=sys.stderr)
        return 2

    if findings:
        print(f"[!] FAIL: Detected {len(findings)} potential leaks:")
        for f in findings:
            print(f"  - [{f.rule_name}] {f.file}:{f.line_number} -> {f.matched_text}")
        return 1

    print("[✓] PASS: No leaks detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
