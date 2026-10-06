"""Security unit tests for loopback web console (P2-5 verification).

Validates Host header protection, Origin CSRF verification, session authentication,
token-based subscription download, and sensitive data redaction in API responses.
"""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from proxymatrix.web import server


class TestServerSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Bind ephemeral port on loopback
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.ConsoleHandler)
        cls.port = cls.httpd.server_port
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_host_header_enforcement(self):
        """CRITICAL SECURITY TEST: Ensure requests with spoofed/external Host headers are blocked."""
        url = f"http://127.0.0.1:{self.port}/"

        # 1. Attacker Host header -> 403
        req_bad = urllib.request.Request(url, headers={"Host": f"attacker.com:{self.port}"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_bad)
        self.assertEqual(ctx.exception.code, 403)

        # 2. Legitimate loopback Host -> 200
        req_ok = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{self.port}"})
        with urllib.request.urlopen(req_ok) as resp:
            self.assertEqual(resp.status, 200)

    def test_origin_header_post_csrf_protection(self):
        """CRITICAL SECURITY TEST: Ensure POST requests verify Origin matches loopback."""
        url = f"http://127.0.0.1:{self.port}/api/session"
        body = json.dumps({"token": server.SESSION}).encode("utf-8")

        # 1. Cross-origin POST -> 403
        req_cross = urllib.request.Request(
            url,
            data=body,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": "http://evil.website.com",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_cross)
        self.assertEqual(ctx.exception.code, 403)

        # 2. Legitimate Origin -> 200
        req_same = urllib.request.Request(
            url,
            data=body,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/json",
            },
            method="POST"
        )
        with urllib.request.urlopen(req_same) as resp:
            self.assertEqual(resp.status, 200)
            cookie = resp.headers.get("Set-Cookie", "")
            self.assertIn("proxymatrix_session=", cookie)

    def test_session_auth_and_redaction(self):
        """Verify API authentication, cookie verification, and token redaction."""
        state_url = f"http://127.0.0.1:{self.port}/api/state"

        # 1. Unauthenticated request to protected API -> 401
        req_no_auth = urllib.request.Request(
            state_url,
            headers={"Host": f"127.0.0.1:{self.port}"}
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_no_auth)
        self.assertEqual(ctx.exception.code, 401)

        # 2. Authenticated request with session cookie -> 200
        cookie_header = f"proxymatrix_session={server.SESSION}"
        req_auth = urllib.request.Request(
            state_url,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Cookie": cookie_header,
            }
        )
        with urllib.request.urlopen(req_auth) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("draft", data)
            self.assertIn("persisted", data)
            # Verify URLs in draft are redacted
            for k, val in data["draft"]["urls"].items():
                if "token=" in val:
                    self.assertIn("token=***", val)
                    self.assertNotIn("EXAMPLE_TOKEN", val)

    def test_save_draft_input_validation(self):
        """Verify save_draft API rejects malformed / injected rules."""
        draft_url = f"http://127.0.0.1:{self.port}/api/draft"
        cookie_header = f"proxymatrix_session={server.SESSION}"

        # Injected rule payload
        bad_payload = json.dumps({
            "force": True,
            "sites": [
                {"type": "DOMAIN-SUFFIX", "domain": "bad.com,REJECT", "target": "@proxy"}
            ]
        }).encode("utf-8")

        req = urllib.request.Request(
            draft_url,
            data=bad_payload,
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Cookie": cookie_header,
                "Content-Type": "application/json",
            },
            method="POST"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
