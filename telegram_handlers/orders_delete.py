from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


EB_CLIENT, EB_CLIENT_OPCIO, EB_DATA, EB_PRODUCTE, EB_PRODUCTE_OPCIO, EB_CONFIRMAR = range(6)


def build_esborrar_handler(**deps) -> ConversationHandler:
    logger = deps["logger"]
    mcp = deps["mcp"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    bound_client = deps["bound_client"]
    keyboard_dates = deps["keyboard_dates"]
    parse_data = deps["parse_data"]
    to_mcp_date = deps["to_mcp_date"]
    manual_order_text = deps["manual_order_text"]
    manual_order_keyboard = deps["manual_order_keyboard"]
    tancar_estat = deps["tancar_estat"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]

    async def eb_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autoritzat(update):
            await rebuig(update, context)
            return ConversationHandler.END

        context.user_data.clear()
        client_code, client_name = bound_client(update)
        if not is_admin(update) and client_code:
            context.user_data["client"] = client_name or f"codi {client_code}"
            context.user_data["client_code"] = client_code
            await update.message.reply_text(
                f"🗑️ *Esborrar línia de comanda*\n\n👤 Client fixat: *{context.user_data['client']}*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return await demanar_data_eb(update)
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
            logger.warning("eb_client: cercar_client excepció: %s", e)
            resultats = []
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        if not opcions:
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
            return await demanar_data_eb(update)

        if len(llista) > 8:
            await update.message.reply_text(f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del client:")
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
        return await demanar_data_eb(update)

    async def demanar_data_eb(update: Update):
        await update.message.reply_text("📅 Quina *data*?", parse_mode="Markdown", reply_markup=keyboard_dates())
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

        data = parse_data(text)
        if not data:
            await update.message.reply_text(
                "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
                parse_mode="Markdown",
                reply_markup=keyboard_dates(),
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
            logger.warning("eb_producte: cercar_article excepció: %s", e)
            resultats = []
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        if not opcions:
            await update.message.reply_text("❌ Producte no trobat. Prova amb un nom diferent:")
            return EB_PRODUCTE

        text_lower = text.lower()
        coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]
        llista = coincidencies if len(coincidencies) >= 1 else opcions
        if len(llista) == 1:
            name, code = llista[0]
            context.user_data["producte"] = name
            context.user_data["article_code"] = code
            return await confirmar_esborrar(update, context)

        if len(llista) > 8:
            await update.message.reply_text(f"⚠️ Massa resultats ({len(llista)}) per «{text}».\nConcreta millor el nom del producte:")
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
        return await confirmar_esborrar(update, context)

    async def confirmar_esborrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        d = context.user_data
        pending = {
            "mode": "delete",
            "client_name": d["client"],
            "client_code": d["client_code"],
            "date_display": d["data"],
            "date_mcp": to_mcp_date(d["data"]),
            "article_name": d["producte"],
            "article_code": d["article_code"],
            "quantity": 0,
            "fields": {"requested", "served", "returned"},
        }
        context.user_data["pending_order_edit"] = pending
        logger.info(
            "manual_order_prompt mode=delete user=%s pending=%s fields=%s",
            update.effective_user.id if update.effective_user else None,
            {k: pending.get(k) for k in ("client_code", "date_mcp", "article_code", "quantity")},
            sorted(pending["fields"]),
        )
        await update.message.reply_text(manual_order_text(pending), parse_mode="Markdown", reply_markup=manual_order_keyboard(pending))
        return ConversationHandler.END

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
        data_mcp = to_mcp_date(d["data"])
        linies = await mcp.linies_article_comanda(data_mcp, client_code, article_code)
        linies_actives = [line for line in linies if line.get("requested", 0) or line.get("served", 0) or line.get("returned", 0)]
        candidates = linies_actives or linies
        order_type = int(candidates[0].get("order_type", 1) or 1) if len(candidates) == 1 else 1
        result = await mcp.canviar_linia_mcp(
            data_mcp,
            client_code,
            article_code,
            order_type,
            requested_quantity=0,
            served_quantity=0,
            returned_quantity=0,
        )
        await tancar_estat(estat_msg)
        if result.get("ok", False):
            await update.message.reply_text(
                f"🗑️ *Esborrat correctament*\n\n👤 {d['client']}\n📅 {d['data']}\n🥖 {d['producte']} → 0",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(f"❌ Error MCP: {result.get('error', 'Error desconegut')}")
        return ConversationHandler.END

    stop_handler_msg = MessageHandler(filters.TEXT & filters.Regex(r"(?i)^stop$"), cmd_stop)
    return ConversationHandler(
        entry_points=[CommandHandler("esborrar", eb_start)],
        states={
            EB_CLIENT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_client)],
            EB_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_client_opcio)],
            EB_DATA: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_data)],
            EB_PRODUCTE: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_producte)],
            EB_PRODUCTE_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_producte_opcio)],
            EB_CONFIRMAR: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_confirmar)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
