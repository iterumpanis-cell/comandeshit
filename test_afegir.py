import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS, HEADLESS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=HEADLESS)
    await b.start()
    print("=== Iniciant prova afegir_linia ===")
    res = await b.afegir_linia(
        client="Cal Cabré",
        data="30/03/2026",
        producte="Gildes 1/4",
        demanat=7
    )
    print(f"Resultat: {res}")
    await b.close()

asyncio.run(main())
