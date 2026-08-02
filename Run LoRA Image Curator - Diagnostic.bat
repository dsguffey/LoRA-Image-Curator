@echo off
setlocal
cd /d "%~dp0"

echo LoRA Image Curator v0.27.19 diagnostic launcher
echo Project folder: %CD%
echo.

if not exist "venv\Scripts\python.exe" (
    echo First-time setup has not been completed yet.
    echo.
    echo Run Setup and Launch LoRA Image Curator.bat first.
    echo.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" "app.py"

echo.
echo LoRA Image Curator exited with code %ERRORLEVEL%.
echo Press any key to close this diagnostic window.
pause >nul
