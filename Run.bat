@echo off
title Certipatch Server Launcher
echo ==========================================
echo Starting Certipatch Backend Engine...
echo ==========================================

:: Switch to the correct drive and directory
cd /d "D:\My Projects\All websites\Certipatch\backend"

:: Tell Windows to wait 2 seconds in the background, then open the browser.
:: This gives the Python server enough time to boot up before the browser looks for it.
start "" cmd /c "timeout /t 2 >nul & start http://127.0.0.1:8000/docs"

:: Start the FastAPI server in this current terminal window so you can see the logs
python -m uvicorn api.main:app --reload

pause