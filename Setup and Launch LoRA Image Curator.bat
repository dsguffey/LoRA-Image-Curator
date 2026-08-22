@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo LoRA Image Curator v0.28.4 - Portable Source Setup and Launcher
echo Project folder: %CD%
echo.

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "setup_assistant.py"
    set "LIC_EXIT=!ERRORLEVEL!"
    goto :finish
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    set "LIC_PROBE=!ERRORLEVEL!"
    if "!LIC_PROBE!"=="0" (
        py -3 "setup_assistant.py"
        set "LIC_EXIT=!ERRORLEVEL!"
        goto :finish
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    set "LIC_PROBE=!ERRORLEVEL!"
    if "!LIC_PROBE!"=="0" (
        python "setup_assistant.py"
        set "LIC_EXIT=!ERRORLEVEL!"
        goto :finish
    )
)

echo ERROR: Python 3.11 or newer was not found.
echo.
echo Install 64-bit Python for Windows from:
echo https://www.python.org/downloads/windows/
echo.
echo During installation, enable the Python launcher or Add Python to PATH.
set "LIC_EXIT=1"

:finish
echo.
if not "%LIC_EXIT%"=="0" echo Setup exited with code %LIC_EXIT%.
pause
exit /b %LIC_EXIT%
