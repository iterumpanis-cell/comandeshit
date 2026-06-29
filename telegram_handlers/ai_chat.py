import asyncio
import tempfile
from pathlib import Path

from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ChatAction
from telegram.ext import CommandHandler, MessageHandler, PollAnswerHandler, ContextTypes, filters

from gemini_hit import NEEDS_CONFIRMATION, NEEDS_SELECTION


def build_ai_chat_handlers(**deps):
    ai = deps["ai"]
    mcp = deps["mcp"]
    logger = deps["logger"]
    autoritzat = deps["autoritzat"]
    rebuig = deps["rebuig"]
    is_admin = deps["is_admin"]
    bound_client = deps["bound_client"]
    deny_scope = deps["deny_scope"]
    cmd_hora = deps["cmd_hora"]
    send_chunks = deps["send_chunks"]
    tancar_estat = deps["tancar_estat"]
    send_hourly_sales_report = deps["send_hourly_sales_report"]
    is_print_queue_request = deps["is_print_queue_request"]
    format_print_queue = deps["format_print_queue"]
    extract_print_text = deps["extract_print_text"]
    send_print_text = deps["send_print_text"]
    is_all_orders_request = deps["is_all_orders_request"]
    is_print_request = deps["is_print_request"]
    is_product_summary_excel_request = deps["is_product_summary_excel_request"]
    parse_all_orders_date = deps["parse_all_orders_date"]
    print_all_orders = deps["print_all_orders"]
    send_product_summary_excel = deps["send_product_summary_excel"]
    to_mcp_date = deps["to_mcp_date"]
    format_all_orders_blocks = deps["format_all_orders_blocks"]
    send_html_blocks = deps["send_html_blocks"]
    is_simple_greeting = deps["is_simple_greeting"]
    save_pending_selection = deps["save_pending_selection"]
    pending_polls = deps["pending_polls"]
    clear_pending_selection = deps["clear_pending_selection"]
    initial_confirmation_fields = deps["initial_confirmation_fields"]
    confirmation_text = deps["confirmation_text"]
    confirmation_keyboard = deps["confirmation_keyboard"]

    def history(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
        return context.user_data.setdefault("ai_history", [])

    def push_history(context: ContextTypes.DEFAULT_TYPE, role: str, text: str):
        items = history(context)
        items.append({"role": role, "text": text})
        if len(items) > 12:
            del items[:-12]

    async def ask_ai(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, transcript: str | None = None, estat_msg=None):
        if not ai.enabled:
            await update.message.reply_text("❌ Falta configurar `GEMINI_API_KEY` al fitxer `.env`.")
            return

        if estat_msg is None:
            estat_msg = await update.message.reply_text("🤔 Processant...")

        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
        topic_change_keywords = ("vendes", "ventes", "sales", "imprimir", "imprim", "/vendes", "/imprimir")
        is_topic_change = any(kw in prompt.lower() for kw in topic_change_keywords)

        if is_simple_greeting(prompt) or is_topic_change:
            current_history = []
            context.user_data.pop("pending_confirmation", None)
            context.user_data.pop("pending_selection", None)
            ai.last_context = {}
        else:
            current_history = list(history(context))

        ctx = ai.last_context
        if ctx and ctx.get("date") and not (is_simple_greeting(prompt) or is_topic_change):
            prompt = f"[Context actual: data={ctx['date']}]\n{prompt}"

        client_code, client_name = bound_client(update)
        effective_prompt = prompt
        if not is_admin(update) and client_code:
            effective_prompt = (
                f"[RESTRICCIO D'ACCÉS] Aquest usuari només pot operar sobre el client "
                f"{client_name or client_code} (codi {client_code}). "
                "No pots consultar ni modificar cap altre client. "
                "Si l'usuari demana un altre client, rebutja-ho.\n\n"
                f"{prompt}"
            )

        try:
            answer = await ai.ask(effective_prompt, current_history)
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
        push_history(context, "user", stored_user_text)
        await handle_ai_answer(update, context, answer, estat_msg, transcript=transcript)

    async def handle_ai_answer(update, context, answer, estat_msg, transcript=None):
        if isinstance(answer, dict) and NEEDS_SELECTION in answer:
            await tancar_estat(estat_msg)
            options = answer.get("options", [])
            question = answer.get("question", "Selecciona una opció:")
            contents = answer.get("__gemini_contents__", [])
            selection_type = answer.get("selection_type", "article")
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
            save_pending_selection(
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

        if isinstance(answer, dict) and NEEDS_CONFIRMATION in answer:
            await tancar_estat(estat_msg)
            lines = answer.get("lines", [])
            contents = answer.get("__gemini_contents__", [])
            client_code, client_name = bound_client(update)
            if not is_admin(update) and client_code:
                invalid = [line for line in lines if int(line.get("client", -1)) != int(client_code)]
                if invalid:
                    await update.message.reply_text(
                        f"❌ Només pots operar sobre les comandes de *{client_name or client_code}*.",
                        parse_mode="Markdown",
                    )
                    return

            fields = initial_confirmation_fields(lines)
            context.user_data["pending_confirmation"] = {
                "lines": lines,
                "contents": contents,
                "fields": fields,
                "choose_order_type": True,
            }
            await update.message.reply_text(
                confirmation_text(lines, fields),
                parse_mode="Markdown",
                reply_markup=confirmation_keyboard(fields, choose_order_type=True),
            )
            return

        await tancar_estat(estat_msg)

        if transcript is not None:
            await send_chunks(update.message, f"📝 _{transcript}_")

        if isinstance(answer, str):
            push_history(context, "assistant", answer)
            parse_mode = "HTML" if answer.lstrip().startswith("<pre>") else None
            await send_chunks(update.message, answer, parse_mode=parse_mode)
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

        if text.lower() in {"🕒 hora actual", "hora actual", "hora"}:
            await cmd_hora(update, context)
            return

        if await send_hourly_sales_report(update, text):
            return

        if is_print_queue_request(text):
            if not is_admin(update):
                await update.message.reply_text("Nomes els administradors poden veure la cua d'impressio.")
                return
            result = await mcp.cua_impressio()
            await update.message.reply_text(format_print_queue(result))
            return

        print_text = extract_print_text(text)
        if print_text is not None:
            await send_print_text(update, print_text)
            return

        if is_product_summary_excel_request(text):
            if not is_admin(update):
                await deny_scope(update, "❌ Només els administradors poden generar l'Excel de tots els clients.")
                return

            data = parse_all_orders_date(text)
            if not data:
                await update.message.reply_text("⚠️ Digues la data, per exemple: Excel del resum de productes de tots els clients del 24/06/26.")
                return

            await send_product_summary_excel(update, data)
            return

        if is_all_orders_request(text) and is_print_request(text):
            if not is_admin(update):
                await deny_scope(update, "❌ Només els administradors poden imprimir totes les comandes del dia.")
                return

            data = parse_all_orders_date(text)
            if not data:
                await update.message.reply_text("⚠️ Digues la data, per exemple: imprimeix totes les comandes del dia 01/05.")
                return

            try:
                await print_all_orders(update, data)
            except Exception as exc:
                logger.exception("Error imprimint totes les comandes")
                await update.message.reply_text(f"❌ Error imprimint les comandes: {exc}")
            return

        if is_all_orders_request(text):
            if not is_admin(update):
                await deny_scope(update, "❌ Només els administradors poden consultar totes les comandes del dia.")
                return

            data = parse_all_orders_date(text)
            if not data:
                await update.message.reply_text("⚠️ Digues la data, per exemple: totes les comandes del dia 01/05.")
                return

            estat_msg = await update.message.reply_text(f"⏳ Carregant totes les comandes del {data}...")
            data_mcp = to_mcp_date(data)
            logger.info("Consulta directa totes les comandes: date=%s", data_mcp)
            try:
                result = await mcp.comandes_per_data(data_mcp)
                blocks = format_all_orders_blocks(data, result)
                await tancar_estat(estat_msg)
                await send_html_blocks(update.message, blocks)
            except Exception as exc:
                logger.exception("Error carregant totes les comandes")
                try:
                    await estat_msg.edit_text("❌")
                except Exception:
                    pass
                await update.message.reply_text(f"❌ Error carregant les comandes: {exc}")
            return

        await ask_ai(update, context, text)

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
            await ask_ai(update, context, transcript, transcript=transcript, estat_msg=estat_msg)
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
        poll_answer = update.poll_answer
        pending = context.user_data.get("pending_selection")
        if pending and pending.get("poll_id") != poll_answer.poll_id:
            pending = None
        if not pending:
            pending = pending_polls(context).get(poll_answer.poll_id)
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
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ La selecció ja no és vàlida. Torna a enviar la petició, si us plau.",
            )
            return

        selected = options[selected_idx]
        contents = pending.get("contents", [])
        clear_pending_selection(context, poll_answer.poll_id)

        try:
            await context.bot.stop_poll(chat_id=chat_id, message_id=pending.get("message_id"))
        except Exception:
            logger.exception("handle_poll_answer: no s'ha pogut tancar el poll %s", poll_answer.poll_id)

        from google.genai import types as gtypes
        contents.append(gtypes.Content(
            role="user",
            parts=[gtypes.Part(text=f"L'usuari ha seleccionat: {selected['n']} (codi {selected['c']})")],
        ))

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

        await tancar_estat(estat_msg)

        if isinstance(answer, dict) and NEEDS_SELECTION in answer:
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
            save_pending_selection(
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
            push_history(context, "assistant", answer)
            parse_mode = "HTML" if answer.lstrip().startswith("<pre>") else None
            for start in range(0, len(answer), 3500):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=answer[start:start + 3500],
                    parse_mode=parse_mode,
                )
        elif isinstance(answer, dict) and NEEDS_CONFIRMATION in answer:
            lines = answer.get("lines", [])
            new_contents = answer.get("__gemini_contents__", [])
            fields = initial_confirmation_fields(lines)
            context.user_data["pending_confirmation"] = {
                "lines": lines,
                "contents": new_contents,
                "fields": fields,
                "choose_order_type": True,
            }
            await context.bot.send_message(
                chat_id=chat_id,
                text=confirmation_text(lines, fields),
                parse_mode="Markdown",
                reply_markup=confirmation_keyboard(fields, choose_order_type=True),
            )
        else:
            logger.warning("handle_poll_answer: resposta inesperada tipus %s: %s", type(answer), str(answer)[:200])
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ No he pogut continuar després de la selecció. Torna a enviar la petició, si us plau.",
            )

    return cmd_reset, ai_voice, ai_text, handle_poll_answer


def register_ai_chat_handlers(app, **deps) -> None:
    cmd_reset, ai_voice, ai_text, handle_poll_answer = build_ai_chat_handlers(**deps)
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(MessageHandler(filters.VOICE, ai_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_text))
    app.add_handler(PollAnswerHandler(handle_poll_answer))
