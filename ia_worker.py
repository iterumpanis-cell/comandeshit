"""Worker local per executar tasques IA demanades des del bot de proves.

Restriccions intencionades:
- Treballa nomes dins aquest projecte de proves.
- No toca produccio, .env, autoritzacions, commits ni push.
- Pot reiniciar nomes hitsystems-bot-proves si OpenCode ho considera necessari.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
TASKS_DIR = BASE_DIR / "ia_tasks"
PENDING_DIR = TASKS_DIR / "pending"
RUNNING_DIR = TASKS_DIR / "running"
DONE_DIR = TASKS_DIR / "done"
FAILED_DIR = TASKS_DIR / "failed"
LOG_PATH = BASE_DIR / "ia_worker.log"

PRODUCTION_DIR = Path(r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
TEST_DIR = BASE_DIR

POLL_SECONDS = 5
TASK_TIMEOUT_SECONDS = 8 * 60


def _opencode_executable() -> str:
    direct = Path.home() / "AppData" / "Roaming" / "npm" / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    if direct.exists():
        return str(direct)
    return shutil.which("opencode.cmd") or "opencode.cmd"


def _setup_logging() -> None:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        encoding="utf-8",
    )


def _ensure_dirs() -> None:
    for directory in (PENDING_DIR, RUNNING_DIR, DONE_DIR, FAILED_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def _redact(text: str) -> str:
    import re

    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"bot\d+:[A-Za-z0-9_\-]+", "bot<TELEGRAM_TOKEN>", text)
    text = re.sub(r"(TELEGRAM_TOKEN=)[^\s]+", r"\1<SECRET>", text)
    text = re.sub(r"(MCP_URL=)[^\s]+", r"\1<SECRET>", text)
    return text


def _telegram_send(text: str) -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id = os.getenv("ADMIN_USER_ID", "").strip()
    if not token or not chat_id:
        return
    for start in range(0, len(text), 3500):
        chunk = text[start:start + 3500]
        data = urlencode({"chat_id": chat_id, "text": chunk}).encode("utf-8")
        try:
            with urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15) as resp:
                resp.read()
        except Exception as e:
            logging.warning("No he pogut enviar Telegram: %s", e)


def _build_prompt(task: dict) -> str:
    command = task.get("command") or task.get("text") or ""
    incident_id = task.get("incident_id") or task.get("id")
    return f"""
Ets OpenCode executat automaticament per una ordre remota Telegram de l'administrador.

Tasca: {command}
ID tasca/incidencia: {incident_id}

RESTRICCIONS OBLIGATORIES:
- Treballa nomes dins: {TEST_DIR}
- No modifiquis res dins produccio: {PRODUCTION_DIR}
- No toquis .env, authorized_users.json, bot_state.pkl, auto_envia_state ni cap secret.
- No facis git commit, git push, git reset, git checkout ni operacions destructives.
- No executis auto_envia.
- Si cal reiniciar, reinicia nomes PM2 hitsystems-bot-proves.
- Pots llegir logs de produccio i proves per diagnosticar.
- Pots modificar codi del bot de proves si cal per corregir el problema.
- Si una correccio requereix tocar produccio, deixa-ho escrit com a recomanacio, pero no ho facis.

OBJECTIU:
1. Revisa la incidencia i els logs rellevants.
2. Si cal, aplica una correccio segura nomes al bot de proves.
3. Verifica amb proves raonables.
4. Retorna un resum clar en catala: que has vist, que has canviat, que queda pendent.

IMPORTANT:
No demanis permis interactiu; si no pots fer alguna accio amb seguretat, explica-ho i atura't.
""".strip()


def _run_opencode(task: dict) -> tuple[int, str]:
    prompt = _build_prompt(task)
    cmd = [
        _opencode_executable(),
        "run",
        "--dir",
        str(TEST_DIR),
        "--title",
        f"IA Telegram {task.get('id', '')}",
        "--dangerously-skip-permissions",
        prompt,
    ]
    logging.info("Executant OpenCode per tasca %s", task.get("id"))
    env = os.environ.copy()
    env.update({"CI": "1", "NO_COLOR": "1", "TERM": "dumb"})
    completed = subprocess.run(
        cmd,
        cwd=TEST_DIR,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=TASK_TIMEOUT_SECONDS,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    logging.info("OpenCode tasca %s acabat amb codi %s", task.get("id"), completed.returncode)
    return completed.returncode, _redact(output.strip())


def _load_task(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_task(path: Path, task: dict) -> None:
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")


def _process_task(path: Path) -> None:
    running_path = RUNNING_DIR / path.name
    try:
        shutil.move(str(path), str(running_path))
    except FileNotFoundError:
        return

    task = _load_task(running_path)
    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat(timespec="seconds")
    _write_task(running_path, task)

    _telegram_send(f"🧠 Tasca IA {task.get('id')} iniciada.\n\n{task.get('command', '')[:500]}")

    try:
        code, output = _run_opencode(task)
        task["finished_at"] = datetime.now().isoformat(timespec="seconds")
        task["returncode"] = code
        task["output"] = output
        if code == 0:
            task["status"] = "done"
            final_path = DONE_DIR / running_path.name
            _write_task(running_path, task)
            shutil.move(str(running_path), str(final_path))
            _telegram_send(f"✅ Tasca IA {task.get('id')} finalitzada.\n\n{output[-2500:]}")
        else:
            task["status"] = "failed"
            final_path = FAILED_DIR / running_path.name
            _write_task(running_path, task)
            shutil.move(str(running_path), str(final_path))
            _telegram_send(f"❌ Tasca IA {task.get('id')} ha fallat.\n\n{output[-2500:]}")
    except Exception as e:
        task["status"] = "failed"
        task["finished_at"] = datetime.now().isoformat(timespec="seconds")
        task["error"] = _redact(str(e))
        final_path = FAILED_DIR / running_path.name
        _write_task(running_path, task)
        shutil.move(str(running_path), str(final_path))
        _telegram_send(f"❌ Tasca IA {task.get('id')} ha fallat: {e}")
        logging.exception("Error processant tasca %s", task.get("id"))


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    _setup_logging()
    _ensure_dirs()
    logging.info("IA worker iniciat")
    while True:
        pending = sorted(PENDING_DIR.glob("*.json"))
        for path in pending[:1]:
            _process_task(path)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
