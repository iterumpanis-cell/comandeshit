@echo off
cd /d "C:\Users\Usuario\CLAUDE CODE\hitsystems-bot"

echo ============================================
echo  Instal-lant el Bot HitSystems
echo ============================================
echo.
echo [1/3] Instal-lant dependencies Python...
pip install -r requirements.txt
echo.
echo [2/3] Instal-lant navegador Chromium per Playwright...
playwright install chromium
echo.
echo [3/3] Configurant arrencada amb PM2...
pm2.cmd start ecosystem.config.js
pm2.cmd save
echo.
echo Fet.
echo.
echo Ara edita el fitxer .env i afegeix les teves credencials.
echo Pots revisar l'estat amb: pm2.cmd status
echo.
pause
