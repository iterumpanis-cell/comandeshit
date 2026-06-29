import json
from pathlib import Path


class AuthStore:
    def __init__(self, path: Path, *, admin_user_id: int | None, allowed_user_ids: list[int] | tuple[int, ...] | set[int]):
        self.path = path
        self.admin_user_id = admin_user_id
        self.allowed_user_ids = {int(uid) for uid in allowed_user_ids}

    def default_data(self) -> dict:
        authorized = sorted({*self.allowed_user_ids, *([self.admin_user_id] if self.admin_user_id else [])})
        users = {}
        for uid in authorized:
            users[str(uid)] = {"role": "admin" if uid == self.admin_user_id else "client"}
        return {
            "admin_user_id": self.admin_user_id,
            "authorized_users": authorized,
            "users": users,
            "pending_requests": {},
        }

    def load(self) -> dict:
        if not self.path.exists():
            data = self.default_data()
            self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return data

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = self.default_data()

        data.setdefault("admin_user_id", self.admin_user_id)
        data.setdefault("authorized_users", [])
        data.setdefault("users", {})
        data.setdefault("pending_requests", {})

        if self.admin_user_id and self.admin_user_id not in data["authorized_users"]:
            data["authorized_users"].append(self.admin_user_id)
        for uid in self.allowed_user_ids:
            if uid not in data["authorized_users"]:
                data["authorized_users"].append(uid)

        data["authorized_users"] = sorted({int(uid) for uid in data["authorized_users"]})
        users = data["users"]
        for uid in data["authorized_users"]:
            key = str(uid)
            users.setdefault(key, {})
            users[key].setdefault("role", "admin" if uid == self.admin_user_id else "client")
        if self.admin_user_id:
            users.setdefault(str(self.admin_user_id), {})
            users[str(self.admin_user_id)]["role"] = "admin"
        return data

    def save(self, data: dict) -> None:
        users = data.get("users", {})
        data["authorized_users"] = sorted({int(uid) for uid in users.keys()})
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get_admin_user_id(self) -> int | None:
        admin_id = self.load().get("admin_user_id")
        return int(admin_id) if admin_id else None

    def is_authorized_user(self, user_id: int) -> bool:
        return str(int(user_id)) in self.load().get("users", {})

    def get_user_profile(self, user_id: int) -> dict | None:
        return self.load().get("users", {}).get(str(int(user_id)))

    def is_admin_user(self, user_id: int) -> bool:
        profile = self.get_user_profile(user_id)
        return bool(profile and profile.get("role") == "admin")

    def get_client_scope(self, user_id: int) -> tuple[int | None, str | None]:
        profile = self.get_user_profile(user_id) or {}
        client_code = profile.get("client_code")
        client_name = profile.get("client_name")
        return (int(client_code) if client_code is not None else None, client_name)


def normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("34") and len(digits) > 9:
        digits = digits[2:]
    return digits
