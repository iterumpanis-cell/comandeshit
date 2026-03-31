import os

from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_list(name: str) -> list[int]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _get_optional_int(name: str) -> int | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    return int(value)


# ============================================================
#  CONFIGURACIO DEL BOT - Variables carregades des de .env
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv("GEMINI_FALLBACK_MODELS", "gemini-2.5-flash-lite").split(",")
    if model.strip()
]

ALLOWED_USER_IDS = _get_int_list("ALLOWED_USER_IDS")
ADMIN_USER_ID = _get_optional_int("ADMIN_USER_ID")

HITSYSTEMS_URL = os.getenv(
    "HITSYSTEMS_URL",
    "https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum",
)
HITSYSTEMS_USER = os.getenv("HITSYSTEMS_USER", "")
HITSYSTEMS_PASS = os.getenv("HITSYSTEMS_PASS", "")

HEADLESS = _get_bool("HEADLESS", True)

shop_code = os.getenv("SHOP_CODE", "").strip()
if shop_code:
    SHOP_CODE = int(shop_code)
