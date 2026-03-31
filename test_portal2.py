"""Test portal2: veure el contingut de llistaComandes.asp."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser, PORTAL_URL, GRAELLA_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()

    page = b._page
    print(f"\nDupres login: {page.url}")

    # Navegar al portal
    print("\n=== Navegant al portal El Forn ===")
    await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(2)
    print(f"URL portal: {page.url}")
    html_portal = await page.evaluate("() => document.documentElement.outerHTML")
    print(f"HTML portal ({len(html_portal)} chars):")
    print(html_portal[:2000])

    # Navegar a GRAELLA
    print("\n=== Navegant a GRAELLA ===")
    await page.goto(GRAELLA_URL, wait_until="domcontentloaded", timeout=15000)
    await asyncio.sleep(3)
    print(f"URL graella: {page.url}")

    html_graella = await page.evaluate("() => document.documentElement.outerHTML")
    print(f"HTML graella ({len(html_graella)} chars):")
    print(html_graella[:2000])

    print("\nIframes:")
    for f in page.frames:
        print(f"  [{f.name}] {f.url}")

    await asyncio.sleep(5)
    await b._browser.close()
    await b._pw.stop()

asyncio.run(main())
