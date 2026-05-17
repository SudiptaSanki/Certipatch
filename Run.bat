@echo off
setlocal EnableExtensions
title Certipatch Server Launcher

echo ==========================================
echo   Certipatch v2 — backend launcher
echo ==========================================

cd /d "%~dp0backend" 2>nul
if errorlevel 1 (
  echo [ERROR] Could not open the backend folder.
  echo Keep Run.bat in the project root next to the "backend" folder.
  pause
  exit /b 1
)

if not exist "api\main.py" (
  echo [ERROR] api\main.py not found. Current directory:
  cd
  pause
  exit /b 1
)

set "PYEXE="
where python >nul 2>nul
if not errorlevel 1 set "PYEXE=python"

if not defined PYEXE (
  where py >nul 2>nul
  if not errorlevel 1 set "PYEXE=py"
)

if not defined PYEXE (
  echo [ERROR] Python was not found in PATH.
  echo Install from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo or install the Windows "py" launcher.
  pause
  exit /b 1
)

echo Using Python: %PYEXE%
if /I "%PYEXE%"=="py" (
  "%PYEXE%" -3 --version 2>nul || (
    echo [ERROR] py launcher found but Python 3 is not available. Try: py -0p
    pause
    exit /b 1
  )
  "%PYEXE%" -3 --version
) else (
  "%PYEXE%" --version
)

echo Checking dependencies ^(fastapi, uvicorn, sqlalchemy^)...
if /I "%PYEXE%"=="py" (
  "%PYEXE%" -3 -c "import uvicorn, fastapi, sqlalchemy" 2>nul
) else (
  "%PYEXE%" -c "import uvicorn, fastapi, sqlalchemy" 2>nul
)
if errorlevel 1 (
  echo Installing missing packages...
  if /I "%PYEXE%"=="py" (
    "%PYEXE%" -3 -m pip install --upgrade pip
    "%PYEXE%" -3 -m pip install fastapi uvicorn sqlalchemy
  ) else (
    "%PYEXE%" -m pip install --upgrade pip
    "%PYEXE%" -m pip install fastapi uvicorn sqlalchemy
  )
  if errorlevel 1 (
    echo [ERROR] pip install failed. Check your network and Python install.
    pause
    exit /b 1
  )
)

echo.
echo Starting server at http://127.0.0.1:8000/
echo A browser tab will open in a few seconds. Press Ctrl+C here to stop.
echo.

:: Slightly longer wait so uvicorn can bind before the browser loads
start "" cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8000/"

if /I "%PYEXE%"=="py" (
  "%PYEXE%" -3 -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
) else (
  "%PYEXE%" -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
)

echo.
echo Server stopped.
pause
endlocal
