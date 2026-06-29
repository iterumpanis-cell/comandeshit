import asyncio
import os
import unicodedata
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes

import mcp_vendes as mcp_module
from mcp_vendes import MCPVendes
from services.diagnostics import (
    diagnose_incident,
    last_error_text,
    pm2_status_text,
    production_logs_text,
    redact_sensitive,
    test_logs_text,
)
from services.incidents import (
    cancel_pending_task,
    create_ia_task,
    find_ia_task,
    list_ia_tasks,
    list_incidents,
    save_incident,
    task_status_name,
)
from services.mcp_runtime import nubehit_url_from_current, set_runtime_mcp_url


def _is_ia_admin(update: Update, get_admin_user_id) -> bool:
    return bool(update.effective_user and update.effective_user.id == get_admin_user_id())


async def _deny_ia_admin(update: Update) -> None:
    if update.message:
        await update.message.reply_text("❌ Aquesta comanda IA només està disponible per a l'administrador principal.")


async def _reply_chunks(update: Update, text: str, parse_mode: str | None = None) -> None:
    if not update.message:
        return
    text = text or "(buit)"
    for start in range(0, len(text), 3500):
        await update.message.reply_text(text[start:start + 3500], parse_mode=parse_mode)


def _normalize_plain_text(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().strip().split())


def _looks_operational_ai_request(text: str) -> bool:
    t = _normalize_plain_text(text)
    problem_words = ("problema", "falla", "fallat", "error", "arregla", "arreglal", "mcp", "automatic", "automatica")
    action_words = ("envia", "enviar", "imprimeix", "imprimir", "comandes", "albarans")
    return any(w in t for w in problem_words) and any(w in t for w in action_words)


def _parse_operational_date(text: str) -> tuple[str, str]:
    match = __import__("re").search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text or "")
    if match:
        d, m, y = match.groups()
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}", f"{d.zfill(2)}/{m.zfill(2)}/{y}"
    t = _normalize_plain_text(text)
    target = date.today() + timedelta(days=1)
    if "avui" in t:
        target = date.today()
    return target.isoformat(), target.strftime("%d/%m/%Y")


async def _try_mcp_orders(url: str, data_mcp: str) -> tuple[dict | None, str | None]:
    previous = getattr(mcp_module, "MCP_URL", "")
    set_runtime_mcp_url(url)
    probe = MCPVendes()
    try:
        return await probe.comandes_per_data(data_mcp), None
    except Exception as exc:
        return None, str(exc)
    finally:
        set_runtime_mcp_url(previous)


def build_ia_handlers(get_admin_user_id, *, mcp, get_copies):
    async def cmd_ia_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        text = (
            "🧠 *Ajuda IA / Diagnòstic*\n\n"
            "Comandes disponibles:\n\n"
            "`/estat`\nMostra si producció i proves estan online.\n\n"
            "`/ia <text>`\nRegistra una incidència i fa un diagnòstic inicial.\n"
            "També crea una tasca OpenCode que pot corregir el bot de proves.\n"
            "Exemple:\n`/ia produccio he intentat esborrar Pages Quadrat de Can Maimo i no ha anat`\n\n"
            "`/ia_tasques`\nMostra tasques IA pendents, en curs i acabades.\n\n"
            "`/ia_resultat <id>`\nMostra el resultat d'una tasca IA.\n\n"
            "`/ia_cancel <id>`\nCancel·la una tasca pendent.\n\n"
            "`/logs_produccio`\nMostra últims logs útils del bot de producció.\n\n"
            "`/logs_proves`\nMostra últims logs útils del bot de proves.\n\n"
            "`/ultim_error`\nMostra els últims errors importants detectats.\n\n"
            "`/incidencies`\nLlista les últimes incidències registrades.\n\n"
            "Seguretat:\n"
            "- Només admin principal.\n"
            "- Pot modificar només el bot de proves.\n"
            "- No reinicia producció.\n"
            "- No fa commits ni push.\n"
            "- Filtra tokens abans d'enviar logs."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_ia_tasques(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        rows = await asyncio.to_thread(list_ia_tasks, 15)
        text = "🧠 Tasques IA\n\n" + ("\n".join(rows) if rows else "No hi ha tasques IA registrades.")
        await update.message.reply_text(text)

    async def cmd_ia_resultat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        if not context.args:
            await update.message.reply_text("Ús: /ia_resultat <id>")
            return
        task_id = context.args[0].strip()
        path, data = await asyncio.to_thread(find_ia_task, task_id)
        if not path:
            await update.message.reply_text(f"No trobo cap tasca amb id {task_id}.")
            return
        if data is None:
            await update.message.reply_text(f"La tasca {task_id} existeix però no puc llegir el JSON.")
            return
        output = str(data.get("output") or data.get("error") or "Encara no hi ha resultat.")
        text = (
            f"🧠 Tasca {task_id}\n"
            f"Estat: {task_status_name(path)}\n"
            f"Creada: {data.get('created_at')}\n"
            f"Inici: {data.get('started_at', '-')}\n"
            f"Final: {data.get('finished_at', '-')}\n\n"
            f"Ordre:\n{data.get('command', '')}\n\n"
            f"Resultat:\n{redact_sensitive(output[-3000:])}"
        )
        await _reply_chunks(update, text)

    async def cmd_ia_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        if not context.args:
            await update.message.reply_text("Ús: /ia_cancel <id>")
            return
        task_id = context.args[0].strip()
        message = await asyncio.to_thread(cancel_pending_task, task_id)
        await update.message.reply_text(message)

    async def cmd_estat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        status = await asyncio.to_thread(pm2_status_text)
        await _reply_chunks(update, "📊 Estat PM2\n\n" + status)

    async def cmd_logs_produccio(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        logs = await asyncio.to_thread(production_logs_text, 45)
        await _reply_chunks(update, "📜 Últims logs útils de producció\n\n" + logs)

    async def cmd_logs_proves(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        logs = await asyncio.to_thread(test_logs_text, 45)
        await _reply_chunks(update, "📜 Últims logs útils de proves\n\n" + logs)

    async def cmd_ultim_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        errors = await asyncio.to_thread(last_error_text, 35)
        await _reply_chunks(update, "🚨 Últims errors detectats\n\n" + errors)

    async def cmd_incidencies(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not _is_ia_admin(update, get_admin_user_id):
            await _deny_ia_admin(update)
            return
        rows = await asyncio.to_thread(list_incidents, 10)
        if not rows:
            await update.message.reply_text("Encara no hi ha incidències registrades.")
            return
        await update.message.reply_text("🗂️ Últimes incidències\n\n" + "\n".join(rows))

    async def cmd_ia(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            if not _is_ia_admin(update, get_admin_user_id):
                await _deny_ia_admin(update)
                return
            text = " ".join(context.args).strip()
            if text.lower() in {"ajuda", "help"}:
                await cmd_ia_ajuda(update, context)
                return
            if not text:
                await update.message.reply_text("Escriu una incidència. Exemple:\n/ia tenim un problema amb l'enviament automatic, arregla-ho i envia les comandes de dema")
                return
            if _looks_operational_ai_request(text):
                await _prepare_operational_send(update, context, text)
                return
            await update.message.reply_text("⏳ Registro la incidència i recullo estat/logs...")
            pm2_status, production_logs, test_logs = await asyncio.gather(
                asyncio.to_thread(pm2_status_text),
                asyncio.to_thread(production_logs_text, 35),
                asyncio.to_thread(test_logs_text, 35),
            )
            logs = "PRODUCCIO\n" + production_logs + "\n\nPROVES\n" + test_logs
            diagnosis = diagnose_incident(text, logs)
            incident_id = await asyncio.to_thread(
                save_incident,
                update.effective_user.id,
                text,
                pm2_status,
                logs,
                diagnosis,
            )
            task_id = await asyncio.to_thread(create_ia_task, update.effective_user.id, text, incident_id)
            response = (
                f"✅ Incidència {incident_id} registrada.\n\n"
                f"🧠 Tasca IA {task_id} creada.\n"
                f"El worker la processarà automàticament. Pots consultar-la amb /ia_resultat {task_id}\n\n"
                f"📝 Text\n{text}\n\n"
                f"🧭 Diagnòstic inicial\n{diagnosis}\n\n"
                f"📜 Logs útils recents\n{logs[:1800]}"
            )
            await _reply_chunks(update, response)
        except Exception as exc:
            await update.message.reply_text(f"❌ He tingut un error processant /ia, però no estic encallat: {exc}")

    async def _prepare_operational_send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        data_mcp, data_display = _parse_operational_date(text)
        status = await update.message.reply_text(f"🔎 Provo l'MCP actual per al {data_display}...")
        current_url = (getattr(mcp_module, "MCP_URL", "") or os.getenv("MCP_URL") or "").strip()
        candidates = []
        if current_url:
            candidates.append(("actual", current_url))
        nubehit_url = nubehit_url_from_current(current_url)
        if nubehit_url and nubehit_url not in [url for _, url in candidates]:
            candidates.append(("nubehit", nubehit_url))

        result = None
        chosen_label = None
        chosen_url = None
        errors = []
        for label, url in candidates:
            await status.edit_text(f"🔎 Provo MCP {label} per al {data_display}...")
            result, error = await _try_mcp_orders(url, data_mcp)
            if error:
                errors.append(f"{label}: {error[:180]}")
                continue
            chosen_label = label
            chosen_url = url
            break

        if result is None or chosen_url is None:
            await status.edit_text(
                "❌ No he pogut consultar les comandes: l'MCP no respon correctament.\n\n"
                + "\n".join(f"• {e}" for e in errors[:3])
            )
            return

        clients = result.get("clients", [])
        if not clients:
            await status.edit_text(f"ℹ️ MCP {chosen_label} respon, però no hi ha comandes imprimibles per al {data_display}.")
            return

        printable = []
        total_copies = 0
        for client in clients:
            codi = int(client.get("codi"))
            nom = client.get("nom", str(codi))
            copies = get_copies(codi)
            printable.append({"codi": codi, "nom": nom, "copies": copies})
            total_copies += copies

        context.user_data["pending_operational_send"] = {
            "date_mcp": data_mcp,
            "date_display": data_display,
            "url": chosen_url,
            "clients": printable,
        }
        lines = [f"• {c['nom']} x{c['copies']}" for c in printable[:20]]
        await status.edit_text(
            "✅ He trobat un MCP funcional i comandes per enviar.\n\n"
            f"MCP: {chosen_label}\n"
            f"Data: {data_display}\n"
            f"Clients: {len(printable)}\n"
            f"Còpies: {total_copies}\n\n"
            "Clients:\n" + "\n".join(lines) + "\n\n"
            "Si confirmes, deixaré aquesta URL MCP guardada i enviaré les comandes.\n\n"
            "Confirmes enviar ara?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Enviar ara", callback_data="op_send_yes")],
                [InlineKeyboardButton("❌ Cancel·lar", callback_data="op_send_no")],
            ]),
        )

    return [
        CommandHandler("ia_ajuda", cmd_ia_ajuda),
        CommandHandler("ia", cmd_ia),
        CommandHandler("ia_tasques", cmd_ia_tasques),
        CommandHandler("ia_resultat", cmd_ia_resultat),
        CommandHandler("ia_cancel", cmd_ia_cancel),
        CommandHandler("estat", cmd_estat),
        CommandHandler("logs_produccio", cmd_logs_produccio),
        CommandHandler("logs_proves", cmd_logs_proves),
        CommandHandler("ultim_error", cmd_ultim_error),
        CommandHandler("incidencies", cmd_incidencies),
    ]


def register_ia_admin_handlers(app: Application, get_admin_user_id, *, mcp, get_copies) -> None:
    for handler in build_ia_handlers(get_admin_user_id, mcp=mcp, get_copies=get_copies):
        app.add_handler(handler)
