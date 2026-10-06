"""Subscription fetcher, local isolated core testing, and network probe engine.

Zero external dependencies: 100% Python 3 standard library.
All output errors and subscription URLs are sanitized through redact_text.
"""

import base64
import contextlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import time
import threading
from typing import Any, Dict, List, Optional, Tuple, Union
import urllib.error
import urllib.parse
import urllib.request

from proxymatrix.targets.clash import dict_to_yaml
from proxymatrix.utils.redact import redact_text, sanitize_exception
from proxymatrix.utils.validator import validate_subscription_entry
from proxymatrix.utils.yaml_parser import parse_clash_yaml_proxies

ACTIVE = set()
ACTIVE_LOCK = threading.Lock()

DEFAULT_CORE = Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo")
APP_HOME = Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"


def find_core() -> Optional[Path]:
    """Find installed Mihomo or Clash Meta kernel binary."""
    if DEFAULT_CORE.is_file():
        return DEFAULT_CORE
    for name in ("mihomo", "verge-mihomo", "clash-meta"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def cleanup_processes() -> None:
    """Terminate any lingering child test processes."""
    with ACTIVE_LOCK:
        processes = list(ACTIVE)
    for process in processes:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


def free_port() -> int:
    """Find an available TCP port on loopback."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def normalize_domain(value: str) -> str:
    """Normalize input URL or host into a clean domain name."""
    clean = (value or "").strip()
    if "://" in clean:
        clean = urllib.parse.urlsplit(clean).netloc
    clean = clean.split(":")[0].strip().lower()
    return clean


class UnixHTTP(http.client.HTTPConnection):
    """HTTP Connection over Unix domain socket for Clash Verge service."""
    def __init__(self, sock_path: str, timeout: int = 10):
        super().__init__("localhost", timeout=timeout)
        self.sock_path = sock_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.sock_path)


def live_api(path: str, method: str = "GET", body: Any = None) -> Dict[str, Any]:
    """Query live Clash Verge Rev core API via local domain socket or return empty."""
    sock_path = f"/var/run/clash-verge-service/users/{os.getuid()}/verge-mihomo.sock"
    if not os.path.exists(sock_path):
        return {}

    connection = UnixHTTP(sock_path, timeout=10)
    try:
        headers = {}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        if response.status >= 400:
            err = redact_text(response.read().decode("utf-8", errors="ignore"))
            raise ValueError(f"Clash Verge API 错误 ({response.status}): {err}")
        if response.status == 204:
            return {}
        raw = response.read()
        return json.loads(raw) if raw else {}
    finally:
        connection.close()


def refresh_provider(name: str) -> Dict[str, Any]:
    """Refresh single proxy provider in live kernel."""
    quoted = urllib.parse.quote(name)
    live_api(f"/providers/proxies/{quoted}", method="PUT")
    provs = live_api("/providers/proxies").get("providers", {})
    info = provs.get(name, {})
    proxies = info.get("proxies", [])
    return {
        "name": name,
        "nodes": len(proxies),
        "alive": sum(1 for p in proxies if p.get("alive")),
        "updated_at": info.get("updatedAt"),
    }


def refresh_all_providers(airport_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Refresh all or selected proxy providers."""
    try:
        provs = live_api("/providers/proxies").get("providers", {})
    except Exception as e:
        provs = {}
        if airport_names:
            return {
                name: {"name": name, "status": "failed", "error": f"内核接口连接失败: {redact_text(str(e))}"}
                for name in airport_names
            }
        return {}

    targets = airport_names if airport_names is not None else list(provs.keys())
    results = {}
    for name in targets:
        if name in provs:
            try:
                res = refresh_provider(name)
                res["status"] = "success"
                results[name] = res
            except Exception as e:
                results[name] = {"name": name, "status": "failed", "error": redact_text(str(e))}
        else:
            results[name] = {"name": name, "status": "unapplied", "error": "该机场尚未应用到内核，请先应用配置。"}
    return results


class IsolatedCore:
    """Manages an isolated sandbox Mihomo kernel instance on loopback for non-destructive testing."""

    def __init__(self, config_dict: Dict[str, Any], yaml_text: str):
        self.config_dict = config_dict
        self.yaml_text = yaml_text
        self.port = free_port()
        self.control = free_port()
        self.secret = secrets.token_hex(24)
        self.core_bin = find_core()
        if not self.core_bin or not self.core_bin.is_file():
            raise FileNotFoundError("未检测到本地 Mihomo / Clash Verge 内核，无法运行沙盒测试。")
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.process: Optional[subprocess.Popen] = None
        self.log_file = None

    def __enter__(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="proxymatrix-sandbox-")
        root = Path(self.temp_dir.name)

        # Copy geoip / geosite if available
        for name in ("geosite.dat", "geoip.dat", "country.mmdb"):
            source = APP_HOME / name
            if source.is_file():
                try:
                    shutil.copy2(source, root / name)
                except Exception:
                    pass

        # Adjust ports for sandbox
        patched_yaml = self.yaml_text
        # Ensure sandbox port overrides
        overrides = (
            f"\nmixed-port: {self.port}\n"
            f"allow-lan: false\n"
            f"bind-address: 127.0.0.1\n"
            f"external-controller: 127.0.0.1:{self.control}\n"
            f"secret: \"{self.secret}\"\n"
        )
        patched_yaml += overrides

        config_path = root / "config.yaml"
        config_path.write_text(patched_yaml, encoding="utf-8")

        self.log_file = (root / "core.log").open("w")
        self.process = subprocess.Popen(
            [str(self.core_bin), "-d", str(root), "-f", str(config_path)],
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with ACTIVE_LOCK:
            ACTIVE.add(self.process)

        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ValueError("测试内核无法启动，请先检查配置语法。")
            try:
                self.api("/version")
                return self
            except (OSError, ValueError):
                time.sleep(0.2)

        raise TimeoutError("测试内核启动超时。")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
        if self.temp_dir:
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass
        if self.process:
            with ACTIVE_LOCK:
                ACTIVE.discard(self.process)

    def api(self, path: str) -> Dict[str, Any]:
        """Query sandbox controller REST API."""
        connection = http.client.HTTPConnection("127.0.0.1", self.control, timeout=3)
        try:
            connection.request("GET", path, headers={"Authorization": f"Bearer {self.secret}"})
            response = connection.getresponse()
            if response.status >= 400:
                raise ValueError(f"测试内核状态读取失败 ({response.status})。")
            return json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def ready(self) -> bool:
        """Wait for proxy outbound readiness."""
        for _ in range(8):
            res = self.http("https://www.gstatic.com/generate_204", timeout=5)
            if res.get("http_status") == 204:
                return True
            time.sleep(0.5)
        return False

    def http(self, url: str, timeout: int = 15) -> Dict[str, Any]:
        """Send HTTP GET through sandbox mixed-port."""
        env = os.environ.copy()
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
            env.pop(key, None)
        try:
            result = subprocess.run(
                [
                    "curl", "--proxy", f"http://127.0.0.1:{self.port}",
                    "--max-time", str(timeout),
                    "-sS", "-o", "/dev/null", "-w", "%{http_code}", url
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout + 3
            )
            code = int(result.stdout) if result.stdout.strip().isdigit() else 0
            return {"http_status": code, "transport_ok": result.returncode == 0, "curl_code": result.returncode}
        except Exception as e:
            return {"http_status": 0, "transport_ok": False, "error": redact_text(str(e))}

    def tls_route(self, host: str) -> Dict[str, Any]:
        """Connect via CONNECT tunnel and check TLS handshake & routing chain."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=15) as raw:
            raw.settimeout(15)
            raw.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode())
            response = bytearray()
            while b"\r\n\r\n" not in response:
                part = raw.recv(4096)
                if not part or len(response) > 16384:
                    raise ValueError("代理连接未能建立。")
                response.extend(part)
            if not response.startswith(b"HTTP/1.1 200"):
                raise ValueError("代理拒绝建立连接。")
            with ssl.create_default_context().wrap_socket(raw, server_hostname=host):
                for _ in range(5):
                    matches = [
                        x for x in self.api("/connections").get("connections", [])
                        if x.get("metadata", {}).get("host") == host
                    ]
                    if matches:
                        item = matches[-1]
                        return {
                            "host": host,
                            "chains": item.get("chains", []),
                            "rule": item.get("rule"),
                            "rule_payload": item.get("rulePayload"),
                            "tls_ok": True,
                        }
                    time.sleep(0.1)
        return {"host": host, "chains": [], "tls_ok": True}


def probe_website(yaml_text: str, value: str) -> Dict[str, Any]:
    """Test routing and HTTP connectivity for target site in sandbox."""
    host = normalize_domain(value)
    if not host:
        raise ValueError("请输入有效的域名或网址。")
    with IsolatedCore({}, yaml_text) as core:
        core.ready()
        route = core.tls_route(host)
        response = core.http(f"https://{host}/")
    return dict(route, **response, message="路由使用沙盒测试配置；HTTP 状态与网站账号是否可用分别判断。")


def probe_google(yaml_text: str) -> Dict[str, Any]:
    """Test connectivity to core Google and ecosystem endpoints."""
    # Note: antigravity.google is an official endpoint exemption
    hosts = [
        "cloudcode-pa.googleapis.com",
        "accounts.google.com",
        "accounts.youtube.com",
        "youtubei.googleapis.com",
        "www.gstatic.com",
        "antigravity.google",
    ]
    with IsolatedCore({}, yaml_text) as core:
        core.ready()
        routes = []
        for host in hosts:
            try:
                routes.append(core.tls_route(host))
            except (OSError, ValueError):
                routes.append({"host": host, "tls_ok": False, "chains": []})
    success_routes = [x for x in routes if x.get("tls_ok")]
    passed = len(success_routes) == len(hosts)
    return {
        "passed": passed,
        "routes": routes,
        "message": "六个核心域名均已成功建立 TLS 连接。" if passed else "部分域名连接未成功，请检查节点状态或分流配置。"
    }


def probe_ai_endpoints(yaml_text: str) -> Dict[str, Any]:
    """Test standard open AI endpoints (OpenAI, Anthropic, Google AI) via TLS handshake.

    Pure standard library TLS probe replacing any legacy vendor-specific CLI tool calls.
    """
    ai_hosts = [
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
    ]
    with IsolatedCore({}, yaml_text) as core:
        core.ready()
        routes = []
        for host in ai_hosts:
            try:
                route = core.tls_route(host)
                routes.append(route)
            except Exception:
                routes.append({"host": host, "tls_ok": False, "chains": []})

    success = [r for r in routes if r.get("tls_ok")]
    passed = len(success) == len(ai_hosts)
    return {
        "passed": passed,
        "routes": routes,
        "message": f"AI 接口连通测试: {len(success)}/{len(ai_hosts)} 端点 TLS 握手成功。"
    }


def safe_b64decode(s: str) -> Optional[bytes]:
    """Base64 decode string with padding fix and urlsafe support."""
    clean = re.sub(r"\s+", "", s)
    if not clean:
        return None
    missing_padding = len(clean) % 4
    if missing_padding:
        clean += "=" * (4 - missing_padding)
    for alt in (None, b"-_"):
        try:
            return base64.b64decode(clean, altchars=alt)
        except Exception:
            pass
    try:
        return base64.urlsafe_b64decode(clean)
    except Exception:
        return None


def parse_ss_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse Shadowsocks URI into proxy dictionary."""
    parsed = urllib.parse.urlsplit(uri)
    name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "SS Node"
    netloc = parsed.netloc or parsed.path
    if "@" in netloc:
        userinfo, hostport = netloc.split("@", 1)
        decoded_userinfo = safe_b64decode(userinfo)
        if decoded_userinfo:
            try:
                userinfo = decoded_userinfo.decode("utf-8")
            except Exception:
                pass
        if ":" not in userinfo or ":" not in hostport:
            return None
        method, password = userinfo.split(":", 1)
        server, port_str = hostport.split(":", 1)
    else:
        decoded = safe_b64decode(netloc)
        if not decoded:
            return None
        try:
            s = decoded.decode("utf-8")
        except Exception:
            return None
        if "@" not in s or ":" not in s:
            return None
        userinfo, hostport = s.split("@", 1)
        if ":" not in userinfo or ":" not in hostport:
            return None
        method, password = userinfo.split(":", 1)
        server, port_str = hostport.split(":", 1)

    try:
        port = int(port_str.split("/")[0])
    except ValueError:
        return None

    return {
        "name": name,
        "type": "ss",
        "server": server,
        "port": port,
        "cipher": method,
        "password": password,
        "udp": True,
    }


def parse_vmess_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse VMess URI into proxy dictionary."""
    b64_part = uri.removeprefix("vmess://").strip()
    decoded = safe_b64decode(b64_part)
    if not decoded:
        return None
    try:
        data = json.loads(decoded.decode("utf-8"))
    except Exception:
        return None

    server = str(data.get("add", "")).strip()
    try:
        port = int(data.get("port", 443))
    except (ValueError, TypeError):
        port = 443
    uuid = str(data.get("id", "")).strip()
    if not server or not uuid:
        return None

    name = str(data.get("ps", f"VMess-{server}:{port}")).strip()
    alter_id = int(data.get("aid", 0)) if str(data.get("aid", 0)).isdigit() else 0
    net = str(data.get("net", "tcp")).strip().lower()
    tls = str(data.get("tls", "")).strip().lower() in ("tls", "true")

    node: Dict[str, Any] = {
        "name": name,
        "type": "vmess",
        "server": server,
        "port": port,
        "uuid": uuid,
        "alterId": alter_id,
        "cipher": "auto",
        "tls": tls,
        "udp": True,
    }
    if net == "ws":
        node["network"] = "ws"
        ws_opts: Dict[str, Any] = {}
        if data.get("path"):
            ws_opts["path"] = str(data["path"])
        if data.get("host"):
            ws_opts["headers"] = {"Host": str(data["host"])}
        if ws_opts:
            node["ws-opts"] = ws_opts
    elif net:
        node["network"] = net

    return node


def parse_trojan_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse Trojan URI into proxy dictionary."""
    parsed = urllib.parse.urlsplit(uri)
    name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "Trojan Node"
    password = parsed.username or ""
    server = parsed.hostname or ""
    port = parsed.port or 443
    if not server or not password:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    sni = query.get("sni", [query.get("peer", [server])[0]])[0]

    return {
        "name": name,
        "type": "trojan",
        "server": server,
        "port": port,
        "password": password,
        "sni": sni,
        "udp": True,
    }


def parse_vless_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse VLESS URI into proxy dictionary."""
    parsed = urllib.parse.urlsplit(uri)
    name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "VLESS Node"
    uuid = parsed.username or ""
    server = parsed.hostname or ""
    port = parsed.port or 443
    if not server or not uuid:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    security = query.get("security", ["none"])[0].lower()
    sni = query.get("sni", [server])[0]

    node: Dict[str, Any] = {
        "name": name,
        "type": "vless",
        "server": server,
        "port": port,
        "uuid": uuid,
        "tls": security in ("tls", "reality"),
        "udp": True,
    }
    if query.get("flow"):
        node["flow"] = query["flow"][0]
    if security == "reality":
        reality_opts: Dict[str, Any] = {"public-key": query.get("pbk", [""])[0]}
        if query.get("sid"):
            reality_opts["short-id"] = query["sid"][0]
        node["reality-opts"] = reality_opts
        node["servername"] = sni
    elif security == "tls":
        node["servername"] = sni

    return node


def parse_hysteria2_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse Hysteria2 URI into proxy dictionary."""
    parsed = urllib.parse.urlsplit(uri)
    name = urllib.parse.unquote(parsed.fragment) if parsed.fragment else "Hy2 Node"
    password = parsed.username or ""
    server = parsed.hostname or ""
    port = parsed.port or 443
    if not server or not password:
        return None
    query = urllib.parse.parse_qs(parsed.query)
    sni = query.get("sni", [server])[0]

    return {
        "name": name,
        "type": "hysteria2",
        "server": server,
        "port": port,
        "password": password,
        "sni": sni,
    }


def parse_proxy_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse any supported proxy URI string."""
    uri_clean = uri.strip()
    if not uri_clean or uri_clean.startswith("#"):
        return None
    if uri_clean.startswith("ss://"):
        return parse_ss_uri(uri_clean)
    if uri_clean.startswith("vmess://"):
        return parse_vmess_uri(uri_clean)
    if uri_clean.startswith("trojan://"):
        return parse_trojan_uri(uri_clean)
    if uri_clean.startswith("vless://"):
        return parse_vless_uri(uri_clean)
    if uri_clean.startswith("hysteria2://") or uri_clean.startswith("hy2://"):
        return parse_hysteria2_uri(uri_clean)
    return None


def parse_subscription_nodes(content_data: Union[bytes, str]) -> List[Dict[str, Any]]:
    """Parse subscription content from Clash YAML or Base64 URI list."""
    if isinstance(content_data, bytes):
        raw_text = content_data.decode("utf-8", errors="ignore")
    else:
        raw_text = content_data

    # 1. Try Clash YAML directly
    if "proxies:" in raw_text or "\n- " in raw_text:
        proxies = parse_clash_yaml_proxies(raw_text)
        if proxies:
            return proxies

    # 2. Try raw URI lines
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    nodes = []
    for line in lines:
        node = parse_proxy_uri(line)
        if node:
            nodes.append(node)
    if nodes:
        return nodes

    # 3. Try Base64 decoding
    b64_clean = re.sub(r"\s+", "", raw_text)
    decoded_bytes = safe_b64decode(b64_clean)
    if decoded_bytes:
        decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
        if "proxies:" in decoded_text or "\n- " in decoded_text:
            proxies = parse_clash_yaml_proxies(decoded_text)
            if proxies:
                return proxies
        lines = [line.strip() for line in decoded_text.splitlines() if line.strip()]
        for line in lines:
            node = parse_proxy_uri(line)
            if node:
                nodes.append(node)
        if nodes:
            return nodes

    return []


def save_profile_yaml(profile_path: Path, proxies: List[Dict[str, Any]], provider_name: str = "") -> None:
    """Atomically save proxies to profiles/<name>.yaml with strict 0600 file permissions."""
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = profile_path.parent / f".tmp_{profile_path.name}_{os.getpid()}"
    content = (
        f"# managed-by-proxy-matrix - Subscribed profile for {provider_name or profile_path.stem}\n"
        f"# [REAL SUBSCRIPTION] Generated from verified subscription feed.\n"
        + dict_to_yaml({"proxies": proxies})
    )
    # Enforce strict 0600 file permissions
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(str(tmp_path), str(profile_path))
        os.chmod(str(profile_path), 0o600)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def fetch_subscription(name: str, url: str, profiles_dir: Path, timeout: int = 20) -> Dict[str, Any]:
    """Download subscription, parse nodes, and atomically save to profiles/<name>.yaml (0600).

    All error messages and subscription URLs are sanitized with redact_text to prevent token leaks.
    """
    safe_url = redact_text(url)
    try:
        validate_subscription_entry(name, url)
    except Exception as e:
        clean_err = sanitize_exception(e)
        return {
            "name": name,
            "status": "failed",
            "error": clean_err,
            "url": safe_url,
        }

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ClashVerge/1.7.0 (Mihomo; +https://github.com/VeeDou/proxy-matrix)",
            "Accept": "*/*",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = response.read(8388609)
            if len(data) > 8388608:
                raise ValueError("订阅内容超过 8MB 安全上限，已停止处理。")
    except Exception as e:
        clean_err = redact_text(sanitize_exception(e))
        return {
            "name": name,
            "status": "failed",
            "error": f"下载订阅失败: {clean_err}",
            "url": safe_url,
        }

    nodes = parse_subscription_nodes(data)
    if not nodes:
        return {
            "name": name,
            "status": "failed",
            "error": "未能从订阅内容中解析出有效节点 (支持 Clash YAML 与 Base64 URI 节点)",
            "url": safe_url,
        }

    profile_path = profiles_dir / f"{name}.yaml"
    try:
        save_profile_yaml(profile_path, nodes, provider_name=name)
    except Exception as e:
        clean_err = redact_text(sanitize_exception(e))
        return {
            "name": name,
            "status": "failed",
            "error": f"写入订阅缓存文件失败: {clean_err}",
            "url": safe_url,
        }

    return {
        "name": name,
        "status": "success",
        "nodes": len(nodes),
        "path": profile_path,
        "url": safe_url,
    }


def fetch_all_subscriptions(
    root_dir: Path,
    urls: Optional[Dict[str, str]] = None,
    timeout: int = 20
) -> Dict[str, Any]:
    """Fetch all configured subscriptions into root_dir/profiles/."""
    profiles_dir = root_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)

    target_urls = urls
    if target_urls is None:
        from manage import load_urls
        target_urls = load_urls()

    if not target_urls:
        return {"total": 0, "success": 0, "failed": 0, "results": {}}

    results = {}
    success_count = 0
    failed_count = 0

    for name, url in target_urls.items():
        res = fetch_subscription(name, url, profiles_dir=profiles_dir, timeout=timeout)
        results[name] = res
        if res.get("status") == "success":
            success_count += 1
        else:
            failed_count += 1

    return {
        "total": len(target_urls),
        "success": success_count,
        "failed": failed_count,
        "results": results,
    }


def test_subscription_url(url: str, proxy_mixed_port: Optional[int] = None) -> Dict[str, Any]:
    """Download subscription content safely with size limits and validate proxy nodes."""
    safe_url = redact_text(url)
    proxies = {}
    if proxy_mixed_port:
        proxies = {
            "http": f"http://127.0.0.1:{proxy_mixed_port}",
            "https": f"http://127.0.0.1:{proxy_mixed_port}",
        }
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ClashVerge/1.7.0 (Mihomo; +https://github.com/VeeDou/proxy-matrix)"}
    )
    try:
        with opener.open(req, timeout=20) as response:
            data = response.read(8388609)
            if len(data) > 8388608:
                raise ValueError("订阅内容超过 8MB 安全上限，已停止处理。")
            status = response.status
    except urllib.error.HTTPError as error:
        return {
            "passed": False,
            "http_status": error.code,
            "message": f"订阅返回 HTTP {error.code}；请检查订阅地址或访问限制。"
        }
    except Exception as error:
        return {
            "passed": False,
            "message": f"无法下载订阅: {redact_text(str(error))}"
        }

    nodes = parse_subscription_nodes(data)
    count = len(nodes)

    return {
        "passed": count > 0,
        "http_status": status,
        "nodes": count,
        "message": f"成功下载并识别到 {count} 个节点。" if count else "文件已成功下载，但未识别到节点；请确认订阅为 Clash/Mihomo 格式或 Base64 节点列表。"
    }
