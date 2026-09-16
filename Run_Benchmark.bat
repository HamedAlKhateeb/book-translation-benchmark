@echo off
chcp 65001 > nul
title Translation Benchmark System
color 0B

echo ================================================================
echo           Book Translation Benchmark System
echo       (COMET wmt22 - MQM Framework - chrF++ - BLEU)
echo ================================================================
echo.
echo [*] Starting local server...
echo [*] Opening browser at: http://localhost:7860
echo.

cd /d "%~dp0"

start "" cmd /c "timeout /t 2 >nul & start http://localhost:7860"
python app_benchmark_web.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] An error occurred while running the application.
    pause
)
