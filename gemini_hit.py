import asyncio
import json
import logging
import re
import unicodedata
from html import escape
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

from google import genai
from google.genai import types
from google.genai.errors import ServerError

from mcp_vendes import MCPVendes


logger = logging.getLogger(__name__)

NEEDS_SELECTION = "__needs_selection__"
NEEDS_CONFIRMATION = "__needs_confirmation__"


class GeminiHitAssistant:
    def __init__(
        self,
        api_key: str,
        model: str,
        mcp: MCPVendes | None = None,
        fallback_models: list[str] | None = None,
    ):
        self.api_key = api_key
        self.model = model or "gemini-2.5-flash"
        self.fallback_models = fallback_models or []
        self.mcp = mcp or MCPVendes()
        self._client = None
        self._tool = types.Tool(function_declarations=self._function_declarations())
        self._tool_handlers = {
            "search_client": self._search_client,
            "search_article": self._search_article,
            "search_clients_batch": self._search_clients_batch,
            "search_articles_batch": self._search_articles_batch,
            "request_user_selection": self._request_user_selection,
            "suggest_articles_for_client": self._suggest_articles_for_client,
            "view_order": self._view_order,
            "add_order": self._add_order,
            "aggregated_sales_day": self._aggregated_sales_day,
            "aggregated_sales_hour": self._aggregated_sales_hour,
            "print_delivery_notes": self._print_delivery_notes,
        }

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    async def _maybe_request_selection(self, text: str, results: list, selection_type: str):
        clean = [r for r in results if isinstance(r, dict) and "c" in r and "n" in r]
        if len(clean) <= 1:
            return clean

        exact = [r for r in clean if self._normalize_text(r["n"]) == self._normalize_text(text)]
        if len(exact) == 1:
            return exact

        question = (
            f'Per "{text}", a quin et refereixes?'
            if selection_type == "article"
            else f'Per "{text}", quin client vols dir?'
        )
        return await self._request_user_selection(question, clean[:10], selection_type)

    def _system_instruction(self) -> str:
        today = date.today().isoformat()
        return (
            "Ets l'assistent del bot de HitSystems. Parlem en catala. Respon sempre en catala, "
            "excepte si l'usuari demana explicitament un altre idioma. Sigues clar i breu. "
            f"La data d'avui es {today}. "
            "Quan la consulta sigui sobre clients, articles, comandes, albarans o vendes, usa les eines MCP disponibles "
            "en lloc d'inventar dades. No inventis codis, quantitats, resultats ni dates. "
            "Per accions que modifiquen dades, com add_order o print_delivery_notes, nomes les has d'executar si la intencio "
            "de l'usuari es explicita i tens tots els parametres necessaris. Si falta alguna dada, pregunta-la de forma concreta. "
            "Si l'usuari et saluda o et fa una pregunta general, pots respondre sense eines. "
            "Quan mostris un albara o una comanda, usa el format curt de Telegram del bot: "
            "primera linia amb el client i la data, linies separades amb una entrada per producte, "
            "prefix '🎗️ ' si order_type es 2, i el format de cada linia ha de ser exactament 'Nom del producte xquantitat'. "
            "Si l'eina view_order et torna un camp telegram_format, prioritza aquest text i no el reescriguis a una taula. "
            "Si search_article no troba el producte o retorna resultats poc clars, usa suggest_articles_for_client "
            "per proposar opcions basades tant en el MCP com en altres comandes del client en dies anteriors. "
            "IMPORTANT: quan necessitis buscar mes d'un client a la vegada, usa SEMPRE search_clients_batch en lloc de "
            "multiples crides a search_client. De la mateixa manera, quan necessitis buscar mes d'un article, usa SEMPRE "
            "search_articles_batch en lloc de multiples crides a search_article. Agrupa totes les busquedes del mateix "
            "tipus en una sola crida batch abans de continuar. "
            "Quan search_article o search_client retornen múltiples opcions molt similars i no pots determinar la correcta, "
            "usa request_user_selection per demanar confirmació a l'usuari. "
            "Quan crides add_order, inclou sempre client_name i article_name si els coneixes."
        )

    def _function_declarations(self) -> list[types.FunctionDeclaration]:
        return [
            types.FunctionDeclaration(
                name="search_client",
                description="Busca UN client de HitSystems per nom. Per buscar múltiples clients usa search_clients_batch.",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Text a buscar, per exemple 'Cabre' o 'Forn'.",
                        }
                    },
                    "required": ["text"],
                },
            ),
            types.FunctionDeclaration(
                name="search_article",
                description="Busca UN article o producte de HitSystems per nom. Per buscar múltiples articles usa search_articles_batch.",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Nom o fragment de nom del producte.",
                        }
                    },
                    "required": ["text"],
                },
            ),
            types.FunctionDeclaration(
                name="search_clients_batch",
                description=(
                    "Busca MÚLTIPLES clients de HitSystems en una sola crida. "
                    "Usa aquesta eina sempre que necessitis buscar 2 o més clients. "
                    "Retorna un diccionari {query: [resultats]} per a cada cerca."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "texts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Llista de noms de clients a buscar, per exemple ['Cabre', 'Forn Nou'].",
                        }
                    },
                    "required": ["texts"],
                },
            ),
            types.FunctionDeclaration(
                name="search_articles_batch",
                description=(
                    "Busca MÚLTIPLES articles o productes de HitSystems en una sola crida. "
                    "Usa aquesta eina sempre que necessitis buscar 2 o més articles. "
                    "Retorna un diccionari {query: [resultats]} per a cada cerca."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "texts": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Llista de noms d'articles a buscar, per exemple ['barra', 'pages kg', 'coca de pa'].",
                        }
                    },
                    "required": ["texts"],
                },
            ),
            types.FunctionDeclaration(
                name="request_user_selection",
                description=(
                    "Demana a l'usuari que seleccioni una opció d'una llista via enquesta. "
                    "Usa quan search_article o search_client retornen múltiples opcions similars "
                    "i no pots determinar la correcta sense confirmació de l'usuari. "
                    "NO uses aquesta eina si ja tens el codi correcte."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "Pregunta per mostrar a l'usuari"},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "c": {"type": "integer"},
                                    "n": {"type": "string"},
                                },
                            },
                            "description": "Llista d'opcions {c, n}, màxim 10",
                        },
                        "selection_type": {"type": "string", "description": "'article' o 'client'"},
                    },
                    "required": ["question", "options", "selection_type"],
                },
            ),
            types.FunctionDeclaration(
                name="suggest_articles_for_client",
                description=(
                    "Proposa articles quan search_article no troba prou be el producte. "
                    "Combina resultats del MCP i articles habituals del client en altres dies."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Nom aproximat o fragment del producte que busca l'usuari.",
                        },
                        "client": {
                            "type": "integer",
                            "description": "Codi numeric del client.",
                        },
                        "reference_date": {
                            "type": "string",
                            "description": "Data de referencia en format YYYY-MM-DD.",
                        },
                        "days_back": {
                            "type": "integer",
                            "description": "Nombre de dies enrere per revisar comandes anteriors del client.",
                        },
                    },
                    "required": ["text", "client", "reference_date"],
                },
            ),
            types.FunctionDeclaration(
                name="view_order",
                description="Mostra la comanda d'un client en una data concreta.",
                parameters={
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Data en format YYYY-MM-DD.",
                        },
                        "client": {
                            "type": "integer",
                            "description": "Codi numeric del client.",
                        },
                    },
                    "required": ["date", "client"],
                },
            ),
            types.FunctionDeclaration(
                name="add_order",
                description="Afegeix o actualitza una linia de comanda a HitSystems.",
                parameters={
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Data en format YYYY-MM-DD.",
                        },
                        "client": {
                            "type": "integer",
                            "description": "Codi numeric del client.",
                        },
                        "article_code": {
                            "type": "integer",
                            "description": "Codi numeric de l'article.",
                        },
                        "quantity": {
                            "type": "integer",
                            "description": "Quantitat a demanar. Pot ser 0 per deixar-la a zero.",
                        },
                        "order_type": {
                            "type": "integer",
                            "description": "1 per normal, 2 per encarrec.",
                        },
                        "client_name": {"type": "string", "description": "Nom del client (per mostrar en confirmació)"},
                        "article_name": {"type": "string", "description": "Nom de l'article (per mostrar en confirmació)"},
                    },
                    "required": ["date", "client", "article_code", "quantity"],
                },
            ),
            types.FunctionDeclaration(
                name="aggregated_sales_day",
                description="Consulta les vendes agregades d'una botiga en un dia.",
                parameters={
                    "type": "object",
                    "properties": {
                        "shop_code": {
                            "type": "integer",
                            "description": "Codi numeric de la botiga.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Data en format YYYY-MM-DD.",
                        },
                    },
                    "required": ["shop_code", "date"],
                },
            ),
            types.FunctionDeclaration(
                name="aggregated_sales_hour",
                description="Consulta les vendes agregades d'una botiga en una hora concreta.",
                parameters={
                    "type": "object",
                    "properties": {
                        "shop_code": {
                            "type": "integer",
                            "description": "Codi numeric de la botiga.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Data en format YYYY-MM-DD.",
                        },
                        "hour": {
                            "type": "integer",
                            "description": "Hora de 0 a 23.",
                        },
                    },
                    "required": ["shop_code", "date", "hour"],
                },
            ),
            types.FunctionDeclaration(
                name="print_delivery_notes",
                description="Envia una peticio d'impressio d'albarans.",
                parameters={
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "description": "Data en format YYYY-MM-DD.",
                        },
                        "client": {
                            "type": "integer",
                            "description": "Codi numeric del client, opcional.",
                        },
                        "trip": {
                            "type": "string",
                            "description": "Nom o codi del viatge, opcional.",
                        },
                    },
                    "required": ["date"],
                },
            ),
        ]

    def _generate_sync(self, contents, config):
        client = self._get_client()
        return client.models.generate_content(
            model=config["model_name"],
            contents=contents,
            config=config["generation_config"],
        )

    async def ask(self, prompt: str, history: list[dict] | None = None) -> str | dict:
        if not self.enabled:
            raise RuntimeError("GEMINI_API_KEY no configurada.")
        contents = self._history_to_contents(history or [])
        contents.append(types.Content(role="user", parts=[types.Part(text=prompt)]))
        return await self._conversation_loop(contents)

    async def continue_from_contents(self, contents: list) -> str | dict:
        """Reprèn la conversa des d'un estat guardat (després d'interacció de l'usuari)."""
        return await self._conversation_loop(contents)

    async def _conversation_loop(self, contents: list) -> str | dict:
        generation_config = types.GenerateContentConfig(
            system_instruction=self._system_instruction(),
            tools=[self._tool],
            temperature=0.3,
        )
        model_candidates = [self.model] + [m for m in self.fallback_models if m != self.model]

        for _ in range(14):
            response = None
            last_error = None
            for model_name in model_candidates:
                try:
                    response = await asyncio.to_thread(
                        self._generate_sync,
                        contents,
                        {"model_name": model_name, "generation_config": generation_config},
                    )
                    break
                except ServerError as exc:
                    last_error = exc
                    logger.warning("Gemini model %s ha fallat temporalment: %s", model_name, exc)
                    continue

            if response is None:
                raise last_error or RuntimeError("Cap model Gemini disponible.")

            tool_calls = list(getattr(response, "function_calls", []) or [])
            if not tool_calls:
                text = (getattr(response, "text", None) or "").strip()
                if text:
                    return text
                candidate = response.candidates[0].content if response.candidates else None
                if candidate and candidate.parts:
                    text_parts = [part.text for part in candidate.parts if getattr(part, "text", None)]
                    if text_parts:
                        return "\n".join(text_parts).strip()
                raise RuntimeError("Gemini no ha retornat cap resposta de text.")

            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            pending_confirmations = []

            for tool_call in tool_calls:
                tool_result = await self._run_tool(tool_call.name, dict(tool_call.args or {}))

                # Interacció: selecció d'usuari
                if isinstance(tool_result, dict) and NEEDS_SELECTION in tool_result:
                    # Afegim resposta placeholder
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(
                                name=tool_call.name,
                                response={"result": {"status": "waiting_for_user"}},
                            )],
                        )
                    )
                    tool_result["__gemini_contents__"] = contents
                    return tool_result

                # Interacció: confirmació de comanda
                if isinstance(tool_result, dict) and NEEDS_CONFIRMATION in tool_result:
                    pending_confirmations.append({**tool_result, "__tool_name__": tool_call.name})
                    contents.append(
                        types.Content(
                            role="user",
                            parts=[types.Part.from_function_response(
                                name=tool_call.name,
                                response={"result": {"status": "pending_confirmation"}},
                            )],
                        )
                    )
                    continue

                contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name=tool_call.name,
                            response={"result": tool_result},
                        )],
                    )
                )

            if pending_confirmations:
                return {
                    NEEDS_CONFIRMATION: True,
                    "lines": pending_confirmations,
                    "__gemini_contents__": contents,
                }

        raise RuntimeError("Gemini ha superat el maxim de crides d'eina en una sola resposta.")

    async def transcribe_audio(self, file_path: str) -> str:
        if not self.enabled:
            raise RuntimeError("GEMINI_API_KEY no configurada.")
        return await asyncio.to_thread(self._transcribe_audio_sync, file_path)

    def _transcribe_audio_sync(self, file_path: str) -> str:
        client = self._get_client()
        uploaded = client.files.upload(file=file_path)
        try:
            response = None
            last_error = None
            for model_name in [self.model] + [m for m in self.fallback_models if m != self.model]:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[
                            "Transcriu l'audio de forma literal i en el mateix idioma del parlant. "
                            "Si no s'enten, indica-ho de forma breu.",
                            uploaded,
                        ],
                    )
                    break
                except ServerError as exc:
                    last_error = exc
                    logger.warning("Gemini model %s ha fallat temporalment transcrivint audio: %s", model_name, exc)
                    continue

            if response is None:
                raise last_error or RuntimeError("Cap model Gemini disponible per transcriure audio.")
            return (response.text or "").strip()
        finally:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                logger.warning("No s'ha pogut esborrar el fitxer temporal de Gemini.", exc_info=True)

    def _history_to_contents(self, history: list[dict]) -> list[types.Content]:
        contents = []
        for item in history[-12:]:
            role = item.get("role")
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if role == "assistant":
                role = "model"
            if role not in {"user", "model"}:
                continue
            contents.append(types.Content(role=role, parts=[types.Part(text=text)]))
        return contents

    async def _run_tool(self, name: str, args: dict):
        handler = self._tool_handlers.get(name)
        if handler is None:
            return {"ok": False, "error": f"Eina desconeguda: {name}"}

        try:
            return await handler(**args)
        except TypeError as exc:
            logger.warning("Arguments invalids per a %s: %s", name, exc)
            return {"ok": False, "error": f"Arguments invalids per a {name}: {exc}"}
        except Exception as exc:
            logger.exception("Error executant l'eina %s", name)
            return {"ok": False, "error": str(exc)}

    async def _secondary_ai_search(self, query: str, items: list, kind: str) -> list:
        """IA secundària: rep el catàleg complet i retorna els millors resultats.
        La IA principal mai veu la llista sencera, només el resultat final."""
        if not items:
            return []

        lines = []
        for item in items:
            if isinstance(item, dict) and "c" in item and "n" in item:
                lines.append(f"c={item['c']} n={item['n']}")
        if not lines:
            return []

        items_text = "\n".join(lines)
        prompt = (
            f"Ets un assistent de cerca especialitzat en productes de fleca i clients. "
            f"Aqui tens la llista completa de {kind}s disponibles:\n"
            f"{items_text}\n\n"
            f"L'usuari busca: \"{query}\"\n\n"
            f"Retorna en format JSON (sense cap altre text) la llista dels {kind}s que millor coincideixen (maxim 5). "
            f"Interpreta variacions col·loquials catalanes: 'mitg' o 'mig' = '1/2', 'quart' = '1/4', "
            f"'pa pages' = 'pages', 'barra' = 'barra de pa', etc. "
            f"Si no hi ha cap coincidencia clara, retorna llista buida. "
            f"Format de resposta: [{{\"c\": 123, \"n\": \"Nom\"}}]"
        )

        def _generate():
            client = self._get_client()
            resp = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=[prompt],
                config=types.GenerateContentConfig(temperature=0.0),
            )
            return (resp.text or "").strip()

        try:
            text = await asyncio.to_thread(_generate)
            logger.info(f"_secondary_ai_search ({kind}) query='{query}' resposta: {text[:200]}")
            match = re.search(r'\[.*?\]', text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"_secondary_ai_search ({kind}): {e}")

        return []

    async def _find_client_matches(self, text: str):
        all_clients = await self.mcp.llistar_tots_clients()
        if all_clients:
            result = await self._secondary_ai_search(text, all_clients, "client")
            if result:
                return result
        logger.info(f"_search_client: fallback cerca directa MCP per '{text}'")
        return await self.mcp.cercar_client(text)

    async def _find_article_matches(self, text: str):
        all_articles = await self.mcp.llistar_tots_articles()
        if all_articles:
            result = await self._secondary_ai_search(text, all_articles, "article")
            if result:
                return result
        logger.info(f"_search_article: fallback cerca directa MCP per '{text}'")
        return await self.mcp.cercar_article(text)

    async def _search_client(self, text: str):
        results = await self._find_client_matches(text)
        return await self._maybe_request_selection(text, results, "client")

    async def _search_article(self, text: str):
        results = await self._find_article_matches(text)
        return await self._maybe_request_selection(text, results, "article")

    async def _search_clients_batch(self, texts: list):
        """Busca múltiples clients en paral·lel i retorna {query: resultats}."""
        results = await asyncio.gather(*[self._find_client_matches(t) for t in texts], return_exceptions=True)
        return {
            text: (r if not isinstance(r, Exception) else [])
            for text, r in zip(texts, results)
        }

    async def _search_articles_batch(self, texts: list):
        """Busca múltiples articles en paral·lel i retorna {query: resultats}."""
        results = await asyncio.gather(*[self._find_article_matches(t) for t in texts], return_exceptions=True)
        return {
            text: (r if not isinstance(r, Exception) else [])
            for text, r in zip(texts, results)
        }

    async def _suggest_articles_for_client(
        self,
        text: str,
        client: int,
        reference_date: str,
        days_back: int = 21,
    ):
        mcp_results = await self.mcp.cercar_article(text)
        recent_order_articles = await self._recent_client_articles(client, reference_date, days_back)

        ranked = []
        seen_codes = set()

        for item in mcp_results:
            code = item.get("c")
            name = item.get("n")
            if code is None or not name:
                continue
            ranked.append({
                "source": "mcp",
                "c": code,
                "n": name,
                "score": round(self._article_score(text, name) + 0.25, 4),
            })
            seen_codes.add(code)

        for item in recent_order_articles:
            code = item.get("c")
            name = item.get("n")
            if code is None or not name or code in seen_codes:
                continue
            score = self._article_score(text, name)
            if score < 0.55:
                continue
            ranked.append({
                "source": "history",
                "c": code,
                "n": name,
                "last_seen": item.get("last_seen"),
                "score": round(score, 4),
            })
            seen_codes.add(code)

        ranked.sort(key=lambda x: (-x["score"], x["n"]))
        return {
            "query": text,
            "client": client,
            "reference_date": reference_date,
            "suggestions": ranked[:8],
        }

    async def _view_order(self, date: str, client: int):
        result = await self.mcp.veure_comanda(date, client)
        result["telegram_format"] = self._format_order_ticket(result)
        return result

    async def _request_user_selection(self, question: str, options: list, selection_type: str):
        return {
            NEEDS_SELECTION: True,
            "question": question,
            "options": options[:10],
            "selection_type": selection_type,
        }

    async def execute_order(self, date: str, client: int, article_code: int, quantity: int, order_type: int = 1) -> dict:
        """Executa directament una comanda (usat per bot.py després de confirmació)."""
        return await self.mcp.afegir_linia_mcp(date, client, article_code, quantity, order_type)

    async def _add_order(
        self,
        date: str,
        client: int,
        article_code: int,
        quantity: int,
        order_type: int = 1,
        client_name: str = "?",
        article_name: str = "?",
    ):
        return {
            NEEDS_CONFIRMATION: True,
            "date": date,
            "client": client,
            "client_name": client_name,
            "article_code": article_code,
            "article_name": article_name,
            "quantity": quantity,
            "order_type": order_type,
        }

    async def _aggregated_sales_day(self, shop_code: int, date: str):
        return await self.mcp.vendes_dia(shop_code, date)

    async def _aggregated_sales_hour(self, shop_code: int, date: str, hour: int):
        return await self.mcp.vendes_hora(shop_code, date, hour)

    async def _print_delivery_notes(self, date: str, client: int | None = None, trip: str | None = None):
        return await self.mcp.imprimir_albarans(date, client, trip)

    def _format_order_ticket(self, result: dict) -> str:
        if "error" in result:
            return f"Error: {result['error']}"

        lines = result.get("order", [])
        client_name = result.get("client_name") or result.get("client") or "Client"
        order_date = result.get("date") or result.get("order_date") or "Data"
        rows = []
        total_units = 0
        for item in lines:
            requested = item.get("requested", 0)
            if not requested:
                continue

            name = item.get("nm") or item.get("artName") or item.get("name") or str(item.get("art", "?"))
            order_type = item.get("order_type", 1)
            total_units += requested
            rows.append({
                "is_order": order_type == 2,
                "qty": requested,
                "name": name,
            })

        if not rows:
            return "No hi ha linies per aquesta comanda."

        qty_width = max(3, max(len(str(row["qty"])) for row in rows))
        name_width = max(len("ARTICLE"), max(len(str(row["name"])) for row in rows))
        header_row = f"{'QTY':>{qty_width}}  {'ARTICLE':<{name_width}}"
        sep_row = f"{'-' * qty_width}  {'-' * min(name_width, 28)}"
        body_lines = []
        for row in rows:
            base = f"{str(row['qty']).rjust(qty_width)}  {row['name']:<{name_width}}"
            if row["is_order"]:
                body_lines.append(f"<b><code>{escape(base)}</code></b> 🎗️")
            else:
                body_lines.append(f"<code>{escape(base)}</code>")
        header = (
            "<b>🧾 Comanda</b>\n"
            f"<b>👤 Client:</b> {escape(str(client_name))}\n"
            f"<b>📅 Data:</b> {escape(str(order_date))}\n"
            f"<b>📦 Línies:</b> {len(rows)}    <b>🥖 Unitats:</b> {total_units}"
        )
        table = [f"<code>{escape(header_row)}</code>", f"<code>{escape(sep_row)}</code>", *body_lines]
        return f"{header}\n" + "\n".join(table)

    def _format_order_for_telegram(self, result: dict) -> str:
        if "error" in result:
            return f"Error: {result['error']}"

        lines = result.get("order", [])
        formatted = []
        for item in lines:
            requested = item.get("requested", 0)
            if not requested:
                continue

            name = item.get("nm") or item.get("artName") or item.get("name") or str(item.get("art", "?"))
            order_type = item.get("order_type", 1)
            prefix = "🎗️ " if order_type == 2 else ""
            formatted.append(f"{prefix}{name} x{requested}")

        if not formatted:
            return "No hi ha linies per aquesta comanda."

        return "\n".join(formatted)

    async def _recent_client_articles(self, client: int, reference_date: str, days_back: int) -> list[dict]:
        try:
            base_date = datetime.strptime(reference_date, "%Y-%m-%d").date()
        except ValueError:
            base_date = date.today()

        articles: dict[int, dict] = {}
        for offset in range(1, max(1, days_back) + 1):
            query_date = (base_date - timedelta(days=offset)).isoformat()
            result = await self.mcp.veure_comanda(query_date, client)
            if "error" in result:
                continue

            for item in result.get("order", []):
                code = item.get("art")
                name = item.get("nm") or item.get("artName") or item.get("name")
                requested = item.get("requested", 0)
                if code is None or not name or not requested:
                    continue

                previous = articles.get(code)
                if previous is None:
                    articles[code] = {"c": code, "n": name, "last_seen": query_date}
                else:
                    previous["last_seen"] = max(previous["last_seen"], query_date)

        return list(articles.values())

    def _article_score(self, query: str, candidate: str) -> float:
        q = self._normalize_text(query)
        c = self._normalize_text(candidate)
        if not q or not c:
            return 0.0

        ratio = SequenceMatcher(None, q, c).ratio()
        q_tokens = [token for token in q.split() if token]
        c_tokens = [token for token in c.split() if token]

        overlap = 0.0
        if q_tokens:
            matches = sum(1 for token in q_tokens if any(token in ct or ct in token for ct in c_tokens))
            overlap = matches / len(q_tokens)

        contains = 1.0 if q in c or any(token in c for token in q_tokens) else 0.0
        return max(ratio, overlap * 0.9, contains * 0.8)

    def _normalize_text(self, text: str) -> str:
        text = unicodedata.normalize("NFKD", text)
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        return " ".join(text.lower().strip().split())
