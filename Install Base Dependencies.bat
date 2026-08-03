@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo LoRA Image Curator v0.28.0 - Required Dependency Installer
echo.

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" "setup_assistant.py" --install-base
    set "LIC_EXIT=!ERRORLEVEL!"
    goto :finish
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    set "LIC_PROBE=!ERRORLEVEL!"
    if "!LIC_PROBE!"=="0" (
        py -3 "setup_assistant.py" --install-base
        set "LIC_EXIT=!ERRORLEVEL!"
        goto :finish
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
    set "LIC_PROBE=!ERRORLEVEL!"
    if "!LIC_PROBE!"=="0" (
        python "setup_assistant.py" --install-base
        set "LIC_EXIT=!ERRORLEVEL!"
        goto :finish
    )
)

echo ERROR: Python 3.11 or newer was not found.
echo Use Setup and Launch LoRA Image Curator.bat for guided setup.
set "LIC_EXIT=1"

:finish
echo.
if not "%LIC_EXIT%"=="0" echo Setup did not complete successfully.
pause
exit /b %LIC_EXIT%
