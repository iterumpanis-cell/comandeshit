import asyncio
from pathlib import Path

import pytest

import ia_worker
import mcp_vendes
from services.mcp_runtime import persist_mcp_url
from security import redact_text, validate_pm2_probe_config


def test_persist_mcp_url_only_updates_test_base(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("MCP_URL=old\n", encoding="utf-8")

    updated = persist_mcp_url("https://example.test/mcp/token", tmp_path)

    assert updated == [str(env_path.resolve())]
    assert env_path.read_text(encoding="utf-8") == "MCP_URL=https://example.test/mcp/token\n"


def test_persist_mcp_url_rejects_missing_base(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        persist_mcp_url("https://example.test/mcp/token", tmp_path / "missing")


def test_mcp_rejects_non_tls_without_network(monkeypatch):
    monkeypatch.setattr(mcp_vendes, "MCP_URL", "http://example.test/mcp/token")
    monkeypatch.setenv("HIT_ALLOW_REAL_MCP", "1")

    with pytest.raises(RuntimeError, match="https://"):
        asyncio.run(mcp_vendes.MCPVendes()._call("initialize", {}))


def test_mcp_is_blocked_by_default_without_network(monkeypatch):
    monkeypatch.setattr(mcp_vendes, "MCP_URL", "https://example.test/mcp/safe-token")
    monkeypatch.delenv("HIT_ALLOW_REAL_MCP", raising=False)
    with pytest.raises(RuntimeError, match="bloquejat"):
        asyncio.run(mcp_vendes.MCPVendes()._call("initialize", {}))


def test_redaction_never_keeps_mcp_or_telegram_secret():
    value = "bot123:telegram-secret https://mcp.nubehit.com/mcp/mcp-secret?token=query-secret"
    redacted = redact_text(value)
    assert "telegram-secret" not in redacted
    assert "mcp-secret" not in redacted
    assert "query-secret" not in redacted


def test_pm2_probe_config_is_validated_locally(tmp_path: Path):
    config = tmp_path / "ecosystem.config.js"
    config.write_text("name: 'hitsystems-bot-proves', cwd: 'C:/proves'", encoding="utf-8")
    assert validate_pm2_probe_config(config) == []


def test_worker_does_not_skip_permissions_or_leak_environment(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(ia_worker, "_opencode_executable", lambda: "opencode.cmd")
    monkeypatch.setattr(ia_worker.subprocess, "run", lambda *args, **kwargs: captured.update(kwargs) or Completed())
    monkeypatch.setenv("TELEGRAM_TOKEN", "secret")

    ia_worker._run_opencode({"id": "local-test", "command": "no-op"})

    command = captured["args"][0]
    assert "--dangerously-skip-permissions" not in command
    assert "TELEGRAM_TOKEN" not in captured["env"]
    assert captured["cwd"] == ia_worker.TEST_DIR


def test_reprint_callback_requires_matching_client_scope():
    from telegram_handlers.callbacks import build_callback_handler

    class Message:
        def __init__(self):
            self.replies = []

        async def reply_text(self, text):
            self.replies.append(text)

    class Query:
        data = "reprint_order:2026-06-24:42"
        from_user = type("User", (), {"id": 7})()

        def __init__(self):
            self.message = Message()

        async def answer(self, *args, **kwargs):
            pass

        async def edit_message_text(self, text, **kwargs):
            self.edited = text

    query = Query()
    update = type("Update", (), {"callback_query": query})()
    context = type("Context", (), {"user_data": {}})()
    deps = {
        "logger": None,
        "mcp": None,
        "ai": None,
        "order_field_labels": {},
        "order_field_kwargs": {},
        "manual_order_text": None,
        "manual_order_keyboard": None,
        "order_type_choice_keyboard": None,
        "format_order_fields": None,
        "confirmation_text": None,
        "confirmation_keyboard": None,
        "format_order_ticket": None,
        "html_delete_line": None,
        "load_auth_data": lambda: {},
        "save_auth_data": lambda data: None,
        "get_admin_user_id": lambda: 99,
        "get_copies": lambda client: 1,
        "base_dir": Path.cwd(),
        "get_auth": lambda user_id: {"role": "client", "client_code": 41},
        "autoritzat": lambda update: True,
    }

    handler = build_callback_handler(**deps)
    asyncio.run(handler(update, context))

    assert query.message.replies == ["❌ No tens permís per reimprimir aquest client."]
