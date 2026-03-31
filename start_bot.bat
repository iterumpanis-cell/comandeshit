@echo off
cd /d "C:\Users\Usuario\CLAUDE CODE\hitsystems-bot"

:loop
echo [%date% %time%] Iniciant bot... >> "C:\Users\Usuario\CLAUDE CODE\hitsystems-bot\bot_log.txt"
"C:\Users\Usuario\AppData\Local\Python\pythoncore-3.14-64\python.exe" bot.py >> "C:\Users\Usuario\CLAUDE CODE\hitsystems-bot\bot_log.txt" 2>&1
echo [%date% %time%] Bot aturat. Reiniciant en 10 segons... >> "C:\Users\Usuario\CLAUDE CODE\hitsystems-bot\bot_log.txt"
timeout /t 10 /nobreak >nul
goto loop
