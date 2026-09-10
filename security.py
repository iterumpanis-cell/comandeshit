"""Small, dependency-free safety helpers for the probe project."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PRODUCTION_MCP_HOSTS = {"mcp.nubehit.com"}
_TELEGRAM_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)
_BEARER = re.compile(r"(Bearer\s+)[^\s,;]+", re.IGNORECASE)
_ENV_SECRET = re.compile(r"(?i)(TELEGRAM_TOKEN|BOT_TOKEN|MCP_URL|API_KEY|SECRET)(\s*=\s*)[^\s]+")
_MCP_PATH = re.compile(r"(/mcp/)[^/?#\s]+", re.IGNORECASE)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|access_token|api_key|apikey|key|secret)=)[^&#\s]+")


def redact_text(value: object) -> str:
    """Return log-safe text without exposing token values."""
    text = str(value)
    text = _TELEGRAM_TOKEN.sub("bot<REDACTED>", text)
    text = _BEARER.sub(r"\1<REDACTED>", text)
    text = _ENV_SECRET.sub(r"\1\2<REDACTED>", text)
    text = _MCP_PATH.sub(r"\1<REDACTED>", text)
    text = _QUERY_SECRET.sub(r"\1<REDACTED>", text)
    try:
        parts = urlsplit(text)
        if parts.scheme and parts.netloc and parts.query:
            secret_names = {"token", "access_token", "api_key", "apikey", "key", "secret"}
            query = [(key, "<REDACTED>" if key.lower() in secret_names else val)
                     for key, val in parse_qsl(parts.query, keep_blank_values=True)]
            text = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except ValueError:
        pass
    return text


def redact(value: object) -> object:
    if isinstance(value, dict):
        secret_names = {"token", "access_token", "api_key", "authorization", "secret"}
        return {key: ("<REDACTED>" if str(key).lower() in secret_names else redact(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    return redact_text(value) if isinstance(value, str) else value


def require_real_mcp(url: str | None = None) -> None:
    if os.getenv("HIT_ALLOW_REAL_MCP") != "1":
        raise RuntimeError("MCP real bloquejat: estableix HIT_ALLOW_REAL_MCP=1 explicitament")
    host = (url or "").split("/", 3)[2].split(":", 1)[0].lower() if "://" in (url or "") else ""
    if host in PRODUCTION_MCP_HOSTS and os.getenv("HIT_ALLOW_PRODUCTION_MCP") != "1":
        raise RuntimeError("MCP de produccio bloquejat: cal HIT_ALLOW_PRODUCTION_MCP=1")


def mcp_call_allowed(url: str | None) -> None:
    script = Path(sys.argv[0]).name.lower()
    if os.getenv("PYTEST_CURRENT_TEST") or script.startswith("test_"):
        require_real_mcp(url)


def validate_pm2_probe_config(path: str | Path) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    errors = []
    if "hitsystems-bot-proves" not in text:
        errors.append("falta el nom del proces de proves")
    if "hitsystems-bot\\" in text or "hitsystems-bot/" in text:
        errors.append("la configuracio apunta a produccio")
    if "--dangerously-skip-permissions" in text:
        errors.append("permisos perillosos activats")
    return errors
