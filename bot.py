"""
bot.py — Bot de Telegram per a la gestió de comandes HitSystems
Tot via MCP: cerca clients, cerca articles, afegir (normal i encarrec), veure comanda.
"""
import logging
import re
from datetime import date, datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
from mcp_vendes import MCPVendes

# ------------------------------------------------------------------ #
#  Logging                                                             #
# ------------------------------------------------------------------ #
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Estats de la conversa                                              #
# ------------------------------------------------------------------ #
(
    AF_CLIENT, AF_CLIENT_OPCIO, AF_DATA, AF_PRODUCTE, AF_PRODUCTE_OPCIO, AF_QUANTITAT, AF_CONFIRMAR,
    VR_CLIENT, VR_CLIENT_OPCIO, VR_DATA,
) = range(10)

# Instància global MCP
mcp = MCPVendes()


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


# ------------------------------------------------------------------ #
#  Autenticació                                                        #
# ------------------------------------------------------------------ #
def autoritzat(update: Update) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return update.effective_user.id in config.ALLOWED_USER_IDS


def rebuig(update: Update):
    uid = update.effective_user.id
    logger.warning(f"Accés denegat a user_id={uid}")
    return update.message.reply_text("❌ No autoritzat.")


# ------------------------------------------------------------------ #
#  /start i /ajuda                                                     #
# ------------------------------------------------------------------ #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update)
        return

    uid = update.effective_user.id
    logger.info(f"Usuari connectat: {update.effective_user.full_name} (id={uid})")

    text = (
        "🥐 *Bot de Gestió de Comandes*\n\n"
        "Comandes disponibles:\n"
        "• /afegir — Afegir producte a una comanda\n"
        "• /veure  — Veure albarà d'un client\n"
        "• /cancel — Cancel·lar operació en curs\n"
        "• /ajuda  — Mostrar aquesta ajuda\n\n"
        f"El teu ID de Telegram és: `{uid}`"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ================================================================== #
#  FLUX: /afegir                                                       #
# ================================================================== #

async def af_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not autoritzat(update):
        await rebuig(update)
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "📋 *Afegir producte a comanda*\n\nQuin *client*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return AF_CLIENT


async def af_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    await update.message.reply_text("🔍 Cercant client...", parse_mode="Markdown")
    try:
        resultats = await mcp.cercar_client(text)  # [{"c": codi, "n": nom}]
    except Exception as e:
        logger.warning(f"af_client: cercar_client excepció: {e}")
        resultats = []

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

    await update.message.reply_text("🔍 Cercant producte...", parse_mode="Markdown")
    try:
        resultats = await mcp.cercar_article(text)  # [{"c": codi, "n": nom}]
    except Exception as e:
        logger.warning(f"af_producte: cercar_article excepció: {e}")
        resultats = []

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
        await rebuig(update)
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "🔍 *Veure albarà*\n\nQuin *client*?",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return VR_CLIENT


async def vr_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    await update.message.reply_text("🔍 Cercant client...", parse_mode="Markdown")
    try:
        resultats = await mcp.cercar_client(text)
    except Exception as e:
        logger.warning(f"vr_client: cercar_client excepció: {e}")
        resultats = []

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
        linies_text = []
        for l in lines:
            requested = l.get("requested", 0)
            if not requested:
                continue
            name = l.get("nm") or l.get("artName") or l.get("name") or str(l.get("art", "?"))
            order_type = l.get("order_type", 1)
            prefix = "📦 " if order_type == 2 else ""
            linies_text.append(f"{prefix}{name} \u00d7{requested}")

        if linies_text:
            cos = "\n".join(linies_text)
            await update.message.reply_text(
                f"📄 *{client_name} — {data}*\n\n{cos[:3500]}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"ℹ️ No hi ha línies per *{client_name}* el {data}.",
                parse_mode="Markdown",
            )

    return ConversationHandler.END


# ================================================================== #
#  /vendes                                                             #
# ================================================================== #

async def cmd_vendes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra les vendes agregades del dia via MCP."""
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
#  Cancel·lar qualsevol flux                                           #
# ================================================================== #

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operació cancel·lada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ------------------------------------------------------------------ #
#  Main                                                                #
# ------------------------------------------------------------------ #

def main():
    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .build()
    )

    # Flux /afegir
    afegir_handler = ConversationHandler(
        entry_points=[CommandHandler("afegir", af_start)],
        states={
            AF_CLIENT:         [MessageHandler(filters.TEXT & ~filters.COMMAND, af_client)],
            AF_CLIENT_OPCIO:   [MessageHandler(filters.TEXT & ~filters.COMMAND, af_client_opcio)],
            AF_DATA:           [MessageHandler(filters.TEXT & ~filters.COMMAND, af_data)],
            AF_PRODUCTE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte)],
            AF_PRODUCTE_OPCIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte_opcio)],
            AF_QUANTITAT:      [MessageHandler(filters.TEXT & ~filters.COMMAND, af_quantitat)],
            AF_CONFIRMAR:      [MessageHandler(filters.TEXT & ~filters.COMMAND, af_confirmar)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    # Flux /veure
    veure_handler = ConversationHandler(
        entry_points=[CommandHandler("veure", vr_start)],
        states={
            VR_CLIENT:       [MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client)],
            VR_CLIENT_OPCIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client_opcio)],
            VR_DATA:         [MessageHandler(filters.TEXT & ~filters.COMMAND, vr_data)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ajuda", cmd_start))
    app.add_handler(CommandHandler("vendes", cmd_vendes))
    app.add_handler(afegir_handler)
    app.add_handler(veure_handler)

    logger.info("=" * 50)
    logger.info("  Bot HitSystems actiu. Ctrl+C per aturar.")
    logger.info("=" * 50)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
