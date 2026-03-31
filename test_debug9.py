"""Debug9: interceptar busca.asp amb page.route() + veure si hi ha clients."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()

    # Interceptar busca.asp ABANS de navegar
    async def handle_busca(route, request):
        response = await route.fetch()
        body = await response.body()
        print(f"\n[ROUTE busca.asp] URL: {request.url[-120:]}")
        print(f"  Status: {response.status} | Body len: {len(body)}")
        if len(body) > 0:
            print(f"  Body: {body.decode('utf-8', errors='replace')[:400]}")
        else:
            print(f"  Body: BUIT!")
        await route.fulfill(response=response)

    await b._page.route("**/busca.asp**", handle_busca)

    await b._go_graella()
    head, new, _ = await b._get_frames()
    await asyncio.sleep(2)

    print("\n=== Omplint data ===")
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("29/03/2026")
    await asyncio.sleep(0.3)

    print("\n=== Teclejant al camp client ===")
    loc = head.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await asyncio.sleep(0.2)
    await loc.fill("")
    await loc.type("Cal ", delay=150)
    await asyncio.sleep(4)

    # Verificar suggest
    sug = await b._page.evaluate("""() => {
        const s = document.getElementById('search_suggest');
        return {vis: s?.style.visibility, html: s?.innerHTML?.slice(0,300), n: s?.children?.length};
    }""")
    print(f"\n=== search_suggest final: {sug} ===")

    # Veure clientes disponibles amb search buit (llista tots)
    print("\n=== Llista de tots els clients (search '') ===")
    try:
        result = await b._page.evaluate("""async () => {
            const resp = await fetch('/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=&Maxim=999&Rnd=0.1&FrameName=llistaComandesHead', {credentials:'include'});
            const text = await resp.text();
            return {status: resp.status, len: text.length, body: text.slice(0,500)};
        }""")
        print(f"  Fetch: {result}")
    except Exception as e:
        print(f"  Error: {e}")

    await asyncio.sleep(3)
    await b.close()

asyncio.run(main())
