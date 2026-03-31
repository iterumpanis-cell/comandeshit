"""Test: flux amb PORTAL_URL primer → verificar que busca.asp retorna clients."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()
    await b._go_graella()
    head, new, _ = await b._get_frames()
    await asyncio.sleep(2)

    print(f"\nURL actual: {b._page.url}")
    print(f"Frames: head={head is not None} new={new is not None}")

    # Test busca.asp: buscar clients amb "Cal"
    print("\n=== Test busca.asp amb search='Cal' ===")
    try:
        result = await head.evaluate("""
            async () => {
                const resp = await fetch('/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=Cal&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead', {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, len: text.length, body: text.slice(0, 300)};
            }
        """)
        print(f"Resultat: {result}")
    except Exception as e:
        print(f"Error: {e}")

    # Test busca.asp: buscar amb "Cal Cabr"
    print("\n=== Test busca.asp amb search='Cal Cabr' ===")
    try:
        result2 = await head.evaluate("""
            async () => {
                const resp = await fetch('/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=Cal+Cabr&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead', {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, len: text.length, body: text.slice(0, 300)};
            }
        """)
        print(f"Resultat: {result2}")
    except Exception as e:
        print(f"Error: {e}")

    # Teclejar al camp i veure si apareix el dropdown
    print("\n=== Teclejant 'Cal' al camp client ===")
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("29/03/2026")
    await asyncio.sleep(0.3)

    async def on_resp(resp):
        if 'busca' in resp.url:
            body = await resp.body()
            print(f"  [RESP busca.asp] len={len(body)} | {body[:200]}")
    b._page.on("response", on_resp)

    loc = head.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await asyncio.sleep(0.2)
    await loc.fill("")
    await loc.type("Cal ", delay=150)
    await asyncio.sleep(4)

    sug = await b._page.evaluate("""() => {
        const s = document.getElementById('search_suggest');
        return {vis: s?.style.visibility, html: s?.innerHTML?.slice(0,300), n: s?.children?.length};
    }""")
    print(f"\nsearch_suggest: {sug}")

    await asyncio.sleep(5)
    await b.close()

asyncio.run(main())
