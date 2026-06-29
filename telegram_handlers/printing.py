from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


IM_DATA, IM_CLIENT, IM_CLIENT_OPCIO, IM_COPIES, IM_SEGUENT, IM_TIPUS, IM_TEXT = range(7)


def build_imprimir_handler(**deps) -> ConversationHandler:
    mcp = deps["mcp"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    keyboard_dates = deps["keyboard_dates"]
    parse_data = deps["parse_data"]
    to_mcp_date = deps["to_mcp_date"]
    get_copies = deps["get_copies"]
    print_all_orders = deps["print_all_orders"]
    extract_print_text = deps["extract_print_text"]
    send_print_text = deps["send_print_text"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]

    async def im_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autoritzat(update):
            await rebuig(update, context)
            return ConversationHandler.END
        if context.args:
            inline_text = " ".join(context.args).strip()
            print_text = extract_print_text(f"imprimir {inline_text}")
            if print_text is not None:
                await send_print_text(update, print_text)
                return ConversationHandler.END
        context.user_data.pop("im_data", None)
        context.user_data.pop("im_client", None)
        context.user_data.pop("im_clients_impresos", None)
        await update.message.reply_text(
            "🖨️ *Impressió*\n\nQue vols imprimir?",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardMarkup([["📋 Albarans"], ["✏️ Text lliure"]], one_time_keyboard=True, resize_keyboard=True),
        )
        return IM_TIPUS

    async def im_tipus(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if "Text lliure" in text:
            await update.message.reply_text("✏️ Escriu el text que vols imprimir:", reply_markup=ReplyKeyboardRemove())
            return IM_TEXT
        await update.message.reply_text("📋 *Albarans* — Quina data?", parse_mode="Markdown", reply_markup=keyboard_dates())
        return IM_DATA

    async def im_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await send_print_text(update, update.message.text.strip())
        return ConversationHandler.END

    async def im_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = parse_data(update.message.text.strip())
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
                one_time_keyboard=True,
                resize_keyboard=True,
            ),
        )
        return IM_CLIENT

    async def im_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if "Tots els clients" in text:
            await print_all_orders(update, context.user_data["im_data"])
            return ConversationHandler.END
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        resultats = await mcp.cercar_client(text)
        if not resultats:
            await update.message.reply_text("❌ Client no trobat. Torna a intentar-ho:")
            return IM_CLIENT
        if len(resultats) == 1:
            c = resultats[0]
            context.user_data["im_client"] = {"codi": c["c"], "nom": c["n"]}
            return await im_imprimir(update, context, get_copies(c["c"]))

        botons = [[r["n"]] for r in resultats[:10]]
        context.user_data["im_resultats"] = resultats[:10]
        await update.message.reply_text("Quin client?", reply_markup=ReplyKeyboardMarkup(botons, one_time_keyboard=True, resize_keyboard=True))
        return IM_CLIENT_OPCIO

    async def im_client_opcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        resultats = context.user_data.get("im_resultats", [])
        trobat = next((r for r in resultats if r["n"] == text), None)
        if not trobat:
            await update.message.reply_text("⚠️ Tria una opció de la llista:")
            return IM_CLIENT_OPCIO
        context.user_data["im_client"] = {"codi": trobat["c"], "nom": trobat["n"]}
        return await im_imprimir(update, context, get_copies(trobat["c"]))

    async def im_imprimir(update: Update, context: ContextTypes.DEFAULT_TYPE, copies: int) -> int:
        client = context.user_data["im_client"]
        data = context.user_data["im_data"]
        data_mcp = to_mcp_date(data)
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
        try:
            copies = int(update.message.text.strip())
            if copies < 1 or copies > 10:
                raise ValueError
        except ValueError:
            await update.message.reply_text("⚠️ Escriu un número entre 1 i 10:")
            return IM_COPIES
        return await im_imprimir(update, context, copies)

    async def im_seguent(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if "Sí" in text or "si" in text.lower():
            await update.message.reply_text("Escriu el nom del client:", reply_markup=ReplyKeyboardRemove())
            return IM_CLIENT

        impresos = context.user_data.get("im_clients_impresos", [])
        resum = "\n".join(f"• {x}" for x in impresos) if impresos else "—"
        await update.message.reply_text(
            f"🖨️ *Impressió completada*\n\n{resum}",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    stop_handler_msg = MessageHandler(filters.TEXT & filters.Regex(r"(?i)^stop$"), cmd_stop)
    return ConversationHandler(
        entry_points=[CommandHandler("imprimir", im_start)],
        states={
            IM_TIPUS: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_tipus)],
            IM_DATA: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_data)],
            IM_CLIENT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_client)],
            IM_CLIENT_OPCIO: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_client_opcio)],
            IM_COPIES: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_copies)],
            IM_SEGUENT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_seguent)],
            IM_TEXT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, im_text)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
