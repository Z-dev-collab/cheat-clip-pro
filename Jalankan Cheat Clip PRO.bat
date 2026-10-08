@echo off
setlocal enabledelayedexpansion
title Cheat Clip PRO
cd /d "%~dp0"

echo ===================================================
echo    Cheat Clip PRO  -  AI Auto Clipper + Live Clip
echo ===================================================
echo.

:: --- Pastikan ffmpeg (dari Hermes) masuk PATH ---
set "FFDIR=%LOCALAPPDATA%\hermes\tools\ffmpeg-9.0.1-win32-x64\bin"
if exist "%FFDIR%\ffmpeg.exe" set "PATH=%FFDIR%;%PATH%"

:: --- Pastikan Node.js tersedia ---
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js / npm tidak ditemukan di PATH.
    echo Silakan install Node.js 18+ dari https://nodejs.org/
    pause
    exit /b 1
)

:: --- Pastikan dependency Node terpasang ---
if not exist "node_modules" (
    echo [INFO] node_modules belum ada, menjalankan "npm install"...
    call npm install
)

echo [INFO] Menjalankan backend (FastAPI :8000) dan frontend (Vite)...
echo [INFO] Browser akan terbuka otomatis. Tutup jendela ini untuk berhenti.
echo.

:: Buka browser setelah beberapa detik (server butuh waktu start)
start "" cmd /c "timeout /t 8 /nobreak >nul & start http://localhost:5173/"

:: Jalankan frontend + backend bersamaan (npm run dev)
call npm run dev

echo.
echo [INFO] Aplikasi berhenti.
pause
