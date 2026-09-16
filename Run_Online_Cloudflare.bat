@echo off
chcp 65001 > nul
title Run Translation Benchmark Online via Cloudflare Tunnel
color 0A

echo ================================================================
echo           Book Translation Benchmark System - Online Mode
echo ================================================================
echo.
echo [*] Starting local server on port 7860...
start "Translation Benchmark Server" cmd /c "cd /d %~dp0 & python app_benchmark_web.py"

echo [*] Launching secure Cloudflare Tunnel...
timeout /t 3 > nul

echo.
echo ================================================================
echo Look for the public HTTPS URL below ending in .trycloudflare.com
echo You can open that link from your phone or any PC worldwide!
echo ================================================================
echo.

"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:7860
