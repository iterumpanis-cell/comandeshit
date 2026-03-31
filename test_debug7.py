"""Debug7: llegir tot l'HTML del HEAD frame per trobar el JS d'autocomplete."""
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

    print("\n=== HTML complet del HEAD frame ===")
    try:
        html = await head.evaluate("() => document.documentElement.innerHTML")
        print(html)
    except Exception as e:
        print(f"Error: {e}")

    print("\n=== HTML complet del NEW frame ===")
    try:
        html2 = await new.evaluate("() => document.documentElement.innerHTML")
        print(html2)
    except Exception as e:
        print(f"Error: {e}")

    await b.close()

asyncio.run(main())
