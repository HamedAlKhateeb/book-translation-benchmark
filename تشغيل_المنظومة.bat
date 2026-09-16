@echo off
chcp 65001 > nul
title منظومة تقييم ترجمة الكتب - Translation Benchmark
color 0B

echo ================================================================
echo           منظومة مقارنة وتقييم جودة ترجمة الكتب
echo       (COMET wmt22 - MQM Framework - chrF++ - BLEU)
echo ================================================================
echo.
echo [*] جاري تشغيل الخادم المحلي...
echo [*] سيتم فتح المتصفح تلقائياً على الرابط: http://localhost:7860
echo.

cd /d "%~dp0"

:: فتح المتصفح تلقائياً بعد ثانيتين
start "" cmd /c "timeout /t 2 >nul & start http://localhost:7860"

:: تشغيل تطبيق الويب
python app_benchmark_web.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [!] حدث خطأ أثناء تشغيل التطبيق.
    pause
)
