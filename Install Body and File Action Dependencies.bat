@echo off
setlocal
cd /d "%~dp0"
echo LoRA Image Curator v0.27.19 - Body and File Action Dependency Installer
echo.
if not exist "venv\Scripts\python.exe" (
  echo ERROR: Required app setup has not been completed.
  echo Run Setup and Launch LoRA Image Curator.bat first.
  echo.
  pause
  exit /b 1
)
"venv\Scripts\python.exe" install_body_dependencies.py
set "LIC_EXIT=%ERRORLEVEL%"
echo.
if not "%LIC_EXIT%"=="0" (
  echo Setup did not complete successfully. Review the messages above.
) else (
  echo Setup finished.
)
pause
exit /b %LIC_EXIT%
