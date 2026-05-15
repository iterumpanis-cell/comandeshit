"""
bot.py — Bot de Telegram per a la gestió de comandes HitSystems
Tot via MCP: cerca clients, cerca articles, afegir (normal i encarrec), veure comanda.
"""
import asyncio
import argparse
import json
import logging
import re
import tempfile
from html import escape
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, Bot
from telegram.constants import ChatAction
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
from gemini_hit import GeminiHitAssistant, NEEDS_SELECTION, NEEDS_CONFIRMATION
from mcp_vendes import MCPVendes
from printer import _format_totals_escpos, imprimir_text_directe

# ------------------------------------------------------------------ #
#  Logging                                                             #
# ------------------------------------------------------------------ #
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
PERSISTENCE_PATH = Path(__file__).with_name("bot_state.pkl")
AUTHORIZED_USERS_PATH = Path(__file__).with_name("authorized_users.json")
CLIENT_COPIES_PATH = Path(__file__).with_name("client_copies.json")


def _get_copies(client_code: int) -> int:
    """Retorna el nombre de còpies configurat per a un client. Default: 2."""
    try:
        data = json.loads(CLIENT_COPIES_PATH.read_text(encoding="utf-8"))
        return int(data.get(str(client_code), data.get("_default", 2)))
    except Exception:
        return 2

# ------------------------------------------------------------------ #
#  Estats de la conversa                                              #
# ------------------------------------------------------------------ #
(
    AF_CLIENT, AF_CLIENT_OPCIO, AF_DATA, AF_PRODUCTE, AF_PRODUCTE_OPCIO, AF_QUANTITAT, AF_CONFIRMAR,
    VR_CLIENT, VR_CLIENT_OPCIO, VR_DATA,
    EB_CLIENT, EB_CLIENT_OPCIO, EB_DATA, EB_PRODUCTE, EB_PRODUCTE_OPCIO, EB_CONFIRMAR,
    IM_DATA, IM_CLIENT, IM_CLIENT_OPCIO, IM_COPIES, IM_SEGUENT,
    IM_TIPUS, IM_TEXT,
) = range(23)

# Instàncies globals
mcp = MCPVendes()

ai = GeminiHitAssistant(
    config.GEMINI_API_KEY,
    config.GEMINI_MODEL,
    mcp,
    config.GEMINI_FALLBACK_MODELS,
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


def _parse_data(text: str) -> str | None:
    """Parseja text de data i retorna DD/MM/YYYY o None si error.
    Accepta: 'avui', 'DD/MM/YYYY', botó 'Dij 03/04' (afegeix any automàticament).
    """
    t = text.strip().lower()
    if t in ("avui", "hoy", "today", ""):
        return date.today().strftime("%d/%m/%Y")
    if t in ("demà", "dema", "mañana", "tomorrow"):
        return (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")

    # Format complet DD/MM/YYYY
    try:
        datetime.strptime(t, "%d/%m/%Y")
        return t.strip()
    except ValueError:
        pass

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


def _is_print_request(text: str) -> bool:
    t = text.strip().lower()
    return any(word in t for word in ("imprim", "imprimeix", "imprimir", "impressio", "impressió"))


def _is_all_orders_request(text: str) -> bool:
    t = text.strip().lower()
    wants_all = any(word in t for word in ("totes", "tots", "tota", "tot el"))
    wants_orders = any(word in t for word in ("comand", "albar"))
    wants_plural_orders = any(word in t for word in ("comandes", "albarans"))
    return wants_orders and (wants_all or (_is_print_request(text) and wants_plural_orders))


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


def _default_auth_data() -> dict:
    authorized = sorted({*config.ALLOWED_USER_IDS, *([config.ADMIN_USER_ID] if config.ADMIN_USER_ID else [])})
    users = {}
    for uid in authorized:
        users[str(uid)] = {"role": "admin" if uid == config.ADMIN_USER_ID else "client"}
    return {
        "admin_user_id": config.ADMIN_USER_ID,
        "authorized_users": authorized,
        "users": users,
        "pending_requests": {},
    }


def _load_auth_data() -> dict:
    if not AUTHORIZED_USERS_PATH.exists():
        data = _default_auth_data()
        AUTHORIZED_USERS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    try:
        data = json.loads(AUTHORIZED_USERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = _default_auth_data()

    data.setdefault("admin_user_id", config.ADMIN_USER_ID)
    data.setdefault("authorized_users", [])
    data.setdefault("users", {})
    data.setdefault("pending_requests", {})

    if config.ADMIN_USER_ID and config.ADMIN_USER_ID not in data["authorized_users"]:
        data["authorized_users"].append(config.ADMIN_USER_ID)
    for uid in config.ALLOWED_USER_IDS:
        if uid not in data["authorized_users"]:
            data["authorized_users"].append(uid)

    data["authorized_users"] = sorted({int(uid) for uid in data["authorized_users"]})
    users = data["users"]
    for uid in data["authorized_users"]:
        key = str(uid)
        users.setdefault(key, {})
        users[key].setdefault("role", "admin" if uid == config.ADMIN_USER_ID else "client")
    if config.ADMIN_USER_ID:
        users.setdefault(str(config.ADMIN_USER_ID), {})
        users[str(config.ADMIN_USER_ID)]["role"] = "admin"
    return data


def _save_auth_data(data: dict) -> None:
    users = data.get("users", {})
    data["authorized_users"] = sorted({int(uid) for uid in users.keys()})
    AUTHORIZED_USERS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_admin_user_id() -> int | None:
    data = _load_auth_data()
    admin_id = data.get("admin_user_id")
    return int(admin_id) if admin_id else None


def _is_authorized_user(user_id: int) -> bool:
    data = _load_auth_data()
    return str(int(user_id)) in data.get("users", {})


def _get_user_profile(user_id: int) -> dict | None:
    data = _load_auth_data()
    return data.get("users", {}).get(str(int(user_id)))


def _is_admin_user(user_id: int) -> bool:
    profile = _get_user_profile(user_id)
    return bool(profile and profile.get("role") == "admin")


def _get_client_scope(user_id: int) -> tuple[int | None, str | None]:
    profile = _get_user_profile(user_id) or {}
    client_code = profile.get("client_code")
    client_name = profile.get("client_name")
    return (int(client_code) if client_code is not None else None, client_name)


def _normalize_phone(phone: str) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("34") and len(digits) > 9:
        digits = digits[2:]
    return digits


async def _search_clients_by_phone(phone: str) -> list[dict]:
    normalized = _normalize_phone(phone)
    if not normalized:
        return []

    clients = await mcp.llistar_tots_clients()
    if not isinstance(clients, list):
        return []

    matches = []
    for item in clients:
        client_phone = _normalize_phone(str(item.get("t", "")))
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

    normalized_phone = _normalize_phone(contact.phone_number)
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
        "• /reset    — Reiniciar la conversa amb la IA\n"
        "• /cancel   — Cancel·lar operació en curs\n"
        "• /ajuda    — Mostrar aquesta ajuda\n\n"
        "També pots escriure preguntes lliures o enviar una nota de veu.\n"
        "La IA usarà Gemini i les eines MCP de HitSystems quan calgui.\n\n"
        f"El teu ID de Telegram és: `{uid}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


def _history(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    return context.user_data.setdefault("ai_history", [])


def _push_history(context: ContextTypes.DEFAULT_TYPE, role: str, text: str):
    history = _history(context)
    history.append({"role": role, "text": text})
    if len(history) > 12:
        del history[:-12]


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


async def _ask_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, transcript: str | None = None, estat_msg=None):
    if not ai.enabled:
        await update.message.reply_text("❌ Falta configurar `GEMINI_API_KEY` al fitxer `.env`.")
        return

    if estat_msg is None:
        estat_msg = await update.message.reply_text("🤔 Processant...")

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    _TOPIC_CHANGE_KEYWORDS = ("vendes", "ventes", "sales", "imprimir", "imprim", "/vendes", "/imprimir")
    is_topic_change = any(kw in prompt.lower() for kw in _TOPIC_CHANGE_KEYWORDS)

    if _is_simple_greeting(prompt) or is_topic_change:
        history = []
        context.user_data.pop("pending_confirmation", None)
        context.user_data.pop("pending_selection", None)
        ai.last_context = {}
    else:
        history = list(_history(context))

    # Injecta només la data del context (mai el codi client: pot ser incorrecte si l'usuari menciona el client pel nom)
    ctx = ai.last_context
    if ctx and ctx.get("date") and not (_is_simple_greeting(prompt) or is_topic_change):
        prompt = f"[Context actual: data={ctx['date']}]\n{prompt}"

    client_code, client_name = _bound_client(update)
    effective_prompt = prompt
    if not _is_admin(update) and client_code:
        effective_prompt = (
            f"[RESTRICCIO D'ACCÉS] Aquest usuari només pot operar sobre el client "
            f"{client_name or client_code} (codi {client_code}). "
            "No pots consultar ni modificar cap altre client. "
            "Si l'usuari demana un altre client, rebutja-ho.\n\n"
            f"{prompt}"
        )

    try:
        answer = await ai.ask(effective_prompt, history)
    except Exception as e:
        logger.exception("Error cridant Gemini")
        try:
            await estat_msg.edit_text("❌")
            await asyncio.sleep(0.5)
            await estat_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Error amb Gemini: {e}")
        return

    stored_user_text = transcript if transcript is not None else prompt
    _push_history(context, "user", stored_user_text)

    await _handle_ai_answer(update, context, answer, estat_msg, transcript=transcript)


async def _handle_ai_answer(update, context, answer, estat_msg, transcript=None):
    """Processa la resposta de la IA: text normal, selecció o confirmació."""

    # --- Necessita selecció d'usuari ---
    if isinstance(answer, dict) and NEEDS_SELECTION in answer:
        await _tancar_estat(estat_msg)
        options = answer.get("options", [])
        question = answer.get("question", "Selecciona una opció:")
        contents = answer.get("__gemini_contents__", [])

        selection_type = answer.get("selection_type", "article")

        # Enviem enquesta de selecció única
        poll_options = [o["n"][:100] for o in options if "n" in o]
        if not poll_options:
            await update.message.reply_text("❌ No s'han trobat opcions per seleccionar.")
            return

        msg = await context.bot.send_poll(
            chat_id=update.effective_chat.id,
            question=question[:300],
            options=poll_options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
        # Guardem el poll_id per correlacionar la resposta.
        _save_pending_selection(
            context,
            user_id=update.effective_user.id,
            chat_id=update.effective_chat.id,
            options=options,
            contents=contents,
            selection_type=selection_type,
            poll_id=msg.poll.id,
            message_id=msg.message_id,
        )
        return

    # --- Necessita confirmació de comanda ---
    if isinstance(answer, dict) and NEEDS_CONFIRMATION in answer:
        await _tancar_estat(estat_msg)
        lines = answer.get("lines", [])
        contents = answer.get("__gemini_contents__", [])
        client_code, client_name = _bound_client(update)
        if not _is_admin(update) and client_code:
            invalid = [line for line in lines if int(line.get("client", -1)) != int(client_code)]
            if invalid:
                await update.message.reply_text(
                    f"❌ Només pots operar sobre les comandes de *{client_name or client_code}*.",
                    parse_mode="Markdown",
                )
                return

        context.user_data["pending_confirmation"] = {
            "lines": lines,
            "contents": contents,
        }

        # Construïm el text de confirmació
        text_lines = ["📋 *Confirmes la comanda?*\n"]
        for line in lines:
            date_fmt = line.get("date", "?")
            client_name = line.get("client_name", f"codi {line.get('client', '?')}")
            article_name = line.get("article_name", f"codi {line.get('article_code', '?')}")
            qty = line.get("quantity", 0)
            order_type = line.get("order_type", 1)
            prefix = "🎗️ " if order_type == 2 else "🥖 "
            text_lines.append(f"{prefix}*{article_name}* × {qty}")
            text_lines.append(f"   👤 {client_name}  📅 {date_fmt}\n")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirmar", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel·lar", callback_data="confirm_no"),
        ]])
        await update.message.reply_text(
            "\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    # --- Resposta de text normal ---
    await _tancar_estat(estat_msg)

    if transcript is not None:
        await _send_chunks(update.message, f"📝 _{transcript}_")

    if isinstance(answer, str):
        _push_history(context, "assistant", answer)
        parse_mode = "HTML" if answer.lstrip().startswith("<pre>") else None
        await _send_chunks(update.message, answer, parse_mode=parse_mode)
    else:
        await update.message.reply_text("❌ Resposta inesperada de la IA.")


async def ai_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return

    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return

    if text.lower() == "stop":
        context.user_data.clear()
        await update.message.reply_text("🛑 Aturat. Quan vulguis, escriu /afegir, /veure o /esborrar.", reply_markup=ReplyKeyboardRemove())
        return

    if _is_print_queue_request(text):
        if not _is_admin(update):
            await update.message.reply_text("Nomes els administradors poden veure la cua d'impressio.")
            return
        result = await mcp.cua_impressio()
        await update.message.reply_text(_format_print_queue(result))
        return

    print_text = _extract_print_text(text)
    if print_text is not None:
        await _send_print_text(update, print_text)
        return

    if _is_all_orders_request(text) and _is_print_request(text):
        if not _is_admin(update):
            await _deny_scope(update, "❌ Només els administradors poden imprimir totes les comandes del dia.")
            return

        data = _parse_all_orders_date(text)
        if not data:
            await update.message.reply_text("⚠️ Digues la data, per exemple: imprimeix totes les comandes del dia 01/05.")
            return

        try:
            await _print_all_orders(update, data)
        except Exception as exc:
            logger.exception("Error imprimint totes les comandes")
            await update.message.reply_text(f"❌ Error imprimint les comandes: {exc}")
        return

    if _is_all_orders_request(text):
        if not _is_admin(update):
            await _deny_scope(update, "❌ Només els administradors poden consultar totes les comandes del dia.")
            return

        data = _parse_all_orders_date(text)
        if not data:
            await update.message.reply_text("⚠️ Digues la data, per exemple: totes les comandes del dia 01/05.")
            return

        estat_msg = await update.message.reply_text(f"⏳ Carregant totes les comandes del {data}...")
        data_mcp = _to_mcp_date(data)
        logger.info("Consulta directa totes les comandes: date=%s", data_mcp)
        try:
            result = await mcp.comandes_per_data(data_mcp)
            blocks = _format_all_orders_blocks(data, result)
            await _tancar_estat(estat_msg)
            await _send_html_blocks(update.message, blocks)
        except Exception as exc:
            logger.exception("Error carregant totes les comandes")
            try:
                await estat_msg.edit_text("❌")
            except Exception:
                pass
            await update.message.reply_text(f"❌ Error carregant les comandes: {exc}")
        return

    await _ask_ai(update, context, text)


async def ai_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return

    if not ai.enabled:
        await update.message.reply_text("❌ Falta configurar `GEMINI_API_KEY` al fitxer `.env`.")
        return

    suffix = ".ogg"
    voice = update.message.voice
    if voice and voice.mime_type == "audio/ogg":
        suffix = ".ogg"

    temp_path = None
    estat_msg = await update.message.reply_text("🎙️ Transcrivint l'àudio...")
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_path = Path(temp_file.name)

        telegram_file = await context.bot.get_file(voice.file_id)
        await telegram_file.download_to_drive(custom_path=str(temp_path))

        transcript = await ai.transcribe_audio(str(temp_path))
        if not transcript:
            await estat_msg.edit_text("❌ No he pogut transcriure l'àudio.")
            return

        if transcript.strip().lower() == "stop":
            context.user_data.clear()
            await estat_msg.edit_text("🛑 Aturat. Quan vulguis, escriu /afegir, /veure o /esborrar.")
            return

        await estat_msg.edit_text("🤔 Processant...")
        await _ask_ai(update, context, transcript, transcript=transcript, estat_msg=estat_msg)
    except Exception as e:
        logger.exception("Error processant àudio")
        try:
            await estat_msg.delete()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Error processant l'àudio: {e}")
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("ai_history", None)
    ai.last_context = {}
    await update.message.reply_text("🧹 Conversa amb la IA reiniciada.")


async def handle_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rep la resposta de l'usuari a l'enquesta de selecció."""
    poll_answer = update.poll_answer
    pending = context.user_data.get("pending_selection")
    if pending and pending.get("poll_id") != poll_answer.poll_id:
        pending = None
    if not pending:
        pending = _pending_polls(context).get(poll_answer.poll_id)
    if not pending:
        logger.warning(
            "handle_poll_answer: cap seleccio pendent per poll_id=%s user_id=%s",
            poll_answer.poll_id,
            poll_answer.user.id,
        )
        await context.bot.send_message(
            chat_id=poll_answer.user.id,
            text="❌ No puc recuperar l'enquesta pendent. Torna a enviar la petició, si us plau.",
        )
        return

    chat_id = pending.get("chat_id") or poll_answer.user.id

    # Obtenim l'opció seleccionada
    selected_idx = poll_answer.option_ids[0] if poll_answer.option_ids else None
    if selected_idx is None:
        logger.info("handle_poll_answer: resposta sense opcio per poll_id=%s", poll_answer.poll_id)
        return

    options = pending.get("options", [])
    if selected_idx >= len(options):
        logger.warning(
            "handle_poll_answer: index fora de rang poll_id=%s idx=%s total=%s",
            poll_answer.poll_id,
            selected_idx,
            len(options),
        )
        return

    selected = options[selected_idx]
    contents = pending.get("contents", [])
    _clear_pending_selection(context, poll_answer.poll_id)

    # Tanquem el poll
    try:
        await context.bot.stop_poll(
            chat_id=chat_id,
            message_id=pending.get("message_id"),
        )
    except Exception:
        logger.exception("handle_poll_answer: no s'ha pogut tancar el poll %s", poll_answer.poll_id)

    # Afegim la selecció al historial de Gemini i continuem
    from google.genai import types as gtypes
    contents.append(gtypes.Content(
        role="user",
        parts=[gtypes.Part(text=f"L'usuari ha seleccionat: {selected['n']} (codi {selected['c']})")]
    ))

    # Enviem missatge d'estat i continuem la conversa
    estat_msg = await context.bot.send_message(chat_id=chat_id, text="🤔 Processant selecció...")

    try:
        answer = await ai.continue_from_contents(contents)
    except Exception as e:
        logger.exception("Error continuant conversa després de selecció")
        try:
            await estat_msg.edit_text("❌")
            await asyncio.sleep(0.5)
            await estat_msg.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Error: {e}")
        return

    await _tancar_estat(estat_msg)

    if isinstance(answer, dict) and NEEDS_SELECTION in answer:
        # La IA necessita una altra selecció (article o client ambigu)
        options = answer.get("options", [])
        question = answer.get("question", "Selecciona una opció:")
        new_contents = answer.get("__gemini_contents__", [])

        selection_type = answer.get("selection_type", "article")

        poll_options = [o["n"][:100] for o in options if "n" in o]
        if not poll_options:
            await context.bot.send_message(chat_id=chat_id, text="❌ No s'han trobat opcions per seleccionar.")
            return

        msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=question[:300],
            options=poll_options,
            is_anonymous=False,
            allows_multiple_answers=False,
        )
        _save_pending_selection(
            context,
            user_id=pending.get("user_id", poll_answer.user.id),
            chat_id=chat_id,
            options=options,
            contents=new_contents,
            selection_type=selection_type,
            poll_id=msg.poll.id,
            message_id=msg.message_id,
        )

    elif isinstance(answer, str):
        _push_history(context, "assistant", answer)
        parse_mode = "HTML" if answer.lstrip().startswith("<pre>") else None
        for start in range(0, len(answer), 3500):
            await context.bot.send_message(
                chat_id=chat_id,
                text=answer[start:start+3500],
                parse_mode=parse_mode,
            )
    elif isinstance(answer, dict) and NEEDS_CONFIRMATION in answer:
        # La IA vol confirmar una comanda
        lines = answer.get("lines", [])
        new_contents = answer.get("__gemini_contents__", [])
        context.user_data["pending_confirmation"] = {"lines": lines, "contents": new_contents}

        text_lines = ["📋 *Confirmes la comanda?*\n"]
        for line in lines:
            client_name = line.get("client_name", f"codi {line.get('client', '?')}")
            article_name = line.get("article_name", f"codi {line.get('article_code', '?')}")
            qty = line.get("quantity", 0)
            order_type = line.get("order_type", 1)
            prefix = "🎗️ " if order_type == 2 else "🥖 "
            text_lines.append(f"{prefix}*{article_name}* × {qty}")
            text_lines.append(f"   👤 {client_name}  📅 {line.get('date', '?')}\n")

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirmar", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel·lar", callback_data="confirm_no"),
        ]])
        await context.bot.send_message(
            chat_id=chat_id,
            text="\n".join(text_lines),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        logger.warning(f"handle_poll_answer: resposta inesperada tipus {type(answer)}: {str(answer)[:200]}")


async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona la confirmació de comanda via inline keyboard."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("auth_client:") or data.startswith("auth_admin:") or data.startswith("auth_deny:"):
        admin_id = _get_admin_user_id()
        if query.from_user.id != admin_id:
            await query.message.reply_text("❌ No autoritzat.")
            return

        parts = data.split(":")
        action = parts[0]
        target_user_id = int(parts[1])
        auth_data = _load_auth_data()
        pending = auth_data.setdefault("pending_requests", {}).pop(str(target_user_id), None)
        users = auth_data.setdefault("users", {})

        if action == "auth_client":
            client_code = int(parts[2])
            client_name = None
            if pending:
                for match in pending.get("matches", []):
                    if int(match.get("c")) == client_code:
                        client_name = match.get("n")
                        break
            users[str(target_user_id)] = {
                "role": "client",
                "client_code": client_code,
                "client_name": client_name or f"codi {client_code}",
                "granted_at": datetime.now().isoformat(timespec="seconds"),
            }
            _save_auth_data(auth_data)
            await query.edit_message_text(f"✅ Usuari autoritzat com a client: {users[str(target_user_id)]['client_name']}")
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"✅ Ja tens accés al bot com a client de {users[str(target_user_id)]['client_name']}.\n"
                    "Només podràs veure i gestionar les teves comandes."
                ),
            )
            return

        if action == "auth_admin":
            users[str(target_user_id)] = {
                "role": "admin",
                "scope": "Cal Forner",
                "granted_at": datetime.now().isoformat(timespec="seconds"),
            }
            _save_auth_data(auth_data)
            await query.edit_message_text("✅ Usuari autoritzat com a administratiu de Cal Forner")
            await context.bot.send_message(
                chat_id=target_user_id,
                text="✅ Ja tens accés administratiu al bot. Pots veure i gestionar totes les dades disponibles.",
            )
            return

        _save_auth_data(auth_data)
        await query.edit_message_text(f"❌ Accés denegat: {target_user_id}")
        await context.bot.send_message(
            chat_id=target_user_id,
            text="❌ L'administrador no ha autoritzat l'accés al bot.",
        )
        return

    if data == "confirm_no":
        context.user_data.pop("pending_confirmation", None)
        await query.edit_message_text("❌ Comanda cancel·lada.")
        return

    if data == "confirm_yes":
        pending = context.user_data.pop("pending_confirmation", None)
        if not pending:
            await query.edit_message_text("❌ No hi ha cap comanda pendent.")
            return

        try:
            await query.message.delete()
        except Exception:
            pass

        lines = pending.get("lines", [])
        results = []
        errors = []

        for line in lines:
            try:
                result = await ai.execute_order(
                    date=line["date"],
                    client=line["client"],
                    article_code=line["article_code"],
                    quantity=line["quantity"],
                    order_type=line.get("order_type", 1),
                )
                if result.get("ok"):
                    results.append(line)
                else:
                    errors.append(f"❌ {line.get('article_name', '?')}: {result.get('error', '?')}")
            except Exception as e:
                errors.append(f"❌ {line.get('article_name', '?')}: {e}")

        if results:
            client_name = results[0].get("client_name", "?")
            date_mcp = results[0].get("date", "?")
            try:
                updated_order = await mcp.veure_comanda(date_mcp, results[0]["client"])
            except Exception as e:
                updated_order = {"error": str(e)}

            if "error" in updated_order:
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=(
                        f"✅ Comanda aplicada a {client_name} ({date_mcp}).\n"
                        f"❌ No he pogut carregar l'albarà actualitzat: {updated_order['error']}"
                    ),
                )
            else:
                ticket = _format_order_ticket(
                    client_name,
                    date_mcp,
                    updated_order.get("order", []),
                )
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=ticket,
                    parse_mode="HTML",
                )

        if errors:
            await context.bot.send_message(chat_id=query.message.chat_id, text="\n".join(errors))


# ================================================================== #
#  FLUX: /afegir                                                       #
# ================================================================== #

async def af_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return ConversationHandler.END

    context.user_data.clear()
    client_code, client_name = _bound_client(update)
    if not _is_admin(update) and client_code:
        context.user_data["client"] = client_name or f"codi {client_code}"
        context.user_data["client_code"] = client_code
        await update.message.reply_text(
            f"📋 *Afegir producte a comanda*\n\n👤 Client fixat: *{context.user_data['client']}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return await _demanar_data(update)
    await update.message.reply_text(
        "📋 *Afegir producte a comanda*\n\nQuin *client*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AF_CLIENT


async def af_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    cerca_msg = await update.message.reply_text("🔍 Cercant client...")
    try:
        resultats = await mcp.cercar_client(text)  # [{"c": codi, "n": nom}]
    except Exception as e:
        logger.warning(f"af_client: cercar_client excepció: {e}")
        resultats = []
    try:
        await cerca_msg.delete()
    except Exception:
        pass

    opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
    logger.info(f"af_client: {len(opcions)} opcions per '{text}': {opcions}")

    if len(opcions) == 0:
        await update.message.reply_text(
            "❌ Client no trobat. Prova amb un nom diferent:",
            parse_mode="Markdown",
        )
        return AF_CLIENT

    text_lower = text.lower()
    coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]

    if len(coincidencies) == 1:
        name, code = coincidencies[0]
        context.user_data["client"] = name
        context.user_data["client_code"] = code
        await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
        return await _demanar_data(update)

    if len(opcions) == 1:
        name, code = opcions[0]
        context.user_data["client"] = name
        context.user_data["client_code"] = code
        await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
        return await _demanar_data(update)

    llista = coincidencies if len(coincidencies) > 1 else opcions

    if len(llista) > 8:
        await update.message.reply_text(
            f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del client:",
            parse_mode="Markdown",
        )
        return AF_CLIENT

    context.user_data["client_opcions"] = {n: c for n, c in llista}
    keyboard = [[n] for n, _ in llista]
    keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
    await update.message.reply_text(
        f"🔍 He trobat *{len(llista)}* clients per «{text}».\nSelecciona el correcte:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return AF_CLIENT_OPCIO


async def af_client_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "❌" in text or "cap d'aquests" in text.lower():
        await update.message.reply_text(
            "👤 Torna a escriure el nom del *client*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AF_CLIENT

    opcions = context.user_data.get("client_opcions", {})
    code = opcions.get(text)
    context.user_data["client"] = text
    context.user_data["client_code"] = code
    await update.message.reply_text(
        f"✅ Client: *{text}*", parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await _demanar_data(update)


async def _demanar_data(update: Update):
    await update.message.reply_text(
        "📅 Quina *data*?",
        parse_mode="Markdown",
        reply_markup=_keyboard_dates(),
    )
    return AF_DATA


async def af_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Botó "Altra data" → tornar a mostrar teclat demanant entrada manual
    if "altra data" in text.lower():
        await update.message.reply_text(
            "✏️ Escriu la data _(dd/mm/aaaa)_ o *avui*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AF_DATA

    data = _parse_data(text)
    if not data:
        await update.message.reply_text(
            "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
            parse_mode="Markdown",
            reply_markup=_keyboard_dates(),
        )
        return AF_DATA

    context.user_data["data"] = data
    await update.message.reply_text("🥖 Quin *producte*?", parse_mode="Markdown")
    return AF_PRODUCTE


async def af_producte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    cerca_msg = await update.message.reply_text("🔍 Cercant producte...")
    try:
        resultats = await mcp.cercar_article(text)  # [{"c": codi, "n": nom}]
    except Exception as e:
        logger.warning(f"af_producte: cercar_article excepció: {e}")
        resultats = []
    try:
        await cerca_msg.delete()
    except Exception:
        pass

    opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
    logger.info(f"af_producte: {len(opcions)} opcions per '{text}': {opcions}")

    if len(opcions) == 0:
        await update.message.reply_text(
            "❌ Producte no trobat. Prova amb un nom diferent:",
            parse_mode="Markdown",
        )
        return AF_PRODUCTE

    text_lower = text.lower()
    coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]

    if len(coincidencies) == 1:
        name, code = coincidencies[0]
        context.user_data["producte"] = name
        context.user_data["article_code"] = code
        await update.message.reply_text(
            f"✅ Producte: *{name}*\n\n🔢 Quina *quantitat*?",
            parse_mode="Markdown",
        )
        return AF_QUANTITAT

    if len(opcions) == 1:
        name, code = opcions[0]
        context.user_data["producte"] = name
        context.user_data["article_code"] = code
        await update.message.reply_text(
            f"✅ Producte: *{name}*\n\n🔢 Quina *quantitat*?",
            parse_mode="Markdown",
        )
        return AF_QUANTITAT

    llista = coincidencies if len(coincidencies) > 1 else opcions

    if len(llista) > 8:
        await update.message.reply_text(
            f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del producte:",
            parse_mode="Markdown",
        )
        return AF_PRODUCTE

    context.user_data["article_opcions"] = {n: c for n, c in llista}
    keyboard = [[n] for n, _ in llista]
    keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
    await update.message.reply_text(
        f"🔍 He trobat *{len(llista)}* productes per «{text}».\nSelecciona el correcte:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return AF_PRODUCTE_OPCIO


async def af_producte_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "❌" in text or "cap d'aquests" in text.lower():
        await update.message.reply_text(
            "🥖 Torna a escriure el nom del *producte*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AF_PRODUCTE

    opcions = context.user_data.get("article_opcions", {})
    code = opcions.get(text)
    context.user_data["producte"] = text
    context.user_data["article_code"] = code
    await update.message.reply_text(
        f"✅ Producte: *{text}*\n\n🔢 Quina *quantitat*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AF_QUANTITAT


async def af_quantitat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = int(update.message.text.strip())
        if q <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Introdueix un número enter positiu.")
        return AF_QUANTITAT

    context.user_data["quantitat"] = q
    d = context.user_data

    keyboard = [["✅ Afegir", "📦 Encarreg"], ["❌ Cancel·lar"]]
    await update.message.reply_text(
        f"*Confirmes?*\n\n"
        f"👤 Client:    *{d['client']}*\n"
        f"📅 Data:      *{d['data']}*\n"
        f"🥖 Producte:  *{d['producte']}*\n"
        f"🔢 Quantitat: *{q}*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return AF_CONFIRMAR


async def af_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    reply_markup = ReplyKeyboardRemove()

    if "❌" in text or "cancel" in text.lower():
        await update.message.reply_text("❌ Cancel·lat.", reply_markup=reply_markup)
        return ConversationHandler.END

    encarreg = "encarreg" in text.lower()
    order_type = 2 if encarreg else 1
    d = context.user_data

    client_code = d.get("client_code")
    article_code = d.get("article_code")
    if not client_code or not article_code:
        await update.message.reply_text(
            "❌ Error intern: no tinc els codis. Torna a iniciar /afegir.",
            reply_markup=reply_markup,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"⏳ {'Encarregant' if encarreg else 'Afegint'} *{d['producte']}* x{d['quantitat']} a *{d['client']}*...",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

    data_mcp = _to_mcp_date(d["data"])
    result = await mcp.afegir_linia_mcp(data_mcp, client_code, article_code, d["quantitat"], order_type)
    ok = result.get("ok", False)
    error = result.get("error", "Error desconegut")

    if ok:
        etiqueta = "Encarreg" if encarreg else "Afegit"
        await update.message.reply_text(
            f"✅ *{etiqueta} correctament!*\n\n"
            f"👤 {d['client']}\n"
            f"📅 {d['data']}\n"
            f"🥖 {d['producte']} × {d['quantitat']}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ Error MCP: {error}")

    return ConversationHandler.END


# ================================================================== #
#  FLUX: /veure                                                        #
# ================================================================== #

async def vr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return ConversationHandler.END

    context.user_data.clear()
    client_code, client_name = _bound_client(update)
    if not _is_admin(update) and client_code:
        context.user_data["client"] = client_name or f"codi {client_code}"
        context.user_data["client_code"] = client_code
        await update.message.reply_text(
            f"🔍 *Veure albarà*\n\n👤 Client fixat: *{context.user_data['client']}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return await _demanar_data_veure(update)
    await update.message.reply_text(
        "🔍 *Veure albarà*\n\nQuin *client*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return VR_CLIENT


async def vr_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    cerca_msg = await update.message.reply_text("🔍 Cercant client...")
    try:
        resultats = await mcp.cercar_client(text)
    except Exception as e:
        logger.warning(f"vr_client: cercar_client excepció: {e}")
        resultats = []
    try:
        await cerca_msg.delete()
    except Exception:
        pass

    opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
    logger.info(f"vr_client: {len(opcions)} opcions per '{text}': {opcions}")

    if len(opcions) == 0:
        await update.message.reply_text(
            "❌ Client no trobat. Prova amb un nom diferent:",
            parse_mode="Markdown",
        )
        return VR_CLIENT

    text_lower = text.lower()
    coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]

    if len(coincidencies) == 1:
        name, code = coincidencies[0]
        context.user_data["client"] = name
        context.user_data["client_code"] = code
        await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
        return await _demanar_data_veure(update)

    if len(opcions) == 1:
        name, code = opcions[0]
        context.user_data["client"] = name
        context.user_data["client_code"] = code
        await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
        return await _demanar_data_veure(update)

    llista = coincidencies if len(coincidencies) > 1 else opcions

    if len(llista) > 8:
        await update.message.reply_text(
            f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del client:",
            parse_mode="Markdown",
        )
        return VR_CLIENT

    context.user_data["client_opcions"] = {n: c for n, c in llista}
    keyboard = [[n] for n, _ in llista]
    keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
    await update.message.reply_text(
        f"🔍 He trobat *{len(llista)}* clients per «{text}».\nSelecciona el correcte:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return VR_CLIENT_OPCIO


async def vr_client_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "❌" in text or "cap d'aquests" in text.lower():
        await update.message.reply_text(
            "👤 Torna a escriure el nom del *client*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return VR_CLIENT

    opcions = context.user_data.get("client_opcions", {})
    code = opcions.get(text)
    context.user_data["client"] = text
    context.user_data["client_code"] = code
    await update.message.reply_text(
        f"✅ Client: *{text}*", parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return await _demanar_data_veure(update)


async def _demanar_data_veure(update: Update):
    await update.message.reply_text(
        "📅 Quina *data*?",
        parse_mode="Markdown",
        reply_markup=_keyboard_dates(),
    )
    return VR_DATA


async def vr_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "altra data" in text.lower():
        await update.message.reply_text(
            "✏️ Escriu la data _(dd/mm/aaaa)_ o *avui*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return VR_DATA

    data = _parse_data(text)
    if not data:
        await update.message.reply_text(
            "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
            parse_mode="Markdown",
            reply_markup=_keyboard_dates(),
        )
        return VR_DATA

    d = context.user_data
    client_code = d.get("client_code")
    client_name = d.get("client", "?")

    if not client_code:
        await update.message.reply_text("❌ No tinc el codi del client. Torna a iniciar /veure.")
        return ConversationHandler.END

    data_mcp = _to_mcp_date(data)
    await update.message.reply_text(
        f"⏳ Carregant albarà de *{client_name}* ({data})...",
        parse_mode="Markdown",
    )

    result = await mcp.veure_comanda(data_mcp, client_code)

    if "error" in result:
        await update.message.reply_text(f"❌ Error: {result['error']}")
    else:
        lines = result.get("order", [])
        ticket = _format_order_ticket(client_name, data, lines)
        if ticket:
            await update.message.reply_text(
                ticket,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"ℹ️ No hi ha línies per *{client_name}* el {data}.",
                parse_mode="Markdown",
            )

    return ConversationHandler.END


# ================================================================== #
#  FLUX: /esborrar                                                     #
# ================================================================== #

async def eb_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return ConversationHandler.END

    context.user_data.clear()
    client_code, client_name = _bound_client(update)
    if not _is_admin(update) and client_code:
        context.user_data["client"] = client_name or f"codi {client_code}"
        context.user_data["client_code"] = client_code
        await update.message.reply_text(
            f"🗑️ *Esborrar línia de comanda*\n\n👤 Client fixat: *{context.user_data['client']}*",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return await _demanar_data_eb(update)
    await update.message.reply_text(
        "🗑️ *Esborrar línia de comanda*\n\nQuin *client*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return EB_CLIENT


async def eb_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    cerca_msg = await update.message.reply_text("🔍 Cercant client...")
    try:
        resultats = await mcp.cercar_client(text)
    except Exception as e:
        logger.warning(f"eb_client: cercar_client excepció: {e}")
        resultats = []
    try:
        await cerca_msg.delete()
    except Exception:
        pass

    opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]

    if len(opcions) == 0:
        await update.message.reply_text("❌ Client no trobat. Prova amb un nom diferent:")
        return EB_CLIENT

    text_lower = text.lower()
    coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]
    llista = coincidencies if len(coincidencies) >= 1 else opcions

    if len(llista) == 1:
        name, code = llista[0]
        context.user_data["client"] = name
        context.user_data["client_code"] = code
        await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
        return await _demanar_data_eb(update)

    if len(llista) > 8:
        await update.message.reply_text(
            f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del client:"
        )
        return EB_CLIENT

    context.user_data["client_opcions"] = {n: c for n, c in llista}
    keyboard = [[n] for n, _ in llista]
    keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
    await update.message.reply_text(
        f"🔍 He trobat *{len(llista)}* clients per «{text}».\nSelecciona el correcte:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EB_CLIENT_OPCIO


async def eb_client_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "❌" in text or "cap d'aquests" in text.lower():
        await update.message.reply_text(
            "👤 Torna a escriure el nom del *client*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EB_CLIENT

    opcions = context.user_data.get("client_opcions", {})
    context.user_data["client"] = text
    context.user_data["client_code"] = opcions.get(text)
    await update.message.reply_text(f"✅ Client: *{text}*", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return await _demanar_data_eb(update)


async def _demanar_data_eb(update: Update):
    await update.message.reply_text("📅 Quina *data*?", parse_mode="Markdown", reply_markup=_keyboard_dates())
    return EB_DATA


async def eb_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "altra data" in text.lower():
        await update.message.reply_text(
            "✏️ Escriu la data _(dd/mm/aaaa)_ o *avui*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EB_DATA

    data = _parse_data(text)
    if not data:
        await update.message.reply_text(
            "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
            parse_mode="Markdown",
            reply_markup=_keyboard_dates(),
        )
        return EB_DATA

    context.user_data["data"] = data
    await update.message.reply_text("🥖 Quin *producte* vols esborrar?", parse_mode="Markdown")
    return EB_PRODUCTE


async def eb_producte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    cerca_msg = await update.message.reply_text("🔍 Cercant producte...")
    try:
        resultats = await mcp.cercar_article(text)
    except Exception as e:
        logger.warning(f"eb_producte: cercar_article excepció: {e}")
        resultats = []
    try:
        await cerca_msg.delete()
    except Exception:
        pass

    opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]

    if len(opcions) == 0:
        await update.message.reply_text("❌ Producte no trobat. Prova amb un nom diferent:")
        return EB_PRODUCTE

    text_lower = text.lower()
    coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]
    llista = coincidencies if len(coincidencies) >= 1 else opcions

    if len(llista) == 1:
        name, code = llista[0]
        context.user_data["producte"] = name
        context.user_data["article_code"] = code
        return await _confirmar_esborrar(update, context)

    if len(llista) > 8:
        await update.message.reply_text(
            f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del producte:"
        )
        return EB_PRODUCTE

    context.user_data["article_opcions"] = {n: c for n, c in llista}
    keyboard = [[n] for n, _ in llista]
    keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
    await update.message.reply_text(
        f"🔍 He trobat *{len(llista)}* productes per «{text}».\nSelecciona el correcte:",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EB_PRODUCTE_OPCIO


async def eb_producte_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if "❌" in text or "cap d'aquests" in text.lower():
        await update.message.reply_text(
            "🥖 Torna a escriure el nom del *producte*:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EB_PRODUCTE

    opcions = context.user_data.get("article_opcions", {})
    context.user_data["producte"] = text
    context.user_data["article_code"] = opcions.get(text)
    await update.message.reply_text(f"✅ Producte: *{text}*", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
    return await _confirmar_esborrar(update, context)


async def _confirmar_esborrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = context.user_data
    keyboard = [["🗑️ Sí, esborra", "❌ Cancel·lar"]]
    await update.message.reply_text(
        f"⚠️ *Confirmes que vols esborrar?*\n\n"
        f"👤 Client:   *{d['client']}*\n"
        f"📅 Data:     *{d['data']}*\n"
        f"🥖 Producte: *{d['producte']}*",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return EB_CONFIRMAR


async def eb_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    reply_markup = ReplyKeyboardRemove()

    if "❌" in text or "cancel" in text.lower():
        await update.message.reply_text("❌ Cancel·lat.", reply_markup=reply_markup)
        return ConversationHandler.END

    d = context.user_data
    client_code = d.get("client_code")
    article_code = d.get("article_code")

    if not client_code or not article_code:
        await update.message.reply_text("❌ Error intern: no tinc els codis. Torna a iniciar /esborrar.", reply_markup=reply_markup)
        return ConversationHandler.END

    estat_msg = await update.message.reply_text(
        f"⏳ Esborrant *{d['producte']}* de *{d['client']}*...",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )

    data_mcp = _to_mcp_date(d["data"])
    result = await mcp.afegir_linia_mcp(data_mcp, client_code, article_code, 0, 1)
    ok = result.get("ok", False)

    await _tancar_estat(estat_msg)

    if ok:
        await update.message.reply_text(
            f"🗑️ *Esborrat correctament*\n\n"
            f"👤 {d['client']}\n"
            f"📅 {d['data']}\n"
            f"🥖 {d['producte']} → 0",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(f"❌ Error MCP: {result.get('error', 'Error desconegut')}")

    return ConversationHandler.END


# ================================================================== #
#  /vendes                                                             #
# ================================================================== #

async def cmd_vendes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra les vendes agregades del dia via MCP."""
    if not autoritzat(update):
        await rebuig(update, context)
        return
    if not _is_admin(update):
        await update.message.reply_text("❌ Aquesta informació només està disponible per a usuaris administratius.")
        return

    args = context.args
    if args and len(args) >= 2:
        try:
            codi = int(args[0])
            data_str = args[1]  # YYYY-MM-DD
        except ValueError:
            await update.message.reply_text("⚠️ Ús: /vendes <codi_botiga> <YYYY-MM-DD>\nEx: /vendes 5 2026-03-31")
            return
    else:
        codi = config.SHOP_CODE if hasattr(config, 'SHOP_CODE') else None
        data_str = date.today().strftime("%Y-%m-%d")
        if not codi:
            await update.message.reply_text(
                "⚠️ Indica el codi de botiga:\n/vendes <codi> <data>\nEx: /vendes 5 2026-03-31"
            )
            return

    await update.message.reply_text(f"📊 Carregant vendes botiga {codi} ({data_str})...")
    try:
        r = await mcp.vendes_dia(codi, data_str)
    except Exception as e:
        await update.message.reply_text(f"❌ Error MCP: {e}")
        return

    tv = r.get("tv", 0)
    nt = r.get("nt", 0)
    vendes = r.get("v", [])

    if not vendes:
        await update.message.reply_text(f"ℹ️ Sense dades de vendes per botiga {codi} el {data_str}.")
        return

    linies = [f"📊 *Vendes botiga {codi} — {data_str}*", f"💰 Total: *{tv:.2f}€*  |  🧾 Tickets: *{nt}*", ""]
    for art in vendes[:30]:
        nom = art.get("n", "?")
        qty = art.get("q", 0)
        imp = art.get("i", 0)
        linies.append(f"• {nom}: {qty} u. ({imp:.2f}€)")

    await update.message.reply_text("\n".join(linies), parse_mode="Markdown")


# ================================================================== #
#  IA: Gemini (text lliure i àudio)                                    #
# ================================================================== #

# ================================================================== #
#  /imprimir — Impressió d'albarans amb còpies per client             #
# ================================================================== #

async def im_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update, context)
        return ConversationHandler.END
    if context.args:
        inline_text = " ".join(context.args).strip()
        print_text = _extract_print_text(f"imprimir {inline_text}")
        if print_text is not None:
            await _send_print_text(update, print_text)
            return ConversationHandler.END
    context.user_data.pop("im_data", None)
    context.user_data.pop("im_client", None)
    context.user_data.pop("im_clients_impresos", None)
    await update.message.reply_text(
        "🖨️ *Impressió*\n\nQue vols imprimir?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["📋 Albarans"], ["✏️ Text lliure"]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
    )
    return IM_TIPUS


async def im_tipus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "Text lliure" in text:
        await update.message.reply_text(
            "✏️ Escriu el text que vols imprimir:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return IM_TEXT
    await update.message.reply_text(
        "📋 *Albarans* — Quina data?",
        parse_mode="Markdown",
        reply_markup=_keyboard_dates(),
    )
    return IM_DATA


async def im_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    await _send_print_text(update, text)
    return ConversationHandler.END


async def im_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = _parse_data(update.message.text.strip())
    if not data:
        await update.message.reply_text("⚠️ Data no vàlida. Escriu DD/MM/AAAA o tria un botó.")
        return IM_DATA
    context.user_data["im_data"] = data
    context.user_data["im_clients_impresos"] = []
    await update.message.reply_text(
        f"📅 Data: *{data}*\n\nImprimeixo tots els clients o un de concret?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [["🖨️ Tots els clients"], ["👤 Un client concret"]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
    )
    return IM_CLIENT


async def im_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "Tots els clients" in text:
        data = context.user_data["im_data"]
        await _print_all_orders(update, data)
        return ConversationHandler.END
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    resultats = await mcp.cercar_client(text)
    if not resultats:
        await update.message.reply_text("❌ Client no trobat. Torna a intentar-ho:")
        return IM_CLIENT
    if len(resultats) == 1:
        c = resultats[0]
        context.user_data["im_client"] = {"codi": c["c"], "nom": c["n"]}
        copies = _get_copies(c["c"])
        return await _im_imprimir(update, context, copies)
    # Múltiples resultats
    botons = [[r["n"]] for r in resultats[:10]]
    context.user_data["im_resultats"] = resultats[:10]
    await update.message.reply_text(
        "Quin client?",
        reply_markup=ReplyKeyboardMarkup(botons, one_time_keyboard=True, resize_keyboard=True),
    )
    return IM_CLIENT_OPCIO


async def im_client_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    resultats = context.user_data.get("im_resultats", [])
    trobat = next((r for r in resultats if r["n"] == text), None)
    if not trobat:
        await update.message.reply_text("⚠️ Tria una opció de la llista:")
        return IM_CLIENT_OPCIO
    context.user_data["im_client"] = {"codi": trobat["c"], "nom": trobat["n"]}
    copies = _get_copies(trobat["c"])
    return await _im_imprimir(update, context, copies)


async def _im_imprimir(update: Update, context: ContextTypes.DEFAULT_TYPE, copies: int) -> int:
    """Obté la comanda del MCP i imprimeix directament a la impressora Star."""
    client = context.user_data["im_client"]
    data = context.user_data["im_data"]
    data_mcp = _to_mcp_date(data)

    estat_msg = await update.message.reply_text(
        f"🖨️ Imprimint {copies} còpia(es) de *{client['nom']}*...",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    r = await mcp.imprimir_albarans(data_mcp, client["codi"], copies)
    errors = 1 if "error" in r else 0

    impresos = context.user_data.setdefault("im_clients_impresos", [])
    impresos.append(f"{client['nom']} ×{copies}")

    try:
        if errors:
            await estat_msg.edit_text(f"⚠️ {client['nom']}: error d'impressió.")
        else:
            await estat_msg.edit_text(
                f"✅ *{client['nom']}* — {copies} còpia(es) en cua.\n\nVols imprimir un altre client?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["✅ Sí, un altre", "🏁 Acabar"]], one_time_keyboard=True, resize_keyboard=True),
            )
    except Exception:
        if errors:
            await update.message.reply_text(f"⚠️ {client['nom']}: error d'impressió.")
        else:
            await update.message.reply_text(
                f"✅ *{client['nom']}* — {copies} còpia(es) en cua.\n\nVols imprimir un altre client?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([["✅ Sí, un altre", "🏁 Acabar"]], one_time_keyboard=True, resize_keyboard=True),
            )
    return IM_SEGUENT


async def im_copies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        copies = int(text)
        if copies < 1 or copies > 10:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Escriu un número entre 1 i 10:")
        return IM_COPIES
    return await _im_imprimir(update, context, copies)


async def im_seguent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "Sí" in text or "si" in text.lower():
        await update.message.reply_text(
            "Escriu el nom del client:",
            reply_markup=ReplyKeyboardRemove(),
        )
        return IM_CLIENT

    impresos = context.user_data.get("im_clients_impresos", [])
    resum = "\n".join(f"• {x}" for x in impresos) if impresos else "—"
    await update.message.reply_text(
        f"🖨️ *Impressió completada*\n\n{resum}",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


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


# ------------------------------------------------------------------ #
#  Auto enviament programat                                            #
# ------------------------------------------------------------------ #

async def _notify_auto_all_users(bot: Bot, text: str):
    """Envia un missatge a tots els usuaris autoritzats (admin + usuaris)."""
    auth_data = _load_auth_data()
    user_ids = set(auth_data.get("authorized_users", []))
    admin_id = _get_admin_user_id()
    if admin_id:
        user_ids.add(admin_id)
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML")
        except Exception as e:
            logger.warning("No s'ha pogut notificar l'usuari %s: %s", uid, e)


async def auto_envia_comandes():
    """Executa una passada d'autoenviament per ser cridada des d'un cron extern."""
    async with Bot(token=config.TELEGRAM_TOKEN) as bot:
        await _run_auto_envia_comandes(bot)


async def _run_auto_envia_comandes(bot: Bot):
    dema = date.today() + timedelta(days=1)
    data_display = dema.strftime("%d/%m/%Y")
    data_mcp = dema.strftime("%Y-%m-%d")

    logger.info("Auto enviament: iniciant per al %s", data_display)

    try:
        resultat = await mcp.comandes_per_data(data_mcp)
    except Exception as e:
        logger.exception("Auto enviament: error MCP per al %s", data_display)
        await _notify_auto_all_users(bot, f"❌ Error auto enviament del {escape(data_display)}: {escape(str(e))}")
        return

    clients = resultat.get("clients", [])
    if not clients:
        msg = f"ℹ️ Auto enviament: no hi ha comandes per al {escape(data_display)}."
        logger.info(msg)
        await _notify_auto_all_users(bot, msg)
        return

    impresos: list[str] = []
    errors: list[str] = []
    total_copies = 0
    totals_articles: dict[str, int] = {}

    for client in clients:
        codi = client.get("codi")
        nom = client.get("nom", str(codi))
        if not codi:
            continue

        copies = _get_copies(int(codi))
        logger.info("Auto enviament: imprimint date=%s client=%s (%s) copies=%s", data_mcp, codi, nom, copies)
        try:
            result = await mcp.imprimir_albarans(data_mcp, int(codi), copies)
            if "error" in result:
                logger.warning("Auto enviament error: date=%s client=%s: %s", data_mcp, codi, result)
                errors.append(f"{nom}: error")
            else:
                impresos.append(f"{nom} x{copies}")
                total_copies += copies
                for linia in client.get("linies", []):
                    nom_art = linia.get("nm") or linia.get("artName") or linia.get("name") or str(linia.get("art", "?"))
                    qty = linia.get("requested", 0)
                    if qty > 0:
                        totals_articles[nom_art] = totals_articles.get(nom_art, 0) + qty
        except Exception as e:
            logger.exception("Auto enviament excepcio: client=%s", codi)
            errors.append(f"{nom}: {e}")

    totals_lines = sorted(totals_articles.items(), key=lambda x: x[0].lower())
    totals_txt = ""
    if totals_lines:
        totals_txt = "\n\n<b>Resum productes:</b>\n" + "\n".join(
            f"• {escape(art)}: {qty}" for art, qty in totals_lines
        )

    if errors:
        summary = (
            "⚠️ <b>Comandes enviades amb errors.</b>\n\n"
            f"📅 {escape(data_display)}\n"
            f"Clients enviats: {len(impresos)}\n"
            f"Còpies totals: {total_copies}\n\n"
            "<b>Clients enviats:</b>\n"
            + ("\n".join(f"• {item}" for item in impresos[:25]) if impresos else "- Cap")
            + "\n\n<b>Errors:</b>\n"
            + "\n".join(f"• {e}" for e in errors[:10])
            + totals_txt
        )
    else:
        summary = (
            "✅ <b>Comandes enviades automàticament.</b>\n\n"
            f"📅 {escape(data_display)}\n"
            f"Clients: {len(impresos)}\n"
            f"Còpies totals: {total_copies}\n\n"
            "<b>Clients enviats:</b>\n"
            + "\n".join(f"• {item}" for item in impresos[:25])
            + totals_txt
        )

    logger.info("Auto enviament completat: %d clients, %d copies", len(impresos), total_copies)
    await _notify_auto_all_users(bot, summary)

    if totals_lines:
        totals_dict = {art: qty for art, qty in totals_lines}
        text_escpos = _format_totals_escpos(data_display, totals_dict, len(impresos))
        await imprimir_text_directe(text_escpos)


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

def run_bot():
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

    # Flux /afegir
    afegir_handler = ConversationHandler(
        entry_points=[CommandHandler("afegir", af_start)],
        states={
            AF_CLIENT:         [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_client)],
            AF_CLIENT_OPCIO:   [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_client_opcio)],
            AF_DATA:           [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_data)],
            AF_PRODUCTE:       [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte)],
            AF_PRODUCTE_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte_opcio)],
            AF_QUANTITAT:      [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_quantitat)],
            AF_CONFIRMAR:      [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_confirmar)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )

    # Flux /veure
    veure_handler = ConversationHandler(
        entry_points=[CommandHandler("veure", vr_start)],
        states={
            VR_CLIENT:       [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client)],
            VR_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client_opcio)],
            VR_DATA:         [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_data)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )

    # Flux /esborrar
    esborrar_handler = ConversationHandler(
        entry_points=[CommandHandler("esborrar", eb_start)],
        states={
            EB_CLIENT:        [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_client)],
            EB_CLIENT_OPCIO:  [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_client_opcio)],
            EB_DATA:          [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_data)],
            EB_PRODUCTE:      [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_producte)],
            EB_PRODUCTE_OPCIO:[stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_producte_opcio)],
            EB_CONFIRMAR:     [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_confirmar)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )

    # Flux /imprimir
    imprimir_handler = ConversationHandler(
        entry_points=[CommandHandler("imprimir", im_start)],
        states={
            IM_TIPUS:        [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_tipus)],
            IM_DATA:         [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_data)],
            IM_CLIENT:       [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_client)],
            IM_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_client_opcio)],
            IM_COPIES:       [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_copies)],
            IM_SEGUENT:      [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_seguent)],
            IM_TEXT:         [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_text)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_start))
    app.add_handler(CommandHandler("vendes", cmd_vendes))
    app.add_handler(CommandHandler("imprimir_text", cmd_imprimir_text))
    app.add_handler(CommandHandler("cua_impressio", cmd_cua_impressio))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(afegir_handler)
    app.add_handler(esborrar_handler)
    app.add_handler(veure_handler)
    app.add_handler(imprimir_handler)
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact_share))
    app.add_handler(MessageHandler(filters.VOICE, ai_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_text))

    from telegram.ext import PollAnswerHandler, CallbackQueryHandler as TGCallbackQueryHandler
    app.add_handler(TGCallbackQueryHandler(handle_callback_query))
    app.add_handler(PollAnswerHandler(handle_poll_answer))

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
