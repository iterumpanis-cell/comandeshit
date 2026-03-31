"""
browser.py — Control automatitzat del navegador per a HitSystems
Playwright 1.58+ — API Locator moderna (sense mètodes Frame directes deprecats)
"""
import asyncio
import logging
import re
from playwright.async_api import async_playwright, Page, Frame, BrowserContext

logger = logging.getLogger(__name__)

LOGIN_EMPRESA_URL = "https://hitsystems.cloud/Entrada/dialogo.asp?loga=empresa&emp=Iterum&img=Iterum"
LOGIN_USUARI_URL  = "https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum"
GRAELLA_URL       = "https://hitsystems.cloud/Facturacion/ElForn/comandes/llistaComandes.asp"


class HitSystemsBrowser:
    def __init__(self, username: str, password: str, headless: bool = False):
        self.username = username
        self.password = password
        self.headless  = headless
        self._pw       = None
        self._browser  = None
        self._context: BrowserContext = None
        self._page: Page = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    #  Cicle de vida                                                       #
    # ------------------------------------------------------------------ #

    async def start(self):
        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(
                channel="msedge", headless=self.headless
            )
            logger.info("Navegador Edge iniciat ✓")
        except Exception:
            self._browser = await self._pw.chromium.launch(headless=self.headless)
            logger.info("Navegador Chromium iniciat ✓")
        self._context = await self._browser.new_context()
        self._page    = await self._context.new_page()
        await self._do_login()

    async def close(self):
        try:
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception as e:
            logger.error(f"Error tancant navegador: {e}")

    # ------------------------------------------------------------------ #
    #  Login                                                               #
    # ------------------------------------------------------------------ #

    async def _login_form(self, page, url: str, usuario: str, clave: str):
        """Omple i envia el formulari de login d'una pàgina dialogo.asp."""
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(0.5)
        await page.locator('input[type="text"]').fill(usuario)
        await page.locator('input[type="password"]').fill(clave)
        await page.locator('input[value="Entrar"]').click()

    async def _do_login(self):
        """Login en DOS PASSOS tal com fa HitSystems des d'un ordinador nou:
           1. Login d'empresa: usuari=ITERUM, clave=inicial
           2. Popup de login personal: usuari=cowork claude, clave=claude
        """
        if self._page is None or self._page.is_closed():
            self._page = await self._context.new_page()

        page = self._page

        # ── PAS 1: login d'empresa ──────────────────────────────────────
        logger.info("Login empresa (ITERUM)...")
        try:
            async with self._context.expect_page(timeout=10000) as p1_info:
                await self._login_form(page, LOGIN_EMPRESA_URL, "ITERUM", "inicial")
            empresa_page = await p1_info.value
            await empresa_page.wait_for_load_state("domcontentloaded")
            await asyncio.sleep(1)
            self._page = empresa_page
            page = empresa_page
            logger.info(f"Empresa login OK (URL: {page.url})")
        except Exception as e:
            # Pot ser que el login d'empresa no obri una nova pàgina
            logger.warning(f"Empresa login: {e}. Continuant amb la mateixa pàgina...")
            await asyncio.sleep(2)

        # ── PAS 2: popup de login personal ─────────────────────────────
        # Després del login d'empresa, el portal crida loga() que obre el popup personal.
        # Esperem que aparegui el popup o el gestionem directament.
        logger.info("Login personal (cowork claude)...")
        try:
            async with self._context.expect_page(timeout=10000) as p2_info:
                # Si el popup no s'ha obert sol, el forcem navegant a la URL personal
                try:
                    popup = await p2_info.value
                except Exception:
                    popup = None

            if popup:
                await popup.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(0.5)
                await popup.locator('input[type="text"]').fill(self.username)
                await popup.locator('input[type="password"]').fill(self.password)
                async with self._context.expect_page(timeout=8000) as p3_info:
                    await popup.locator('input[value="Entrar"]').click()
                final_page = await p3_info.value
                await final_page.wait_for_load_state("domcontentloaded")
                self._page = final_page
                logger.info(f"Login personal OK via popup (URL: {self._page.url})")
                return
        except Exception:
            pass

        # Fallback: login personal directe (sense popup)
        logger.info("Login personal directe...")
        try:
            async with self._context.expect_page(timeout=8000) as p_info:
                await self._login_form(page, LOGIN_USUARI_URL, self.username, self.password)
            new_page = await p_info.value
            await new_page.wait_for_load_state("domcontentloaded")
            self._page = new_page
        except Exception:
            await asyncio.sleep(2)
            self._page = page

        logger.info(f"Login correcte (URL: {self._page.url})")

        # ── PAS 3: pre-navegar a GRAELLA per establir context de sessió ──
        # Això permet que cerca_opcions funcioni immediatament sense navegar de nou.
        logger.info("Pre-navegant a GRAELLA per establir context...")
        try:
            # La pàgina pot haver-se tancat per onsubmit="self.close()" del formulari
            if self._page is None or self._page.is_closed():
                logger.info("Pàgina tancada post-login. Obrint nova pàgina...")
                self._page = await self._context.new_page()
            await self._page.goto(GRAELLA_URL, wait_until="domcontentloaded", timeout=15000)
            await self._page.wait_for_selector(
                'iframe[name="llistaComandesHead"]', timeout=10000
            )
            await asyncio.sleep(1)
            logger.info(f"GRAELLA pre-carregada OK (URL: {self._page.url})")
        except Exception as e:
            logger.warning(f"Pre-navegació GRAELLA fallida (no crític): {e}")

    # ------------------------------------------------------------------ #
    #  Navegació robusta a GRAELLA                                         #
    # ------------------------------------------------------------------ #

    async def _get_valid_page(self) -> Page:
        """Retorna sempre una pàgina vàlida, re-fent login si cal."""
        if self._page and not self._page.is_closed():
            return self._page

        logger.warning("Pàgina tancada. Obrint nova pàgina...")
        try:
            self._page = await self._context.new_page()
            return self._page
        except Exception:
            pass

        logger.warning("Context tancat. Tornant a fer login complet...")
        self._context = await self._browser.new_context()
        self._page    = await self._context.new_page()
        await self._do_login()
        return self._page

    async def _go_graella(self):
        """Navega a la GRAELLA assegurant-se que la sessió és vàlida."""
        page = await self._get_valid_page()

        try:
            await page.goto(GRAELLA_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            logger.warning(f"Error navegant a GRAELLA: {e}. Tornant a fer login...")
            await self._do_login()
            page = await self._get_valid_page()
            await page.goto(GRAELLA_URL, wait_until="domcontentloaded", timeout=15000)

        try:
            await page.wait_for_selector('iframe[name="llistaComandesHead"]', timeout=12000)
            await page.wait_for_selector('iframe[name="llistaComandesNew"]', timeout=12000)
            await asyncio.sleep(1)
        except Exception:
            logger.warning("Iframes no trobats via selector, esperant més...")
            await asyncio.sleep(6)

        self._page = page
        frame_urls = [f.url for f in page.frames]
        logger.info(f"GRAELLA carregada | Frame URLs: {frame_urls}")

    async def _get_frames(self):
        """Obté els iframes via content_frame() — funciona en headless i visible."""
        head = new = main = None
        try:
            el = await self._page.query_selector('iframe[name="llistaComandesHead"]')
            if el:
                head = await el.content_frame()
        except Exception:
            pass
        try:
            el = await self._page.query_selector('iframe[name="llistaComandesNew"]')
            if el:
                new = await el.content_frame()
        except Exception:
            pass
        try:
            el = await self._page.query_selector('iframe[name="llistaComandesMain"]')
            if el:
                main = await el.content_frame()
        except Exception:
            pass
        logger.info(f"Frames: head={head is not None} new={new is not None} main={main is not None}")
        return head, new, main

    # ------------------------------------------------------------------ #
    #  Autocomplete                                                        #
    # ------------------------------------------------------------------ #

    async def _autocomplete(self, frame: Frame, input_id: str, hidden_id: str, text: str) -> bool:
        """Escriu en un camp d'autocomplete i selecciona el resultat que coincideix amb text.
        El dropdown #search_suggest pot estar dins l'iframe o a nivell de pàgina."""
        loc = frame.locator(f"#{input_id}")
        text_lower = text.lower()

        for attempt_text in [text[:4], text[:6], text]:
            await loc.click(force=True)
            await asyncio.sleep(0.2)
            await loc.fill("")
            await loc.type(attempt_text, delay=120)
            await asyncio.sleep(0.3)

            for search_ctx in [frame, self._page]:
                try:
                    await search_ctx.wait_for_function(
                        "() => { const d = document.getElementById('search_suggest'); "
                        "return d && d.querySelectorAll('div').length > 0; }",
                        timeout=3500
                    )
                    divs = await search_ctx.locator("#search_suggest div").all()
                    if not divs:
                        continue

                    # Buscar el div que coincideix millor amb el text demanat
                    target = None
                    for div in divs:
                        t = await div.text_content()
                        if t and text_lower in t.strip().lower():
                            target = div
                            logger.info(f"Autocomplete: coincidència '{t.strip()}' per '{text}'")
                            break

                    # Si no hi ha coincidència exacta, usar el primer
                    if target is None:
                        target = divs[0]
                        t = await target.text_content()
                        logger.info(f"Autocomplete: sense coincidència, usant primer '{t.strip() if t else '?'}' per '{text}'")

                    await target.click()
                    await asyncio.sleep(0.6)
                    val = await self._get_hidden(frame, hidden_id)
                    if val:
                        logger.info(f"Autocomplete OK: {hidden_id}={val}")
                        return True
                except Exception:
                    pass

        # Darrer recurs: Tab
        try:
            await loc.press("Tab")
            await asyncio.sleep(0.5)
            val = await self._get_hidden(frame, hidden_id)
            if val:
                logger.info(f"Autocomplete OK [Tab]: {hidden_id}={val}")
                return True
        except Exception:
            pass

        return False

    async def _get_hidden(self, frame: Frame, hidden_id: str) -> str:
        try:
            return await frame.locator(f"#{hidden_id}").evaluate("el => el.value", timeout=1000)
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    #  Afegir línia de comanda                                            #
    # ------------------------------------------------------------------ #

    async def afegir_linia(self, client: str, data: str, producte: str,
                           demanat: int, servit: int | None = None,
                           encarreg: bool = False) -> dict:
        if servit is None:
            servit = demanat

        async with self._lock:
            try:
                await self._go_graella()
                head, new, _ = await self._get_frames()

                if not head or not new:
                    return {"ok": False, "error": "No es troben els iframes de la pàgina."}

                # 1. Data
                await head.locator("#fecha").click(click_count=3)
                await head.locator("#fecha").fill(data)

                # 2. Client
                ok_client = await self._autocomplete(head, "Suggest_Cliente", "Cliente", client)
                if not ok_client:
                    return {"ok": False, "error": f"Client '{client}' no trobat a HitSystems. Comprova l'ortografia."}
                await asyncio.sleep(0.5)

                # Re-obtenir iframes i esperar que el camp de producte existeixi
                for attempt in range(10):
                    _, new, _ = await self._get_frames()
                    if new:
                        try:
                            await new.wait_for_selector("#Suggest_artAux", state="attached", timeout=3000)
                            break
                        except Exception:
                            pass
                    await asyncio.sleep(1.5)
                else:
                    return {"ok": False, "error": "No es troba el formulari d'article."}

                # 3. Producte
                ok_prod = await self._autocomplete(new, "Suggest_artAux", "artAux", producte)
                if not ok_prod:
                    return {"ok": False, "error": f"Producte '{producte}' no trobat a HitSystems. Comprova l'ortografia."}

                # 4. Quantitats (esperar que el camp estigui habilitat)
                await new.locator('[name="qd"]').wait_for(state="visible", timeout=5000)
                await new.locator('[name="qd"]').click(click_count=3)
                await new.locator('[name="qd"]').fill(str(demanat))
                await new.locator('[name="qs"]').click(click_count=3)
                await new.locator('[name="qs"]').fill(str(servit))

                tipus = "Encarreg" if encarreg else "Afegir"
                logger.info(f"{tipus}: {client} | {data} | {producte} | D={demanat} S={servit}")

                # 5. Botó Afegir o Encarreg
                if encarreg:
                    # Log dels botons disponibles per depuració
                    try:
                        botons = await new.evaluate(
                            "() => Array.from(document.querySelectorAll('input[type=submit],input[type=button],button'))"
                            ".map(b => b.name + '|' + b.value + '|' + b.textContent)"
                        )
                        logger.info(f"Botons disponibles: {botons}")
                    except Exception:
                        pass
                    await new.locator('input[name="FesEncarreg"]').click()
                else:
                    await new.locator('input[name="Afegir"]').click()
                await asyncio.sleep(2)

                return {"ok": True}

            except Exception as e:
                logger.error(f"Error afegir_linia: {e}", exc_info=True)
                return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ #
    #  Cerca de clients via desplegable real del navegador                 #
    # ------------------------------------------------------------------ #

    async def cerca_clients(self, text: str) -> list:
        """Escriu text al camp client de la GRAELLA i retorna les opcions del desplegable real."""
        try:
            return await asyncio.wait_for(
                self._cerca_clients_impl(text), timeout=25.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"cerca_clients: timeout per '{text}'")
            return []
        except Exception as e:
            logger.warning(f"cerca_clients: error: {e}")
            return []

    async def _cerca_clients_impl(self, text: str) -> list:
        """Retorna llista de tuples (nom, codi_client) del desplegable real."""
        async with self._lock:
            await self._go_graella()
            head, _, _ = await self._get_frames()
            if not head:
                logger.warning("cerca_clients: no frame_head")
                return []

            loc = head.locator("#Suggest_Cliente")
            await loc.click(force=True)
            await asyncio.sleep(0.2)
            await loc.fill("")
            tipus_text = text[:6] if len(text) >= 6 else text
            await loc.type(tipus_text, delay=100)
            await asyncio.sleep(0.5)

            options = []
            for search_ctx in [head, self._page]:
                try:
                    await search_ctx.wait_for_function(
                        "() => { const d = document.getElementById('search_suggest'); "
                        "return d && d.querySelectorAll('div').length > 0; }",
                        timeout=4500
                    )
                    divs = await search_ctx.locator("#search_suggest div").all()
                    for div in divs:
                        t = await div.text_content()
                        if not t or not t.strip():
                            continue
                        # Extreure el codi de client de l'atribut onclick: "setClient(1040, ...)"
                        onclick = await div.get_attribute("onclick") or ""
                        m = re.search(r'\b(\d+)\b', onclick)
                        code = int(m.group(1)) if m else None
                        options.append((t.strip(), code))
                    if options:
                        logger.info(f"cerca_clients '{text}': {len(options)} opcions: {options}")
                        break
                except Exception:
                    pass

            try:
                await loc.press("Escape")
                await asyncio.sleep(0.2)
                await loc.fill("")
            except Exception:
                pass

            return options

