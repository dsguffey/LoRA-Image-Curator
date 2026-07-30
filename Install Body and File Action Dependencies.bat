@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  set "LIC_PYTHON=venv\Scripts\python.exe"
) else (
  set "LIC_PYTHON=python"
)
"%LIC_PYTHON%" install_body_dependencies.py
set "LIC_EXIT=%ERRORLEVEL%"
echo.
if not "%LIC_EXIT%"=="0" (
  echo Setup did not complete successfully. Review the messages above.
) else (
  echo Setup finished.
)
pause
exit /b %LIC_EXIT%
