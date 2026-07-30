@echo off
setlocal
cd /d "%~dp0"

echo LoRA Image Curator v0.27.17 launcher
echo Project folder: %CD%
echo.

if not exist "venv\Scripts\python.exe" (
    echo ERROR: Could not find venv\Scripts\python.exe
    echo.
    echo Keep this launcher beside the existing venv folder.
    echo.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" "app.py"

echo.
echo LoRA Image Curator exited with code %ERRORLEVEL%.
echo Press any key to close this diagnostic window.
pause >nul
