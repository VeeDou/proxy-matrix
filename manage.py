#!/usr/bin/env python3
"""ProxyMatrix: Local-first proxy configuration manager and multi-client compiler.

Subcommands:
- build:  Compile all client profiles (Clash YAML, Shadowrocket .conf).
- check:  Compile and validate profiles against local Mihomo kernel (if available).
- apply:  Compile, validate, and atomically apply profile to Clash Verge Rev with backup.
- update: Pull latest rules/subscriptions, compile, and re-apply.
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from proxymatrix.sources.fetcher import fetch_all_subscriptions, fetch_subscription
from proxymatrix.targets.clash import ClashCompiler
from proxymatrix.targets.shadowrocket_conf import LossyConversionReport, compile_shadowrocket_conf
from proxymatrix.targets.shadowrocket_yaml import compile_shadowrocket_yaml, load_subscription_nodes
from proxymatrix.utils.redact import redact_text, sanitize_exception

ROOT = Path(__file__).resolve().parent
MARKER = "# proxy-matrix-managed: v1"
APP_HOME = Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
DEFAULT_CORE = Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo")
DEFAULT_CLASH_OUTPUT = ROOT / "dist/Clash-Verge-Rev.yaml"
DEFAULT_SR_CONF_OUTPUT = ROOT / "dist/shadowrocket.conf"
DEFAULT_SR_YAML_OUTPUT = ROOT / "dist/shadowrocket.yaml"


def load_json(rel_path: str, fallback_example: Optional[str] = None) -> Any:
    """Load JSON from path, with optional fallback from .example.json."""
    target = ROOT / rel_path
    if not target.is_file():
        if fallback_example:
            example = ROOT / fallback_example
            if example.is_file():
                # Auto-initialize from example if missing
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(example, target)
                print(f"[提示] 已从 {fallback_example} 自动初始化 {rel_path}。")
                return json.loads(target.read_text(encoding="utf-8"))
        return None
    return json.loads(target.read_text(encoding="utf-8"))


def load_urls() -> Dict[str, str]:
    """Load user subscription URLs from local/urls.json or subscriptions/urls.json."""
    # Check local/ directory first (highest privacy priority)
    local_urls = load_json("local/urls.json")
    if local_urls:
        return local_urls
    subs_urls = load_json("subscriptions/urls.json", "subscriptions/urls.example.json")
    return subs_urls or {}


def load_sites() -> List[Dict[str, Any]]:
    """Load custom site routing rules from local/sites.json or rules/sites.json."""
    local_sites = load_json("local/sites.json")
    if local_sites:
        return local_sites
    rules_sites = load_json("rules/sites.json", "rules/sites.example.json")
    return rules_sites or []


def build_all(
    clash_output: Path = DEFAULT_CLASH_OUTPUT,
    sr_conf_output: Path = DEFAULT_SR_CONF_OUTPUT,
    sr_yaml_output: Path = DEFAULT_SR_YAML_OUTPUT
) -> Dict[str, Path]:
    """Build Clash YAML, Shadowrocket CONF, and Shadowrocket YAML artifacts."""
    urls = load_urls()
    sites = load_sites()
    providers_meta = load_json("subscriptions/providers.json", "subscriptions/providers.example.json")

    clash_output.parent.mkdir(parents=True, exist_ok=True)
    sr_conf_output.parent.mkdir(parents=True, exist_ok=True)
    sr_yaml_output.parent.mkdir(parents=True, exist_ok=True)

    # 1. Compile Clash YAML
    compiler = ClashCompiler(ROOT)
    clash_yaml_text = compiler.compile(
        urls=urls,
        providers_meta=providers_meta,
        custom_sites=sites,
    )
    if not clash_yaml_text.startswith(MARKER):
        clash_yaml_text = f"{MARKER}\n" + clash_yaml_text
    clash_output.write_text(clash_yaml_text, encoding="utf-8")

    # 2. Compile Shadowrocket CONF and lossy conversion report
    lossy_report = LossyConversionReport()
    sr_conf_text = compile_shadowrocket_conf(
        root_dir=ROOT,
        custom_sites=sites,
        dns_preset="cn-mainland",
        report=lossy_report,
    )
    sr_conf_output.write_text(sr_conf_text, encoding="utf-8")

    # Generate lossy conversion report
    report_output = ROOT / "dist/lossy_conversion_report.md"
    report_output.write_text(lossy_report.to_markdown(), encoding="utf-8")

    # 3. Compile Shadowrocket YAML (Proxies subscription)
    sr_yaml_status = "未生成"
    try:
        nodes_by_prov, is_sample = load_subscription_nodes(ROOT, urls)
        sr_yaml_text = compile_shadowrocket_yaml(nodes_by_prov, is_sample=is_sample)
        sr_yaml_output.write_text(sr_yaml_text, encoding="utf-8")
        mode_str = "示例离线节点" if is_sample else "真实订阅节点"
        sr_yaml_status = f"{sr_yaml_output} ({sr_yaml_output.stat().st_size} 字节, {mode_str})"
    except FileNotFoundError as e:
        if sr_yaml_output.is_file():
            sr_yaml_output.unlink()
        print(f"[!] 提示: 跳过构建 shadowrocket.yaml (已清理旧产物): {e}")
        sr_yaml_status = "[跳过] 本地 profiles/ 缺少真实订阅节点缓存 (已清理旧产物)"

    print(f"[✓] 构建完成:")
    print(f"  - Clash Profile:        {clash_output} ({clash_output.stat().st_size} 字节)")
    print(f"  - Shadowrocket Config:  {sr_conf_output} ({sr_conf_output.stat().st_size} 字节)")
    print(f"  - Shadowrocket Proxies: {sr_yaml_status}")
    print(f"  - Lossy Report:         {report_output} ({len(lossy_report.dropped_rules)} 处丢弃, {len(lossy_report.converted_rules)} 处转换)")

    return {
        "clash": clash_output,
        "shadowrocket_conf": sr_conf_output,
        "shadowrocket_yaml": sr_yaml_output,
        "lossy_report": report_output,
    }


def validate_with_core(profile_path: Path, core_path: Path = DEFAULT_CORE) -> bool:
    """Validate generated YAML profile with local Clash Verge / Mihomo kernel if installed."""
    if not core_path.is_file():
        in_path = shutil.which(str(core_path.name)) or shutil.which("verge-mihomo") or shutil.which("mihomo")
        if in_path:
            core_path = Path(in_path)
        else:
            print(f"[!] 警告: 未检测到本地 Mihomo / Clash Verge 内核 ({core_path})，跳过内核语法校验。")
            return False

    print(f"[*] 正在调用本地内核进行语法校验: {core_path.name}...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_profile = Path(tmp_dir) / "config.yaml"
        shutil.copy2(profile_path, tmp_profile)

        cmd = [str(core_path), "-t", "-f", str(tmp_profile), "-d", tmp_dir]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = redact_text(proc.stderr or proc.stdout)
            raise ValueError(f"内核语法校验未通过:\n{err}")

    print("[✓] 内核语法校验通过。")
    return True


def apply_profile(profile_path: Path, app_home: Path = APP_HOME, backup_dir: Optional[Path] = None) -> bool:
    """Atomically apply profile to Clash Verge Rev active profiles with automated backup."""
    profiles_dir = app_home / "profiles"
    if not profiles_dir.is_dir():
        print(f"[!] 未检测到 Clash Verge Rev 安装目录 ({profiles_dir})，跳过客户端热替换。")
        return False

    # Find profile file containing our MARKER
    target_profile = None
    for item in profiles_dir.glob("*.yaml"):
        try:
            head = item.read_text(encoding="utf-8", errors="ignore")[:200]
            if MARKER in head:
                target_profile = item
                break
        except Exception:
            continue

    if backup_dir is None:
        backup_dir = ROOT / ".state/backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    if target_profile:
        # Create timestamped backup of old profile
        backup_file = backup_dir / f"{target_profile.stem}_{timestamp}.yaml"
        shutil.copy2(target_profile, backup_file)
        print(f"[*] 已备份旧配置至: {backup_file}")

        # Atomic replacement
        tmp_target = target_profile.with_suffix(".tmp")
        shutil.copy2(profile_path, tmp_target)
        tmp_target.replace(target_profile)
        print(f"[✓] 已成功替换 Clash Verge 现有配置: {target_profile.name}")
    else:
        print(f"[提示] 首次使用请双击运行 '首次导入.command'，或手动将 {profile_path} 导入 Clash Verge Profiles。")

    return True


def pull_latest() -> bool:
    """Pull latest Git updates if repository has remote."""
    try:
        res = subprocess.run(
            ["git", "-C", str(ROOT), "pull", "--ff-only"],
            capture_output=True,
            text=True
        )
        if res.returncode == 0:
            print("[*] Git 远端规则已拉取到最新。")
            return True
    except Exception:
        pass
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="ProxyMatrix: 本地代理配置管理与规则编译器")
    parser.add_argument("command", choices=["build", "check", "apply", "update", "fetch", "console"], help="操作指令")
    parser.add_argument("--clash-output", type=Path, default=DEFAULT_CLASH_OUTPUT, help="Clash YAML 输出路径")
    parser.add_argument("--sr-output", type=Path, default=DEFAULT_SR_CONF_OUTPUT, help="Shadowrocket CONF 输出路径")
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE, help="Mihomo 内核路径")
    parser.add_argument("--port", type=int, default=8787, help="Web 管理台端口 (默认: 8787)")
    parser.add_argument("--timeout", type=int, default=20, help="订阅拉取超时时间 (秒)")
    parser.add_argument("--airport", type=str, default=None, help="指定单家机场订阅名称")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    os.umask(0o077)
    try:
        if args.command == "console":
            from proxymatrix.web.server import run_server
            run_server(port=args.port, auto_open=not args.no_open)
            return 0

        if args.command == "fetch":
            urls = load_urls()
            if not urls:
                print("[!] 未配置任何订阅 URL (subscriptions/urls.json 或 local/urls.json)。")
                return 1
            if args.airport:
                if args.airport not in urls:
                    print(f"[!] 未找到机场 [{args.airport}]，已配置机场: {list(urls.keys())}")
                    return 1
                urls = {args.airport: urls[args.airport]}

            print(f"[*] 开始拉取订阅 ({len(urls)} 家机场)...")
            res = fetch_all_subscriptions(ROOT, urls=urls, timeout=args.timeout)
            for name, r in res.get("results", {}).items():
                if r.get("status") == "success":
                    print(f"[✓] 订阅 [{name}] 拉取成功: {r.get('nodes')} 个节点 -> {r.get('path')} (权限 0600)")
                else:
                    print(f"[!] 订阅 [{name}] 拉取失败: {r.get('error')}")

            if res.get("success", 0) == 0:
                print("[!] 未能成功拉取到有效节点缓存，终止后续构建。")
                return 1

            # Rebuild profiles with fresh nodes
            build_all(args.clash_output, args.sr_output)
            return 0

        if args.command == "update":
            pull_latest()
            urls = load_urls()
            if urls:
                print(f"[*] 正在拉取订阅更新 ({len(urls)} 家机场)...")
                fetch_all_subscriptions(ROOT, urls=urls, timeout=args.timeout)

        # Build profiles
        artifacts = build_all(args.clash_output, args.sr_output)

        # Check syntax
        if args.command in ("check", "apply", "update"):
            core_ok = validate_with_core(artifacts["clash"], args.core)
            if not core_ok and args.command == "check":
                print("[!] 检查未完成: 未检测到可用内核，未执行语义校验。", file=sys.stderr)
                return 2

        # Apply to local client
        if args.command in ("apply", "update"):
            apply_profile(artifacts["clash"])

    except Exception as e:
        print(f"[错误] 操作失败: {sanitize_exception(e)}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
