import json
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from telegram import Bot


class AutoSender:
    def __init__(
        self,
        *,
        state_dir: Path,
        telegram_token: str,
        logger,
        mcp,
        get_copies,
        load_auth_data,
        get_admin_user_id,
        format_totals_escpos,
        imprimir_text_directe,
    ):
        self.state_dir = state_dir
        self.telegram_token = telegram_token
        self.logger = logger
        self.mcp = mcp
        self.get_copies = get_copies
        self.load_auth_data = load_auth_data
        self.get_admin_user_id = get_admin_user_id
        self.format_totals_escpos = format_totals_escpos
        self.imprimir_text_directe = imprimir_text_directe

    def state_path(self, data_mcp: str) -> Path:
        return self.state_dir / f"{data_mcp}.json"

    def read_state(self, data_mcp: str) -> dict | None:
        path = self.state_path(data_mcp)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("Auto enviament: no puc llegir estat %s: %s", path, exc)
            return {"status": "unknown", "path": str(path)}

    def write_state(self, data_mcp: str, state: dict) -> None:
        self.state_dir.mkdir(exist_ok=True)
        path = self.state_path(data_mcp)
        payload = {**state, "date": data_mcp, "updated_at": datetime.now().isoformat(timespec="seconds")}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def claim_run(self, data_mcp: str) -> bool:
        self.state_dir.mkdir(exist_ok=True)
        path = self.state_path(data_mcp)
        now = datetime.now()
        payload = {
            "date": data_mcp,
            "status": "running",
            "started_at": now.isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
        }
        try:
            with path.open("x", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            return True
        except FileExistsError:
            state = self.read_state(data_mcp) or {}
            status = state.get("status")
            if status == "success":
                self.logger.info("Auto enviament: %s ja consta enviat correctament, saltant", data_mcp)
                return False
            if status == "running":
                started_raw = state.get("started_at") or state.get("updated_at")
                try:
                    started = datetime.fromisoformat(started_raw) if started_raw else now
                except ValueError:
                    started = now
                if now - started < timedelta(hours=2):
                    self.logger.warning("Auto enviament: %s ja s'està executant, saltant duplicat", data_mcp)
                    return False
                self.logger.warning("Auto enviament: estat running antic per %s, reintentant", data_mcp)
            else:
                self.logger.info("Auto enviament: estat anterior %s per %s, reintentant", status, data_mcp)
            self.write_state(data_mcp, payload)
            return True

    def mark_success(self, data_mcp: str, *, clients: int, copies: int) -> None:
        self.write_state(data_mcp, {"status": "success", "clients": clients, "copies": copies})

    def mark_failed(self, data_mcp: str, error: str) -> None:
        self.write_state(data_mcp, {"status": "failed", "error": error})

    async def notify_all_users(self, bot: Bot, text: str):
        auth_data = self.load_auth_data()
        user_ids = set(auth_data.get("authorized_users", []))
        admin_id = self.get_admin_user_id()
        if admin_id:
            user_ids.add(admin_id)
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=int(uid), text=text, parse_mode="HTML")
            except Exception as e:
                self.logger.warning("No s'ha pogut notificar l'usuari %s: %s", uid, e)

    async def run_with_new_bot(self):
        async with Bot(token=self.telegram_token) as bot:
            await self.run(bot)

    async def run(self, bot: Bot):
        dema = date.today() + timedelta(days=1)
        data_display = dema.strftime("%d/%m/%Y")
        data_mcp = dema.strftime("%Y-%m-%d")
        self.logger.info("Auto enviament: iniciant per al %s", data_display)

        if not self.claim_run(data_mcp):
            return

        try:
            resultat = await self.mcp.comandes_per_data(data_mcp)
        except Exception as e:
            self.logger.exception("Auto enviament: error MCP per al %s", data_display)
            self.mark_failed(data_mcp, str(e))
            await self.notify_all_users(bot, f"❌ Error auto enviament del {escape(data_display)}: {escape(str(e))}")
            return

        clients = resultat.get("clients", [])
        if not clients:
            msg = f"ℹ️ Auto enviament: no hi ha comandes per al {escape(data_display)}."
            self.logger.info(msg)
            self.mark_success(data_mcp, clients=0, copies=0)
            await self.notify_all_users(bot, msg)
            return

        impresos: list[str] = []
        errors: list[str] = []
        total_copies = 0
        totals_articles: dict[str, int] = {}

        for client in clients:
            codi = client.get("codi")
            nom = client.get("nom", str(codi))
            if not codi:
                continue
            copies = self.get_copies(int(codi))
            self.logger.info("Auto enviament: imprimint date=%s client=%s (%s) copies=%s", data_mcp, codi, nom, copies)
            try:
                result = await self.mcp.imprimir_albarans(data_mcp, int(codi), copies)
                if "error" in result:
                    self.logger.warning("Auto enviament error: date=%s client=%s: %s", data_mcp, codi, result)
                    errors.append(f"{nom}: error")
                else:
                    impresos.append(f"{nom} x{copies}")
                    total_copies += copies
                    for linia in client.get("linies", []):
                        nom_art = linia.get("nm") or linia.get("artName") or linia.get("name") or str(linia.get("art", "?"))
                        qty = linia.get("requested", 0)
                        if qty > 0:
                            totals_articles[nom_art] = totals_articles.get(nom_art, 0) + qty
            except Exception as e:
                self.logger.exception("Auto enviament excepcio: client=%s", codi)
                errors.append(f"{nom}: {e}")

        totals_lines = sorted(totals_articles.items(), key=lambda x: x[0].lower())
        totals_txt = ""
        if totals_lines:
            totals_txt = "\n\n<b>Resum productes:</b>\n" + "\n".join(f"• {escape(art)}: {qty}" for art, qty in totals_lines)

        if errors:
            summary = (
                "⚠️ <b>Comandes enviades amb errors.</b>\n\n"
                f"📅 {escape(data_display)}\n"
                f"Clients enviats: {len(impresos)}\n"
                f"Còpies totals: {total_copies}\n\n"
                "<b>Clients enviats:</b>\n"
                + ("\n".join(f"• {item}" for item in impresos[:25]) if impresos else "- Cap")
                + "\n\n<b>Errors:</b>\n"
                + "\n".join(f"• {e}" for e in errors[:10])
                + totals_txt
            )
        else:
            summary = (
                "✅ <b>Comandes enviades automàticament.</b>\n\n"
                f"📅 {escape(data_display)}\n"
                f"Clients: {len(impresos)}\n"
                f"Còpies totals: {total_copies}\n\n"
                "<b>Clients enviats:</b>\n"
                + "\n".join(f"• {item}" for item in impresos[:25])
                + totals_txt
            )

        self.logger.info("Auto enviament completat: %d clients, %d copies", len(impresos), total_copies)
        if errors:
            self.mark_failed(data_mcp, "; ".join(errors[:10]))
        else:
            self.mark_success(data_mcp, clients=len(impresos), copies=total_copies)
        await self.notify_all_users(bot, summary)

        if totals_lines:
            totals_dict = {art: qty for art, qty in totals_lines}
            text_escpos = self.format_totals_escpos(data_display, totals_dict, len(impresos))
            await self.imprimir_text_directe(text_escpos)
