@echo off
setlocal
cd /d "%~dp0"

echo LoRA Image Curator v0.27.21 - Face Analysis Dependency Installer
echo.

if not exist "venv\Scripts\python.exe" (
    echo ERROR: Required app setup has not been completed.
    echo Run Setup and Launch LoRA Image Curator.bat first.
    echo.
    pause
    exit /b 1
)

"venv\Scripts\python.exe" "install_face_dependencies.py"
set EXIT_CODE=%ERRORLEVEL%

echo.
if not "%EXIT_CODE%"=="0" (
    echo The installer reported an error. Review the messages above.
) else (
    echo Face-analysis dependencies are ready.
)
echo.
pause
exit /b %EXIT_CODE%
