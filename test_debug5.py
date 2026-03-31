"""Debug5: fetch directe de busca.asp + llista de clients disponibles."""
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

    # Esperar que els iframes carreguin
    await asyncio.sleep(2)

    # Fetch directe des del context del HEAD iframe (manté cookies de sessió)
    print("\n=== Fetch busca.asp amb search='' (tots els clients) ===")
    try:
        result = await head.evaluate("""
            async () => {
                const url = '/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead';
                const resp = await fetch(url, {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, body: text.slice(0, 1000)};
            }
        """)
        print(f"Status: {result['status']}")
        print(f"Body: {result['body']}")
    except Exception as e:
        print(f"Error fetch (head): {e}")

    # Intentar també des del frame PAGE
    print("\n=== Fetch busca.asp des de PAGE ===")
    try:
        result2 = await b._page.evaluate("""
            async () => {
                const url = '/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead';
                const resp = await fetch(url, {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, body: text.slice(0, 1000)};
            }
        """)
        print(f"Status: {result2['status']}")
        print(f"Body: {result2['body']}")
    except Exception as e:
        print(f"Error fetch (page): {e}")

    # Fetch amb search='Cal'
    print("\n=== Fetch busca.asp amb search='Cal' ===")
    try:
        result3 = await head.evaluate("""
            async () => {
                const url = '/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=Cal&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead';
                const resp = await fetch(url, {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, body: text.slice(0, 500)};
            }
        """)
        print(f"Status: {result3['status']}")
        print(f"Body: {result3['body']}")
    except Exception as e:
        print(f"Error fetch Cal: {e}")

    # Fetch amb search='El' per veure si retorna alguna cosa
    print("\n=== Fetch busca.asp amb search='El' ===")
    try:
        result4 = await head.evaluate("""
            async () => {
                const url = '/Facturacion/include/busca.asp?Fecha=29/03/2026&Control=Cliente&Tipus=Clients&search=El&Maxim=40&Rnd=0.5&FrameName=llistaComandesHead';
                const resp = await fetch(url, {credentials: 'include'});
                const text = await resp.text();
                return {status: resp.status, body: text.slice(0, 500)};
            }
        """)
        print(f"Status: {result4['status']}")
        print(f"Body: {result4['body']}")
    except Exception as e:
        print(f"Error fetch El: {e}")

    await b.close()

asyncio.run(main())
