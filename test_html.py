"""Llegir l'HTML complet del HEAD frame i guardar-lo."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_USER, HITSYSTEMS_PASS
from browser import HitSystemsBrowser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

async def main():
    b = HitSystemsBrowser(HITSYSTEMS_USER, HITSYSTEMS_PASS, headless=True)
    await b.start()
    await b._go_graella()
    head, new, _ = await b._get_frames()

    # Esperar que el frame carregui
    for i in range(20):
        await asyncio.sleep(0.5)
        try:
            body_html = await head.evaluate("() => document.body ? document.body.innerHTML : ''")
            if body_html and len(body_html) > 50:
                break
        except:
            pass

    html = await head.evaluate("() => document.documentElement.outerHTML")
    with open(r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot\head_frame.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Guardat head_frame.html ({len(html)} chars)")

    html2 = await new.evaluate("() => document.documentElement.outerHTML")
    with open(r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot\new_frame.html", "w", encoding="utf-8") as f:
        f.write(html2)
    print(f"Guardat new_frame.html ({len(html2)} chars)")

    await b.close()

asyncio.run(main())
