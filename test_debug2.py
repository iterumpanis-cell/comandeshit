"""Debug2: mode visible + xarxa per diagnosticar l'autocomplete del client."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    # FORÇAR headless=False per veure el navegador
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=False)
    await b.start()

    await b._go_graella()
    head, new, _ = await b._get_frames()

    # Veure els iframes en detall
    print("\n=== URLs dels frames ===")
    for f in b._page.frames:
        print(f"  [{f.name}] {f.url}")

    # Posar la data
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("29/03/2026")

    # Interceptar les peticions de xarxa
    requests_made = []
    def on_request(req):
        if 'suggest' in req.url.lower() or 'cliente' in req.url.lower() or 'ajax' in req.url.lower():
            requests_made.append(req.url)

    b._page.on("request", on_request)
    # Interceptar des de tots els frames
    for f in b._page.frames:
        try:
            f.on("request", on_request)  # type: ignore
        except Exception:
            pass

    # Monitoritzar totes les peticions
    all_requests = []
    b._page.on("request", lambda r: all_requests.append(r.url))

    # Clicar el camp client i escriure
    loc = head.locator("#Suggest_Cliente")
    await loc.click(force=True)
    await asyncio.sleep(0.3)
    await loc.fill("")
    await asyncio.sleep(0.2)

    print("\n=== Teclejant 'Cal' ===")
    for char in "Cal ":
        await loc.press(char if char != ' ' else 'Space')
        await asyncio.sleep(0.15)

    print("Esperant 4 segons per AJAX...")
    await asyncio.sleep(4)

    # Inspecció del suggest a tots els frames
    print("\n=== Inspecció search_suggest ===")
    for fname, ctx in [("PAGE", b._page), ("HEAD", head), ("NEW", new)]:
        try:
            html = await ctx.evaluate(
                "() => { const d = document.getElementById('search_suggest'); "
                "return d ? {display: d.style.display, innerHTML: d.innerHTML.slice(0,300), childCount: d.children.length} : null; }"
            )
            print(f"[{fname}] search_suggest: {html}")
        except Exception as e:
            print(f"[{fname}] error: {e}")

    # Inspecció del camp (valor actual)
    try:
        val_text = await head.locator("#Suggest_Cliente").input_value()
        val_hidden = await head.locator("#Cliente").evaluate("el => el.value")
        print(f"\n=== Camp client: text='{val_text}' hidden='{val_hidden}' ===")
    except Exception as e:
        print(f"Error llegint camp: {e}")

    # Peticions de xarxa fetes
    print(f"\n=== Peticions AJAX (suggest/cliente): {requests_made} ===")
    print(f"=== Totes les peticions recents: {[r for r in all_requests[-20:]]} ===")

    # Cercar qualsevol dropdown visible
    print("\n=== Divs visibles amb text ===")
    try:
        divs = await b._page.evaluate(
            "() => Array.from(document.querySelectorAll('div,ul,li')).filter(d => {"
            "  const s = window.getComputedStyle(d);"
            "  return s.display !== 'none' && d.innerText.trim().length > 2 && d.id;"
            "}).map(d => ({id: d.id, tag: d.tagName, text: d.innerText.slice(0,100)}))"
        )
        print(f"PAGE: {divs}")
    except Exception as e:
        print(f"Error: {e}")

    try:
        divs = await head.evaluate(
            "() => Array.from(document.querySelectorAll('div,ul,li')).filter(d => {"
            "  const s = window.getComputedStyle(d);"
            "  return s.display !== 'none' && d.innerText.trim().length > 2 && d.id;"
            "}).map(d => ({id: d.id, tag: d.tagName, text: d.innerText.slice(0,100)}))"
        )
        print(f"HEAD: {divs}")
    except Exception as e:
        print(f"Error HEAD: {e}")

    print("\nEspera 8 segons per observar el navegador...")
    await asyncio.sleep(8)
    await b.close()

asyncio.run(main())
