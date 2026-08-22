@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo LoRA Image Curator v0.28.4 smart launcher
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

"venv\Scripts\python.exe" "setup_assistant.py" --smart-launch
set "LIC_EXIT=%ERRORLEVEL%"

if "!LIC_EXIT!"=="2" (
    echo.
    echo Opening guided setup and repair...
    call "Setup and Launch LoRA Image Curator.bat"
    exit /b !ERRORLEVEL!
)

echo.
echo LoRA Image Curator exited with code !LIC_EXIT!.
echo Press any key to close this diagnostic window.
pause >nul
exit /b !LIC_EXIT!
