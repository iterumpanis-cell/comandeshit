"""Debug: mostra el contingut de #search_suggest quan es tecleja al camp de client."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS, HEADLESS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=HEADLESS)
    await b.start()

    await b._go_graella()
    head, new, _ = await b._get_frames()

    # Posar la data
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("30/03/26")

    # Clicar el camp client i escriure
    loc = head.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await loc.fill("")
    await loc.type("Cal ", delay=120)

    print("Esperant 4 segons per AJAX...")
    await asyncio.sleep(4)

    # Inspeccionar search_suggest a tots els frames
    for name, ctx in [("PAGE", b._page), ("HEAD", head), ("NEW", new)]:
        try:
            html = await ctx.evaluate(
                "() => { const d = document.getElementById('search_suggest'); "
                "return d ? {display: d.style.display, innerHTML: d.innerHTML, childCount: d.children.length} : null; }"
            )
            print(f"[{name}] search_suggest: {html}")
        except Exception as e:
            print(f"[{name}] error: {e}")

    # Inspeccionar si hi ha algun div visible a la pàgina que sembli un dropdown
    for name, ctx in [("PAGE", b._page), ("HEAD", head)]:
        try:
            divs = await ctx.evaluate(
                "() => Array.from(document.querySelectorAll('div')).filter(d => {"
                "  const s = window.getComputedStyle(d);"
                "  return s.display !== 'none' && s.visibility !== 'hidden' && d.children.length > 0 && d.id;"
                "}).map(d => ({id: d.id, children: d.children.length, text: d.innerText.slice(0,80)}))"
            )
            print(f"[{name}] divs visibles amb ID: {divs}")
        except Exception as e:
            print(f"[{name}] error divs: {e}")

    await b.close()

asyncio.run(main())
