"""Test nav2: inspecció completa de cp.asp per trobar El Forn."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()
    page = b._page

    # Esperar que carregui bé
    await asyncio.sleep(2)

    print(f"\nURL: {page.url}")
    print(f"\n=== Frames ===")
    for f in page.frames:
        print(f"  [{f.name}] {f.url}")

    # HTML complet de la pàgina
    html = await page.evaluate("() => document.documentElement.outerHTML")
    print(f"\n=== HTML cp.asp ({len(html)} chars) ===")
    print(html[:3000])

    await b.close()

asyncio.run(main())
