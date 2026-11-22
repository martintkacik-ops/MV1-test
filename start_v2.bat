@echo off
cd /d "%~dp0"
rem Opens the newest .html in this folder. Use the refresh button on the page to update the data.
py serve_v2.py
pause
