@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tools\desktop_panel.py"
    exit /b
)
where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo Python for Windows is required. Install Python with tkinter enabled.
    pause
    exit /b 1
)
start "" pythonw.exe "%~dp0tools\desktop_panel.py"
