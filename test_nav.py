"""Test navegació: cp.asp → El Forn portal → Comandes → GRAELLA."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()

    # Estem a cp.asp — buscar el link d'El Forn
    page = b._page
    print(f"\nURL actual: {page.url}")

    # Veure links disponibles a cp.asp
    links = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('a, img[onclick], div[onclick], td[onclick]'))
            .map(el => ({tag: el.tagName, href: el.href||'', onclick: el.onclick?.toString()?.slice(0,100)||'', text: el.innerText?.slice(0,40)||'', title: el.title||'', src: el.src||''}))
            .filter(el => el.href || el.onclick)
            .slice(0, 30)
    """)
    print("\nElements clicables a cp.asp:")
    for l in links:
        print(f"  {l}")

    # Cercar específicament El Forn
    forn_links = await page.evaluate("""() =>
        Array.from(document.querySelectorAll('*'))
            .filter(el => el.innerText?.includes('Forn') || el.title?.includes('Forn') || el.src?.includes('Forn') || el.href?.includes('Forn'))
            .map(el => ({tag: el.tagName, href: el.href||'', text: el.innerText?.slice(0,50)||'', src: el.src||'', id: el.id}))
            .slice(0,10)
    """)
    print("\nElements relacionats amb 'Forn':")
    for l in forn_links:
        print(f"  {l}")

    await asyncio.sleep(15)
    await b.close()

asyncio.run(main())
