@echo off
setlocal
cd /d "%~dp0"

echo LoRA Image Curator v0.27.23 launcher
echo Project folder: %CD%
echo.

if not exist "venv\Scripts\python.exe" (
    echo First-time setup has not been completed yet.
    echo.
    echo Opening the guided setup and launcher...
    echo.
    call "Setup and Launch LoRA Image Curator.bat"
    exit /b %ERRORLEVEL%
)

"venv\Scripts\python.exe" "app.py"

echo.
echo LoRA Image Curator exited with code %ERRORLEVEL%.
echo Press any key to close this diagnostic window.
pause >nul
