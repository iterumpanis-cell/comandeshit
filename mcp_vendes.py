"""
mcp_vendes.py — Client MCP per al servidor de vendes d'octomes.com
Protocol: MCP Streamable HTTP amb sessions (mcp-session-id header)
"""
import asyncio
import json
import logging
import aiohttp

logger = logging.getLogger(__name__)

MCP_URL = "https://octomes.com/mcp/ded3a2f73f433c5fa4ab3edf03cbe661e0953614a646094ff32497c3c482af9f"


def _parse_sse(text: str) -> dict | None:
    """Extreu el JSON del format SSE: 'event: message\\ndata: {...}'"""
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except Exception:
                pass
    return None


class MCPVendes:
    def __init__(self):
        self._session_id: str | None = None
        self._lock = asyncio.Lock()
        self._req_id = 0

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _call(self, method: str, params: dict) -> dict:
        """Crida al servidor MCP."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }

        ssl_ctx = False  # Temporalment desactivat per certificat caducat a octomes.com
        async with aiohttp.ClientSession() as http:
            async with http.post(MCP_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15), ssl=ssl_ctx) as resp:
                new_sid = resp.headers.get("mcp-session-id")
                if new_sid:
                    self._session_id = new_sid
                text = await resp.text()
                data = _parse_sse(text)
                if data is None:
                    raise ValueError(f"Resposta inesperada: {text[:200]}")
                return data

    async def _ensure_session(self):
        """Inicialitza sessió si no n'hi ha."""
        if self._session_id:
            return
        result = await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "hitsystems-bot", "version": "1.0"},
        })
        logger.info(f"MCP sessió iniciada: {self._session_id} | server={result.get('result', {}).get('serverInfo', {})}")

    async def _tool(self, name: str, arguments: dict) -> dict:
        """Crida una eina MCP amb reintents si la sessió ha caducat."""
        logger.info("MCP call: %s args=%s", name, arguments)
        async with self._lock:
            for attempt in range(2):
                try:
                    await self._ensure_session()
                    result = await self._call("tools/call", {"name": name, "arguments": arguments})
                    if "error" in result:
                        raise ValueError(result["error"])
                    content = result.get("result", {}).get("content", [])
                    if content and content[0].get("type") == "text":
                        text = content[0]["text"]
                        try:
                            parsed = json.loads(text)
                            logger.info("MCP result: %s -> %s", name, str(parsed)[:300])
                            return parsed
                        except Exception:
                            logger.info("MCP result: %s -> (text) %s", name, text[:300])
                            return {"text": text}
                    logger.info("MCP result: %s -> (raw) %s", name, str(result.get("result", {}))[:300])
                    return result.get("result", {})
                except Exception as e:
                    if "Session not found" in str(e) or "session" in str(e).lower():
                        logger.warning(f"Sessió MCP caducada, reiniciant... (intent {attempt+1})")
                        self._session_id = None
                        continue
                    logger.error("MCP error: %s args=%s -> %s", name, arguments, e)
                    raise
            raise RuntimeError("No s'ha pogut establir sessió MCP")

    # ------------------------------------------------------------------ #
    #  API pública                                                         #
    # ------------------------------------------------------------------ #

    async def cercar_client(self, text: str) -> list:
        """Cerca clients per nom. Si no troba res, reintenta truncant l'últim caràcter
        fins a 3 cops (workaround per accents: 'Cabre' → 'Cabr' → troba 'Cabré')."""
        try:
            for i in range(min(4, len(text) - 2)):
                query = text[:len(text) - i] if i > 0 else text
                if len(query) < 3:
                    break
                r = await self._tool("search_client", {"text": query})
                results = r if isinstance(r, list) else []
                if results:
                    if i > 0:
                        logger.info(f"cercar_client: fallback '{text}'->'{query}', {len(results)} resultats")
                    return results
            return []
        except Exception as e:
            logger.warning(f"cercar_client: {e}")
            return []

    async def cercar_article(self, text: str) -> list:
        """Cerca articles per nom. Retorna llista de {c, n}."""
        try:
            r = await self._tool("search_article", {"text": text})
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.warning(f"cercar_article: {e}")
            return []

    async def llistar_tots_articles(self) -> list:
        """Retorna el catàleg complet d'articles via tool dedicat del MCP."""
        try:
            r = await self._tool("list_all_articles", {})
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.warning(f"llistar_tots_articles: {e}")
            return []

    async def llistar_tots_clients(self) -> list:
        """Retorna la llista completa de clients via tool dedicat del MCP."""
        try:
            r = await self._tool("list_all_clients", {})
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.warning(f"llistar_tots_clients: {e}")
            return []

    async def vendes_dia(self, codi_botiga: int, data: str) -> dict:
        """Vendes agregades del dia. data format YYYY-MM-DD."""
        try:
            return await self._tool("aggregated_sales_day", {"shop_code": codi_botiga, "date": data})
        except Exception as e:
            logger.warning(f"vendes_dia: {e}")
            return {}

    async def vendes_hora(self, codi_botiga: int, data: str, hora: int) -> dict:
        """Vendes d'una hora concreta."""
        try:
            return await self._tool("aggregated_sales_hour", {"shop_code": codi_botiga, "date": data, "hour": hora})
        except Exception as e:
            logger.warning(f"vendes_hora: {e}")
            return {}

    async def veure_comanda(self, data: str, client: int) -> dict:
        """Veure comanda d'un client per data. data format YYYY-MM-DD."""
        try:
            return await self._tool("view_order", {"date": data, "client": client})
        except Exception as e:
            logger.warning(f"veure_comanda: {e}")
            return {"error": str(e)}

    async def imprimir_text(self, text: str) -> dict:
        """Encua text lliure a la impressora Star via MCP."""
        try:
            return await self._tool("print_text", {"text": text})
        except Exception as e:
            logger.warning(f"imprimir_text: {e}")
            return {"error": str(e)}

    async def cua_impressio(self) -> dict | list:
        """Retorna feines pendents de la cua d'impressio MCP."""
        try:
            return await self._tool("get_print_queue", {})
        except Exception as e:
            logger.warning(f"cua_impressio: {e}")
            return {"error": str(e)}

    async def afegir_linia_mcp(self, data: str, client: int, article_code: int,
                                quantity: int, order_type: int = 1) -> dict:
        """Afegir línia de comanda via MCP.
        order_type: 1=normal (default), 2=encarrec
        """
        try:
            args = {
                "date": data,
                "client": client,
                "article_code": article_code,
                "quantity": quantity,
                "order_type": order_type,
            }
            return await self._tool("add_order", args)
        except Exception as e:
            logger.warning(f"afegir_linia_mcp: {e}")
            return {"error": str(e)}

    async def llistar_clients_amb_comanda(self, data: str) -> list:
        """Retorna llista de clients que tenen comanda en una data (ràpid, una sola crida MCP)."""
        try:
            r = await self._tool("list_order_clients", {"date": data})
            if isinstance(r, list):
                return r
            if isinstance(r, dict) and isinstance(r.get("list"), list):
                return r["list"]
            return []
        except Exception as e:
            logger.warning(f"llistar_clients_amb_comanda: {e}")
            return []

    async def detall_tickets_dia(self, codi_botiga: int, data: str, limit: int = 1000, offset: int = 0) -> list:
        """Detall de tiquets de caixa d'una botiga per dia."""
        try:
            args = {"shop_code": codi_botiga, "date": data, "limit": limit, "offset": offset}
            r = await self._tool("ticket_detail_day", args)
            return r if isinstance(r, list) else []
        except Exception as e:
            logger.warning(f"detall_tickets_dia: {e}")
            return []

    async def comandes_per_data(self, data: str, concurrencia: int = 8) -> dict:
        """Retorna totes les comandes de clients per una data.
        Usa list_order_clients per obtenir només els clients amb comanda, després consulta cada un.
        Format: {"clients": [...], "totals_per_article": {...}}
        """
        clients_raw = await self.llistar_clients_amb_comanda(data)
        clients_raw = [
            c for c in clients_raw
            if not str(c.get("n") or c.get("name") or "").strip().upper().startswith("BOT")
        ]
        logger.info("comandes_per_data: %s -> %d clients amb comanda", data, len(clients_raw))
        if not clients_raw:
            return {"clients": [], "totals_per_article": {}}

        sem = asyncio.Semaphore(concurrencia)

        async def consulta_client(c):
            async with sem:
                codi = c.get("c") or c.get("code") or c.get("id")
                nom = c.get("n") or c.get("name") or str(codi)
                if not codi:
                    return None
                try:
                    logger.info("comandes_per_data: consultant view_order date=%s client=%s (%s)", data, codi, nom)
                    r = await self.veure_comanda(data, codi)
                    linies = r.get("order", [])
                    linies_amb_qty = [l for l in linies if l.get("requested", 0) > 0]
                    if not linies_amb_qty:
                        return None
                    return {"codi": codi, "nom": nom, "linies": linies_amb_qty}
                except Exception:
                    return None

        resultats = await asyncio.gather(*[consulta_client(c) for c in clients_raw])
        clients_amb_comanda = [r for r in resultats if r is not None]

        totals = {}
        for client in clients_amb_comanda:
            for linia in client["linies"]:
                nom_art = linia.get("nm") or linia.get("artName") or linia.get("name") or str(linia.get("art", "?"))
                qty = linia.get("requested", 0)
                totals[nom_art] = totals.get(nom_art, 0) + qty

        return {"clients": clients_amb_comanda, "totals_per_article": totals}

    async def imprimir_albarans(self, data: str, client: int, copies: int = 1) -> dict:
        """Encua impressió d'albarà d'un client via ImpresoraIpAlbaranes."""
        try:
            return await self._tool("print_delivery_orders", {"date": data, "client": client, "copies": copies})
        except Exception as e:
            logger.warning(f"imprimir_albarans: {e}")
            return {"error": str(e)}
