@echo off
cd /d "%~dp0"

py -V >nul 2>nul && (py start.py & exit /b)
python -V >nul 2>nul && (python start.py & exit /b)

echo.
echo   Python is not installed on this computer (lmfao)
echo   Installing Python automatically via Windows Winget (takes ~1 minute)...
echo.
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements

if %errorlevel% neq 0 (
    echo.
    echo   Auto-install couldn't run. Opening python.org so you can install it manually...
    echo   (Make sure to check "Add Python to PATH" in the installer!)
    start https://www.python.org/downloads/
    pause
    exit /b
)

echo.
echo   Python installed! Please close this window and double-click start.bat again.
pause