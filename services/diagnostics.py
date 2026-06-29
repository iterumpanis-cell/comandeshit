import re
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
BOT_LOG_PATH = BASE_DIR / "bot_log.txt"
PM2_LOG_DIR = Path.home() / ".pm2" / "logs"
PRODUCTION_BOT_LOG = PM2_LOG_DIR / "hitsystems-bot-error.log"
TEST_BOT_LOG = PM2_LOG_DIR / "hitsystems-bot-proves-error.log"


def redact_sensitive(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"bot\d+:[A-Za-z0-9_\-]+", "bot<TELEGRAM_TOKEN>", text)
    text = re.sub(r"(TELEGRAM_TOKEN=)[^\s]+", r"\1<SECRET>", text)
    text = re.sub(r"(MCP_URL=)[^\s]+", r"\1<SECRET>", text)
    return text


def run_readonly_command(args: list[str], cwd: str | None = None, timeout: int = 8) -> str:
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as e:
        return f"Error executant {' '.join(args)}: {e}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return redact_sensitive(output.strip())


def tail_file(path: Path, max_lines: int = 120) -> list[str]:
    if not path.exists():
        return [f"No existeix: {path}"]
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return [f"No puc llegir {path}: {e}"]
    return [redact_sensitive(line.rstrip()) for line in lines[-max_lines:]]


def useful_log_lines(lines: list[str], limit: int = 40) -> list[str]:
    patterns = (
        "ERROR", "WARNING", "MCP", "order_apply", "manual_order_prompt",
        "add_order", "view_order", "Auto enviament", "Error MCP",
        "order_type", "Usuari connectat", "sendMessage",
    )
    useful = [line for line in lines if any(pattern in line for pattern in patterns)]
    if not useful:
        useful = [line for line in lines if "getUpdates" not in line]
    return useful[-limit:]


def pm2_status_text() -> str:
    return run_readonly_command(["pm2.cmd", "status"], timeout=10)


def production_logs_text(limit: int = 40) -> str:
    lines = useful_log_lines(tail_file(PRODUCTION_BOT_LOG, 300), limit=limit)
    return "\n".join(lines) if lines else "No hi ha línies útils recents."


def test_logs_text(limit: int = 40) -> str:
    lines = useful_log_lines(tail_file(TEST_BOT_LOG, 300), limit=limit)
    return "\n".join(lines) if lines else "No hi ha línies útils recents."


def last_error_text(limit: int = 30) -> str:
    lines = tail_file(PRODUCTION_BOT_LOG, 500) + tail_file(TEST_BOT_LOG, 500) + tail_file(BOT_LOG_PATH, 300)
    errors = [
        line for line in lines
        if any(pattern in line for pattern in ("ERROR", "WARNING", "Error MCP", "MCP error", "order_type must"))
    ]
    return "\n".join(errors[-limit:]) if errors else "No he trobat errors recents als logs disponibles."


def diagnose_incident(text: str, logs: str) -> str:
    combined = f"{text}\n{logs}".lower()
    hints = []
    if "order_type=3" in combined or "order_type': 3" in combined or "unknown(3)" in combined or "order_type must" in combined:
        hints.append("Possible causa: la línia és order_type=3. El MCP actual pot llegir-la però add_order només permet modificar tipus 1 o 2.")
    if "mcp error" in combined or "error mcp" in combined:
        hints.append("Hi ha errors MCP recents; revisa l'última crida i arguments.")
    if "can maimo" in combined or "maimo" in combined:
        hints.append("Context detectat: CAN MAIMO. Revisa si el producte existeix com a línia servida/order_type=3.")
    if not hints:
        hints.append("No puc identificar una causa clara automàtica. Revisa els logs adjunts i l'hora exacta de la incidència.")
    return "\n".join(f"- {hint}" for hint in hints)
