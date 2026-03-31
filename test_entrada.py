"""Llegir entrada.asp amb emp=Iterum i entendre el flux de login."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import EMP_GUID
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="msedge", headless=False)
    context = await browser.new_context()

    await context.add_cookies([{"name": "idEmp", "value": EMP_GUID, "domain": "hitsystems.cloud", "path": "/"}])

    page = await context.new_page()

    # Llegir entrada.asp amb emp=Iterum
    print("\n=== entrada.asp amb emp=Iterum ===")
    await page.goto("https://hitsystems.cloud/Entrada/Js/entrada.asp?loga=usuario&emp=Iterum&img=Iterum")
    await asyncio.sleep(1)
    html = await page.evaluate("() => document.body ? document.body.innerText : document.documentElement.outerHTML")
    print(html)

    # Fer un POST directe a cp.asp via fetch amb les credencials i veure la resposta COMPLETA
    print("\n\n=== POST directe a cp.asp ===")
    try:
        result = await page.evaluate(f"""async () => {{
            const resp = await fetch('/Entrada/cp.asp', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer': 'https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum'
                }},
                body: 'usuario=cowork+claude&clave=claude&emp=Iterum&img=Iterum',
                credentials: 'include'
            }});
            const text = await resp.text();
            return {{status: resp.status, len: text.length, body: text}};
        }}""")
        print(f"Status: {result['status']}, len: {result['len']}")
        print(f"Body: {result['body']}")
    except Exception as e:
        print(f"Error: {e}")

    # Provar també amb usuario sense espai (cowork%20claude)
    print("\n\n=== POST directe amb cowork%20claude ===")
    try:
        result2 = await page.evaluate(f"""async () => {{
            const resp = await fetch('/Entrada/cp.asp', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'Referer': 'https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum'
                }},
                body: 'usuario=cowork%20claude&clave=claude&emp=Iterum&img=Iterum',
                credentials: 'include'
            }});
            const text = await resp.text();
            return {{status: resp.status, len: text.length, body: text.slice(0,1000)}};
        }}""")
        print(f"Status: {result2['status']}, len: {result2['len']}")
        print(f"Body: {result2['body']}")
    except Exception as e:
        print(f"Error: {e}")

    await browser.close()
    await pw.stop()

asyncio.run(main())
