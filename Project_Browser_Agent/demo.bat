@echo off
REM Double-click this. It opens the demonstration menu.
REM
REM `cd /d "%~dp0"` moves to the folder holding THIS file, whatever folder
REM Explorer thought it was launching from - the project's scripts resolve
REM their paths from the repository root and silently misbehave otherwise.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   The virtual environment is missing: .venv\Scripts\python.exe
  echo   Create it once with:  python -m venv .venv
  echo   then:                 .venv\Scripts\python.exe -m pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

.venv\Scripts\python.exe scripts\demo.py

REM Without this the window closes the instant the menu exits and any closing
REM message is unreadable.
echo.
pause
