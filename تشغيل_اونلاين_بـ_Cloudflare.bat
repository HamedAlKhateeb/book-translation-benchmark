@echo off
chcp 65001 > nul
title تشغيل المنظومة أونلاين عبر Cloudflare Tunnel
color 0A

echo ================================================================
echo           منظومة تقييم ترجمة الكتب - التشغيل أونلاين
echo       (COMET wmt22 - MQM Framework - chrF++ - BLEU)
echo ================================================================
echo.
echo [*] جاري تشغيل الخادم المحلي على المنفذ 7860...
start "Translation Benchmark Server" cmd /c "cd /d %~dp0 & python app_benchmark_web.py"

echo [*] جاري إنشاء نفق Cloudflare المشفر للإنترنت...
timeout /t 3 > nul

echo.
echo ================================================================
echo ستظهر لك الآن الروابط العامة (Public HTTPS URL) من كلاود فلير:
echo انسخ الرابط الذي ينتهي بـ .trycloudflare.com وافتحه من أي جهاز!
echo ================================================================
echo.

"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:7860
