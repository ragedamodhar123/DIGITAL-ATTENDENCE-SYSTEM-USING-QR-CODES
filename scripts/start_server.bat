@echo off
rem Starts the attendance app in production mode (waitress).
rem Double-click, or run from Task Scheduler "At startup" (see README, Production on Windows).
cd /d "%~dp0.."
call venv\Scripts\activate.bat
python serve.py