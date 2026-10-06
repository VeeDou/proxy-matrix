"""Unified sensitive data redaction utility."""

import re
from typing import Any

# Regex to mask secret tokens, airport passwords/keys in URLs and strings
URL_TOKEN_RE = re.compile(r"(https?://[^?/\s]+[^\s?]*\?(?:[^&\s]*&)*?(?:token|key|secret|password|access_token)=)([a-zA-Z0-9_\-]+)", re.IGNORECASE)
HEX64_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
USER_PATH_RE = re.compile(r"/(Users|home)/([a-zA-Z0-9_.-]+)/")


def redact_text(text: str) -> str:
    """Mask tokens, private hexes, and user paths from text/exception messages."""
    if not isinstance(text, str):
        text = str(text)

    # 1. Mask token parameter values in URLs: token=123456... -> token=***
    text = URL_TOKEN_RE.sub(r"\1***", text)

    # 2. Mask 64-hex tokens: keep first 6 and last 4
    text = HEX64_RE.sub(lambda m: f"{m.group()[:6]}...{m.group()[-4:]}", text)

    # 3. Mask username in paths: /Users/<user>/... -> /Users/***...
    text = USER_PATH_RE.sub(r"/\1/***/", text)

    return text


import urllib.parse


def redact_url(url: str) -> str:
    """Mask sensitive path tokens and parameters in subscription URLs, preserving only scheme and host."""
    if not isinstance(url, str):
        url = str(url)
    clean = url.strip()
    try:
        parts = urllib.parse.urlsplit(clean)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}/***"
        return redact_text(clean)
    except Exception:
        return "***"


def sanitize_exception(e: BaseException) -> str:
    """Format and redact an exception message safely."""
    return redact_text(f"{type(e).__name__}: {e}")
