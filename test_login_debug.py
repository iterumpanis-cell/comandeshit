"""Debug login: veure el formulari de dialogo.asp i el flux complet."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

LOGIN_URL = "https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum"

async def main():
    b = HitSystemsBrowser("cowork_claude", HITSYSTEMS_PASS, headless=False)
    b._pw = await __import__('playwright.async_api', fromlist=['async_playwright']).async_playwright().start()
    b._browser = await b._pw.chromium.launch(channel="msedge", headless=False)
    b._context = await b._browser.new_context()
    b._page = await b._context.new_page()

    page = b._page

    # 1. Navegar al login
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await asyncio.sleep(1)

    # 2. Veure el formulari
    html = await page.evaluate("() => document.documentElement.outerHTML")
    print(f"\n=== dialogo.asp HTML ({len(html)} chars) ===")
    print(html[:4000])

    # 3. Veure cookies actuals
    cookies = await b._context.cookies()
    print(f"\n=== Cookies pre-login: {cookies} ===")

    # 4. Fer login
    print("\n=== Fent login... ===")
    await page.locator('input[type="text"]').fill("cowork_claude")
    await page.locator('input[type="password"]').fill(HITSYSTEMS_PASS)

    # Interceptar la petició d'enviament del formulari
    all_reqs = []
    page.on("request", lambda r: all_reqs.append(f"{r.method} {r.url[:100]}"))

    try:
        async with b._context.expect_page(timeout=8000) as new_page_info:
            await page.locator('input[value="Entrar"]').click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(2)
        print(f"Nova pàgina URL: {new_page.url}")
        html2 = await new_page.evaluate("() => document.documentElement.outerHTML")
        print(f"\n=== cp.asp HTML ({len(html2)} chars) ===")
        print(html2)
        # Cookies
        cookies2 = await b._context.cookies()
        print(f"\n=== Cookies post-login: {cookies2} ===")
    except Exception as e:
        print(f"No nova pàgina: {e}")
        await asyncio.sleep(2)
        cookies2 = await b._context.cookies()
        print(f"\n=== Cookies: {cookies2} ===")

    print(f"\n=== Peticions fetes: {all_reqs[:20]} ===")

    await asyncio.sleep(5)
    await b._browser.close()
    await b._pw.stop()

asyncio.run(main())
