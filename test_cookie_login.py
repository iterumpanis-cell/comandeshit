"""Test: login amb cookie + veure cp.asp i la URL d'El Forn per a cowork claude."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser, EMP_GUID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()

    page = b._page
    print(f"\nURL despres login: {page.url}")

    # Veure el contingut de cp.asp (ara hauria de mostrar portals)
    await asyncio.sleep(2)
    html = await page.evaluate("() => document.documentElement.outerHTML")
    print(f"\n=== cp.asp HTML ({len(html)} chars) ===")
    print(html)

    # Capturar qualsevol peticio que apunti a El Forn
    forn_urls = []
    def on_req(req):
        if 'ElForn' in req.url or 'Facturacion' in req.url:
            forn_urls.append(req.url)
    b._context.on("request", on_req)

    # Esperar que la pàgina faci les seves peticions AJAX
    await asyncio.sleep(5)
    print(f"\n=== URLs El Forn/Facturacion: {forn_urls} ===")

    # Cookies actuals
    cookies = await b._context.cookies()
    print(f"\n=== Cookies: {[c for c in cookies if 'hitsystems' in c.get('domain','')]} ===")

    await b._browser.close()
    await b._pw.stop()

asyncio.run(main())
