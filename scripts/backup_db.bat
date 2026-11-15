@echo off
rem Daily database backup. Schedule it with Task Scheduler (see README, Backups).
rem Writes logs\backup.log so you can see whether last night's backup worked.
cd /d "%~dp0.."
if not exist logs mkdir logs
call venv\Scripts\activate.bat
echo ==== %date% %time% >> logs\backup.log
flask --app wsgi backup-db >> logs\backup.log 2>&1
flask --app wsgi close-expired-sessions >> logs\backup.log 2>&1