@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] Virtual environment not found: .venv\Scripts\python.exe
  echo Create it first: py -3.11 -m venv .venv
  exit /b 1
)

".venv\Scripts\python.exe" bot.py
