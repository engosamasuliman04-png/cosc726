@echo off
REM Double-click this. WAIT for the line that says READY, then open the address
REM it prints in any browser.
REM
REM TO ADD A SITE: repeat --allow for each one, with the bare host name only.
REM No https://, no path, no brackets. www.example.org and example.org are two
REM different names to the gate, so copy the host exactly as the browser's
REM address bar shows it.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   The virtual environment is missing: .venv\Scripts\python.exe
  echo.
  pause
  exit /b 1
)

echo.
echo   Starting. Chromium, then the start page, then the model: about a minute.
echo   WAIT for READY, then open the address in a browser.
echo.

.venv\Scripts\python.exe scripts\web.py --allow en.wikipedia.org --allow ar.wikipedia.org --allow www.neelain.edu.sd --allow neelain.edu.sd --start https://en.wikipedia.org/wiki/Main_Page

echo.
pause
