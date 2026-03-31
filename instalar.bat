@echo off
echo ============================================
echo  Instal·lant el Bot HitSystems
echo ============================================

echo.
echo [1/3] Instal·lant dependencies Python...
pip install -r requirements.txt

echo.
echo [2/3] Instal·lant navegador Chromium per Playwright...
playwright install chromium

echo.
echo [3/3] Fet!
echo.
echo Ara edita el fitxer config.py i afegeix el teu TELEGRAM_TOKEN.
echo Despres executa run.bat per arrencar el bot.
echo.
pause
