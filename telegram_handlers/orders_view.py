from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


VR_CLIENT, VR_CLIENT_OPCIO, VR_DATA = range(3)


def build_veure_handler(**deps) -> ConversationHandler:
    logger = deps["logger"]
    mcp = deps["mcp"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    bound_client = deps["bound_client"]
    keyboard_dates = deps["keyboard_dates"]
    parse_data = deps["parse_data"]
    to_mcp_date = deps["to_mcp_date"]
    format_order_ticket = deps["format_order_ticket"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]

    async def vr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autoritzat(update):
            await rebuig(update, context)
            return ConversationHandler.END

        context.user_data.clear()
        client_code, client_name = bound_client(update)
        if not is_admin(update) and client_code:
            context.user_data["client"] = client_name or f"codi {client_code}"
            context.user_data["client_code"] = client_code
            await update.message.reply_text(
                f"🔍 *Veure albarà*\n\n👤 Client fixat: *{context.user_data['client']}*",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardRemove(),
            )
            return await demanar_data_veure(update)
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
            logger.warning("vr_client: cercar_client excepció: %s", e)
            resultats = []
        try:
            await cerca_msg.delete()
        except Exception:
            pass

        opcions = [(r["n"], r["c"]) for r in resultats if "n" in r and "c" in r]
        logger.info("vr_client: %s opcions per %r: %s", len(opcions), text, opcions)
        if not opcions:
            await update.message.reply_text("❌ Client no trobat. Prova amb un nom diferent:", parse_mode="Markdown")
            return VR_CLIENT

        text_lower = text.lower()
        coincidencies = [(n, c) for n, c in opcions if text_lower in n.lower()]
        if len(coincidencies) == 1:
            name, code = coincidencies[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data_veure(update)

        if len(opcions) == 1:
            name, code = opcions[0]
            context.user_data["client"] = name
            context.user_data["client_code"] = code
            await update.message.reply_text(f"✅ Client: *{name}*", parse_mode="Markdown")
            return await demanar_data_veure(update)

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
        context.user_data["client"] = text
        context.user_data["client_code"] = opcions.get(text)
        await update.message.reply_text(f"✅ Client: *{text}*", parse_mode="Markdown", reply_markup=ReplyKeyboardRemove())
        return await demanar_data_veure(update)

    async def demanar_data_veure(update: Update):
        await update.message.reply_text("📅 Quina *data*?", parse_mode="Markdown", reply_markup=keyboard_dates())
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

        data = parse_data(text)
        if not data:
            await update.message.reply_text(
                "⚠️ Format incorrecte. Usa *dd/mm/aaaa* o escriu *avui*.",
                parse_mode="Markdown",
                reply_markup=keyboard_dates(),
            )
            return VR_DATA

        d = context.user_data
        client_code = d.get("client_code")
        client_name = d.get("client", "?")
        if not client_code:
            await update.message.reply_text("❌ No tinc el codi del client. Torna a iniciar /veure.")
            return ConversationHandler.END

        data_mcp = to_mcp_date(data)
        await update.message.reply_text(f"⏳ Carregant albarà de *{client_name}* ({data})...", parse_mode="Markdown")
        result = await mcp.veure_comanda(data_mcp, client_code)
        if "error" in result:
            await update.message.reply_text(f"❌ Error: {result['error']}")
        else:
            lines = result.get("order", [])
            ticket = format_order_ticket(client_name, data, lines)
            if ticket:
                await update.message.reply_text(ticket, parse_mode="HTML")
            else:
                await update.message.reply_text(f"ℹ️ No hi ha línies per *{client_name}* el {data}.", parse_mode="Markdown")
        return ConversationHandler.END

    stop_handler_msg = MessageHandler(filters.TEXT & filters.Regex(r"(?i)^stop$"), cmd_stop)
    return ConversationHandler(
        entry_points=[CommandHandler("veure", vr_start)],
        states={
            VR_CLIENT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client)],
            VR_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_client_opcio)],
            VR_DATA: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vr_data)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
