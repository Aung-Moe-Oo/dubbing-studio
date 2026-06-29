@echo off
title Myanmar Dubbing Studio - Setup and Run
echo.
echo ============================================================
echo   Myanmar Dubbing Studio - AI Video Dubbing
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

:: Check FFmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] FFmpeg is not installed or not in PATH.
    echo Please install FFmpeg from https://ffmpeg.org/download.html
    echo and add it to your system PATH.
    pause
    exit /b 1
)

echo [OK] Python found
echo [OK] FFmpeg found
echo.

:: Create virtual environment if needed
if not exist "venv" (
    echo [SETUP] Creating virtual environment...
    python -m venv venv
    echo [OK] Virtual environment created
)

:: Activate virtual environment
call venv\Scripts\activate.bat

:: Install dependencies
echo [SETUP] Installing dependencies...
pip install -r requirements.txt --quiet
echo [OK] Dependencies installed
echo.

:: Start the server
echo ============================================================
echo   Starting server at http://localhost:5000
echo   Press Ctrl+C to stop
echo ============================================================
echo.
python app.py
