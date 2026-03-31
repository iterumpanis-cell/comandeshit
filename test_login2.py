"""Test login2: flux real — obre pàgina principal d'El Forn primer, llavors login popup."""
import asyncio, logging, sys
sys.path.insert(0, r"C:\Users\Usuario\CLAUDE CODE\hitsystems-bot")
from config import HITSYSTEMS_PASS
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")

# Provar diverses variacions de l'usuari
USERS_TO_TRY = ["cowork_claude", "cowork claude", "claude", "cowork"]
LOGIN_POPUP = "https://hitsystems.cloud/Entrada/dialogo.asp?loga=usuario&emp=Iterum&img=Iterum"
PORTAL_URL  = "https://hitsystems.cloud/Facturacion/ElForn/"

async def try_login(username, password):
    """Prova el login obrint el context correcte primer."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(channel="msedge", headless=False)
    context = await browser.new_context()

    # Capturar TOTES les peticions del context
    all_reqs = []
    context.on("request", lambda r: all_reqs.append(f"{r.method} {r.url[:80]}"))

    # MÈTODE 1: Navegar al login popup directament, però modificar l'action del form
    page = await context.new_page()
    await page.goto(LOGIN_POPUP, wait_until="domcontentloaded")
    await asyncio.sleep(0.5)

    await page.locator('input[type="text"]').fill(username)
    await page.locator('input[type="password"]').fill(password)

    # Modificar l'action del formulari per incloure emp=Iterum
    await page.evaluate("""() => {
        // El form té target=ENTRADA però les inputs estan fora
        // Creem un form amb els paràmetres correctes i el submittem
        const f = document.querySelector('form[name="entrada"]') || document.createElement('form');
        f.method = 'POST';
        f.action = '/Entrada/cp.asp';
        f.target = '_blank';

        // Afegir emp i img com a hidden fields
        const emp = document.createElement('input');
        emp.type = 'hidden'; emp.name = 'emp'; emp.value = 'Iterum';
        f.appendChild(emp);

        const img = document.createElement('input');
        img.type = 'hidden'; img.name = 'img'; img.value = 'Iterum';
        f.appendChild(img);

        // Posar els valors actuals del formulari
        const usuario = document.querySelector('input[name="usuario"]');
        const clave = document.querySelector('input[name="clave"]');
        if (usuario) { const u = document.createElement('input'); u.type='hidden'; u.name='usuario'; u.value=usuario.value; f.appendChild(u); }
        if (clave) { const c = document.createElement('input'); c.type='hidden'; c.name='clave'; c.value=clave.value; f.appendChild(c); }

        document.body.appendChild(f);
        f.submit();
    }""")

    try:
        new_page = await context.wait_for_event("page", timeout=8000)
        await new_page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(1)
        url = new_page.url
        html = await new_page.evaluate("() => document.documentElement.outerHTML")

        # Comprovar si el login ha funcionat
        if "no existe" in html:
            print(f"  X '{username}': no existe")
        elif "Mis Portales" in html or "ElForn" in html or "portal" in html.lower():
            print(f"  OK '{username}': LOGIN OK! URL={url}")
            print(f"    HTML fragment: {html[:200]}")
            return True
        else:
            print(f"  ? '{username}': url={url}")
            print(f"    emp cookie: {[c for c in await context.cookies() if 'emp' in c['name'].lower() or 'Emp' in c['name']]}")
            # Mirar si el idEmp cookie té valor
            print(f"    cookies: {await context.cookies()}")
            print(f"    cp.asp snippet: {html[500:1200]}")
    except Exception as e:
        print(f"  X '{username}': error: {e}")

    await browser.close()
    await pw.stop()
    return False

async def main():
    print("=== Provant variacions d'usuari ===")
    for user in USERS_TO_TRY:
        result = await try_login(user, HITSYSTEMS_PASS)
        if result:
            print(f"\n>>> CREDENCIALS CORRECTES: username='{user}' <<<")
            break
        await asyncio.sleep(1)

asyncio.run(main())
