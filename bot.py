"""
bot.py — Bot de Telegram per a la gestió de comandes HitSystems
Tot via MCP: cerca clients, cerca articles, afegir (normal i encarrec), veure comanda.
"""
import asyncio
import argparse
import atexit
import json
import logging
import msvcrt
import os
import re
import tempfile
import unicodedata
import zipfile
from difflib import SequenceMatcher
from html import escape
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    PicklePersistence,
    filters,
)

import config
from gemini_hit import GeminiHitAssistant
from mcp_vendes import MCPVendes
from printer import _format_totals_escpos, imprimir_text_directe
from services.auth import AuthStore, normalize_phone
from services.auto_send import AutoSender
from telegram_handlers.ai_chat import register_ai_chat_handlers
from telegram_handlers.callbacks import register_callback_handlers
from telegram_handlers.ia_admin import register_ia_admin_handlers
from telegram_handlers.orders_add import build_afegir_handler
from telegram_handlers.orders_delete import build_esborrar_handler
from telegram_handlers.orders_view import build_veure_handler
from telegram_handlers.printing import build_imprimir_handler
from telegram_handlers.sales import build_vendes_handler

# ------------------------------------------------------------------ #
#  Logging                                                             #
# ------------------------------------------------------------------ #
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
PERSISTENCE_PATH = Path(__file__).with_name("bot_state.pkl")
AUTHORIZED_USERS_PATH = Path(__file__).with_name("authorized_users.json")
CLIENT_COPIES_PATH = Path(__file__).with_name("client_copies.json")
AUTO_ENVIA_STATE_DIR = Path(__file__).with_name("auto_envia_state")
BOT_LOG_PATH = Path(__file__).with_name("bot_log.txt")
BOT_LOCK_PATH = Path(__file__).with_name("bot.lock")
_BOT_LOCK_FILE = None


def _setup_file_logging() -> None:
    root = logging.getLogger()
    log_path = str(BOT_LOG_PATH.resolve())
    for handler in root.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None) == log_path:
            return
    handler = logging.FileHandler(BOT_LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root.addHandler(handler)


def _release_single_instance_lock() -> None:
    global _BOT_LOCK_FILE
    if not _BOT_LOCK_FILE:
        return
    try:
        _BOT_LOCK_FILE.seek(0)
        msvcrt.locking(_BOT_LOCK_FILE.fileno(), msvcrt.LK_UNLCK, 1)
    except Exception:
        pass
    try:
        _BOT_LOCK_FILE.close()
    except Exception:
        pass
    _BOT_LOCK_FILE = None


def _acquire_single_instance_lock() -> bool:
    global _BOT_LOCK_FILE
    BOT_LOCK_PATH.touch(exist_ok=True)
    lock_file = BOT_LOCK_PATH.open("r+", encoding="utf-8")
    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            lock_file.close()
        except Exception:
            pass
        return False
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _BOT_LOCK_FILE = lock_file
    atexit.register(_release_single_instance_lock)
    return True


_setup_file_logging()


def _get_copies(client_code: int) -> int:
    """Retorna el nombre de còpies configurat per a un client. Default: 2."""
    try:
        data = json.loads(CLIENT_COPIES_PATH.read_text(encoding="utf-8"))
        return int(data.get(str(client_code), data.get("_default", 2)))
    except Exception:
        return 2

# Instàncies globals
mcp = MCPVendes()

ai = GeminiHitAssistant(
    config.GEMINI_API_KEY,
    config.GEMINI_MODEL,
    mcp,
    config.GEMINI_FALLBACK_MODELS,
)

auth_store = AuthStore(
    AUTHORIZED_USERS_PATH,
    admin_user_id=config.ADMIN_USER_ID,
    allowed_user_ids=config.ALLOWED_USER_IDS,
)

auto_sender = AutoSender(
    state_dir=AUTO_ENVIA_STATE_DIR,
    telegram_token=config.TELEGRAM_TOKEN,
    logger=logger,
    mcp=mcp,
    get_copies=_get_copies,
    load_auth_data=lambda: _load_auth_data(),
    get_admin_user_id=lambda: _get_admin_user_id(),
    format_totals_escpos=_format_totals_escpos,
    imprimir_text_directe=imprimir_text_directe,
)


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #
def _to_mcp_date(data_ddmmyyyy: str) -> str:
    """Converteix DD/MM/YYYY a YYYY-MM-DD per al MCP."""
    d, m, y = data_ddmmyyyy.split("/")
    return f"{y}-{m}-{d}"


DIES_CA = ["Dil", "Dim", "Dmc", "Dij", "Div", "Dis", "Diu"]


def _keyboard_dates() -> ReplyKeyboardMarkup:
    """Botonera amb demà fins a 7 dies + opció manual."""
    avui = date.today()
    buttons = []
    row = []
    for i in range(1, 8):
        d = avui + timedelta(days=i)
        label = f"{DIES_CA[d.weekday()]} {d.strftime('%d/%m')}"
        row.append(label)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(["✏️ Altra data (dd/mm/aaaa)"])
    return ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True)


def _keyboard_sales_dates() -> ReplyKeyboardMarkup:
    """Botonera per vendes: avui i dies enrere + opcio manual."""
    avui = date.today()
    buttons = []
    row = []
    for i in range(0, 8):
        d = avui - timedelta(days=i)
        prefix = "Avui" if i == 0 else DIES_CA[d.weekday()]
        row.append(f"{prefix} {d.strftime('%d/%m/%Y')}")
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append(["✏️ Data lliure (dd/mm/aaaa)"])
    return ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True)


ORDER_FIELD_LABELS = {
    "requested": "Demanat",
    "served": "Servit",
    "returned": "Tornat",
}

ORDER_FIELD_KWARGS = {
    "requested": "requested_quantity",
    "served": "served_quantity",
    "returned": "returned_quantity",
}


def _format_order_fields(fields) -> str:
    return " + ".join(ORDER_FIELD_LABELS[f] for f in ORDER_FIELD_LABELS if f in fields) or "cap"


def _order_field_buttons(selected, prefix: str) -> list[list[InlineKeyboardButton]]:
    return [[
        InlineKeyboardButton(
            f"{'✅' if field in selected else '☐'} {label}",
            callback_data=f"{prefix}_field:{field}",
        )
        for field, label in ORDER_FIELD_LABELS.items()
    ]]


def _manual_order_text(pending: dict) -> str:
    action = "posar a 0" if pending.get("quantity") == 0 else f"posar a {pending.get('quantity')}"
    return (
        f"*Quins camps vols canviar?*\n\n"
        f"👤 Client: *{pending.get('client_name')}*\n"
        f"📅 Data: *{pending.get('date_display')}*\n"
        f"🥖 Producte: *{pending.get('article_name')}*\n"
        f"🔢 Acció: *{action}*\n"
        f"📌 Camps: *{_format_order_fields(pending.get('fields', set()))}*"
    )


def _manual_order_keyboard(pending: dict) -> InlineKeyboardMarkup:
    rows = _order_field_buttons(pending.get("fields", set()), "order")
    if pending.get("mode") == "delete":
        rows.append([InlineKeyboardButton("🗑️ Esborrar aquests camps", callback_data="order_apply:auto")])
    else:
        rows.append([
            InlineKeyboardButton("✅ Afegir", callback_data="order_apply:1"),
            InlineKeyboardButton("📦 Encarreg", callback_data="order_apply:2"),
        ])
    rows.append([InlineKeyboardButton("❌ Cancel·lar", callback_data="order_cancel")])
    return InlineKeyboardMarkup(rows)


def _order_type_label(line: dict) -> str:
    order_type = int(line.get("order_type", 1) or 1)
    name = line.get("order_type_name") or ""
    qty = f"D{line.get('requested', 0)}/S{line.get('served', 0)}/T{line.get('returned', 0)}"
    if order_type == 1:
        label = "Normal"
    elif order_type == 2:
        label = "Encarreg"
    else:
        label = name if name and not name.startswith("unknown") else f"Tipus {order_type}"
    return f"{label} ({qty})"


def _order_type_choice_keyboard(pending: dict, linies: list[dict]) -> InlineKeyboardMarkup:
    rows = _order_field_buttons(pending.get("fields", set()), "order")
    for line in linies:
        order_type = int(line.get("order_type", 1) or 1)
        rows.append([InlineKeyboardButton(
            f"🗑️ {_order_type_label(line)}",
            callback_data=f"order_apply:{order_type}",
        )])
    rows.append([InlineKeyboardButton("❌ Cancel·lar", callback_data="order_cancel")])
    return InlineKeyboardMarkup(rows)


def _confirmation_text(lines: list, fields) -> str:
    text_lines = ["📋 *Confirmes la comanda?*\n"]
    for line in lines:
        date_fmt = line.get("date", "?")
        client_name = line.get("client_name", f"codi {line.get('client', '?')}")
        article_name = line.get("article_name", f"codi {line.get('article_code', '?')}")
        qty = line.get("quantity", 0)
        order_type = line.get("order_type", 1)
        prefix = "🎗️ " if order_type == 2 else "🥖 "
        text_lines.append(f"{prefix}*{article_name}* × {qty}")
        text_lines.append(f"   👤 {client_name}  📅 {date_fmt}")
    text_lines.append(f"\n📌 Camps: *{_format_order_fields(fields)}*")
    return "\n".join(text_lines)


def _confirmation_keyboard(fields, choose_order_type: bool = False) -> InlineKeyboardMarkup:
    rows = _order_field_buttons(fields, "confirm")
    if choose_order_type:
        rows.append([
            InlineKeyboardButton("✅ Afegir", callback_data="confirm_yes:1"),
            InlineKeyboardButton("📦 Encarreg", callback_data="confirm_yes:2"),
        ])
        rows.append([InlineKeyboardButton("❌ Cancel·lar", callback_data="confirm_no")])
    else:
        rows.append([
            InlineKeyboardButton("✅ Confirmar", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel·lar", callback_data="confirm_no"),
        ])
    return InlineKeyboardMarkup(rows)


def _initial_confirmation_fields(lines: list) -> set:
    explicit = set()
    for line in lines:
        for field in line.get("fields", []) or []:
            if field in ORDER_FIELD_LABELS:
                explicit.add(field)
    return explicit or {"requested", "served"}


def _parse_data(text: str) -> str | None:
    """Parseja text de data i retorna DD/MM/YYYY o None si error.
    Accepta: 'avui', 'DD/MM/YYYY', botó 'Dij 03/04' (afegeix any automàticament).
    """
    t = text.strip().lower()
    if t in ("avui", "hoy", "today", ""):
        return date.today().strftime("%d/%m/%Y")
    if t in ("demà", "dema", "mañana", "tomorrow"):
        return (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")

    # Format complet DD/MM/YYYY o DD/MM/YY
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(t, fmt)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            pass

    m_full = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})\b', t)
    if m_full:
        d_str = m_full.group(1).zfill(2)
        mo_str = m_full.group(2).zfill(2)
        y_str = m_full.group(3)
        fmt = "%d/%m/%y" if len(y_str) == 2 else "%d/%m/%Y"
        try:
            parsed = datetime.strptime(f"{d_str}/{mo_str}/{y_str}", fmt)
            return parsed.strftime("%d/%m/%Y")
        except ValueError:
            return None

    # Format botó "Dij 03/04" → extreure DD/MM i afegir any
    m = re.search(r'\b(\d{1,2})/(\d{1,2})\b', t)
    if m:
        d_str = m.group(1).zfill(2)
        mo_str = m.group(2).zfill(2)
        year = date.today().year
        try:
            d_test = datetime.strptime(f"{d_str}/{mo_str}/{year}", "%d/%m/%Y")
            if d_test.date() < date.today():
                year += 1
        except ValueError:
            return None
        return f"{d_str}/{mo_str}/{year}"

    return None


def _next_date_for_day(day: int) -> date | None:
    """Retorna la propera data amb aquest dia de mes."""
    if day < 1 or day > 31:
        return None

    today = date.today()
    year = today.year
    month = today.month
    for _ in range(14):
        try:
            candidate = date(year, month, day)
            if candidate >= today:
                return candidate
        except ValueError:
            pass
        month += 1
        if month > 12:
            month = 1
            year += 1
    return None


def _parse_all_orders_date(text: str) -> str | None:
    """Extreu una data d'una peticio tipus 'totes les comandes del dia 01'."""
    parsed = _parse_data(text)
    if parsed:
        return parsed

    t = text.strip().lower()
    if re.search(r"\b(avui|hoy|today)\b", t):
        return date.today().strftime("%d/%m/%Y")
    if re.search(r"\b(demà|dema|mañana|tomorrow)\b", t):
        return (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")

    match = re.search(r"\b(?:dia|del|pel)\s+(\d{1,2})\b", t)
    if not match:
        match = re.search(r"\b(\d{1,2})\b", t)
    if not match:
        return None

    candidate = _next_date_for_day(int(match.group(1)))
    return candidate.strftime("%d/%m/%Y") if candidate else None


def _normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _article_match_score(query: str, article_name: str) -> float:
    query_norm = _normalize_search_text(query)
    name_norm = _normalize_search_text(article_name)
    if not query_norm or not name_norm:
        return 0.0
    if query_norm == name_norm:
        return 1.0
    if query_norm in name_norm or name_norm in query_norm:
        return 0.9

    query_tokens = set(re.findall(r"[a-z0-9]+", query_norm))
    name_tokens = set(re.findall(r"[a-z0-9]+", name_norm))
    token_score = len(query_tokens & name_tokens) / len(query_tokens) if query_tokens else 0.0
    ratio = SequenceMatcher(None, query_norm, name_norm).ratio()
    return max(ratio, token_score)


async def _fallback_article_options(text: str, limit: int = 8) -> list[tuple[str, int]]:
    articles = await mcp.llistar_tots_articles()
    ranked = []
    for item in articles:
        name = item.get("n") or item.get("name")
        code = item.get("c") or item.get("code") or item.get("id")
        if not name or code is None:
            continue
        score = _article_match_score(text, str(name))
        if score >= 0.45:
            ranked.append((score, str(name), int(code)))

    ranked.sort(key=lambda item: (-item[0], item[1].lower()))
    return [(name, code) for _, name, code in ranked[:limit]]


def _is_print_request(text: str) -> bool:
    t = text.strip().lower()
    return any(word in t for word in ("imprim", "imprimeix", "imprimir", "impressio", "impressió"))


def _is_all_orders_request(text: str) -> bool:
    t = text.strip().lower()
    wants_all = any(word in t for word in ("totes", "tots", "tota", "tot el"))
    wants_orders = any(word in t for word in ("comand", "albar"))
    wants_plural_orders = any(word in t for word in ("comandes", "albarans"))
    wants_all_clients = wants_all and "client" in t and _parse_all_orders_date(text)
    return bool(
        (wants_orders and (wants_all or (_is_print_request(text) and wants_plural_orders)))
        or wants_all_clients
    )


def _format_all_orders_blocks(data: str, result: dict) -> list[str]:
    """Construeix blocs HTML de totes les comandes, agrupats per client."""
    clients = result.get("clients", [])
    totals = result.get("totals_per_article", {})

    if not clients:
        return [f"No hi ha comandes per al {escape(data)}."]

    blocks = [
        (
            f"<b>Comandes del {escape(data)}</b>\n"
            f"<b>Clients:</b> {len(clients)}"
        )
    ]

    for client in clients:
        nom = client.get("nom") or client.get("name") or str(client.get("codi", "Client"))
        linies = [line for line in client.get("linies", []) if line.get("requested", 0)]
        if not linies:
            continue

        body = [f"\n<b>{escape(str(nom))}</b>"]
        for line in linies:
            qty = line.get("requested", 0)
            art = line.get("nm") or line.get("artName") or line.get("name") or str(line.get("art", "?"))
            prefix = "🎗️ " if line.get("order_type", 1) == 2 else ""
            body.append(f"<code>{escape(prefix + str(art))} x{escape(str(qty))}</code>")
        blocks.append("\n".join(body))

    if totals:
        total_lines = ["\n<b>Totals per article</b>"]
        for name, qty in sorted(totals.items(), key=lambda item: str(item[0]).lower()):
            total_lines.append(f"<code>{escape(str(name))} x{escape(str(qty))}</code>")
        blocks.append("\n".join(total_lines))

    return blocks


async def _send_html_blocks(message, blocks: list[str], max_size: int = 3400):
    chunk = ""
    for block in blocks:
        candidate = f"{chunk}\n{block}" if chunk else block
        if len(candidate) > max_size and chunk:
            await message.reply_text(chunk, parse_mode="HTML")
            chunk = block
        else:
            chunk = candidate
    if chunk:
        await message.reply_text(chunk, parse_mode="HTML")


def _is_product_summary_excel_request(text: str) -> bool:
    normalized = _normalize_search_text(text)
    wants_excel = any(word in normalized for word in ("excel", "xlsx", "full de calcul", "hoja de calcul"))
    wants_summary = any(word in normalized for word in ("resum", "resumen", "total", "totals"))
    wants_products = any(word in normalized for word in ("product", "article", "articulo", "articles", "productos"))
    wants_clients = "client" in normalized
    return wants_excel and wants_summary and wants_products and wants_clients


def _xlsx_cell_ref(col: int, row: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


def _xlsx_cell(value, row: int, col: int) -> str:
    ref = _xlsx_cell_ref(col, row)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = escape("" if value is None else str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def _write_product_summary_xlsx(path: Path, rows: list[list[object]]) -> None:
    sheet_rows = []
    for r_idx, row in enumerate(rows, start=1):
        cells = "".join(_xlsx_cell(value, r_idx, c_idx) for c_idx, value in enumerate(row, start=1))
        sheet_rows.append(f'<row r="{r_idx}">{cells}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<cols><col min="1" max="1" width="14" customWidth="1"/><col min="2" max="2" width="45" customWidth="1"/>'
        '<col min="3" max="99" width="14" customWidth="1"/></cols>'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Resum productes" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


async def _send_product_summary_excel(update: Update, data: str) -> None:
    data_mcp = _to_mcp_date(data)
    estat_msg = await update.message.reply_text(f"⏳ Preparant Excel del resum de productes del {data}...")
    try:
        result = await mcp.comandes_per_data(data_mcp, incloure_botigues=True)
        clients: list[tuple[str, str]] = []
        productes: dict[tuple[str, str], dict[str, object]] = {}
        for client in result.get("clients", []):
            client_code = str(client.get("codi") or client.get("code") or client.get("id") or "")
            client_name = str(client.get("nom") or client.get("name") or client_code or "Client")
            client_key = client_code or client_name
            clients.append((client_key, client_name))
            for line in client.get("linies", []):
                qty = int(line.get("requested", 0) or 0)
                if qty <= 0:
                    continue
                code = str(line.get("art") or line.get("code") or "")
                name = str(line.get("nm") or line.get("artName") or line.get("name") or code or "Producte")
                item = productes.setdefault((code, name), {"total": 0, "clients": {}})
                item["total"] = int(item["total"]) + qty
                per_client = item["clients"]
                per_client[client_key] = int(per_client.get(client_key, 0)) + qty

        rows: list[list[object]] = [
            ["Resum per productes", data, "", ""],
            ["Clients amb comanda", len(clients), "Productes", len(productes)],
            [],
            ["Codi producte", "Producte", *[name for _, name in clients], "Total"],
        ]
        for (code, name), item in sorted(productes.items(), key=lambda kv: (-int(kv[1]["total"]), kv[0][1].lower())):
            per_client = item["clients"]
            rows.append([
                code,
                name,
                *[int(per_client.get(client_key, 0)) or "" for client_key, _ in clients],
                int(item["total"]),
            ])

        filename = f"resum_productes_{data_mcp}.xlsx"
        path = Path(tempfile.gettempdir()) / filename
        _write_product_summary_xlsx(path, rows)
        await _tancar_estat(estat_msg)
        with path.open("rb") as fh:
            await update.message.reply_document(
                document=fh,
                filename=filename,
                caption=f"Resum per productes de tots els clients amb comanda del {data}",
            )
    except Exception as exc:
        logger.exception("Error generant Excel de resum de productes")
        try:
            await estat_msg.edit_text("❌")
        except Exception:
            pass
        await update.message.reply_text(f"❌ Error generant l'Excel: {exc}")


def _parse_sales_date(text: str) -> tuple[str, str] | None:
    iso_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if iso_match:
        data_mcp = iso_match.group(0)
        y, m, d = iso_match.groups()
        return data_mcp, f"{d}/{m}/{y}"

    date_match = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text)
    if date_match:
        d, m, y = date_match.groups()
        d = d.zfill(2)
        m = m.zfill(2)
        return f"{y}-{m}-{d}", f"{d}/{m}/{y}"

    parsed = _parse_data(text)
    if parsed:
        return _to_mcp_date(parsed), parsed
    return None


def _parse_sales_shop(text: str) -> tuple[int, str] | None:
    normalized = _normalize_search_text(text)
    for name, code in getattr(config, "SHOPS", {}).items():
        if _normalize_search_text(name) in normalized:
            return int(code), name.capitalize()

    match = re.search(r"\b(?:botiga|shop|tienda)\s+(\d+)\b", normalized)
    if not match:
        match = re.search(r"\b(?:codi|codigo|code)\s+(\d+)\b", normalized)
    if match:
        code = int(match.group(1))
        return code, f"botiga {code}"

    default_code = getattr(config, "SHOP_CODE", None)
    if default_code:
        return int(default_code), f"botiga {default_code}"
    return None


def _is_hourly_sales_request(text: str) -> bool:
    normalized = _normalize_search_text(text)
    wants_sales = any(word in normalized for word in ("vendes", "ventes", "ventas", "sales"))
    wants_hours = any(word in normalized for word in ("hora", "hores", "horari", "franja", "franges"))
    return wants_sales and wants_hours


def _format_hourly_sales_report(result: dict, shop_name: str, data_display: str) -> str:
    hourly: dict[int, dict[str, float | int]] = {}
    for item in result.get("v", []):
        hour = item.get("h")
        if hour is None:
            continue
        hour = int(hour)
        bucket = hourly.setdefault(hour, {"amount": 0.0, "quantity": 0, "lines": 0})
        bucket["amount"] = float(bucket["amount"]) + float(item.get("i", 0) or 0)
        bucket["quantity"] = int(bucket["quantity"]) + int(item.get("q", 0) or 0)
        bucket["lines"] = int(bucket["lines"]) + 1

    total_day = float(result.get("tv", 0) or 0)
    tickets = int(result.get("nt", 0) or 0)
    total_hours = round(sum(float(v["amount"]) for v in hourly.values()), 2)

    lines = [
        f"📊 *Vendes per hores — {shop_name}*",
        f"📅 {data_display}",
        f"💰 Total MCP: *{total_day:.2f}€*  |  🧾 Tickets: *{tickets}*",
        "",
    ]

    for hour in sorted(hourly):
        bucket = hourly[hour]
        lines.append(
            f"• {hour:02d}:00 — {float(bucket['amount']):.2f}€ "
            f"({int(bucket['quantity'])} u., {int(bucket['lines'])} línies)"
        )

    lines.extend(["", f"Σ Hores: *{total_hours:.2f}€*"])
    diff = round(total_day - total_hours, 2)
    if abs(diff) >= 0.01:
        lines.append(f"⚠️ Desquadre: {diff:+.2f}€")
    else:
        lines.append("✅ Total per hores quadrat amb el total MCP.")
    return "\n".join(lines)


def _format_sales_summary_report(result: dict, shop_name: str, data_display: str) -> str:
    total = float(result.get("tv", 0) or 0)
    tickets = int(result.get("nt", 0) or 0)
    avg = total / tickets if tickets else 0
    return "\n".join([
        f"📊 *Resum vendes — {shop_name}*",
        f"📅 {data_display}",
        "",
        f"💰 Total: *{total:.2f}€*",
        f"🧾 Tickets: *{tickets}*",
        f"📈 Ticket mig: *{avg:.2f}€*",
    ])


def _format_product_totals_report(result: dict, shop_name: str, data_display: str) -> str:
    totals: dict[str, dict[str, float]] = {}
    for item in result.get("v", []):
        name = str(item.get("n") or item.get("nm") or "?")
        bucket = totals.setdefault(name, {"q": 0.0, "i": 0.0})
        bucket["q"] += float(item.get("q", 0) or 0)
        bucket["i"] += float(item.get("i", 0) or 0)

    lines = [
        f"🥖 *Productes total dia — {shop_name}*",
        f"📅 {data_display}",
        f"💰 Total: *{float(result.get('tv', 0) or 0):.2f}€*",
        "",
    ]
    for name, values in sorted(totals.items(), key=lambda item: (-item[1]["i"], item[0].lower())):
        lines.append(f"• {name}: {values['q']:g} u. ({values['i']:.2f}€)")
    return "\n".join(lines)


def _format_products_by_hour_report(result: dict, shop_name: str, data_display: str) -> str:
    hourly: dict[int, dict[str, dict[str, float]]] = {}
    for item in result.get("v", []):
        hour = item.get("h")
        if hour is None:
            continue
        name = str(item.get("n") or item.get("nm") or "?")
        bucket = hourly.setdefault(int(hour), {}).setdefault(name, {"q": 0.0, "i": 0.0})
        bucket["q"] += float(item.get("q", 0) or 0)
        bucket["i"] += float(item.get("i", 0) or 0)

    lines = [
        f"🕒 *Productes per hora — {shop_name}*",
        f"📅 {data_display}",
        f"💰 Total: *{float(result.get('tv', 0) or 0):.2f}€*",
    ]
    for hour in sorted(hourly):
        hour_total = sum(values["i"] for values in hourly[hour].values())
        lines.append("")
        lines.append(f"*{hour:02d}:00 — {hour_total:.2f}€*")
        for name, values in sorted(hourly[hour].items(), key=lambda item: (-item[1]["i"], item[0].lower())):
            lines.append(f"• {name}: {values['q']:g} u. ({values['i']:.2f}€)")
    return "\n".join(lines)


def _ticket_client_text(ticket: dict) -> str:
    values = []
    for key in ("client", "client_name", "customer", "customer_name", "cli", "cn", "ncli", "name"):
        value = ticket.get(key)
        if value:
            values.append(str(value))
    return " ".join(values)


def _format_hora_feliz_report(detail: dict, shop_name: str, data_display: str) -> str:
    tickets = detail.get("tickets", []) if isinstance(detail, dict) else []
    regular_prices: dict[int, float] = {}
    for ticket in tickets:
        for line in ticket.get("lines", []):
            article_code = line.get("art")
            price = float(line.get("p", 0) or 0)
            if article_code is None or price <= 0:
                continue
            article_code = int(article_code)
            regular_prices[article_code] = max(regular_prices.get(article_code, 0.0), price)

    totals: dict[str, dict[str, float]] = {}
    total_discounted = 0.0
    total_regular = 0.0
    times = []
    ticket_ids = set()

    for ticket in tickets:
        ticket_has_discount = False
        for line in ticket.get("lines", []):
            article_code = line.get("art")
            if article_code is None:
                continue
            article_code = int(article_code)
            regular_price = regular_prices.get(article_code, 0.0)
            price = float(line.get("p", 0) or 0)
            qty = float(line.get("q", 0) or 0)
            amount = float(line.get("i", 0) or 0)
            if regular_price <= 0 or price <= 0 or qty <= 0:
                continue

            ratio = price / regular_price
            if not (0.45 <= ratio <= 0.55) or abs(regular_price - price) < 0.05:
                continue

            name = str(line.get("nm") or line.get("n") or "?")
            if _normalize_search_text(name) == "tallar":
                continue
            regular_amount = regular_price * qty
            bucket = totals.setdefault(name, {"q": 0.0, "i": 0.0, "regular": 0.0, "p": price, "regular_p": regular_price})
            bucket["q"] += qty
            bucket["i"] += amount
            bucket["regular"] += regular_amount
            bucket["p"] = price
            bucket["regular_p"] = regular_price
            total_discounted += amount
            total_regular += regular_amount
            ticket_has_discount = True

        if ticket_has_discount:
            ticket_ids.add(ticket.get("tick"))
            if ticket.get("time"):
                times.append(str(ticket["time"]))

    if not totals:
        return f"🎉 *Hora Feliz — {shop_name}*\n📅 {data_display}\n\nℹ️ No he trobat línies amb preu aproximadament al 50%."

    lines = [
        f"🎉 *Hora Feliz — {shop_name}*",
        f"📅 {data_display}",
        f"💰 Venut 50%: *{total_discounted:.2f}€*  |  🧾 Tickets: *{len(ticket_ids)}*",
        f"🏷️ Preu habitual estimat: *{total_regular:.2f}€*",
        f"📉 Descompte estimat: *{(total_regular - total_discounted):.2f}€*",
    ]
    if times:
        lines.append(f"🕒 Franja: *{min(times)} - {max(times)}*")
    lines.append("")
    for name, values in sorted(totals.items(), key=lambda item: (-item[1]["i"], item[0].lower())):
        discount = values["regular"] - values["i"]
        lines.append(
            f"• {name}: {values['q']:g} u. x {values['p']:.2f}€ "
            f"(habitual {values['regular_p']:.2f}€) = {values['i']:.2f}€ "
            f"[-{discount:.2f}€]"
        )
    return "\n".join(lines)


async def _send_hourly_sales_report(update: Update, text: str) -> bool:
    if not _is_hourly_sales_request(text):
        return False
    if not _is_admin(update):
        await update.message.reply_text("❌ Aquesta informació només està disponible per a usuaris administratius.")
        return True

    parsed_date = _parse_sales_date(text)
    if not parsed_date:
        await update.message.reply_text("⚠️ Indica la data, per exemple: vendes per hores de Granollers del 16/05/2026.")
        return True

    parsed_shop = _parse_sales_shop(text)
    if not parsed_shop:
        await update.message.reply_text("⚠️ Indica la botiga, per exemple: Granollers o el codi de botiga.")
        return True

    data_mcp, data_display = parsed_date
    shop_code, shop_name = parsed_shop
    estat_msg = await update.message.reply_text(f"📊 Carregant vendes per hores de {shop_name} ({data_display})...")
    try:
        result = await mcp.vendes_dia(shop_code, data_mcp)
        if not result.get("v"):
            await estat_msg.edit_text(f"ℹ️ Sense dades de vendes per {shop_name} el {data_display}.")
            return True
        await estat_msg.delete()
        await _send_chunks(update.message, _format_hourly_sales_report(result, shop_name, data_display), parse_mode="Markdown")
    except Exception as exc:
        logger.exception("Error carregant vendes per hores")
        try:
            await estat_msg.edit_text("❌")
        except Exception:
            pass
        await update.message.reply_text(f"❌ Error carregant vendes per hores: {exc}")
    return True


async def _print_all_orders(update: Update, data: str) -> None:
    data_mcp = _to_mcp_date(data)
    estat_msg = await update.message.reply_text(f"🖨️ Carregant albarans per imprimir del {data}...")
    logger.info("Impressio directa totes les comandes: date=%s", data_mcp)

    resultat = await mcp.comandes_per_data(data_mcp)
    clients = resultat.get("clients", [])
    if not clients:
        await estat_msg.edit_text(f"ℹ️ No hi ha comandes per imprimir el {data}.")
        return

    impresos = []
    errors = []
    total_copies = 0
    totals_articles = {}

    for client in clients:
        codi = client.get("codi")
        nom = client.get("nom", str(codi))
        if not codi:
            continue

        copies = _get_copies(int(codi))
        logger.info("Imprimint albara date=%s client=%s (%s) copies=%s", data_mcp, codi, nom, copies)
        result = await mcp.imprimir_albarans(data_mcp, int(codi), copies)
        if "error" in result:
            logger.warning("Error imprimint albara date=%s client=%s: %s", data_mcp, codi, result)
            errors.append(f"{nom}: error")
        else:
            impresos.append(f"{nom} x{copies}")
            total_copies += copies
            for linia in client.get("linies", []):
                nom_art = linia.get("nm") or linia.get("artName") or linia.get("name") or str(linia.get("art", "?"))
                qty = linia.get("requested", 0)
                if qty > 0:
                    totals_articles[nom_art] = totals_articles.get(nom_art, 0) + qty

    totals_lines = sorted(totals_articles.items(), key=lambda x: x[0].lower())
    totals_txt = "\n\nTotals per producte:\n" + "\n".join(
        f"- {art}: {qty}" for art, qty in totals_lines
    ) if totals_lines else ""

    if errors:
        await estat_msg.edit_text(
            "⚠️ Impressio acabada amb errors.\n\n"
            f"Data: {data}\n"
            f"Enviats: {len(impresos)} clients, {total_copies} copies.\n\n"
            "Clients enviats:\n"
            + ("\n".join(f"- {item}" for item in impresos[:25]) if impresos else "- Cap")
            + "\n\nErrors:\n"
            + "\n".join(errors[:10])
            + totals_txt
        )
    else:
        await estat_msg.edit_text(
            "✅ Albarans enviats a imprimir.\n\n"
            f"Data: {data}\n"
            f"Clients: {len(impresos)}\n"
            f"Copies totals: {total_copies}\n\n"
            "Clients enviats:\n"
            + "\n".join(f"- {item}" for item in impresos[:25])
            + totals_txt
        )

    if totals_lines:
        totals_dict = {art: qty for art, qty in totals_lines}
        text_escpos = _format_totals_escpos(data, totals_dict, len(impresos))
        await imprimir_text_directe(text_escpos)


def _pending_polls(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Retorna el registre global d'enquestes pendents, indexat per poll_id."""
    return context.application.bot_data.setdefault("pending_polls", {})


def _save_pending_selection(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    options: list,
    contents: list,
    selection_type: str,
    poll_id: str,
    message_id: int,
) -> None:
    """Guarda una selecció pendent tant a user_data com globalment per poll_id."""
    pending = {
        "user_id": user_id,
        "chat_id": chat_id,
        "options": options,
        "contents": contents,
        "selection_type": selection_type,
        "poll_id": poll_id,
        "message_id": message_id,
    }
    context.user_data["pending_selection"] = pending
    _pending_polls(context)[poll_id] = pending


def _clear_pending_selection(context: ContextTypes.DEFAULT_TYPE, poll_id: str | None) -> None:
    """Esborra una selecció pendent de user_data i del registre global."""
    context.user_data.pop("pending_selection", None)
    if poll_id:
        _pending_polls(context).pop(poll_id, None)


def _load_auth_data() -> dict:
    return auth_store.load()


def _save_auth_data(data: dict) -> None:
    auth_store.save(data)


def _get_admin_user_id() -> int | None:
    return auth_store.get_admin_user_id()


def _is_authorized_user(user_id: int) -> bool:
    return auth_store.is_authorized_user(user_id)


def _get_user_profile(user_id: int) -> dict | None:
    return auth_store.get_user_profile(user_id)


def _is_admin_user(user_id: int) -> bool:
    return auth_store.is_admin_user(user_id)


def _get_client_scope(user_id: int) -> tuple[int | None, str | None]:
    return auth_store.get_client_scope(user_id)


async def _search_clients_by_phone(phone: str) -> list[dict]:
    normalized = normalize_phone(phone)
    if not normalized:
        return []

    clients = await mcp.llistar_tots_clients()
    if not isinstance(clients, list):
        return []

    matches = []
    for item in clients:
        client_phone = normalize_phone(str(item.get("t", "")))
        if client_phone and client_phone == normalized:
            matches.append(item)
    return matches


async def _notify_admin_authorization_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    data = _load_auth_data()
    admin_id = _get_admin_user_id()
    request_key = str(user.id)

    pending_requests = data.setdefault("pending_requests", {})
    request_info = pending_requests.get(request_key, {})
    already_notified = bool(request_info.get("notified_admin"))
    request_info.update({
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    })
    pending_requests[request_key] = request_info
    _save_auth_data(data)

    if not admin_id or already_notified:
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Autoritzar", callback_data=f"auth_allow:{user.id}"),
        InlineKeyboardButton("❌ Denegar", callback_data=f"auth_deny:{user.id}"),
    ]])
    username = f"@{user.username}" if user.username else "(sense username)"
    await context.bot.send_message(
        chat_id=admin_id,
        text=(
            "🔐 Nova sol·licitud d'accés\n\n"
            f"Nom: {user.full_name}\n"
            f"Usuari: {username}\n"
            f"ID: {user.id}"
        ),
        reply_markup=keyboard,
    )
    request_info["notified_admin"] = True
    pending_requests[request_key] = request_info
    _save_auth_data(data)


async def _notify_admin_contact_request(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    full_name: str,
    username: str | None,
    phone: str,
    matches: list[dict],
) -> None:
    data = _load_auth_data()
    admin_id = _get_admin_user_id()
    if not admin_id:
        return

    keyboard_rows = []
    for match in matches[:5]:
        keyboard_rows.append([
            InlineKeyboardButton(
                f"✅ Client: {match['n'][:24]}",
                callback_data=f"auth_client:{user_id}:{match['c']}",
            )
        ])
    keyboard_rows.append([InlineKeyboardButton("🛠️ Admin Cal Forner", callback_data=f"auth_admin:{user_id}")])
    keyboard_rows.append([InlineKeyboardButton("❌ Denegar", callback_data=f"auth_deny:{user_id}")])

    username_text = f"@{username}" if username else "(sense username)"
    if matches:
        match_lines = "\n".join(f"- {item['n']} ({item['c']})" for item in matches[:5])
    else:
        match_lines = "- Cap client trobat"

    await context.bot.send_message(
        chat_id=admin_id,
        text=(
            "🔐 Nova sol·licitud d'accés amb contacte\n\n"
            f"Nom: {full_name}\n"
            f"Usuari: {username_text}\n"
            f"ID Telegram: {user_id}\n"
            f"Telèfon: {phone}\n\n"
            "Coincidències MCP:\n"
            f"{match_lines}\n\n"
            "Decideix si entra com a client o com a administratiu de Cal Forner."
        ),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def rebuig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id if user else None
    logger.warning(f"Accés denegat a user_id={uid}")

    text = (
        "🔒 Aquest bot només respon a usuaris autoritzats.\n"
        "Per demanar accés, envia el teu contacte de Telegram amb el botó de sota."
    )
    if not _get_admin_user_id():
        text += "\n\nℹ️ Encara no hi ha cap administrador configurat."

    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Compartir contacte", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=keyboard)


async def handle_contact_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    user = update.effective_user
    contact = message.contact if message else None
    if not user or not contact:
        return

    if contact.user_id and contact.user_id != user.id:
        await message.reply_text("❌ Has d'enviar el teu propi contacte de Telegram.")
        return

    normalized_phone = normalize_phone(contact.phone_number)
    matches = await _search_clients_by_phone(contact.phone_number)
    clean_matches = [m for m in matches if isinstance(m, dict) and "c" in m and "n" in m]

    auth_data = _load_auth_data()
    auth_data.setdefault("pending_requests", {})[str(user.id)] = {
        "user_id": user.id,
        "full_name": user.full_name,
        "username": user.username,
        "telegram_phone": normalized_phone,
        "matches": clean_matches[:5],
        "requested_at": datetime.now().isoformat(timespec="seconds"),
        "notified_admin": True,
    }
    _save_auth_data(auth_data)

    await _notify_admin_contact_request(
        context,
        user_id=user.id,
        full_name=user.full_name,
        username=user.username,
        phone=normalized_phone,
        matches=clean_matches,
    )

    await message.reply_text(
        "✅ Sol·licitud enviada a l'administrador. Quan et doni accés, ja podràs fer servir el bot.",
        reply_markup=ReplyKeyboardRemove(),
    )


def _format_order_ticket(client_name: str, order_date: str, lines: list[dict]) -> str:
    """Construeix un ticket monoespaiat per Telegram."""
    rows = []
    total_units = 0
    for item in lines:
        requested = item.get("requested", 0)
        if not requested:
            continue

        name = item.get("nm") or item.get("artName") or item.get("name") or str(item.get("art", "?"))
        order_type = item.get("order_type", 1)
        total_units += requested
        rows.append({
            "is_order": order_type == 2,
            "qty": requested,
            "name": name,
        })

    if not rows:
        return ""

    qty_width = max(3, max(len(str(row["qty"])) for row in rows))
    name_width = max(len("ARTICLE"), max(len(str(row["name"])) for row in rows))
    header_row = f"{'QTY':>{qty_width}}  {'ARTICLE':<{name_width}}"
    sep_row = f"{'-' * qty_width}  {'-' * min(name_width, 28)}"
    body_lines = []
    for row in rows:
        base = f"{str(row['qty']).rjust(qty_width)}  {row['name']:<{name_width}}"
        if row["is_order"]:
            body_lines.append(f"<b><code>{escape(base)}</code></b> 🎗️")
        else:
            body_lines.append(f"<code>{escape(base)}</code>")
    header = (
        "<b>🧾 Comanda</b>\n"
        f"<b>👤 Client:</b> {escape(client_name)}\n"
        f"<b>📅 Data:</b> {escape(order_date)}\n"
        f"<b>📦 Línies:</b> {len(rows)}    <b>🥖 Unitats:</b> {total_units}"
    )
    table = [f"<code>{escape(header_row)}</code>", f"<code>{escape(sep_row)}</code>", *body_lines]
    return f"{header}\n" + "\n".join(table)


def _is_simple_greeting(text: str) -> bool:
    normalized = text.strip().lower()
    greetings = {
        "hola", "bon dia", "bona tarda", "bona nit", "ei", "hey", "hello", "holi",
    }
    return normalized in greetings


# ------------------------------------------------------------------ #
#  Autenticació                                                        #
# ------------------------------------------------------------------ #
def autoritzat(update: Update) -> bool:
    if not update.effective_user:
        return False
    return _is_authorized_user(update.effective_user.id)


def _is_admin(update: Update) -> bool:
    return bool(update.effective_user and _is_admin_user(update.effective_user.id))


def _bound_client(update: Update) -> tuple[int | None, str | None]:
    if not update.effective_user:
        return (None, None)
    return _get_client_scope(update.effective_user.id)


async def _deny_scope(update: Update, text: str = "❌ No tens permís per accedir a aquest client.") -> None:
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text)


# ------------------------------------------------------------------ #
#  /start i /ajuda                                                     #
# ------------------------------------------------------------------ #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return

    uid = update.effective_user.id
    logger.info(f"Usuari connectat: {update.effective_user.full_name} (id={uid})")

    text = (
        "🥐 *Bot de Gestió de Comandes*\n\n"
        "Comandes disponibles:\n"
        "• /afegir   — Afegir producte a una comanda\n"
        "• /esborrar — Esborrar producte d'una comanda\n"
        "• /veure    — Veure albarà d'un client\n"
        "• /imprimir — Imprimir albarans\n"
        "• /hora    — Mostrar l'hora actual\n"
        "• /reset    — Reiniciar la conversa amb la IA\n"
        "• /cancel   — Cancel·lar operació en curs\n"
        "• /ajuda    — Mostrar aquesta ajuda\n\n"
        "També pots escriure preguntes lliures o enviar una nota de veu.\n"
        "La IA usarà Gemini i les eines MCP de HitSystems quan calgui.\n\n"
        f"El teu ID de Telegram és: `{uid}`"
    )
    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup([["🕒 Hora actual"]], resize_keyboard=True),
    )


async def cmd_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return
    ara = datetime.now()
    hora = ara.strftime("%H:%M:%S")
    dia = ara.strftime("%d/%m/%Y")
    any_actual = ara.strftime("%Y")
    await update.message.reply_text(f"🕒 Hora actual: {hora}\n📅 Dia: {dia}\n📆 Any: {any_actual}")


async def _send_chunks(message, text: str, parse_mode: str | None = None):
    chunk_size = 3500
    for start in range(0, len(text), chunk_size):
        await message.reply_text(text[start:start + chunk_size], parse_mode=parse_mode)


async def _tancar_estat(msg):
    """Edita el missatge d'estat a ✅ i l'esborra al cap d'un moment."""
    try:
        await msg.edit_text("✅")
        await asyncio.sleep(0.8)
        await msg.delete()
    except Exception:
        try:
            await msg.delete()
        except Exception:
            pass


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    if error:
        logger.error("Error no gestionat al bot", exc_info=(type(error), error, error.__traceback__))
    else:
        logger.error("Error no gestionat al bot sense excepcio associada. update=%s", update)


# ================================================================== #
#  Cancel·lar qualsevol flux                                           #
# ================================================================== #

def _extract_print_text(text: str) -> str | None:
    cleaned = text.strip()
    if not cleaned:
        return None

    lower = cleaned.lower()
    blocked_topics = (
        "comanda", "comandes", "albara", "albarà", "albarans",
        "ticket", "tiquet", "tiquets", "venda", "vendes",
        "cua", "queue", "estat", "pendent", "pendents",
    )
    if any(word in lower for word in blocked_topics):
        return None

    patterns = [
        r"(?is)^imprimeix\s+text\s*[:\-]\s*(.+)$",
        r"(?is)^imprimir\s+text\s*[:\-]\s*(.+)$",
        r"(?is)^imprimeix\s+text\s+(.+)$",
        r"(?is)^imprimir\s+text\s+(.+)$",
        r"(?is)^imprimeix\s+(?:a\s+)?(?:la\s+)?(?:impresora|impressora|star)\s*[:\-]?\s*(.+)$",
        r"(?is)^imprimir\s+(?:a\s+)?(?:la\s+)?(?:impresora|impressora|star)\s*[:\-]?\s*(.+)$",
        r"(?is)^imprimeix\s+a\s+la\s+star\s*[:\-]\s*(.+)$",
        r"(?is)^imprimir\s+a\s+la\s+star\s*[:\-]\s*(.+)$",
        r"(?is)^imprimeix\s+(?:aixo|això|esto|este\s+texto|aquest\s+text)\s*[:\-]?\s*(.+)$",
        r"(?is)^imprimir\s+(?:aixo|això|esto|este\s+texto|aquest\s+text)\s*[:\-]?\s*(.+)$",
    ]
    patterns.extend([
        r"(?is)^imprimeix\s+(.+)$",
        r"(?is)^imprimir\s+(.+)$",
    ])
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if match:
            return match.group(1).strip()
    return None


def _is_print_queue_request(text: str) -> bool:
    t = text.strip().lower()
    has_queue = any(word in t for word in ("cua", "queue", "pendent", "pendents", "estat"))
    has_print = any(word in t for word in ("impress", "impresora", "impressora", "star"))
    return has_queue and has_print


def _format_print_queue(result) -> str:
    if isinstance(result, dict) and "error" in result:
        return f"Error cua impressio: {result['error']}"

    if isinstance(result, dict):
        jobs = (
            result.get("jobs")
            or result.get("queue")
            or result.get("pending")
            or result.get("rows")
            or result.get("items")
            or result.get("list")
        )
        if jobs is None:
            count = result.get("count") or result.get("pending_count") or result.get("total")
            if count in (0, "0"):
                return "Cua d'impressio buida."
            return f"Estat cua impressio:\n{result}"
    else:
        jobs = result

    if not jobs:
        return "Cua d'impressio buida."

    lines = [f"Cua d'impressio - pendents: {len(jobs)}"]
    for job in jobs[:20]:
        if isinstance(job, dict):
            job_id = job.get("id") or job.get("Id") or job.get("ID") or job.get("codi") or "?"
            printer = job.get("printer") or job.get("impresora") or job.get("Impresora") or job.get("dest") or "?"
            text = job.get("text") or job.get("Text") or job.get("missatge") or str(job)
            lines.append(f"- #{job_id} {printer}: {str(text)[:140]}")
        else:
            lines.append(f"- {str(job)[:160]}")
    if len(jobs) > 20:
        lines.append(f"... i {len(jobs) - 20} mes")
    return "\n".join(lines)


async def _send_print_text(update: Update, text: str) -> None:
    if not _is_admin(update):
        await update.message.reply_text("Nomes els administradors poden imprimir text lliure.")
        return
    if not text.strip():
        await update.message.reply_text("Text buit. Usa: /imprimir_text text a imprimir")
        return

    # ESC/POS: init + doble alçada i amplada + text + mida normal + avanç paper
    ESC_INIT   = "\x1b\x40"       # inicialitza impressora
    ESC_DOBLE  = "\x1d\x21\x66"   # 7x amplada + 7x alçada
    ESC_NORMAL = "\x1d\x21\x00"   # mida normal
    content = text.strip().upper()
    text_to_print = ESC_INIT + ESC_DOBLE + content + ESC_NORMAL + "\n\n\n"
    logger.info("Impressio directa text Star: len=%s preview=%r", len(content), content[:80])
    result = await mcp.imprimir_text(text_to_print)
    if "error" in result:
        logger.warning("Error imprimint text Star: %s", result)
        await update.message.reply_text(f"Error enviant a imprimir: {result['error']}")
        return
    await update.message.reply_text(f"Text enviat a la impressora Star:\n\n{text_to_print[:1000]}")


async def cmd_imprimir_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return
    await _send_print_text(update, " ".join(context.args))


async def cmd_cua_impressio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return
    if not _is_admin(update):
        await update.message.reply_text("Nomes els administradors poden veure la cua d'impressio.")
        return
    result = await mcp.cua_impressio()
    await update.message.reply_text(_format_print_queue(result))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operació cancel·lada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Para qualsevol procés en curs i torna a l'estat inicial. Funciona des de qualsevol estat."""
    context.user_data.clear()
    await update.message.reply_text("🛑 Aturat. Quan vulguis, escriu /afegir, /veure o /esborrar.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def auto_envia_comandes():
    """Executa una passada d'autoenviament per ser cridada des d'un cron extern."""
    await auto_sender.run_with_new_bot()


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

def run_bot():
    if not _acquire_single_instance_lock():
        logger.error("No s'arrenca el bot: ja hi ha una altra instancia activa (lock: %s)", BOT_LOCK_PATH)
        return

    persistence = PicklePersistence(filepath=str(PERSISTENCE_PATH))
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .persistence(persistence)
        .build()
    )

    # Filtre "stop" — text case-insensitive
    stop_filter = filters.TEXT & filters.Regex(r"(?i)^stop$")
    stop_handler_msg = MessageHandler(stop_filter, cmd_stop)

    afegir_handler = build_afegir_handler(
        logger=logger,
        mcp=mcp,
        autoritzat=autoritzat,
        rebuig=rebuig,
        is_admin=_is_admin,
        bound_client=_bound_client,
        keyboard_dates=_keyboard_dates,
        parse_data=_parse_data,
        to_mcp_date=_to_mcp_date,
        normalize_search_text=_normalize_search_text,
        fallback_article_options=_fallback_article_options,
        manual_order_text=_manual_order_text,
        manual_order_keyboard=_manual_order_keyboard,
        cmd_stop=cmd_stop,
        cmd_cancel=cmd_cancel,
    )

    veure_handler = build_veure_handler(
        logger=logger,
        mcp=mcp,
        autoritzat=autoritzat,
        rebuig=rebuig,
        is_admin=_is_admin,
        bound_client=_bound_client,
        keyboard_dates=_keyboard_dates,
        parse_data=_parse_data,
        to_mcp_date=_to_mcp_date,
        format_order_ticket=_format_order_ticket,
        cmd_stop=cmd_stop,
        cmd_cancel=cmd_cancel,
    )

    esborrar_handler = build_esborrar_handler(
        logger=logger,
        mcp=mcp,
        autoritzat=autoritzat,
        rebuig=rebuig,
        is_admin=_is_admin,
        bound_client=_bound_client,
        keyboard_dates=_keyboard_dates,
        parse_data=_parse_data,
        to_mcp_date=_to_mcp_date,
        manual_order_text=_manual_order_text,
        manual_order_keyboard=_manual_order_keyboard,
        tancar_estat=_tancar_estat,
        cmd_stop=cmd_stop,
        cmd_cancel=cmd_cancel,
    )

    imprimir_handler = build_imprimir_handler(
        mcp=mcp,
        autoritzat=autoritzat,
        rebuig=rebuig,
        keyboard_dates=_keyboard_dates,
        parse_data=_parse_data,
        to_mcp_date=_to_mcp_date,
        get_copies=_get_copies,
        print_all_orders=_print_all_orders,
        extract_print_text=_extract_print_text,
        send_print_text=_send_print_text,
        cmd_stop=cmd_stop,
        cmd_cancel=cmd_cancel,
    )

    vendes_handler = build_vendes_handler(
        logger=logger,
        mcp=mcp,
        shops=config.SHOPS,
        autoritzat=autoritzat,
        rebuig=rebuig,
        is_admin=_is_admin,
        normalize_search_text=_normalize_search_text,
        keyboard_sales_dates=_keyboard_sales_dates,
        parse_sales_date=_parse_sales_date,
        format_hora_feliz_report=_format_hora_feliz_report,
        format_products_by_hour_report=_format_products_by_hour_report,
        format_product_totals_report=_format_product_totals_report,
        format_sales_summary_report=_format_sales_summary_report,
        send_chunks=_send_chunks,
        cmd_stop=cmd_stop,
        cmd_cancel=cmd_cancel,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_start))
    app.add_handler(CommandHandler("hora", cmd_hora))
    app.add_handler(CommandHandler("imprimir_text", cmd_imprimir_text))
    app.add_handler(CommandHandler("cua_impressio", cmd_cua_impressio))
    register_ia_admin_handlers(app, _get_admin_user_id, mcp=mcp, get_copies=_get_copies)
    app.add_handler(afegir_handler)
    app.add_handler(esborrar_handler)
    app.add_handler(veure_handler)
    app.add_handler(imprimir_handler)
    app.add_handler(vendes_handler)
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact_share))
    register_ai_chat_handlers(
        app,
        ai=ai,
        mcp=mcp,
        logger=logger,
        autoritzat=autoritzat,
        rebuig=rebuig,
        is_admin=_is_admin,
        bound_client=_bound_client,
        deny_scope=_deny_scope,
        cmd_hora=cmd_hora,
        send_chunks=_send_chunks,
        tancar_estat=_tancar_estat,
        send_hourly_sales_report=_send_hourly_sales_report,
        is_print_queue_request=_is_print_queue_request,
        format_print_queue=_format_print_queue,
        extract_print_text=_extract_print_text,
        send_print_text=_send_print_text,
        is_all_orders_request=_is_all_orders_request,
        is_print_request=_is_print_request,
        is_product_summary_excel_request=_is_product_summary_excel_request,
        parse_all_orders_date=_parse_all_orders_date,
        print_all_orders=_print_all_orders,
        send_product_summary_excel=_send_product_summary_excel,
        to_mcp_date=_to_mcp_date,
        format_all_orders_blocks=_format_all_orders_blocks,
        send_html_blocks=_send_html_blocks,
        is_simple_greeting=_is_simple_greeting,
        save_pending_selection=_save_pending_selection,
        pending_polls=_pending_polls,
        clear_pending_selection=_clear_pending_selection,
        initial_confirmation_fields=_initial_confirmation_fields,
        confirmation_text=_confirmation_text,
        confirmation_keyboard=_confirmation_keyboard,
    )

    register_callback_handlers(
        app,
        logger=logger,
        mcp=mcp,
        ai=ai,
        order_field_labels=ORDER_FIELD_LABELS,
        order_field_kwargs=ORDER_FIELD_KWARGS,
        manual_order_text=_manual_order_text,
        manual_order_keyboard=_manual_order_keyboard,
        order_type_choice_keyboard=_order_type_choice_keyboard,
        format_order_fields=_format_order_fields,
        confirmation_text=_confirmation_text,
        confirmation_keyboard=_confirmation_keyboard,
        format_order_ticket=_format_order_ticket,
        load_auth_data=_load_auth_data,
        save_auth_data=_save_auth_data,
        get_admin_user_id=_get_admin_user_id,
        base_dir=Path(__file__).resolve().parent,
    )
    app.add_error_handler(error_handler)

    logger.info("=" * 50)
    logger.info("  Bot HitSystems actiu. Ctrl+C per aturar.")
    logger.info("=" * 50)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def main(mode: str = "bot"):
    if mode == "bot":
        run_bot()
        return
    if mode in {"auto_envia", "auto-envia", "cron"}:
        asyncio.run(auto_envia_comandes())
        return
    raise ValueError(f"Mode desconegut: {mode}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Bot HitSystems")
    parser.add_argument(
        "--mode",
        default="bot",
        choices=("bot", "auto_envia", "auto-envia", "cron"),
        help="Mode d'execucio: bot normal o passada d'autoenviament per cron extern.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    main(args.mode)
