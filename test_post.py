"""Interceptar el POST de login per veure exactament el que s'envia."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import EMP_GUID
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

LOGIN_URL = "https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum"

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="msedge", headless=False)
    context = await browser.new_context()

    # Cookie idEmp
    await context.add_cookies([{"name": "idEmp", "value": EMP_GUID, "domain": "hitsystems.cloud", "path": "/"}])

    # Interceptar cp.asp POST
    async def intercept_cp(route, request):
        print(f"\n[POST cp.asp]")
        print(f"  URL: {request.url}")
        print(f"  Method: {request.method}")
        print(f"  Headers: {dict(request.headers)}")
        body = request.post_data
        print(f"  Body: {body}")
        await route.continue_()

    await context.route("**/cp.asp**", intercept_cp)

    # Interceptar entrada.asp (el JS que valida el login)
    async def intercept_entrada(route, request):
        print(f"\n[entrada.asp] {request.url}")
        resp = await route.fetch()
        body = await resp.text()
        print(f"  Response ({len(body)} chars): {body[:500]}")
        await route.fulfill(response=resp)

    await context.route("**/entrada.asp**", intercept_entrada)

    page = await context.new_page()

    # Capturar totes les peticions del context
    context.on("request", lambda r: print(f"  REQ: {r.method} {r.url[:80]}") if any(x in r.url for x in ['cp.asp', 'entrada', 'loga']) else None)

    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await page.locator('input[type="text"]').fill(HITSYSTEMS_USER)
    await page.locator('input[type="password"]').fill(HITSYSTEMS_PASS)

    print(f"\n=== Fent click Entrar ===")
    try:
        async with context.expect_page(timeout=10000) as new_page_info:
            await page.locator('input[value="Entrar"]').click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(3)
        print(f"\nNova pàgina URL: {new_page.url}")
        html = await new_page.evaluate("() => document.documentElement.outerHTML")
        print(f"cp.asp HTML snippet: {html[500:1200]}")
    except Exception as e:
        print(f"Error: {e}")

    await asyncio.sleep(3)
    await browser.close()
    await pw.stop()

asyncio.run(main())
