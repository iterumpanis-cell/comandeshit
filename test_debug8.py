"""Debug8: cridar searchSuggest() directament i inspeccionar parent."""
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

    # 1. Comprovar search_suggest_Resultats al parent (PAGE)
    print("\n=== Elements clau al PAGE (parent) ===")
    try:
        info = await b._page.evaluate("""() => {
            const r = document.getElementById('search_suggest_Resultats');
            const s = document.getElementById('search_suggest');
            return {
                resultats_val: r ? r.value : 'NO EXISTEIX',
                suggest_exists: s !== null,
                suggest_visible: s ? s.style.visibility : 'N/A',
                suggest_html: s ? s.innerHTML.slice(0,200) : 'N/A'
            };
        }""")
        print(f"PAGE: {info}")
    except Exception as e:
        print(f"Error PAGE: {e}")

    # 2. Posar data i escriure client via la funció JS real
    await head.locator("#fecha").click(click_count=3)
    await head.locator("#fecha").fill("29/03/2026")
    await asyncio.sleep(0.3)

    # 3. Cridar searchSuggest directament via evaluate
    print("\n=== Cridant searchSuggest('Cliente','Clients') via JS ===")
    try:
        await head.locator("#Suggest_Cliente").fill("Cal")
        # Cridar la funció JS directament
        result = await head.evaluate("""() => {
            const input = document.getElementById('Suggest_Cliente');
            if (!input) return 'NO INPUT';

            // Verificar l'accés a parent
            try {
                const p = parent.document.getElementById('search_suggest');
                const r = parent.document.getElementById('search_suggest_Resultats');
                return {
                    input_val: input.value,
                    parent_suggest: p !== null,
                    parent_resultats: r ? r.value : 'NO',
                    can_access_parent: true
                };
            } catch(e) {
                return {error: e.toString(), can_access_parent: false};
            }
        }""")
        print(f"Accés parent: {result}")
    except Exception as e:
        print(f"Error: {e}")

    # 4. Cridar searchSuggest() explícitament
    print("\n=== Cridant searchSuggest() directament ===")
    try:
        await head.evaluate("() => { searchSuggest('Cliente', 'Clients'); }")
        await asyncio.sleep(3)

        # Veure resultat
        sug = await b._page.evaluate("""() => {
            const s = document.getElementById('search_suggest');
            return {vis: s?.style.visibility, html: s?.innerHTML?.slice(0,300), len: s?.innerHTML?.length};
        }""")
        print(f"Suggest after call: {sug}")
    except Exception as e:
        print(f"Error cridant searchSuggest: {e}")

    # 5. Interceptar la resposta de busca.asp
    print("\n=== Resposta busca.asp via XHR natiu ===")
    try:
        resp = await head.evaluate("""async () => {
            return new Promise((resolve) => {
                const xhr = new XMLHttpRequest();
                const fecha = document.getElementById('fecha')?.value || '29/03/2026';
                const val = document.getElementById('Suggest_Cliente')?.value || 'Cal';
                const maxim = parent.document.getElementById('search_suggest_Resultats')?.value || '40';
                const url = '/Facturacion/include/busca.asp?Fecha=' + fecha +
                            '&Control=Cliente&Tipus=Clients&search=' + escape(val) +
                            '&Maxim=' + maxim + '&Rnd=' + Math.random() + '&FrameName=llistaComandesHead';
                console.log('URL:', url);
                xhr.open('GET', url, true);
                xhr.onreadystatechange = function() {
                    if (xhr.readyState === 4) {
                        resolve({status: xhr.status, len: xhr.responseText.length, body: xhr.responseText.slice(0,300)});
                    }
                };
                xhr.send(null);
                setTimeout(() => resolve({timeout: true}), 8000);
            });
        }""")
        print(f"Resposta XHR: {resp}")
    except Exception as e:
        print(f"Error XHR: {e}")

    await asyncio.sleep(3)
    await b.close()

asyncio.run(main())
