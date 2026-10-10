@echo off
rem VRMify launcher: double-click to open the converter UI (ui\app.py).
rem Uses the project's .venv when it exists (pywebview window, Pillow for texture slimming);
rem otherwise the Python on PATH, which opens the UI in the default browser if pywebview is missing.
rem Files or folders dropped onto this .bat are added to the list.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" ui\app.py %*
    exit /b
)
where pyw >nul 2>nul
if not errorlevel 1 (
    start "" pyw -3 ui\app.py %*
    exit /b
)
where pythonw >nul 2>nul
if not errorlevel 1 (
    start "" pythonw ui\app.py %*
    exit /b
)
echo Python 3 was not found.
echo Install Python 3.9 or newer from https://www.python.org/ and run this file again,
echo or create the project environment:  uv venv .venv  ^&^&  uv pip install -r requirements.txt
pause
