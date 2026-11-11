@echo off
title Suparagent Desktop
cd /d "%~dp0desktop"
npm run start

@REM .venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
@REM .venv\Scripts\python.exe -m app.auth.cli pairing-code --scope chat