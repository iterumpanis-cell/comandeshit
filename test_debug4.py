"""Debug4: veure respostes de busca.asp i polling de search_suggest."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser
from playwright.async_api import Page, Frame

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()
    await b._go_graella()
    head, new, _ = await b._get_frames()

    # Esperar que HEAD frame estigui realment carregat (per URL)
    await asyncio.sleep(1)
    print("\n=== Frames reals ===")
    head_frame = None
    for f in b._page.frames:
        print(f"  [{f.name}] {f.url}")
        if f.name == 'llistaComandesHead':
            head_frame = f

    if not head_frame:
        print("ERROR: No trobo llistaComandesHead per nom!")
        # Intentar amb el frame que tenim
        head_frame = head

    print(f"\nUsant head_frame URL: {head_frame.url}")

    # Interceptar TOTES les respostes (no sols busca.asp)
    async def on_response(response):
        url = response.url
        if 'busca' in url or 'suggest' in url or 'Clients' in url:
            try:
                body = await response.body()
                print(f"\n[RESP {response.status}] {url[:100]}")
                print(f"  Body: {body[:400]}")
            except Exception as e:
                print(f"\n[RESP {response.status}] {url[:100]} | Error body: {e}")

    b._page.on("response", on_response)
    # Afegir també al context del frame
    head_frame.on("response", on_response) if hasattr(head_frame, 'on') else None

    # Posar data
    await head_frame.locator("#fecha").click(click_count=3)
    await head_frame.locator("#fecha").fill("29/03/2026")
    await asyncio.sleep(0.5)

    loc = head_frame.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await asyncio.sleep(0.3)
    await loc.fill("")

    # Escriure lletra a lletra i vigilar search_suggest
    print("\n=== Teclejant i fent polling de search_suggest ===")
    for char in "Cal ":
        key = char if char != ' ' else 'Space'
        await loc.press(key)
        await asyncio.sleep(0.15)

        # Polling: comprovar si search_suggest té contingut
        for _ in range(10):
            await asyncio.sleep(0.3)
            try:
                sug = await b._page.evaluate(
                    "() => { const d = document.getElementById('search_suggest'); "
                    "return d ? d.innerHTML.slice(0,200) : 'NO EXISTEIX'; }"
                )
                if sug and sug != 'NO EXISTEIX' and len(sug) > 5:
                    print(f"  >>> SUGGEST ACTIU: {sug[:200]}")
                    break
            except Exception:
                pass

    print("\nEspera final 5s...")
    await asyncio.sleep(5)

    # Inspecció final
    print("\n=== Inspecció final search_suggest ===")
    for fname, ctx in [("PAGE", b._page), ("HEAD", head_frame)]:
        try:
            r = await ctx.evaluate(
                "() => { const d = document.getElementById('search_suggest'); "
                "return d ? {v: d.style.display, h: d.innerHTML.slice(0,300), n: d.children.length} : 'NO'; }"
            )
            print(f"  [{fname}]: {r}")
        except Exception as e:
            print(f"  [{fname}] error: {e}")

    # Intenta clicar un element del suggest si té contingut
    try:
        sug_count = await b._page.locator("#search_suggest div").count()
        print(f"\n#search_suggest divs a PAGE: {sug_count}")
        if sug_count > 0:
            texts = await b._page.locator("#search_suggest div").all_inner_texts()
            print(f"  Opcions: {texts[:5]}")
    except Exception as e:
        print(f"Error suggest: {e}")

    await b.close()

asyncio.run(main())
