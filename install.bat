@echo off
echo ============================================
echo  Claude Usage Tracker - Install
echo ============================================
echo.
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found.
    echo Download from https://python.org and tick Add Python to PATH
    pause
    exit /b 1
)
python --version
echo.
echo Installing packages...
python -m pip install --upgrade pystray pillow requests
echo.
echo Done. Run debug.bat to test, or start.bat to run silently.
pause
