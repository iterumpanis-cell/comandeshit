"""Debug6: buscar clients amb diverses cerques per veure si n'hi ha cap."""
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

    # Provar diverses cerques per veure si n'hi ha cap client
    searches = ['a', 'e', 'i', 'o', 'cal', 'can', 'el', 'la', 'casa', '']
    base = '/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead&search='

    for s in searches:
        try:
            result = await head.evaluate(f"""
                async () => {{
                    const resp = await fetch('{base}{s}', {{credentials: 'include'}});
                    const text = await resp.text();
                    // Buscar divs de resultats - normalment JSON o HTML
                    return {{status: resp.status, len: text.length, snippet: text.slice(0,150)}};
                }}
            """)
            print(f"search='{s}': status={result['status']} len={result['len']} => {result['snippet'][:100]}")
        except Exception as e:
            print(f"search='{s}': ERROR {e}")

    # Provar amb l'URL que utilitza el JS real (mirant el codi de la pàgina)
    # La funció busca() del JS llança la petició — vegem quin és l'endpoint real
    print("\n=== Llegir codi JS del HEAD frame per trobar l'endpoint real ===")
    try:
        js_code = await head.evaluate("() => document.documentElement.innerHTML.slice(0, 3000)")
        print(js_code)
    except Exception as e:
        print(f"Error: {e}")

    await b.close()

asyncio.run(main())
