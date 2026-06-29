from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters


VD_SHOP, VD_DATE, VD_REPORT = range(3)


def build_vendes_handler(**deps) -> ConversationHandler:
    logger = deps["logger"]
    mcp = deps["mcp"]
    shops = deps["shops"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    normalize_search_text = deps["normalize_search_text"]
    keyboard_sales_dates = deps["keyboard_sales_dates"]
    parse_sales_date = deps["parse_sales_date"]
    format_hora_feliz_report = deps["format_hora_feliz_report"]
    format_products_by_hour_report = deps["format_products_by_hour_report"]
    format_product_totals_report = deps["format_product_totals_report"]
    format_sales_summary_report = deps["format_sales_summary_report"]
    send_chunks = deps["send_chunks"]
    cmd_stop = deps["cmd_stop"]
    cmd_cancel = deps["cmd_cancel"]

    async def cmd_vendes(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not autoritzat(update):
            await rebuig(update, context)
            return ConversationHandler.END
        if not is_admin(update):
            await update.message.reply_text("❌ Aquesta informació només està disponible per a usuaris administratius.")
            return ConversationHandler.END

        context.user_data["vendes"] = {}
        buttons = [["Granollers", "Montornès"], ["✏️ Codi botiga manual"], ["❌ Cancel·lar"]]
        await update.message.reply_text(
            "📊 De quina botiga vols l'informe?",
            reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True),
        )
        return VD_SHOP

    async def vd_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text.startswith("❌"):
            await update.message.reply_text("Cancel·lat.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        normalized = normalize_search_text(text)
        if normalized == "granollers":
            shop_code, shop_name = int(shops["granollers"]), "Granollers"
        elif normalized in {"montornes", "montornès"}:
            shop_code, shop_name = int(shops["montornes"]), "Montornès"
        else:
            import re
            match = re.search(r"\d+", text)
            if not match:
                await update.message.reply_text("Escriu el codi de botiga o tria una opció.")
                return VD_SHOP
            shop_code = int(match.group(0))
            shop_name = f"Botiga {shop_code}"

        context.user_data["vendes"] = {"shop_code": shop_code, "shop_name": shop_name}
        await update.message.reply_text("📅 Tria la data:", reply_markup=keyboard_sales_dates())
        return VD_DATE

    async def vd_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text.startswith("❌"):
            await update.message.reply_text("Cancel·lat.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        if "data lliure" in normalize_search_text(text):
            await update.message.reply_text("✏️ Escriu la data de vendes en format dd/mm/aaaa:", reply_markup=ReplyKeyboardRemove())
            return VD_DATE

        parsed = parse_sales_date(text)
        if not parsed:
            await update.message.reply_text("⚠️ Data no vàlida. Escriu-la com dd/mm/aaaa.")
            return VD_DATE

        data_mcp, data_display = parsed
        context.user_data.setdefault("vendes", {}).update({"data_mcp": data_mcp, "data_display": data_display})
        buttons = [
            ["📊 Resum vendes"],
            ["🕒 Productes per hora"],
            ["🥖 Productes total dia"],
            ["🎉 Hora Feliz"],
            ["❌ Cancel·lar"],
        ]
        await update.message.reply_text(
            "Quin informe vols?",
            reply_markup=ReplyKeyboardMarkup(buttons, one_time_keyboard=True, resize_keyboard=True),
        )
        return VD_REPORT

    async def vd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        if text.startswith("❌"):
            await update.message.reply_text("Cancel·lat.", reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        data = context.user_data.get("vendes", {})
        shop_code = int(data["shop_code"])
        shop_name = data["shop_name"]
        data_mcp = data["data_mcp"]
        data_display = data["data_display"]
        normalized = normalize_search_text(text)

        estat_msg = await update.message.reply_text(
            f"📊 Carregant informe de {shop_name} ({data_display})...",
            reply_markup=ReplyKeyboardRemove(),
        )
        try:
            if "hora feliz" in normalized:
                detail = await mcp.detall_tickets_dia(shop_code, data_mcp, limit=1000, offset=0)
                if detail.get("error"):
                    await estat_msg.edit_text(f"❌ Error MCP: {detail['error']}")
                    return ConversationHandler.END
                report = format_hora_feliz_report(detail, shop_name, data_display)
            else:
                r = await mcp.vendes_dia(shop_code, data_mcp)
                if not r.get("v"):
                    await estat_msg.edit_text(f"ℹ️ Sense dades de vendes per {shop_name} el {data_display}.")
                    return ConversationHandler.END
                if "hora" in normalized:
                    report = format_products_by_hour_report(r, shop_name, data_display)
                elif "product" in normalized or "total dia" in normalized:
                    report = format_product_totals_report(r, shop_name, data_display)
                else:
                    report = format_sales_summary_report(r, shop_name, data_display)
        except Exception as e:
            logger.exception("Error carregant informe de vendes")
            await estat_msg.edit_text(f"❌ Error MCP: {e}")
            return ConversationHandler.END

        await estat_msg.delete()
        await send_chunks(update.message, report, parse_mode="Markdown")
        return ConversationHandler.END

    stop_handler_msg = MessageHandler(filters.TEXT & filters.Regex(r"(?i)^stop$"), cmd_stop)
    return ConversationHandler(
        entry_points=[
            CommandHandler("vendes", cmd_vendes),
            MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\s*(vendes|ventas|ventes)\s*$"), cmd_vendes),
        ],
        states={
            VD_SHOP: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vd_shop)],
            VD_DATE: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vd_date)],
            VD_REPORT: [stop_handler_msg, MessageHandler(filters.TEXT & ~filters.COMMAND, vd_report)],
        },
        fallbacks=[stop_handler_msg, CommandHandler("cancel", cmd_cancel)],
    )
