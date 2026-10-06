"""Authenticated loopback web console for ProxyMatrix configuration management.

Zero third-party dependencies: 100% Python 3 standard library.
All logs, error tracebacks, and external URLs are filtered via redact_text.
"""

import argparse
import copy
import datetime
import hashlib
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid
import webbrowser
from typing import Any, Dict, List, Optional

import manage
from proxymatrix.sources import fetcher
from proxymatrix.targets.clash import ClashCompiler
from proxymatrix.utils.redact import redact_text, redact_url, sanitize_exception
from proxymatrix.utils.validator import validate_site_rule, validate_subscription_entry

ROOT = manage.ROOT
ADMIN = ROOT / ".state/admin"


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Safely and atomically write bytes to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def write_json(path: Path, value: Any) -> None:
    """Write JSON data atomically."""
    atomic_write_bytes(path, json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def get_session_token() -> str:
    """Get or generate persistent local session token."""
    session_file = ADMIN / "session_token"
    if session_file.is_file():
        try:
            tok = session_file.read_text(encoding="utf-8").strip()
            if len(tok) >= 32:
                return tok
        except Exception:
            pass

    tok = secrets.token_urlsafe(32)
    ADMIN.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ADMIN, 0o700)
    except Exception:
        pass
    atomic_write_bytes(session_file, (tok + "\n").encode("utf-8"))
    try:
        os.chmod(session_file, 0o600)
    except Exception:
        pass
    return tok


SESSION = get_session_token()
LOCK = threading.RLock()
JOB_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}


def get_sub_token() -> str:
    """Get or generate subscriber download token."""
    token_file = ADMIN / "sub_token"
    if token_file.is_file():
        try:
            token = token_file.read_text(encoding="utf-8").strip()
            if len(token) >= 32:
                return token
        except Exception:
            pass
    return reset_sub_token()


def reset_sub_token() -> str:
    """Generate new download token."""
    token = secrets.token_urlsafe(32)
    ADMIN.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(ADMIN, 0o700)
    except Exception:
        pass
    token_file = ADMIN / "sub_token"
    atomic_write_bytes(token_file, (token + "\n").encode("utf-8"))
    try:
        os.chmod(token_file, 0o600)
    except Exception:
        pass
    return token


def stamp() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def clean_draft() -> Dict[str, Any]:
    """Load base persisted state into a draft structure."""
    urls = manage.load_urls()
    sites = manage.load_sites()
    return {
        "urls": copy.deepcopy(urls),
        "sites": copy.deepcopy(sites),
        "rev": uuid.uuid4().hex[:12],
        "saved_at": stamp(),
    }


def draft() -> Dict[str, Any]:
    """Get current active draft or initialize if absent."""
    draft_path = ADMIN / "draft.json"
    if draft_path.is_file():
        try:
            data = json.loads(draft_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "urls" in data and "sites" in data:
                return data
        except Exception:
            pass
    fresh = clean_draft()
    write_json(draft_path, fresh)
    return fresh


class DraftConflictError(ValueError):
    def __init__(self, message="草稿已被其他页面修改，请重新载入最新草稿。", current_draft=None):
        super().__init__(message)
        self.current_draft = current_draft


def save_draft(data: Dict[str, Any]) -> Dict[str, Any]:
    """Save user modifications to draft."""
    current = draft()
    base_rev = data.get("base_rev")
    is_force = bool(data.get("force") or data.get("overwrite"))
    cur_rev = current.get("rev")

    if cur_rev and not is_force:
        if not base_rev:
            raise DraftConflictError("保存草稿必须提供基准版本号 (base_rev)。", current_draft=current)
        if base_rev != cur_rev:
            raise DraftConflictError("草稿已被其他页面修改（版本不匹配），已拦截防止覆盖。", current_draft=current)

    conflict_detected = bool(base_rev and cur_rev and base_rev != cur_rev)

    if "sites" in data:
        sites = data["sites"]
        if not isinstance(sites, list):
            raise ValueError("网站规则列表格式错误。")
        normalized = []
        for index, item in enumerate(sites):
            validated = validate_site_rule(item)
            validated.setdefault("id", uuid.uuid4().hex)
            normalized.append(validated)
        current["sites"] = normalized

    if "url_changes" in data:
        url_changes = data["url_changes"]
        if not isinstance(url_changes, dict):
            raise ValueError("订阅地址格式错误。")
        for name, url in url_changes.items():
            validate_subscription_entry(name, url)
            current["urls"][name] = url.strip()

    if "delete_subscription" in data:
        del_name = data["delete_subscription"]
        current["urls"].pop(del_name, None)

    current["conflict_merged"] = conflict_detected
    current["rev"] = uuid.uuid4().hex[:12]
    current["saved_at"] = stamp()
    write_json(ADMIN / "draft.json", current)
    return current


def status() -> Dict[str, Any]:
    """Query current system state for console overview."""
    item = draft()
    urls = manage.load_urls()
    sites = manage.load_sites()
    live = {"connected": False, "selections": {}, "providers": {}}
    try:
        proxies = fetcher.live_api("/proxies").get("proxies", {})
        providers = fetcher.live_api("/providers/proxies").get("providers", {})
        configs = fetcher.live_api("/configs")
        tracked = ["🚀 节点选择", "🤖 AI 服务", "🌐 谷歌服务", "🎬 国际流媒体", "⚡️ 自动优选"]
        live = {
            "connected": True,
            "mode": configs.get("mode"),
            "ipv6": configs.get("ipv6"),
            "tun": configs.get("tun", {}).get("enable"),
            "selections": {name: proxies.get(name, {}).get("now") for name in tracked if name in proxies},
            "providers": {
                name: {
                    "nodes": len(value.get("proxies", [])),
                    "updated_at": value.get("updatedAt"),
                    "alive": sum(1 for node in value.get("proxies", []) if node.get("alive")),
                }
                for name, value in providers.items()
            },
        }
    except Exception:
        pass

    redacted_urls = {k: redact_url(v) for k, v in item["urls"].items()}
    sub_token = get_sub_token()

    return {
        "draft": {
            "urls": redacted_urls,
            "sites": item["sites"],
            "rev": item.get("rev"),
            "saved_at": item.get("saved_at"),
        },
        "persisted": {
            "urls": {k: redact_url(v) for k, v in urls.items()},
            "sites": sites,
        },
        "live": live,
        "sub_token": sub_token,
        "clash_profile": str(manage.DEFAULT_CLASH_OUTPUT),
    }


def publish(item: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """Publish draft to local config files and apply to Clash Verge Rev."""
    # Persist to local/ directory or subscriptions/
    local_dir = ROOT / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    urls_file = local_dir / "urls.json"
    sites_file = local_dir / "sites.json"

    write_json(urls_file, item["urls"])
    write_json(sites_file, item["sites"])

    # Build profiles
    artifacts = manage.build_all()
    # Apply to Clash Verge Rev
    applied = manage.apply_profile(artifacts["clash"])

    # Reset draft on success
    write_json(ADMIN / "draft.json", clean_draft())

    return {
        "published": True,
        "applied": applied,
        "message": "配置已成功编译并应用到本地客户端！" if applied else "配置已成功生成到 dist/ 目录，可导入客户端。",
        "clash_path": str(artifacts["clash"]),
        "sr_path": str(artifacts["shadowrocket_conf"]),
    }


def run_job(kind: str, data: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    """Execute background console tasks."""
    compiler = ClashCompiler(ROOT)
    yaml_text = compiler.compile(
        urls=item["urls"],
        providers_meta=manage.load_json("subscriptions/providers.json", "subscriptions/providers.example.json"),
        custom_sites=item["sites"],
    )

    if kind == "check":
        manage.validate_with_core(manage.DEFAULT_CLASH_OUTPUT)
        return {"passed": True, "message": "配置编译与内核语法校验通过。"}

    if kind == "google":
        return fetcher.probe_google(yaml_text)

    if kind in ("ai", "model"):
        return fetcher.probe_ai_endpoints(yaml_text)

    if kind == "website":
        url = data.get("url", "")
        return fetcher.probe_website(yaml_text, url)

    if kind == "subscription":
        name = data.get("name")
        if name not in item["urls"]:
            raise ValueError(f"未知订阅: {name}")
        url = item["urls"][name]
        return fetcher.test_subscription_url(url)

    if kind == "refresh_providers":
        name = data.get("name")
        names = [name] if name else list(item["urls"].keys())
        return fetcher.refresh_all_providers(names)

    if kind in ("publish", "apply"):
        return publish(item, force=data.get("force", False))

    raise ValueError(f"未知任务类型: {kind}")


def start_job(kind: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Launch async background task."""
    if not JOB_LOCK.acquire(blocking=False):
        raise ValueError("已有任务正在运行，请稍候。")
    job_id = uuid.uuid4().hex
    with LOCK:
        item = copy.deepcopy(draft())
        JOBS[job_id] = {"id": job_id, "type": kind, "state": "running", "started_at": stamp()}

    def worker():
        try:
            result = run_job(kind, data, item)
            with LOCK:
                JOBS[job_id].update(state="done", result=result, finished_at=stamp())
        except Exception as error:
            safe_msg = sanitize_exception(error)
            with LOCK:
                JOBS[job_id].update(state="failed", result={"message": safe_msg, "passed": False}, finished_at=stamp())
        finally:
            JOB_LOCK.release()

    threading.Thread(target=worker, daemon=True).start()
    return {"id": job_id, "state": "running"}


class ConsoleHandler(BaseHTTPRequestHandler):
    """HTTP request handler for ProxyMatrix console."""

    def log_message(self, *args):
        pass  # Suppress default noisy console logs

    def send(self, code: int, value: Any, content_type: str = "application/json; charset=utf-8", cookie: Optional[str] = None):
        if content_type.startswith("application/json"):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        elif isinstance(value, str):
            body = value.encode("utf-8")
        else:
            body = value

        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        allowed = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
        return host in allowed

    def authorized(self) -> bool:
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
            val = cookie.get("proxymatrix_session")
            if val and secrets.compare_digest(val.value, SESSION):
                return True
        except Exception:
            pass
        return False

    def do_GET(self):
        if not self.host_ok():
            return self.send(403, {"message": "仅支持本机环回访问。"})

        parsed = urlsplit(self.path)
        static = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/app.js": ("app.js", "text/javascript; charset=utf-8"),
            "/style.css": ("style.css", "text/css; charset=utf-8"),
        }
        if parsed.path in static:
            name, mime = static[parsed.path]
            file_path = ROOT / "web" / name
            if file_path.is_file():
                return self.send(200, file_path.read_bytes(), mime)
            return self.send(404, {"message": f"未找到前端资源文件: {name}"})

        # Subscription export endpoint
        if parsed.path in ("/clash.yaml", "/profile.yaml", "/sub"):
            query_token = parse_qs(parsed.query).get("token", [""])[0]
            current_token = get_sub_token()
            token_valid = bool(query_token) and secrets.compare_digest(query_token, current_token)
            if not token_valid and not self.authorized():
                return self.send(401, {"message": "未授权：订阅下载需要提供有效凭证 token。"})
            yaml_path = ROOT / "dist/Clash-Verge-Rev.yaml"
            if not yaml_path.is_file():
                manage.build_all()
            data = yaml_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/yaml; charset=utf-8")
            self.send_header("Content-Disposition", 'inline; filename="Clash-Verge-Rev.yaml"')
            self.send_header("Profile-Update-Interval", "24")
            self.send_header("subscription-userinfo", "upload=0; download=0; total=1073741824000; expire=0")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        # Authorized API routes
        if not self.authorized():
            return self.send(401, {"message": "未授权：请通过 '打开管理台.command' 启动授权页面。"})

        try:
            with LOCK:
                if parsed.path == "/api/state":
                    return self.send(200, status())
                if parsed.path == "/api/url":
                    name = parse_qs(parsed.query).get("name", [""])[0]
                    urls = draft()["urls"]
                    if name not in urls:
                        raise ValueError("未知订阅名称。")
                    return self.send(200, {"url": urls[name]})
                if parsed.path.startswith("/api/jobs/"):
                    key = parsed.path.rsplit("/", 1)[-1]
                    if key in JOBS:
                        return self.send(200, JOBS[key])
            return self.send(404, {"message": "页面或接口不存在。"})
        except Exception as error:
            return self.send(400, {"message": sanitize_exception(error)})

    def do_POST(self):
        origins = (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}")
        if not self.host_ok() or self.headers.get("Origin") not in origins:
            return self.send(403, {"message": "请求来源未通过验证。"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = {}
            if length > 0:
                if length > 131072:
                    raise ValueError("请求数据超过限制。")
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            elif self.path not in ("/api/draft/reset", "/api/sub_token/reset", "/api/url_reload/ack"):
                raise ValueError("请求格式无效。")

            if self.path == "/api/session":
                tok = str(data.get("token", ""))
                if not secrets.compare_digest(tok, SESSION):
                    return self.send(401, {"message": "访问密钥不正确。"})
                cookie = f"proxymatrix_session={SESSION}; HttpOnly; SameSite=Strict; Path=/; Max-Age=31536000"
                return self.send(200, {"authorized": True}, cookie=cookie)

            if not self.authorized():
                return self.send(401, {"message": "会话已过期，请重新登录。"})

            if self.path == "/api/jobs":
                return self.send(202, start_job(data.get("type", ""), data))

            with LOCK:
                if JOB_LOCK.locked():
                    raise ValueError("任务正在后台运行中，请完成后再修改草稿。")
                if self.path == "/api/draft":
                    return self.send(200, save_draft(data))
                if self.path == "/api/draft/reset":
                    write_json(ADMIN / "draft.json", clean_draft())
                    return self.send(200, {"message": "已重置草稿并重新载入配置。"})
                if self.path == "/api/sub_token/reset":
                    new_token = reset_sub_token()
                    return self.send(200, {"sub_token": new_token, "message": "已重置订阅访问 Token。"})
                if self.path == "/api/url_reload/ack":
                    return self.send(200, {"message": "已确认订阅地址重载。", "acknowledged": True})

            return self.send(404, {"message": "不存在的操作。"})
        except DraftConflictError as error:
            current_d = getattr(error, "current_draft", None)
            return self.send(409, {
                "message": sanitize_exception(error),
                "code": "conflict",
                "rev": current_d.get("rev") if isinstance(current_d, dict) else None,
            })
        except Exception as error:
            return self.send(400, {"message": sanitize_exception(error)})


def run_server(port: int = 8787, auto_open: bool = True) -> None:
    """Run console HTTP server."""
    os.umask(0o077)
    ADMIN.mkdir(parents=True, exist_ok=True)
    server_info = ADMIN / "server.json"
    if server_info.is_file():
        try:
            old = json.loads(server_info.read_text(encoding="utf-8"))
            conn = http.client.HTTPConnection("127.0.0.1", old["port"], timeout=1)
            conn.request("GET", "/api/state", headers={"Cookie": f"proxymatrix_session={old['session']}"})
            if conn.getresponse().status == 200:
                if auto_open:
                    webbrowser.open(old["url"])
                print(f"[*] ProxyMatrix 管理台已在本机运行: {old['url']}")
                return
        except Exception:
            pass

    server = None
    selected_port = port
    for p in range(port, port + 50):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", p), ConsoleHandler)
            selected_port = p
            break
        except OSError:
            continue

    if not server:
        raise OSError("未能找到可用的本机端口启动控制台。")

    url = f"http://127.0.0.1:{selected_port}/#session={SESSION}"
    write_json(server_info, {"port": selected_port, "session": SESSION, "url": url})

    print(f"[✓] ProxyMatrix 控制台服务已启动: http://127.0.0.1:{selected_port}/")
    if auto_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[*] 控制台服务已停止。")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="ProxyMatrix Web 管理台")
    parser.add_argument("--port", type=int, default=8787, help="控制台监听端口 (默认: 8787)")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    run_server(port=args.port, auto_open=not args.no_open)


if __name__ == "__main__":
    main()
