"""Test manual: obre la GRAELLA i pausa 60s per interactuar manualment."""
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

    await asyncio.sleep(1)
    print("\n>>> PAUSA DE 60 SEGONS: interactua amb el navegador manualment")
    print(">>> Escriu 'Cal' al camp Client i mira si surt el dropdown")
    print(">>> Apunta quins noms de client apareixen")

    # Interceptar totes les peticions mentre interactues
    def on_req(req):
        if 'busca' in req.url:
            print(f"  [REQ] {req.url[-150:]}")
    b._page.on("request", on_req)

    async def on_resp(resp):
        if 'busca' in resp.url:
            try:
                body = await resp.text()
                print(f"  [RESP {resp.status}] len={len(body)} | {body[:300]}")
            except:
                pass
    b._page.on("response", on_resp)

    # Esperar que arribi el contingut del HEAD iframe
    print("\n>>> Esperant que el HEAD frame carregui el formulari...")
    for i in range(20):
        await asyncio.sleep(0.5)
        try:
            body_html = await head.evaluate("() => document.body ? document.body.innerHTML.slice(0,100) : 'NO BODY'")
            if body_html and body_html != 'NO BODY' and len(body_html) > 10:
                print(f">>> HEAD frame carregat! ({len(body_html)} chars)")
                break
            else:
                print(f"  {i}: HEAD buit...")
        except Exception as e:
            print(f"  {i}: error: {e}")

    # Llegir l'HTML actual del HEAD
    try:
        html = await head.evaluate("() => document.documentElement.outerHTML")
        print(f"\n>>> HEAD HTML ({len(html)} chars):")
        print(html[:2000])
    except Exception as e:
        print(f"Error HEAD: {e}")

    print("\n>>> 60 SEGONS per interacció manual...")
    await asyncio.sleep(60)

    # Llegir l'estat final
    print("\n>>> Inspecció final:")
    for fname, ctx in [("PAGE", b._page), ("HEAD", head)]:
        try:
            r = await ctx.evaluate(
                "() => { const d = document.getElementById('search_suggest'); "
                "return d ? {v: d.style.display, h: d.innerHTML.slice(0,300)} : 'NO'; }"
            )
            print(f"  [{fname}] suggest: {r}")
        except Exception as e:
            print(f"  [{fname}] error: {e}")

    await b.close()

asyncio.run(main())
