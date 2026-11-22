@echo off
cd /d "%~dp0"
py fetch_yf.py
if errorlevel 1 (
  echo.
  echo Something went wrong. Scroll up to read the error message.
  pause
  exit /b 1
)
start "" index.html
