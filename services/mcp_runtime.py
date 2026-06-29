import os
import re
from pathlib import Path

import mcp_vendes as mcp_module


MCP_TOKEN_RE = re.compile(r"/mcp/([^/?#\s]+)")


def nubehit_url_from_current(current_url: str | None = None) -> str | None:
    current_url = (current_url or os.getenv("MCP_URL") or getattr(mcp_module, "MCP_URL", "") or "").strip()
    match = MCP_TOKEN_RE.search(current_url)
    if not match:
        return None
    return f"https://mcp.nubehit.com/mcp/{match.group(1)}"


def set_runtime_mcp_url(url: str, mcp=None) -> None:
    os.environ["MCP_URL"] = url
    mcp_module.MCP_URL = url
    if mcp is not None:
        try:
            mcp._session_id = None
        except Exception:
            pass


def _replace_env_value(path: Path, key: str, value: str) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^({re.escape(key)}=).*?$", re.MULTILINE)
    if not pattern.search(text):
        return False
    new_text = pattern.sub(rf"\g<1>{value}", text)
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
    return True


def persist_mcp_url(url: str, base_dir: Path) -> list[str]:
    base = base_dir.resolve()
    targets = [
        (base / ".env", "MCP_URL"),
        (base.parent / "hitsystems-bot" / ".env", "MCP_URL"),
        (base.parent / "FACTURES REBUDES ITERUM 2026" / ".env", "HITSYSTEMS_MCP_URL"),
    ]
    updated = []
    for path, key in targets:
        if _replace_env_value(path, key, url):
            updated.append(str(path))
    return updated
