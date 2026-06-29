# HitSystems Bot - Proves

Bot de Telegram per gestionar comandes de fleca a HitSystems amb suport de Gemini i eines MCP.

Aquest directori correspon al bot de proves: `C:\Users\Usuario\CLAUDE CODE\comandesHitProbes`.
El bot de produccio viu separat a `C:\Users\Usuario\CLAUDE CODE\hitsystems-bot`.

## Branques

- `main`: branca tecnica aparcada; no es fa servir per treballar.
- `proves`: canvis i proves reals amb el bot de Telegram de proves.
- `produccio`: versio estable del bot real.

Flux: es treballa a `proves` i, quan una versio queda validada, es passen els canvis cap a `produccio`.

## Autoenviaments

El bot de proves no executa `auto_envia` per cron ni per PM2. Nomes pot fer enviaments quan un usuari autoritzat ho demana explicitament.

Els autoenviaments programats corresponen nomes al bot de produccio.

## Diagnòstic IA Admin

El bot de proves inclou un menú de diagnòstic només per a l'administrador principal:

- `/ia_ajuda`: mostra el menú de comandes IA/admin.
- `/estat`: mostra l'estat PM2 de producció i proves.
- `/ia <text>`: registra una incidència, retorna un diagnòstic inicial i crea una tasca OpenCode automàtica.
- `/ia_tasques`: mostra tasques IA pendents, en curs i acabades.
- `/ia_resultat <id>`: mostra el resultat d'una tasca IA.
- `/ia_cancel <id>`: cancel·la una tasca pendent.
- `/logs_produccio`: mostra últims logs útils de producció amb tokens filtrats.
- `/logs_proves`: mostra últims logs útils del bot de proves amb tokens filtrats.
- `/ultim_error`: mostra errors recents dels logs disponibles.
- `/incidencies`: llista les últimes incidències guardades.

Les tasques IA poden modificar només el bot de proves. No poden tocar producció, `.env`, secrets, commits, push ni autoenviaments.
Les incidències es guarden localment a `ia_incidents/`, fora del git.
Les tasques es guarden localment a `ia_tasks/`, fora del git, i les processa `ia-worker-proves` via PM2.

## Què fa

- Afegeix línies de comanda.
- Esborra línies posant quantitat `0`.
- Mostra albarans en format ticket.
- Resol ambigüitats amb enquestes de Telegram.
- Demana confirmació abans d'escriure comandes.
- Restringeix l'accés a usuaris autoritzats.

## Fitxers clau

- [bot.py](bot.py): lògica principal del bot i fluxos de Telegram.
- [gemini_hit.py](gemini_hit.py): integració amb Gemini i eines.
- [mcp_vendes.py](mcp_vendes.py): client MCP per HitSystems.
- [ecosystem.config.js](ecosystem.config.js): arrencada amb PM2 del bot de proves.
- `authorized_users.json`: usuaris autoritzats i peticions pendents locals, fora del git.
- `bot_state.pkl`: persistència de conversa i estat local del bot, fora del git.
- [assets/logo-bot-baker.svg](assets/logo-bot-baker.svg): logo vectorial de l'app.

## Configuració

Omple el fitxer `.env` amb:

```env
TELEGRAM_TOKEN=
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-2.5-flash-lite
ALLOWED_USER_IDS=
ADMIN_USER_ID=
HITSYSTEMS_URL=
HITSYSTEMS_USER=
HITSYSTEMS_PASS=
HEADLESS=True
SHOP_CODE=
```

## Arrencada

El bot es gestiona amb PM2:

```powershell
pm2.cmd start ecosystem.config.js
pm2.cmd status
pm2.cmd logs hitsystems-bot-proves
pm2.cmd restart hitsystems-bot-proves --update-env
```

L'autoenviament tambe surt de `bot.py`, pero al bot de proves no queda programat a PM2:

```powershell
python bot.py --mode auto_envia
```

Els horaris programats nomes s'han de definir al `ecosystem.config.js` de produccio amb `cron_restart`, no al de proves.

## Seguretat

- Només respon a usuaris autoritzats.
- El Toni és l'administrador i rep peticions d'accés.
- Els permisos es guarden a `authorized_users.json`.

## Imatge de l'app

He deixat un logo SVG vectorial perquè es pugui reutilitzar per branding o convertir-lo a PNG per a Telegram:

- [assets/logo-bot-baker.svg](assets/logo-bot-baker.svg)
