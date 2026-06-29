import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
IA_INCIDENTS_DIR = BASE_DIR / "ia_incidents"
IA_TASKS_DIR = BASE_DIR / "ia_tasks"
IA_TASKS_PENDING_DIR = IA_TASKS_DIR / "pending"
IA_TASKS_RUNNING_DIR = IA_TASKS_DIR / "running"
IA_TASKS_DONE_DIR = IA_TASKS_DIR / "done"
IA_TASKS_FAILED_DIR = IA_TASKS_DIR / "failed"


def save_incident(user_id: int, text: str, pm2_status: str, logs: str, diagnosis: str) -> str:
    IA_INCIDENTS_DIR.mkdir(exist_ok=True)
    incident_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "id": incident_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "text": text,
        "pm2_status": pm2_status,
        "logs": logs,
        "diagnosis": diagnosis,
    }
    path = IA_INCIDENTS_DIR / f"{incident_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return incident_id


def list_incidents(limit: int = 10) -> list[str]:
    if not IA_INCIDENTS_DIR.exists():
        return []
    rows = []
    for path in sorted(IA_INCIDENTS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows.append(f"- {data.get('id')} {data.get('created_at')} - {str(data.get('text', ''))[:80]}")
        except Exception:
            rows.append(f"- {path.stem}")
    return rows


def ensure_ia_task_dirs() -> None:
    for directory in (IA_TASKS_PENDING_DIR, IA_TASKS_RUNNING_DIR, IA_TASKS_DONE_DIR, IA_TASKS_FAILED_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def create_ia_task(user_id: int, command: str, incident_id: str) -> str:
    ensure_ia_task_dirs()
    task_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    payload = {
        "id": task_id,
        "incident_id": incident_id,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "user_id": user_id,
        "command": command,
        "status": "pending",
        "scope": "diagnostic_and_testbot_fix",
        "allowed_actions": [
            "read_logs",
            "inspect_code",
            "modify_test_bot",
            "restart_hitsystems_bot_proves",
        ],
        "forbidden_actions": [
            "modify_production",
            "restart_production",
            "touch_env_or_secrets",
            "git_commit",
            "git_push",
            "run_auto_envia",
        ],
    }
    path = IA_TASKS_PENDING_DIR / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return task_id


def find_ia_task(task_id: str) -> tuple[Path | None, dict | None]:
    for directory in (IA_TASKS_PENDING_DIR, IA_TASKS_RUNNING_DIR, IA_TASKS_DONE_DIR, IA_TASKS_FAILED_DIR):
        path = directory / f"{task_id}.json"
        if path.exists():
            try:
                return path, json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return path, None
    return None, None


def task_status_name(path: Path | None) -> str:
    parent = path.parent.name if path else "unknown"
    return {
        "pending": "pendent",
        "running": "en curs",
        "done": "feta",
        "failed": "fallida",
    }.get(parent, parent)


def list_ia_tasks(limit: int = 12) -> list[str]:
    ensure_ia_task_dirs()
    rows = []
    paths = []
    for directory in (IA_TASKS_RUNNING_DIR, IA_TASKS_PENDING_DIR, IA_TASKS_DONE_DIR, IA_TASKS_FAILED_DIR):
        paths.extend(directory.glob("*.json"))
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            command = str(data.get("command", ""))[:80]
            rows.append(f"- {data.get('id')} [{task_status_name(path)}] {command}")
        except Exception:
            rows.append(f"- {path.stem} [{task_status_name(path)}]")
    return rows


def cancel_pending_task(task_id: str) -> str:
    path, data = find_ia_task(task_id)
    if not path:
        return f"No trobo cap tasca amb id {task_id}."
    if path.parent != IA_TASKS_PENDING_DIR:
        return f"La tasca {task_id} no es pot cancel·lar perquè està {task_status_name(path)}."
    IA_TASKS_FAILED_DIR.mkdir(parents=True, exist_ok=True)
    data = data or {"id": task_id}
    data["status"] = "cancelled"
    data["cancelled_at"] = datetime.now().isoformat(timespec="seconds")
    failed_path = IA_TASKS_FAILED_DIR / path.name
    failed_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    path.unlink()
    return f"Tasca {task_id} cancel·lada."
