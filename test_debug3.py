"""Debug3: interceptar resposta de busca.asp per veure el contingut retornat."""
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

    # Interceptar les respostes de busca.asp
    responses_body = {}
    async def on_response(response):
        if 'busca.asp' in response.url:
            try:
                body = await response.text()
                responses_body[response.url] = body[:500]
                print(f"\n>>> RESPOSTA busca.asp [{response.status}]: {body[:300]}")
            except Exception as e:
                print(f"Error llegint resposta: {e}")

    b._page.on("response", on_response)

    # Interceptar errors de consola
    b._page.on("console", lambda msg: print(f"[CONSOLE {msg.type}] {msg.text}") if msg.type in ('error','warning') else None)

    # Posar data
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("29/03/2026")

    # Escriure al camp client
    loc = head.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await asyncio.sleep(0.3)
    await loc.fill("")

    print("\n=== Teclejant 'Cal ' (lletra a lletra) ===")
    for char in "Cal ":
        await loc.press(char if char != ' ' else 'Space')
        await asyncio.sleep(0.2)

    print("Esperant resposta AJAX...")
    await asyncio.sleep(5)

    # Verificar el contingut de HEAD iframe després de la resposta
    print("\n=== Contingut iframe HEAD (body) ===")
    try:
        html = await head.evaluate("() => document.body.innerHTML.slice(0, 1000)")
        print(f"HEAD body: {html}")
    except Exception as e:
        print(f"Error: {e}")

    # Comprovar #search_suggest a tots els contextos
    print("\n=== search_suggest en tots els frames ===")
    for fname, ctx in [("PAGE", b._page), ("HEAD", head), ("NEW", new)]:
        try:
            r = await ctx.evaluate(
                "() => { const d = document.getElementById('search_suggest'); "
                "return d ? {visible: d.style.display, html: d.innerHTML.slice(0,300), n: d.children.length} : 'NO EXISTEIX'; }"
            )
            print(f"  [{fname}]: {r}")
        except Exception as e:
            print(f"  [{fname}] error: {e}")

    # Veure tots els frames del context
    print("\n=== Tots els frames del navegador ===")
    for f in b._page.frames:
        print(f"  frame name={f.name!r} url={f.url}")

    print("\nEspera 5s per observar...")
    await asyncio.sleep(5)
    await b.close()

asyncio.run(main())
