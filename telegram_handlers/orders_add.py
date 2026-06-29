from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


AF_CLIENT, AF_CLIENT_OPCIO, AF_DATA, AF_PRODUCTE, AF_PRODUCTE_OPCIO, AF_QUANTITAT, AF_CONFIRMAR = range(7)


def build_afegir_handler(**deps) -> ConversationHandler:
    logger = deps["logger"]
    mcp = deps["mcp"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    bound_client = deps["bound_client"]
    keyboard_dates = deps["keyboard_dates"]
    parse_data = deps["parse_data"]
    to_mcp_date = deps["to_mcp_date"]
    normalize_search_text = deps["normalize_search_text"]
    fallback_article_options = deps["fallback_article_options"]
    manual_order_text = deps["manual_order_text"]
    manual_order_keyboard = deps["manual_order_keyboard"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]

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
        try:
            resultats = await mcp.cercar_client(text)
        except Exception as e:
            logger.warning("af_client: cercar_client excepció: %s", e)
            resultats = []
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        logger.info("af_client: %s opcions per %r: %s", len(opcions), text, opcions)

        if not opcions:
            await update.message.reply_text("❌ Client no trobat. Prova amb un nom diferent:", parse_mode="Markdown")
            return AF_CLIENT

        text_lower = text.lower()
        coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]
        if len(coincidencies) == 1:
            name, code = coincidencies[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data(update)

        if len(opcions) == 1:
            name, code = opcions[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data(update)

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
        if not resultats:
            try:
                fallback_options = await fallback_article_options(text)
                if fallback_options:
                    fallback_used = True
                    resultats = [{"n": name, "c": code} for name, code in fallback_options]
                    logger.info("af_producte: fallback cataleg per %r -> %s", text, fallback_options)
            except Exception as e:
                logger.warning("af_producte: fallback cataleg excepcio per %r: %s", text, e)
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        logger.info("af_producte: %s opcions per %r: %s", len(opcions), text, opcions)
        if not opcions:
            await update.message.reply_text("❌ Producte no trobat. Prova amb un nom diferent:", parse_mode="Markdown")
            return AF_PRODUCTE

        text_norm = normalize_search_text(text)
        coincidencies = [(n, c) for n, c in opcions if text_norm and text_norm in normalize_search_text(n)]
        if len(coincidencies) == 1:
            name, code = coincidencies[0]
            context.user_data["producte"] = name
            context.user_data["article_code"] = code
            await update.message.reply_text(f"✅ Producte: *{name}*\n\n🔢 Quina *quantitat*?", parse_mode="Markdown")
            return AF_QUANTITAT

        if len(opcions) == 1:
            name, code = opcions[0]
            context.user_data["producte"] = name
            context.user_data["article_code"] = code
            await update.message.reply_text(f"✅ Producte: *{name}*\n\n🔢 Quina *quantitat*?", parse_mode="Markdown")
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
            {k: pending.get(k) for k in ("client_code", "date_mcp", "article_code", "quantity")},
            sorted(pending["fields"]),
        )
        await update.message.reply_text(manual_order_text(pending), parse_mode="Markdown", reply_markup=manual_order_keyboard(pending))
        return ConversationHandler.END

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
            await update.message.reply_text("❌ Error intern: no tinc els codis. Torna a iniciar /afegir.", reply_markup=reply_markup)
            return ConversationHandler.END

        await update.message.reply_text(
            f"⏳ {'Encarregant' if encarreg else 'Afegint'} *{d['producte']}* x{d['quantitat']} a *{d['client']}*...",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        result = await mcp.afegir_linia_mcp(to_mcp_date(d["data"]), client_code, article_code, d["quantitat"], order_type)
        if result.get("ok", False):
            etiqueta = "Encarreg" if encarreg else "Afegit"
            await update.message.reply_text(
                f"✅ *{etiqueta} correctament!*\n\n👤 {d['client']}\n📅 {d['data']}\n🥖 {d['producte']} × {d['quantitat']}",
                parse_mode="Markdown",
            )
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
