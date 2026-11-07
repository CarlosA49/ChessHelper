@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 goto setup_failed
    call .venv\Scripts\activate.bat
) else (
    call .venv\Scripts\activate.bat
)
python -c "import cv2, numpy, mss, chess, pynput, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing missing dependencies...
    python -m pip install --disable-pip-version-check -q -r requirements.txt
    if errorlevel 1 goto setup_failed
)
python setup.py
if errorlevel 1 goto setup_failed
python main.py
if errorlevel 1 pause
exit /b

:setup_failed
echo Setup failed. Check the messages above.
pause
exit /b 1
