@echo off
setlocal
cd /d "%~dp0"

echo LoRA Image Curator v0.27.18 - Face Analysis Setup Check
echo.

if not exist "venv\Scripts\python.exe" (
    echo ERROR: Could not find venv\Scripts\python.exe
    echo.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" "face_setup_check.py"
set EXIT_CODE=%ERRORLEVEL%

echo.
pause
exit /b %EXIT_CODE%
