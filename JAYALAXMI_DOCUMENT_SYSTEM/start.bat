@echo off
title Jayalaxmi Silks - Document System
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist venv\Scripts\python.exe (
  echo First run: creating virtual environment...
  python -m venv venv || goto :fail
)

rem Always make sure the libraries are present (quick no-op when they already are)
venv\Scripts\python -c "import flask, pymupdf" >nul 2>nul
if errorlevel 1 (
  echo Installing required libraries - internet needed once...
  venv\Scripts\python -m pip install -r requirements.txt || goto :fail
)

echo Starting the application...
venv\Scripts\python app.py
echo.
echo The application has stopped. See any error message above.
pause
exit /b 0

:fail
echo.
echo Setup failed. Please read the message above.
pause
exit /b 1
