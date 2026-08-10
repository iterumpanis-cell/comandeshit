from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


EB_CLIENT, EB_CLIENT_OPCIO, EB_DATA, EB_PRODUCTE, EB_PRODUCTE_OPCIO, EB_QUANTITAT, EB_CONFIRMAR = range(7)


def build_esborrar_handler(**deps) -> ConversationHandler:
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
    manual_order_text = deps["manual_order_text"]
    manual_order_keyboard = deps["manual_order_keyboard"]
    html_update_line = deps["html_update_line"]
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
        resultats = [{"n": name, "c": code} for name, code in await client_resolver.resolve(text)]
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        if not opcions:
            await update.message.reply_text("❌ Client no trobat. Prova amb un nom diferent:")
            return EB_CLIENT

        text_norm = normalize_search_text(text)
        exactes = [(n, c) for n, c in opcions if normalize_search_text(n) == text_norm]
        coincidencies = [(n, c) for n, c in opcions if text_norm and text_norm in normalize_search_text(n)]
        if len(exactes) == 1:
            name, code = exactes[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data_eb(update)

        llista = (coincidencies if coincidencies else opcions)[:8]

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
            data_mcp = to_mcp_date(context.user_data["data"])
            order_result = await mcp.veure_comanda(data_mcp, int(context.user_data["client_code"]))
            resultats = []
            active_lines = [
                line for line in order_result.get("order", [])
                if line.get("requested", 0) or line.get("served", 0) or line.get("returned", 0)
            ]
            counts = {}
            for line in active_lines:
                key = (line.get("art") or line.get("article_code"), line.get("nm") or line.get("name"))
                counts[key] = counts.get(key, 0) + 1
            for line in active_lines:
                name = line.get("nm") or line.get("name")
                code = line.get("art") or line.get("article_code")
                if name and code is not None:
                    order_type = int(line.get("order_type", 1) or 1)
                    label = str(name)
                    if counts.get((code, name), 0) > 1:
                        label = f"{name} [tipus {order_type}, D{line.get('requested', 0)}/S{line.get('served', 0)}/T{line.get('returned', 0)}]"
                    resultats.append({
                        "n": label,
                        "c": code,
                        "order_type": order_type,
                        "requested": int(line.get("requested", 0) or 0),
                        "served": int(line.get("served", 0) or 0),
                        "returned": int(line.get("returned", 0) or 0),
                    })
        except Exception as e:
            logger.warning("eb_producte: veure_comanda excepció: %s", e)
            resultats = []
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        context.user_data["article_lines"] = {str(r["n"]): r for r in resultats if r.get("n") is not None}
        if not opcions:
            await update.message.reply_text("❌ Producte no trobat. Prova amb un nom diferent:")
            return EB_PRODUCTE

        text_norm = normalize_search_text(text)
        exactes = [(n, c) for n, c in opcions if normalize_search_text(n) == text_norm]
        coincidencies = [(n, c) for n, c in opcions if text_norm and text_norm in normalize_search_text(n)]
        if len(exactes) == 1:
            name, code = exactes[0]
            context.user_data["producte"] = name
            context.user_data["article_code"] = code
            context.user_data["article_line"] = context.user_data.get("article_lines", {}).get(name, {})
            return await demanar_quantitat_esborrar(update, context)

        llista = (coincidencies if coincidencies else opcions)[:8]

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
        context.user_data["article_line"] = context.user_data.get("article_lines", {}).get(text, {})
        await update.message.reply_text(f"✅ Producte: *{text}*", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return await demanar_quantitat_esborrar(update, context)

    async def demanar_quantitat_esborrar(update: Update, context: ContextTypes.DEFAULT_TYPE):
        line = context.user_data.get("article_line", {})
        await update.message.reply_text(
            f"📋 *Línia seleccionada*\n\n"
            f"👤 {context.user_data['client']}\n"
            f"📅 {context.user_data['data']}\n"
            f"🥖 {context.user_data['producte']}\n"
            f"📌 Demanat: *{line.get('requested', 0)}*  "
            f"Servit: *{line.get('served', 0)}*  "
            f"Tornat: *{line.get('returned', 0)}*\n"
            f"🔖 Tipus: *{line.get('order_type', 1)}*\n\n"
            "Quina quantitat final vols que quedi?\n"
            "Escriu *0* per esborrar completament la línia.",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EB_QUANTITAT

    async def eb_quantitat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            quantity = int(update.message.text.strip())
            if quantity < 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Escriu un número enter igual o superior a 0.")
            return EB_QUANTITAT

        if quantity == 0:
            return await confirmar_esborrar(update, context)

        d = context.user_data
        line = d.get("article_line", {})
        pending = {
            "client_name": d["client"],
            "date_display": d["data"],
            "article_name": d["producte"],
            "article_code": d["article_code"],
            "requested": int(line.get("requested", 0) or 0),
            "served": int(line.get("served", 0) or 0),
            "returned": int(line.get("returned", 0) or 0),
            "order_type": int(line.get("order_type", 1) or 1),
        }
        estat_msg = await update.message.reply_text(
            f"🧠💭 Actualitzant *{d['producte']}* a Demanat {quantity} i Servit {quantity}...",
            parse_mode="Markdown",
        )
        result = await html_update_line(pending, quantity)
        try:
            await estat_msg.delete()
        except Exception:
            pass
        if result.get("ok"):
            await update.message.reply_text(
                f"✅ Línia actualitzada.\n\n👤 {d['client']}\n📅 {d['data']}\n"
                f"🥖 {d['producte']}\n📌 Demanat: {quantity} · Servit: {quantity}",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await update.message.reply_text(f"❌ No s'ha pogut actualitzar la línia: {result.get('error', 'Error desconegut')}")
        return ConversationHandler.END

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
            "requested": int(d.get("article_line", {}).get("requested", 0) or 0),
            "served": int(d.get("article_line", {}).get("served", 0) or 0),
            "returned": int(d.get("article_line", {}).get("returned", 0) or 0),
            "order_type": int(d.get("article_line", {}).get("order_type", 1) or 1),
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
            f"🧠💭 Preparant l'esborrament de *{d['producte']}* de *{d['client']}*...",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )
        data_mcp = to_mcp_date(d["data"])
        linies = await mcp.linies_article_comanda(data_mcp, client_code, article_code)
        linies_actives = [line for line in linies if line.get("requested", 0) or line.get("served", 0) or line.get("returned", 0)]
        candidates = linies_actives or linies
        order_type = int(candidates[0].get("order_type", 1) or 1) if len(candidates) == 1 else 1
        result = await order_service.cancel_line(data_mcp, client_code, article_code, order_type)
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
            EB_QUANTITAT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_quantitat)],
            EB_CONFIRMAR: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, eb_confirmar)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
