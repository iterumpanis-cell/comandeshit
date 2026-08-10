import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


AF_CLIENT, AF_CLIENT_OPCIO, AF_DATA, AF_PRODUCTE, AF_PRODUCTE_OPCIO, AF_QUANTITAT, AF_CONFIRMAR = range(7)


def build_afegir_handler(**deps) -> ConversationHandler:
    logger = deps["logger"]
    mcp = deps["mcp"]
    order_service = deps["order_service"]
    client_resolver = deps["client_resolver"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    bound_client = deps["bound_client"]
    keyboard_dates = deps["keyboard_dates"]
    parse_data = deps["parse_data"]
    to_mcp_date = deps["to_mcp_date"]
    normalize_search_text = deps["normalize_search_text"]
    rank_article_options = deps["rank_article_options"]
    fallback_article_options = deps["fallback_article_options"]
    manual_order_text = deps["manual_order_text"]
    manual_order_keyboard = deps["manual_order_keyboard"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]
    get_copies = deps.get("get_copies")
    auto_sender = deps.get("auto_sender")

    async def existing_special_order(data_mcp: str, client_code: int, article_code: int) -> dict | None:
        linies = await mcp.linies_article_comanda(data_mcp, client_code, article_code)
        for line in linies:
            if int(line.get("order_type", 1) or 1) != 2:
                continue
            if line.get("requested", 0) or line.get("served", 0) or line.get("returned", 0):
                return line
        return None

    def duplicate_text(pending: dict) -> str:
        existing = int(pending.get("existing_quantity", 0) or 0)
        new = int(pending.get("new_quantity", pending.get("quantity", 0)) or 0)
        return (
            "⚠️ *Aquest producte ja té un encàrrec.*\n\n"
            f"👤 Client: *{pending.get('client_name')}*\n"
            f"📅 Data: *{pending.get('date_display')}*\n"
            f"🥖 Producte: *{pending.get('article_name')}*\n"
            f"📦 Encàrrec existent: *{existing}*\n"
            f"➕ Nou encàrrec escrit: *{new}*\n\n"
            "Quina quantitat total vols deixar?"
        )

    def duplicate_keyboard(pending: dict) -> ReplyKeyboardMarkup:
        existing = int(pending.get("existing_quantity", 0) or 0)
        new = int(pending.get("new_quantity", pending.get("quantity", 0)) or 0)
        return ReplyKeyboardMarkup(
            [[f"✅ Deixar {existing + new} en total", f"↩️ Substituir per {new}"], ["❌ Cancel·lar"]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )

    def printed_before(data_mcp: str, client_code: int) -> bool:
        if not auto_sender:
            return False
        state = auto_sender.read_state(data_mcp) or {}
        client_state = (state.get("clients") or {}).get(str(client_code)) or {}
        return client_state.get("status") == "success"

    async def warn_printed_if_needed(message, data_mcp: str, client_code: int, client_name: str,
                                    date_display: str, article_name: str, quantity: int) -> None:
        if not printed_before(data_mcp, client_code):
            return
        copies = get_copies(client_code) if get_copies else 2
        await message.reply_text(
            "⚠️ *Has modificat una comanda ja impresa.*\n\n"
            f"👤 Client: *{client_name}*\n"
            f"📅 Data: *{date_display}*\n"
            f"🥖 Producte: *{article_name}*\n"
            f"🔢 Quantitat final: *{quantity}*\n\n"
            f"Cal reimprimir l'albarà amb les còpies configurades del client: *{copies}*.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🖨️ Reimprimir albarà ({copies} còpia/es)", callback_data=f"reprint_order:{data_mcp}:{client_code}")
            ]]),
        )

    async def af_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autoritzat(update):
            await rebuig(update, context)
            return ConversationHandler.END

        context.user_data.clear()
        client_code, client_name = bound_client(update)
        if not is_admin(update) and client_code:
            context.user_data["client"] = client_name or f"codi {client_code}"
            context.user_data["client_code"] = client_code
            await update.message.reply_text(
                f"📋 *Afegir producte a comanda*\n\n👤 Client fixat: *{context.user_data['client']}*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return await demanar_data(update)
        await update.message.reply_text(
            "📋 *Afegir producte a comanda*\n\nQuin *client*?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return AF_CLIENT

    async def af_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        cerca_msg = await update.message.reply_text("🔍 Cercant client...")
        resultats = [{"n": name, "c": code} for name, code in await client_resolver.resolve(text)]
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        logger.info("af_client: %s opcions per %r: %s", len(opcions), text, opcions)

        if not opcions:
            await update.message.reply_text("❌ Client no trobat. Prova amb un nom diferent:", parse_mode="Markdown")
            return AF_CLIENT

        text_norm = normalize_search_text(text)
        exactes = [(n, c) for n, c in opcions if normalize_search_text(n) == text_norm]
        coincidencies = [(n, c) for n, c in opcions if text_norm and text_norm in normalize_search_text(n)]
        if len(exactes) == 1:
            name, code = exactes[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data(update)

        llista = coincidencies if coincidencies else opcions
        llista = llista[:8]

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
        await update.message.reply_text(f"✅ Client: *{text}*", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return await demanar_data(update)

    async def demanar_data(update: Update):
        await update.message.reply_text("📅 Quina *data*?", parse_mode="Markdown", reply_markup=keyboard_dates())
        return AF_DATA

    async def af_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if "altra data" in text.lower():
            await update.message.reply_text(
                "✏️ Escriu la data _(dd/mm/aaaa)_ o *avui*:",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return AF_DATA

        data = parse_data(text)
        if not data:
            await update.message.reply_text(
                "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
                parse_mode="Markdown",
                reply_markup=keyboard_dates(),
            )
            return AF_DATA

        context.user_data["data"] = data
        await update.message.reply_text("🥖 Quin *producte*?", parse_mode="Markdown")
        return AF_PRODUCTE

    async def af_producte(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        cerca_msg = await update.message.reply_text("🔍 Cercant producte...")
        try:
            resultats = await mcp.cercar_article(text)
        except Exception as e:
            logger.warning("af_producte: cercar_article excepció: %s", e)
            resultats = []
        fallback_used = False
        query_tokens = set(normalize_search_text(text).split())
        structural_query = query_tokens.intersection({"1/2", "1/4", "kg", "rodo", "xusco"})
        if not resultats or structural_query:
            try:
                fallback_options = await fallback_article_options(text)
                if fallback_options:
                    fallback_used = not resultats
                    resultats = resultats + [
                        {"n": name, "c": code}
                        for name, code in fallback_options
                        if not any(existing.get("c") == code for existing in resultats)
                    ]
                    logger.info("af_producte: fallback cataleg per %r -> %s", text, fallback_options)
            except Exception as e:
                logger.warning("af_producte: fallback cataleg excepcio per %r: %s", text, e)
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        opcions = rank_article_options(text, opcions)
        logger.info("af_producte: %s opcions per %r: %s", len(opcions), text, opcions)
        if not opcions:
            await update.message.reply_text("❌ Producte no trobat. Prova amb un nom diferent:", parse_mode="Markdown")
            return AF_PRODUCTE

        text_norm = normalize_search_text(text)
        coincidencies = [(n, c) for n, c in opcions if text_norm and text_norm in normalize_search_text(n)]
        exactes = [(n, c) for n, c in opcions if normalize_search_text(n) == text_norm]
        if len(exactes) == 1 and len(coincidencies) == 1:
            name, code = exactes[0]
            context.user_data["producte"] = name
            context.user_data["article_code"] = code
            logger.info("af_producte: auto seleccionat %r -> (%s, %s)", text, name, code)
            await update.message.reply_text(f"✅ Producte: *{name}*\n\n🔢 Quina *quantitat*?", parse_mode="Markdown")
            return AF_QUANTITAT

        # Amb mides/tipus, el rànquing per paraules clau és més fiable que exigir
        # que tota la frase aparegui seguida al nom de l'article.
        llista = opcions if structural_query else (coincidencies if coincidencies else opcions)
        llista = llista[:8]

        context.user_data["article_opcions"] = {n: c for n, c in llista}
        keyboard = [[n] for n, _ in llista]
        keyboard.append(["❌ Cap d'aquests (tornar a escriure)"])
        await update.message.reply_text(
            f"🔍 He trobat *{len(llista)}* productes {'semblants ' if fallback_used else ''}per «{text}».\nSelecciona el correcte:",
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
        logger.info("af_producte_opcio: seleccionat %r -> (%s, %s)", text, text, code)
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

        d = context.user_data
        pending = {
            "mode": "add",
            "client_name": d["client"],
            "client_code": d["client_code"],
            "date_display": d["data"],
            "date_mcp": to_mcp_date(d["data"]),
            "article_name": d["producte"],
            "article_code": d["article_code"],
            "quantity": q,
            "fields": {"requested", "served"},
        }
        context.user_data["quantitat"] = q
        context.user_data["pending_order_edit"] = pending
        logger.info(
            "manual_order_prompt mode=add user=%s pending=%s fields=%s",
            update.effective_user.id if update.effective_user else None,
            {k: pending.get(k) for k in ("client_code", "date_mcp", "article_name", "article_code", "quantity")},
            sorted(pending["fields"]),
        )
        await update.message.reply_text(manual_order_text(pending), parse_mode="Markdown", reply_markup=manual_order_keyboard(pending))
        return AF_CONFIRMAR

    async def af_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text
        reply_markup = ReplyKeyboardRemove()
        text_lower = text.lower()

        duplicate_pending = context.user_data.get("pending_duplicate_special_order")
        if duplicate_pending:
            if "❌" in text or "cancel" in text_lower:
                context.user_data.pop("pending_duplicate_special_order", None)
                await update.message.reply_text("❌ Cancel·lat.", reply_markup=reply_markup)
                return ConversationHandler.END
            existing = int(duplicate_pending.get("existing_quantity", 0) or 0)
            new = int(duplicate_pending.get("new_quantity", 0) or 0)
            if "substituir" in text_lower:
                total_quantity = new
            else:
                match = re.search(r"\d+", text)
                if not match:
                    await update.message.reply_text(
                        "⚠️ Escriu la quantitat total o tria un botó.",
                        reply_markup=duplicate_keyboard(duplicate_pending),
                    )
                    return AF_CONFIRMAR
                total_quantity = int(match.group(0))
            if total_quantity <= 0:
                await update.message.reply_text("⚠️ La quantitat total ha de ser positiva.")
                return AF_CONFIRMAR
            context.user_data.pop("pending_duplicate_special_order", None)
            context.user_data["quantitat"] = total_quantity
            context.user_data["duplicate_special_order_summary"] = {
                "existing": existing,
                "new": new,
                "total": total_quantity,
            }
            text_lower = "encarreg"

        if "altre" in text_lower and ("sí" in text_lower or "si" in text_lower):
            for key in ("producte", "article_code", "quantitat", "pending_order_edit"):
                context.user_data.pop(key, None)
            await update.message.reply_text("🥖 Quin *producte*?", parse_mode="Markdown", reply_markup=reply_markup)
            return AF_PRODUCTE

        if "acabar" in text_lower:
            context.user_data.clear()
            await update.message.reply_text("🏁 Procés acabat.", reply_markup=reply_markup)
            return ConversationHandler.END

        if "❌" in text or "cancel" in text.lower():
            await update.message.reply_text("❌ Cancel·lat.", reply_markup=reply_markup)
            return ConversationHandler.END

        encarreg = "encarreg" in text_lower
        order_type = 2 if encarreg else 1
        d = context.user_data
        client_code = d.get("client_code")
        article_code = d.get("article_code")
        if not client_code or not article_code:
            await update.message.reply_text("❌ Error intern: no tinc els codis. Torna a iniciar /afegir.", reply_markup=reply_markup)
            return ConversationHandler.END

        data_mcp = to_mcp_date(d["data"])
        if encarreg and not context.user_data.get("duplicate_special_order_summary"):
            existing = await existing_special_order(data_mcp, client_code, article_code)
            if existing:
                existing_quantity = int(existing.get("served", existing.get("requested", 0)) or 0)
                pending_duplicate = {
                    **context.user_data.get("pending_order_edit", {}),
                    "client_name": d["client"],
                    "client_code": client_code,
                    "date_display": d["data"],
                    "date_mcp": data_mcp,
                    "article_name": d["producte"],
                    "article_code": article_code,
                    "quantity": d["quantitat"],
                    "new_quantity": d["quantitat"],
                    "existing_quantity": existing_quantity,
                }
                context.user_data["pending_duplicate_special_order"] = pending_duplicate
                await update.message.reply_text(
                    duplicate_text(pending_duplicate),
                    parse_mode="Markdown",
                    reply_markup=duplicate_keyboard(pending_duplicate),
                )
                return AF_CONFIRMAR

        await update.message.reply_text(
            f"⏳ {'Encarregant' if encarreg else 'Afegint'} *{d['producte']}* x{d['quantitat']} a *{d['client']}*...",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        result = await order_service.add_line(data_mcp, client_code, article_code, d["quantitat"], order_type)
        if result.get("ok", False):
            etiqueta = "Encarreg" if encarreg else "Afegit"
            duplicate_summary = context.user_data.pop("duplicate_special_order_summary", None)
            await update.message.reply_text(
                f"✅ *{etiqueta} correctament!*\n\n👤 {d['client']}\n📅 {d['data']}\n🥖 {d['producte']} × {d['quantitat']}\n\n"
                + (
                    f"📦 Encàrrec anterior: {duplicate_summary['existing']}\n"
                    f"➕ Nou encàrrec: {duplicate_summary['new']}\n"
                    f"🔢 Total deixat: {duplicate_summary['total']}\n\n"
                    if duplicate_summary else ""
                )
                + "Vols afegir algun producte més al mateix client i data?",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup(
                    [["✅ Sí, un altre", "🏁 Acabar"]],
                    one_time_keyboard=True,
                    resize_keyboard=True,
                ),
            )
            await warn_printed_if_needed(update.message, data_mcp, client_code, d["client"], d["data"], d["producte"], d["quantitat"])
        else:
            await update.message.reply_text(f"❌ Error MCP: {result.get('error', 'Error desconegut')}")
        return ConversationHandler.END

    stop_handler_msg = MessageHandler(filters.TEXT & filters.Regex(r"(?i)^stop$"), cmd_stop)
    return ConversationHandler(
        entry_points=[CommandHandler("afegir", af_start)],
        states={
            AF_CLIENT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_client)],
            AF_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_client_opcio)],
            AF_DATA: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_data)],
            AF_PRODUCTE: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte)],
            AF_PRODUCTE_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_producte_opcio)],
            AF_QUANTITAT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_quantitat)],
            AF_CONFIRMAR: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, af_confirmar)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
