from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from services.mcp_runtime import persist_mcp_url, set_runtime_mcp_url


def build_callback_handler(
    *,
    logger,
    mcp,
    ai,
    order_field_labels: dict,
    order_field_kwargs: dict,
    manual_order_text,
    manual_order_keyboard,
    order_type_choice_keyboard,
    format_order_fields,
    confirmation_text,
    confirmation_keyboard,
    format_order_ticket,
    load_auth_data,
    save_auth_data,
    get_admin_user_id,
    base_dir,
):
    async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Gestiona callbacks inline: edició de comandes, confirmacions i autoritzacions."""
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "op_send_no":
            context.user_data.pop("pending_operational_send", None)
            await query.edit_message_text("❌ Enviament operatiu cancel·lat.")
            return

        if data == "op_send_yes":
            if query.from_user.id != get_admin_user_id():
                await query.message.reply_text("❌ No autoritzat.")
                return
            pending = context.user_data.pop("pending_operational_send", None)
            if not pending:
                await query.edit_message_text("❌ No hi ha cap enviament pendent.")
                return

            data_mcp = pending["date_mcp"]
            data_display = pending["date_display"]
            url = pending["url"]
            clients = pending.get("clients", [])
            set_runtime_mcp_url(url, mcp)
            persist_mcp_url(url, Path(base_dir))

            await query.edit_message_text(f"⏳ Enviant comandes del {data_display}...")
            impresos = []
            errors = []
            total_copies = 0
            for client in clients:
                codi = int(client["codi"])
                nom = client.get("nom", str(codi))
                copies = int(client.get("copies", 1) or 1)
                logger.info("Enviament operatiu: imprimint date=%s client=%s (%s) copies=%s", data_mcp, codi, nom, copies)
                try:
                    result = await mcp.imprimir_albarans(data_mcp, codi, copies)
                    if isinstance(result, dict) and "error" in result:
                        errors.append(f"{nom}: {result.get('error')}")
                    else:
                        impresos.append(f"{nom} x{copies}")
                        total_copies += copies
                except Exception as exc:
                    logger.exception("Enviament operatiu: error client=%s", codi)
                    errors.append(f"{nom}: {exc}")

            if errors:
                text = (
                    "⚠️ Enviament acabat amb errors.\n\n"
                    f"📅 {data_display}\n"
                    f"Clients enviats: {len(impresos)}\n"
                    f"Còpies: {total_copies}\n\n"
                    "Errors:\n" + "\n".join(f"• {e}" for e in errors[:10])
                )
            else:
                text = (
                    "✅ Comandes enviades correctament.\n\n"
                    f"📅 {data_display}\n"
                    f"Clients: {len(impresos)}\n"
                    f"Còpies: {total_copies}\n\n"
                    "Clients enviats:\n" + "\n".join(f"• {i}" for i in impresos[:25])
                )
            await query.message.reply_text(text)
            return

        if data.startswith("order_field:"):
            pending = context.user_data.get("pending_order_edit")
            if not pending:
                await query.edit_message_text("❌ No hi ha cap canvi pendent.")
                return
            field = data.split(":", 1)[1]
            fields = set(pending.get("fields", set()))
            if field in fields:
                fields.remove(field)
            elif field in order_field_labels:
                fields.add(field)
            pending["fields"] = fields
            logger.info(
                "order_field toggle user=%s field=%s fields=%s pending=%s",
                query.from_user.id,
                field,
                sorted(fields),
                {k: pending.get(k) for k in ("mode", "client_code", "date_mcp", "article_code", "quantity")},
            )
            await query.edit_message_text(
                manual_order_text(pending),
                parse_mode="Markdown",
                reply_markup=manual_order_keyboard(pending),
            )
            return

        if data == "order_cancel":
            context.user_data.pop("pending_order_edit", None)
            await query.edit_message_text("❌ Canvi cancel·lat.")
            return

        if data.startswith("order_apply:"):
            pending = context.user_data.pop("pending_order_edit", None)
            if not pending:
                await query.edit_message_text("❌ No hi ha cap canvi pendent.")
                return
            fields = set(pending.get("fields", set()))
            if not fields:
                context.user_data["pending_order_edit"] = pending
                await query.answer("Tria almenys un camp: Demanat, Servit o Tornat.", show_alert=True)
                return
            order_type_raw = data.split(":", 1)[1]
            quantity = int(pending.get("quantity", 0))
            kwargs = {order_field_kwargs[f]: quantity for f in fields}
            if order_type_raw == "auto":
                linies = await mcp.linies_article_comanda(
                    pending["date_mcp"],
                    pending["client_code"],
                    pending["article_code"],
                )
                linies_actives = [
                    line for line in linies
                    if line.get("requested", 0) or line.get("served", 0) or line.get("returned", 0)
                ]
                candidates = linies_actives or linies
                if len(candidates) == 1:
                    order_type = int(candidates[0].get("order_type", 1) or 1)
                elif len(candidates) > 1:
                    context.user_data["pending_order_edit"] = pending
                    await query.edit_message_text(
                        manual_order_text(pending)
                        + "\n\n⚠️ Aquest producte existeix amb més d'un tipus. Tria quina línia vols canviar:",
                        parse_mode="Markdown",
                        reply_markup=order_type_choice_keyboard(pending, candidates),
                    )
                    return
                else:
                    order_type = 1
            else:
                order_type = int(order_type_raw)
            logger.info(
                "order_apply user=%s order_type=%s fields=%s kwargs=%s pending=%s",
                query.from_user.id,
                order_type,
                sorted(fields),
                kwargs,
                {k: pending.get(k) for k in ("mode", "client_code", "date_mcp", "article_code", "quantity")},
            )
            await query.edit_message_text("⏳ Aplicant canvi...")
            result = await mcp.canviar_linia_mcp(
                pending["date_mcp"],
                pending["client_code"],
                pending["article_code"],
                order_type,
                **kwargs,
            )
            if result.get("ok"):
                action = "posat a 0" if quantity == 0 else f"posat a {quantity}"
                await query.message.reply_text(
                    f"✅ Canvi aplicat correctament.\n\n"
                    f"👤 {pending['client_name']}\n"
                    f"📅 {pending['date_display']}\n"
                    f"🥖 {pending['article_name']}\n"
                    f"📌 {format_order_fields(fields)}: {action}"
                )
            else:
                await query.message.reply_text(f"❌ Error MCP: {result.get('error', 'Error desconegut')}")
            return

        if data.startswith("confirm_field:"):
            pending = context.user_data.get("pending_confirmation")
            if not pending:
                await query.edit_message_text("❌ No hi ha cap comanda pendent.")
                return
            field = data.split(":", 1)[1]
            fields = set(pending.get("fields", {"requested", "served"}))
            if field in fields:
                fields.remove(field)
            elif field in order_field_labels:
                fields.add(field)
            pending["fields"] = fields
            logger.info(
                "confirm_field toggle user=%s field=%s fields=%s lines=%s",
                query.from_user.id,
                field,
                sorted(fields),
                [
                    {k: line.get(k) for k in ("date", "client", "article_code", "quantity", "order_type")}
                    for line in pending.get("lines", [])
                ],
            )
            await query.edit_message_text(
                confirmation_text(pending.get("lines", []), fields),
                parse_mode="Markdown",
                reply_markup=confirmation_keyboard(fields, choose_order_type=pending.get("choose_order_type", False)),
            )
            return

        if data.startswith("auth_allow:") or data.startswith("auth_client:") or data.startswith("auth_admin:") or data.startswith("auth_deny:"):
            admin_id = get_admin_user_id()
            if query.from_user.id != admin_id:
                await query.message.reply_text("❌ No autoritzat.")
                return

            parts = data.split(":")
            action = parts[0]
            target_user_id = int(parts[1])
            auth_data = load_auth_data()
            pending = auth_data.setdefault("pending_requests", {}).pop(str(target_user_id), None)
            users = auth_data.setdefault("users", {})

            if action == "auth_allow":
                users[str(target_user_id)] = {
                    "role": "admin",
                    "scope": "Cal Forner",
                    "granted_at": datetime.now().isoformat(timespec="seconds"),
                }
                save_auth_data(auth_data)
                await query.edit_message_text("✅ Usuari autoritzat com a administratiu de Cal Forner")
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="✅ Ja tens accés administratiu al bot. Pots veure i gestionar totes les dades disponibles.",
                )
                return

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
                save_auth_data(auth_data)
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
                save_auth_data(auth_data)
                await query.edit_message_text("✅ Usuari autoritzat com a administratiu de Cal Forner")
                await context.bot.send_message(
                    chat_id=target_user_id,
                    text="✅ Ja tens accés administratiu al bot. Pots veure i gestionar totes les dades disponibles.",
                )
                return

            save_auth_data(auth_data)
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

        if data.startswith("confirm_yes"):
            pending = context.user_data.pop("pending_confirmation", None)
            if not pending:
                await query.edit_message_text("❌ No hi ha cap comanda pendent.")
                return
            fields = set(pending.get("fields", {"requested", "served"}))
            if not fields:
                context.user_data["pending_confirmation"] = pending
                await query.answer("Tria almenys un camp: Demanat, Servit o Tornat.", show_alert=True)
                return
            forced_order_type = None
            if ":" in data:
                try:
                    forced_order_type = int(data.split(":", 1)[1])
                except ValueError:
                    forced_order_type = None

            try:
                await query.message.delete()
            except Exception:
                pass

            lines = pending.get("lines", [])
            results = []
            errors = []

            for line in lines:
                try:
                    order_type = forced_order_type if forced_order_type in (1, 2) else line.get("order_type", 1)
                    logger.info(
                        "confirm_apply user=%s order_type=%s fields=%s line=%s",
                        query.from_user.id,
                        order_type,
                        sorted(fields),
                        {k: line.get(k) for k in ("date", "client", "article_code", "quantity", "order_type")},
                    )
                    result = await ai.execute_order(
                        date=line["date"],
                        client=line["client"],
                        article_code=line["article_code"],
                        quantity=line["quantity"],
                        fields=fields,
                        order_type=order_type,
                    )
                    if forced_order_type in (1, 2):
                        line["order_type"] = forced_order_type
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
                    ticket = format_order_ticket(
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

    return handle_callback_query


def register_callback_handlers(app: Application, **deps) -> None:
    app.add_handler(CallbackQueryHandler(build_callback_handler(**deps)))
