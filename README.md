# HitSystems Bot

Bot de Telegram per gestionar comandes de fleca a HitSystems amb suport de Gemini i eines MCP.

## Què fa

- Afegeix línies de comanda.
- Esborra línies posant quantitat `0`.
- Mostra albarans en format ticket.
- Resol ambigüitats amb enquestes de Telegram.
- Demana confirmació abans d'escriure comandes.
- Restringeix l'accés a usuaris autoritzats.

## Fitxers clau

- [bot.py](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/bot.py): lògica principal del bot i fluxos de Telegram.
- [gemini_hit.py](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/gemini_hit.py): integració amb Gemini i eines.
- [mcp_vendes.py](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/mcp_vendes.py): client MCP per HitSystems.
- [ecosystem.config.js](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/ecosystem.config.js): arrencada amb PM2.
- [authorized_users.json](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/authorized_users.json): usuaris autoritzats i peticions pendents.
- [bot_state.pkl](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/bot_state.pkl): persistència de conversa i estat del bot.
- [assets/logo-bot-baker.svg](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/assets/logo-bot-baker.svg): logo vectorial de l'app.

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
pm2.cmd logs hitsystems-bot
pm2.cmd restart hitsystems-bot --update-env
```

## Seguretat

- Només respon a usuaris autoritzats.
- El Toni és l'administrador i rep peticions d'accés.
- Els permisos es guarden a `authorized_users.json`.

## Imatge de l'app

He deixat un logo SVG vectorial perquè es pugui reutilitzar per branding o convertir-lo a PNG per a Telegram:

- [assets/logo-bot-baker.svg](/C:/Users/Usuario/CLAUDE%20CODE/hitsystems-bot/assets/logo-bot-baker.svg)
