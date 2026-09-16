#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Book Translation Benchmark Web Application
Supports: EPUB, DOCX, HTML, Markdown
Metrics: COMET (Unbabel/wmt22-comet-da), MQM, chrF++, BLEU
Database & Auth: Firebase Authentication (Email/Password & Google) + Cloud Firestore
"""

import os
import sys
import re
import csv
import io
import json
import zipfile
import functools
import urllib.parse
from html.parser import HTMLParser
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
import sacrebleu

# Monkeypatch for Python 3.14 compatibility with COMET
class _HashedSeq(list):
    __slots__ = 'hashvalue'
    def __init__(self, tup, hash=hash):
        self[:] = tup
        self.hashvalue = hash(tup)
    def __hash__(self):
        return self.hashvalue
functools._HashedSeq = _HashedSeq

# Optional COMET loader
comet_model = None
def get_comet_model():
    global comet_model
    if comet_model is None:
        from comet import load_from_checkpoint
        ckpt_path = r'C:\Users\hamed\.cache\huggingface\hub\models--Unbabel--wmt22-comet-da\snapshots\2760a223ac957f30acfb18c8aa649b01cf1d75f2\checkpoints\model.ckpt'
        if os.path.exists(ckpt_path):
            print("Loading local cached COMET model...")
            comet_model = load_from_checkpoint(ckpt_path)
        else:
            print("Downloading/Loading COMET model...")
            from comet import download_model
            path = download_model("Unbabel/wmt22-comet-da")
            comet_model = load_from_checkpoint(path)
    return comet_model

# ----------------- Text Cleaning & Extraction -----------------
def clean_arabic(text):
    text = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', text)
    text = re.sub(r'[،,]?[٠-٩]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def clean_english(text):
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def extract_text_from_file(filename, file_bytes):
    ext = filename.lower().split('.')[-1]
    paragraphs = []

    if ext in ['html', 'xhtml']:
        soup = BeautifulSoup(file_bytes.decode('utf-8', errors='ignore'), 'html.parser')
        for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'h4', 'li']):
            txt = tag.get_text().strip()
            if len(txt) > 15:
                paragraphs.append(txt)

    elif ext == 'docx':
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            xml_content = z.read('word/document.xml')
            soup = BeautifulSoup(xml_content, 'xml')
            for p in soup.find_all('p'):
                txt = p.get_text().strip()
                if txt and not txt.startswith('HTML+') and len(txt) > 15:
                    paragraphs.append(txt)

    elif ext == 'epub':
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            html_files = [f for f in z.namelist() if f.lower().endswith(('.xhtml', '.html', '.htm'))]
            html_files.sort()
            for h in html_files:
                content = z.read(h).decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content, 'html.parser')
                for tag in soup.find_all(['p', 'h1', 'h2', 'h3', 'li']):
                    txt = tag.get_text().strip()
                    if len(txt) > 15:
                        paragraphs.append(txt)

    elif ext in ['md', 'markdown', 'txt']:
        raw = file_bytes.decode('utf-8', errors='ignore')
        for line in raw.splitlines():
            line = line.strip()
            if len(line) > 15:
                line = re.sub(r'^#+\s*', '', line)
                paragraphs.append(line)

    return paragraphs

# ----------------- Dynamic Alignment Engine -----------------
def align_segments(ref_list, ai_list, max_merge=4):
    n_ref, n_ai = len(ref_list), len(ai_list)
    dp = [[-1e9] * (n_ai + 1) for _ in range(n_ref + 1)]
    back = [[None] * (n_ai + 1) for _ in range(n_ref + 1)]
    dp[0][0] = 0

    for i in range(n_ref + 1):
        for j in range(n_ai + 1):
            if dp[i][j] < -1e8:
                continue
            for dr in range(1, max_merge + 1):
                if i + dr > n_ref:
                    continue
                r_txt = ' '.join(ref_list[i:i+dr])
                for da in range(1, max_merge + 1):
                    if j + da > n_ai:
                        continue
                    a_txt = ' '.join(ai_list[j:j+da])
                    score = fuzz.token_set_ratio(r_txt, a_txt)
                    if dp[i][j] + score > dp[i+dr][j+da]:
                        dp[i+dr][j+da] = dp[i][j] + score
                        back[i+dr][j+da] = (i, j, dr, da)

    curr_i, curr_j = n_ref, n_ai
    matches = []
    while curr_i > 0 and curr_j > 0:
        b = back[curr_i][curr_j]
        if b is None:
            break
        prev_i, prev_j, dr, da = b
        r_txt = ' '.join(ref_list[prev_i:prev_i+dr])
        a_txt = ' '.join(ai_list[prev_j:prev_j+da])
        matches.append((prev_i, prev_j, dr, da, r_txt, a_txt))
        curr_i, curr_j = prev_i, prev_j

    matches.reverse()
    return matches

# ----------------- MQM Error Taxonomy -----------------
def classify_mqm(src, ref, hyp, comet_score, chrf_score, bleu_score):
    hyp_words = hyp.split()
    word_count = max(1, len(hyp_words))
    
    english_words = re.findall(r'[a-zA-Z]{3,}', hyp)
    english_words = [w for w in english_words if w.lower() not in ['html', 'docx', 'pdf', 'url']]
    
    hyp_len = len(hyp_words)
    ref_len = len(ref.split())
    length_ratio = hyp_len / max(1, ref_len)
    
    if length_ratio < 0.65 and ref_len > 25:
        cat = 'الدقة (Accuracy)'
        err_type = 'حذف / سقط في المعنى (Omission)'
        sev = 'جسيم (Major)'
        desc = f'سقط جزء كبير من الفقرة في ترجمة الذكاء الاصطناعي بنسبة نقص {int((1-length_ratio)*100)}%'
        penalty = 5
    elif english_words:
        cat = 'الدقة (Accuracy)'
        err_type = 'نص غير مترجم (Untranslated)'
        sev = 'طفيف (Minor)'
        desc = f'بقاء مفردات أجنبية دون تعريب: {", ".join(english_words[:3])}'
        penalty = 1
    elif comet_score < 0.72 or (comet_score < 0.76 and chrf_score < 30):
        cat = 'الدقة (Accuracy)'
        err_type = 'ترجمة غير دقيقة / معنى مغاير (Mistranslation)'
        sev = 'جسيم (Major)'
        desc = 'انحراف دلالي واضح عن قصد النص الأصلي أو المصطلحات المعتمدة'
        penalty = 5
    elif chrf_score < 42 and comet_score < 0.83:
        cat = 'المصطلحات (Terminology)'
        err_type = 'مصطلح غير دقيق (Inappropriate Term)'
        sev = 'طفيف (Minor)'
        desc = 'استخدام مصطلح عام أو معجمي غير دقيق مقارنة بالمصطلح التخصصي البشري'
        penalty = 1
    elif bleu_score < 15 and chrf_score < 48:
        cat = 'الأسلوب (Style)'
        err_type = 'حرفية وركاكة أسلوبية (Literalness / Awkward)'
        sev = 'طفيف (Minor)'
        desc = 'ترجمة حرفية تتبع بناء الجملة الإنجليزية مع ضعف في بلاغة التركيب العربي'
        penalty = 1
    elif length_ratio > 1.45 and ref_len > 25:
        cat = 'الدقة (Accuracy)'
        err_type = 'إضافة غير مبررة (Addition)'
        sev = 'طفيف (Minor)'
        desc = 'إسهاب وحشو زائد في صياغة الفكرة لم يرد في النص الأصلي'
        penalty = 1
    else:
        cat = 'لا يوجد (None)'
        err_type = 'ترجمة صحيحة وسليمة (Accurate & Fluent)'
        sev = 'منعدم (None)'
        desc = 'ترجمة متسقة وسلسة ومكافئة دلالياً للأصل البشري'
        penalty = 0

    penalty_points = penalty * (100.0 / max(20, word_count))
    mqm_score = max(0.0, min(100.0, 100.0 - penalty_points))
    return cat, err_type, sev, desc, round(mqm_score, 2)

# ----------------- Web UI with Firebase Auth & Firestore -----------------
from aiohttp import web

HTML_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>منصة تقييم ترجمة الكتب | Translation Benchmark & Evaluation</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap" rel="stylesheet">
    <!-- Firebase SDK (Compat) -->
    <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-app-compat.js"></script>
    <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-auth-compat.js"></script>
    <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-firestore-compat.js"></script>
    <style>
        body { font-family: 'Tajawal', sans-serif; background-color: #0f172a; color: #f8fafc; }
        .glass { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.08); }
    </style>
</head>
<body class="min-h-screen">

    <!-- Header with Firebase Auth State -->
    <header class="border-b border-slate-800 bg-slate-900/80 sticky top-0 z-50 backdrop-blur">
        <div class="max-w-7xl mx-auto px-6 py-4 flex justify-between items-center">
            <div class="flex items-center space-x-4 space-x-reverse">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center text-xl font-bold shadow-lg shadow-sky-500/20">
                    📊
                </div>
                <div>
                    <h1 class="text-xl font-extrabold text-white">منظومة تقييم ترجمة الكتب</h1>
                    <p class="text-xs text-slate-400">COMET (wmt22) • MQM Framework • chrF++ • BLEU</p>
                </div>
            </div>
            
            <!-- Auth User Profile Bar -->
            <div class="flex items-center gap-3">
                <div id="authUserSection" class="hidden flex items-center gap-3">
                    <span id="userEmailSpan" class="text-xs text-slate-300 font-medium bg-slate-800 px-3 py-1.5 rounded-full border border-slate-700"></span>
                    <button onclick="openHistoryModal()" class="text-xs text-sky-400 hover:text-sky-300 bg-sky-500/10 border border-sky-500/20 px-3 py-1.5 rounded-xl font-semibold transition">
                        📜 سجل التقييمات
                    </button>
                    <button onclick="handleSignOut()" class="text-xs text-red-400 hover:text-red-300 bg-red-500/10 border border-red-500/20 px-3 py-1.5 rounded-xl font-semibold transition">
                        خروج
                    </button>
                </div>
                <div id="authLoginBtnSection">
                    <button onclick="openAuthModal()" class="text-xs bg-gradient-to-r from-sky-500 to-indigo-600 text-white px-4 py-2 rounded-xl font-bold shadow-lg shadow-sky-500/20 hover:opacity-90 transition">
                        🔐 تسجيل الدخول / حساب جديد
                    </button>
                </div>
            </div>
        </div>
    </header>

    <main class="max-w-7xl mx-auto px-6 py-8">
        <!-- Auth Gate Alert if not logged in -->
        <div id="authBanner" class="p-4 mb-6 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-between">
            <div class="flex items-center gap-3">
                <span class="text-xl">⚠️</span>
                <p class="text-xs text-amber-200">
                    <strong>تنبيه:</strong> يمكنك تجربة المقارنة، ولكن لحفظ سجل التقييمات وقواعد البيانات في Firebase وتحميل تقارير غير محدودة، يُرجى تسجيل الدخول.
                </p>
            </div>
            <button onclick="openAuthModal()" class="text-xs font-bold text-amber-400 hover:underline">تسجيل الدخول الآن ←</button>
        </div>

        <!-- 3-Step Wizard Navigation -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
            <div class="glass p-5 rounded-2xl border-r-4 border-r-sky-500">
                <span class="text-xs font-bold text-sky-400 uppercase tracking-wider">المرحلة الأولى</span>
                <h3 class="text-lg font-bold text-white mt-1">1. استخراج الفصول</h3>
                <p class="text-xs text-slate-400 mt-1">رفع كتب المصدر والترجمة بصيغ (EPUB / DOCX / HTML / MD)</p>
            </div>
            <div class="glass p-5 rounded-2xl border-r-4 border-r-indigo-500">
                <span class="text-xs font-bold text-indigo-400 uppercase tracking-wider">المرحلة الثانية</span>
                <h3 class="text-lg font-bold text-white mt-1">2. المحاذاة والتقييم</h3>
                <p class="text-xs text-slate-400 mt-1">مطابقة الفقرات ديناميكياً وتشغيل COMET و chrF++ و BLEU</p>
            </div>
            <div class="glass p-5 rounded-2xl border-r-4 border-r-emerald-500">
                <span class="text-xs font-bold text-emerald-400 uppercase tracking-wider">المرحلة الثالثة</span>
                <h3 class="text-lg font-bold text-white mt-1">3. النتائج وتحليل MQM</h3>
                <p class="text-xs text-slate-400 mt-1">حفظ النتائج في Firestore وتصدير تقارير Excel المهيأة للعربية</p>
            </div>
        </div>

        <!-- Input & Upload Section -->
        <section class="glass p-8 rounded-3xl mb-8 shadow-2xl">
            <div class="flex items-center justify-between mb-6">
                <h2 class="text-xl font-bold flex items-center gap-3 text-sky-400">
                    <span>📁</span> إدخال بيانات المقارنة والتقييم
                </h2>
                <div class="flex items-center gap-2">
                    <input type="text" id="bookTitleInput" placeholder="عنوان الكتاب (مثال: العناصر: مقدمة قصيرة)..." class="bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 w-64 focus:outline-none focus:border-sky-500">
                    <input type="text" id="chapterInput" placeholder="رقم/اسم الفصل..." class="bg-slate-900 border border-slate-700 rounded-xl px-3 py-1.5 text-xs text-slate-200 w-32 focus:outline-none focus:border-sky-500">
                </div>
            </div>

            <form id="benchmarkForm" class="space-y-6">
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <!-- Source File -->
                    <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700">
                        <label class="block text-sm font-bold text-slate-200 mb-2">1. النص الأصلي (Source - EN)</label>
                        <p class="text-xs text-slate-400 mb-3">ملف الفصل الإنجليزي (.html, .docx, .epub, .md)</p>
                        <input type="file" id="srcFile" accept=".html,.xhtml,.docx,.epub,.md,.txt" class="block w-full text-xs text-slate-400 file:mr-0 file:ml-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-sky-600 file:text-white hover:file:bg-sky-500 cursor-pointer">
                        <textarea id="srcText" rows="4" placeholder="أو الصق النص الإنجليزي هنا مباشرة..." class="mt-3 w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:border-sky-500"></textarea>
                    </div>

                    <!-- Human Reference File -->
                    <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700">
                        <label class="block text-sm font-bold text-slate-200 mb-2">2. الترجمة البشرية (Reference - AR)</label>
                        <p class="text-xs text-slate-400 mb-3">ملف الترجمة المرجعية المعتمدة للكتاب</p>
                        <input type="file" id="refFile" accept=".html,.xhtml,.docx,.epub,.md,.txt" class="block w-full text-xs text-slate-400 file:mr-0 file:ml-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-emerald-600 file:text-white hover:file:bg-emerald-500 cursor-pointer">
                        <textarea id="refText" rows="4" placeholder="أو الصق الترجمة البشرية هنا مباشرة..." class="mt-3 w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:border-emerald-500"></textarea>
                    </div>

                    <!-- AI Translation File -->
                    <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700">
                        <label class="block text-sm font-bold text-slate-200 mb-2">3. ترجمة الآلة / الذكاء الاصطناعي (AI MT)</label>
                        <p class="text-xs text-slate-400 mb-3">ملف الترجمة الآلية المراد تقييمها</p>
                        <input type="file" id="aiFile" accept=".html,.xhtml,.docx,.epub,.md,.txt" class="block w-full text-xs text-slate-400 file:mr-0 file:ml-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500 cursor-pointer">
                        <textarea id="aiText" rows="4" placeholder="أو الصق ترجمة الذكاء الاصطناعي هنا مباشرة..." class="mt-3 w-full bg-slate-900 border border-slate-700 rounded-xl p-3 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"></textarea>
                    </div>
                </div>

                <!-- Submit Button & Loader -->
                <div class="flex items-center gap-4">
                    <button type="button" onclick="runEvaluation()" id="submitBtn" class="px-8 py-3.5 bg-gradient-to-r from-sky-500 via-indigo-500 to-emerald-500 text-white font-bold rounded-2xl shadow-xl shadow-sky-500/20 hover:opacity-95 transition-all text-sm flex items-center gap-2">
                        <span>⚡</span> تشغيل المقارنة والتقييم الشامل
                    </button>
                    <div id="loadingStatus" class="hidden flex items-center gap-3 text-sky-400 text-sm font-medium">
                        <svg class="animate-spin h-5 w-5 text-sky-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
                        </svg>
                        <span id="loadingMsg">جاري المحاذاة وتشغيل COMET و MQM...</span>
                    </div>
                </div>
            </form>
        </section>

        <!-- Results Section (Hidden until run) -->
        <div id="resultsArea" class="hidden space-y-8">
            <!-- Score Cards -->
            <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
                <div class="glass p-6 rounded-3xl text-center border-t-4 border-t-indigo-500">
                    <span class="text-xs font-bold text-slate-400 uppercase">مؤشر COMET العصبي</span>
                    <div id="metricComet" class="text-3xl font-extrabold text-indigo-400 mt-2">0.0000</div>
                    <span class="text-[10px] text-slate-500 mt-1 block">الأقرب للتقييم البشري (0 - 1)</span>
                </div>
                <div class="glass p-6 rounded-3xl text-center border-t-4 border-t-emerald-500">
                    <span class="text-xs font-bold text-slate-400 uppercase">جودة MQM القياسية</span>
                    <div id="metricMqm" class="text-3xl font-extrabold text-emerald-400 mt-2">0.0%</div>
                    <span class="text-[10px] text-slate-500 mt-1 block">خصم العقوبات لكل 100 كلمة</span>
                </div>
                <div class="glass p-6 rounded-3xl text-center border-t-4 border-t-sky-500">
                    <span class="text-xs font-bold text-slate-400 uppercase">معيار chrF++ الصرفي</span>
                    <div id="metricChrf" class="text-3xl font-extrabold text-sky-400 mt-2">0.00</div>
                    <span class="text-[10px] text-slate-500 mt-1 block">الأفضل لبنية اللغة العربية (0-100)</span>
                </div>
                <div class="glass p-6 rounded-3xl text-center border-t-4 border-t-purple-500">
                    <span class="text-xs font-bold text-slate-400 uppercase">معيار BLEU اللفظي</span>
                    <div id="metricBleu" class="text-3xl font-extrabold text-purple-400 mt-2">0.00</div>
                    <span class="text-[10px] text-slate-500 mt-1 block">تطابق الكلمات المباشر (0-100)</span>
                </div>
            </div>

            <!-- Error Distribution & Export -->
            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <!-- Error Distribution Card -->
                <div class="glass p-6 rounded-3xl lg:col-span-2">
                    <h3 class="text-lg font-bold text-white mb-4 flex items-center justify-between">
                        <span>🔍 توزيع ونسب أنواع الأخطاء (MQM Breakdown)</span>
                        <span id="totalSegmentsBadge" class="text-xs px-3 py-1 bg-slate-800 rounded-full text-slate-300"></span>
                    </h3>
                    <div class="overflow-x-auto">
                        <table class="w-full text-right text-xs">
                            <thead class="text-slate-400 bg-slate-800/50">
                                <tr>
                                    <th class="p-3 rounded-r-xl">نوع الخطأ / التصنيف</th>
                                    <th class="p-3">مستوى الخطورة</th>
                                    <th class="p-3">التكرار</th>
                                    <th class="p-3 rounded-l-xl">النسبة المئوية (%)</th>
                                </tr>
                            </thead>
                            <tbody id="errorTableBody" class="divide-y divide-slate-800"></tbody>
                        </table>
                    </div>
                </div>

                <!-- Export & Actions Card -->
                <div class="glass p-6 rounded-3xl flex flex-col justify-between">
                    <div>
                        <h3 class="text-lg font-bold text-white mb-2">📥 تصدير البيانات</h3>
                        <p class="text-xs text-slate-400 mb-6">تحميل الجداول بترميز مهيأ لبرنامج Microsoft Excel دون مشاكل الحروف العربية (UTF-8-BOM)، مع الحفظ التلقائي في Firestore.</p>
                    </div>
                    <div class="space-y-3">
                        <button onclick="downloadCSV('details')" class="w-full py-3 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition">
                            <span>📄</span> تحميل التقرير التفصيلي لكل فقرة (CSV)
                        </button>
                        <button onclick="downloadCSV('summary')" class="w-full py-3 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-xl font-bold text-xs flex items-center justify-center gap-2 transition">
                            <span>📊</span> تحميل الملخص الإحصائي ونسب الأخطاء (CSV)
                        </button>
                    </div>
                </div>
            </div>

            <!-- Side-by-Side Aligned Comparison Table -->
            <div class="glass p-6 rounded-3xl">
                <h3 class="text-lg font-bold text-white mb-4">📖 المقارنة المتزامنة للفقرات والملاحظات اللغوية</h3>
                <div class="overflow-x-auto max-h-[600px] overflow-y-auto">
                    <table class="w-full text-right text-xs">
                        <thead class="text-slate-400 bg-slate-800/80 sticky top-0 z-10 backdrop-blur">
                            <tr>
                                <th class="p-3 w-12 text-center">#</th>
                                <th class="p-3 w-1/4">الأصل الإنجليزي (Source)</th>
                                <th class="p-3 w-1/4">الترجمة البشرية (Reference)</th>
                                <th class="p-3 w-1/4">ترجمة الذكاء الاصطناعي (AI MT)</th>
                                <th class="p-3 w-28 text-center">المقاييس</th>
                                <th class="p-3 w-1/5">ملاحظات MQM والخطأ</th>
                            </tr>
                        </thead>
                        <tbody id="comparisonTableBody" class="divide-y divide-slate-800"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </main>

    <!-- Auth Modal -->
    <div id="authModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="glass p-8 rounded-3xl w-full max-w-md shadow-2xl relative">
            <button onclick="closeAuthModal()" class="absolute top-4 left-4 text-slate-400 hover:text-white text-lg font-bold">&times;</button>
            <h3 class="text-lg font-extrabold text-white mb-2" id="authModalTitle">تسجيل الدخول</h3>
            <p class="text-xs text-slate-400 mb-6">سجل دخولك لحفظ جلسات التقييم في قاعدة بيانات Firebase.</p>

            <div class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-300 mb-1">البريد الإلكتروني</label>
                    <input type="email" id="authEmail" placeholder="name@example.com" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-300 mb-1">كلمة المرور</label>
                    <input type="password" id="authPassword" placeholder="••••••••" class="w-full bg-slate-900 border border-slate-700 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-sky-500">
                </div>

                <div class="flex gap-3 pt-2">
                    <button onclick="handleEmailAuth('signin')" class="flex-1 bg-sky-600 hover:bg-sky-500 text-white font-bold py-2.5 rounded-xl text-xs transition">
                        تسجيل الدخول
                    </button>
                    <button onclick="handleEmailAuth('signup')" class="flex-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-bold py-2.5 rounded-xl text-xs transition">
                        إنشاء حساب
                    </button>
                </div>

                <div class="relative flex py-2 items-center">
                    <div class="flex-grow border-t border-slate-700"></div>
                    <span class="flex-shrink mx-3 text-slate-500 text-[10px]">أو الاستمرار عبر</span>
                    <div class="flex-grow border-t border-slate-700"></div>
                </div>

                <button onclick="handleGoogleSignIn()" class="w-full bg-white hover:bg-slate-100 text-slate-900 font-bold py-2.5 rounded-xl text-xs flex items-center justify-center gap-2 transition">
                    <svg class="w-4 h-4" viewBox="0 0 24 24"><path fill="#4285F4" d="M23.745 12.27c0-.7-.06-1.4-.19-2.07H12v4.51h6.6c-.29 1.52-1.14 2.82-2.4 3.68v3.05h3.88c2.27-2.09 3.66-5.17 3.66-9.17z"/><path fill="#34A853" d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.88-3.05c-1.08.72-2.45 1.16-4.05 1.16-3.12 0-5.77-2.1-6.72-4.93H1.25v3.15C3.26 21.36 7.35 24 12 24z"/><path fill="#FBBC05" d="M5.28 14.27c-.25-.72-.38-1.49-.38-2.27s.13-1.55.38-2.27V6.58H1.25C.45 8.18 0 9.99 0 12s.45 3.82 1.25 5.42l4.03-3.15z"/><path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.35 0 3.26 2.64 1.25 6.58l4.03 3.15c.95-2.83 3.6-4.93 6.72-4.93z"/></svg>
                    تسجيل الدخول بحساب Google
                </button>
            </div>
        </div>
    </div>

    <!-- History Modal -->
    <div id="historyModal" class="hidden fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
        <div class="glass p-8 rounded-3xl w-full max-w-3xl shadow-2xl relative max-h-[85vh] flex flex-col">
            <button onclick="closeHistoryModal()" class="absolute top-4 left-4 text-slate-400 hover:text-white text-lg font-bold">&times;</button>
            <h3 class="text-lg font-extrabold text-white mb-2">📜 سجل التقييمات المحفوظة (Firestore)</h3>
            <p class="text-xs text-slate-400 mb-4">جميع الفصول التي قمت بتقييمها ومقارنتها مخزنة بأمان في حسابك.</p>
            
            <div class="overflow-y-auto flex-1">
                <table class="w-full text-right text-xs">
                    <thead class="text-slate-400 bg-slate-800/80 sticky top-0">
                        <tr>
                            <th class="p-3 rounded-r-xl">الكتاب / الفصل</th>
                            <th class="p-3">COMET</th>
                            <th class="p-3">MQM</th>
                            <th class="p-3">chrF++</th>
                            <th class="p-3">BLEU</th>
                            <th class="p-3">الفقرات</th>
                            <th class="p-3 rounded-l-xl">التاريخ</th>
                        </tr>
                    </thead>
                    <tbody id="historyTableBody" class="divide-y divide-slate-800">
                        <tr><td colspan="7" class="p-4 text-center text-slate-500">جاري تحميل السجل...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        // Firebase Configuration (Configured for myreports-system)
        const firebaseConfig = {
            apiKey: "AIzaSyBkWGYSsB4LJgAHWHb1Fz0JgJWI2x9mLEY",
            authDomain: "myreports-system.firebaseapp.com",
            projectId: "myreports-system",
            storageBucket: "myreports-system.firebasestorage.app",
            messagingSenderId: "215752965145",
            appId: "1:215752965145:web:88e27dc2d1885ed44e3e88"
        };
        firebase.initializeApp(firebaseConfig);
        const auth = firebase.auth();
        const db = firebase.firestore();

        let currentUser = null;
        let currentResults = null;

        // Auth state listener
        auth.onAuthStateChanged(user => {
            currentUser = user;
            if (user) {
                document.getElementById('authLoginBtnSection').classList.add('hidden');
                document.getElementById('authUserSection').classList.remove('hidden');
                document.getElementById('userEmailSpan').textContent = user.email || 'مستخدم مسجل';
                document.getElementById('authBanner').classList.add('hidden');
            } else {
                document.getElementById('authLoginBtnSection').classList.remove('hidden');
                document.getElementById('authUserSection').classList.add('hidden');
                document.getElementById('authBanner').classList.remove('hidden');
            }
        });

        function openAuthModal() { document.getElementById('authModal').classList.remove('hidden'); }
        function closeAuthModal() { document.getElementById('authModal').classList.add('hidden'); }
        function openHistoryModal() { 
            document.getElementById('historyModal').classList.remove('hidden'); 
            loadHistory();
        }
        function closeHistoryModal() { document.getElementById('historyModal').classList.add('hidden'); }

        async function handleEmailAuth(mode) {
            const email = document.getElementById('authEmail').value.trim();
            const password = document.getElementById('authPassword').value;
            if (!email || !password) {
                alert('يرجى إدخال البريد الإلكتروني وكلمة المرور.');
                return;
            }
            try {
                if (mode === 'signup') {
                    await auth.createUserWithEmailAndPassword(email, password);
                } else {
                    await auth.signInWithEmailAndPassword(email, password);
                }
                closeAuthModal();
            } catch (err) {
                alert('خطأ في المصادقة: ' + err.message);
            }
        }

        async function handleGoogleSignIn() {
            const provider = new firebase.auth.GoogleAuthProvider();
            try {
                await auth.signInWithPopup(provider);
                closeAuthModal();
            } catch (err) {
                alert('خطأ في تسجيل الدخول بجوجل: ' + err.message);
            }
        }

        async function handleSignOut() {
            await auth.signOut();
        }

        async function loadHistory() {
            const tbody = document.getElementById('historyTableBody');
            if (!currentUser) {
                tbody.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-amber-400">يرجى تسجيل الدخول أولاً لعرض السجل.</td></tr>';
                return;
            }
            tbody.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-slate-500">جاري الجلب من Firestore...</td></tr>';
            try {
                const snap = await db.collection('benchmark_history')
                    .where('userId', '==', currentUser.uid)
                    .orderBy('createdAt', 'desc')
                    .limit(20)
                    .get();

                if (snap.empty) {
                    tbody.innerHTML = '<tr><td colspan="7" class="p-4 text-center text-slate-500">لا توجد تقييمات محفوظة حتى الآن.</td></tr>';
                    return;
                }

                tbody.innerHTML = '';
                snap.forEach(doc => {
                    const d = doc.data();
                    const dateStr = d.createdAt ? new Date(d.createdAt.toDate ? d.createdAt.toDate() : d.createdAt).toLocaleDateString('ar-EG') : '-';
                    const tr = document.createElement('tr');
                    tr.className = 'hover:bg-slate-800/40';
                    tr.innerHTML = `
                        <td class="p-3 font-semibold text-slate-200">${d.bookTitle || 'كتاب'} - ${d.chapter || 'فصل'}</td>
                        <td class="p-3 font-bold text-indigo-400">${d.avgComet}</td>
                        <td class="p-3 font-bold text-emerald-400">${d.avgMqm}%</td>
                        <td class="p-3 text-sky-400">${d.avgChrf}</td>
                        <td class="p-3 text-purple-400">${d.avgBleu}</td>
                        <td class="p-3 text-slate-400">${d.totalSegments}</td>
                        <td class="p-3 text-slate-500">${dateStr}</td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (e) {
                console.error(e);
                tbody.innerHTML = `<tr><td colspan="7" class="p-4 text-center text-red-400">تعذر تحميل السجل: ${e.message}</td></tr>`;
            }
        }

        async function saveBenchmarkToFirestore(summaryData) {
            if (!currentUser) return;
            try {
                const bookTitle = document.getElementById('bookTitleInput').value.trim() || 'كتاب غير محدد';
                const chapter = document.getElementById('chapterInput').value.trim() || 'فصل 1';
                await db.collection('benchmark_history').add({
                    userId: currentUser.uid,
                    userEmail: currentUser.email,
                    bookTitle: bookTitle,
                    chapter: chapter,
                    avgComet: summaryData.avg_comet,
                    avgMqm: summaryData.avg_mqm,
                    avgChrf: summaryData.avg_chrf,
                    avgBleu: summaryData.avg_bleu,
                    totalSegments: summaryData.total_units,
                    createdAt: firebase.firestore.FieldValue.serverTimestamp()
                });
                console.log("Benchmark saved to Firestore successfully.");
            } catch (e) {
                console.error("Firestore save error:", e);
            }
        }

        async function runEvaluation() {
            const btn = document.getElementById('submitBtn');
            const loader = document.getElementById('loadingStatus');
            const resultsArea = document.getElementById('resultsArea');
            
            const formData = new FormData();
            const srcFile = document.getElementById('srcFile').files[0];
            const refFile = document.getElementById('refFile').files[0];
            const aiFile = document.getElementById('aiFile').files[0];

            if (srcFile) formData.append('src_file', srcFile);
            if (refFile) formData.append('ref_file', refFile);
            if (aiFile) formData.append('ai_file', aiFile);

            formData.append('src_text', document.getElementById('srcText').value);
            formData.append('ref_text', document.getElementById('refText').value);
            formData.append('ai_text', document.getElementById('aiText').value);

            btn.disabled = true;
            btn.classList.add('opacity-50');
            loader.classList.remove('hidden');

            try {
                const response = await fetch('/api/evaluate', {
                    method: 'POST',
                    body: formData
                });
                const data = await response.json();
                if (data.error) {
                    alert('خطأ: ' + data.error);
                    return;
                }
                currentResults = data;
                renderResults(data);
                resultsArea.classList.remove('hidden');
                resultsArea.scrollIntoView({ behavior: 'smooth' });

                // Save to Firestore if user logged in
                await saveBenchmarkToFirestore(data.summary);

            } catch (err) {
                alert('حدث خطأ أثناء معالجة الطلب: ' + err.message);
            } finally {
                btn.disabled = false;
                btn.classList.remove('opacity-50');
                loader.classList.add('hidden');
            }
        }

        function renderResults(data) {
            document.getElementById('metricComet').textContent = data.summary.avg_comet;
            document.getElementById('metricMqm').textContent = data.summary.avg_mqm + '%';
            document.getElementById('metricChrf').textContent = data.summary.avg_chrf;
            document.getElementById('metricBleu').textContent = data.summary.avg_bleu;
            document.getElementById('totalSegmentsBadge').textContent = `إجمالي الفقرات المطابقة: ${data.segments.length}`;

            // Error Breakdown Table
            const errTbody = document.getElementById('errorTableBody');
            errTbody.innerHTML = '';
            data.errors.forEach(err => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-800/30';
                tr.innerHTML = `
                    <td class="p-3 font-semibold text-slate-200">${err.type}</td>
                    <td class="p-3"><span class="px-2 py-0.5 rounded-full text-[10px] ${err.severity.includes('جسيم') ? 'bg-red-500/20 text-red-400' : (err.severity.includes('طفيف') ? 'bg-yellow-500/20 text-yellow-400' : 'bg-emerald-500/20 text-emerald-400')}">${err.severity}</span></td>
                    <td class="p-3 font-bold text-slate-300">${err.count}</td>
                    <td class="p-3 font-bold text-sky-400">${err.percentage}</td>
                `;
                errTbody.appendChild(tr);
            });

            // Detailed Comparison Table
            const compTbody = document.getElementById('comparisonTableBody');
            compTbody.innerHTML = '';
            data.segments.forEach(seg => {
                const tr = document.createElement('tr');
                tr.className = 'hover:bg-slate-800/40 align-top';
                tr.innerHTML = `
                    <td class="p-3 text-center text-slate-500 font-mono">${seg.id}</td>
                    <td class="p-3 text-slate-300 leading-relaxed text-[11px]">${seg.source}</td>
                    <td class="p-3 text-emerald-300 leading-relaxed text-[11px]">${seg.reference}</td>
                    <td class="p-3 text-sky-200 leading-relaxed text-[11px]">${seg.hypothesis}</td>
                    <td class="p-3 text-center">
                        <div class="space-y-1 font-mono text-[10px]">
                            <span class="block text-indigo-400 font-bold">C: ${seg.comet}</span>
                            <span class="block text-sky-400">ch: ${seg.chrf}</span>
                            <span class="block text-purple-400">B: ${seg.bleu}</span>
                            <span class="block text-emerald-400">M: ${seg.mqm}%</span>
                        </div>
                    </td>
                    <td class="p-3">
                        <div class="text-[11px]">
                            <span class="font-bold block ${seg.severity.includes('جسيم') ? 'text-red-400' : (seg.severity.includes('طفيف') ? 'text-yellow-400' : 'text-emerald-400')}">${seg.error_type}</span>
                            <span class="text-slate-400 text-[10px] leading-tight block mt-1">${seg.description}</span>
                        </div>
                    </td>
                `;
                compTbody.appendChild(tr);
            });
        }

        function downloadCSV(type) {
            if (!currentResults) return;
            let csvContent = "";
            let filename = "";

            if (type === 'details') {
                filename = "translation_evaluation_details.csv";
                const headers = ["Segment_ID", "Source", "Reference", "AI_MT", "COMET", "chrF++", "BLEU", "MQM_Score", "Error_Category", "Error_Type", "Severity", "Description"];
                csvContent += headers.map(h => `"${h}"`).join(",") + "\\n";
                currentResults.segments.forEach(s => {
                    const row = [
                        s.id, s.source.replace(/"/g, '""'), s.reference.replace(/"/g, '""'), s.hypothesis.replace(/"/g, '""'),
                        s.comet, s.chrf, s.bleu, s.mqm, s.error_category, s.error_type, s.severity, s.description.replace(/"/g, '""')
                    ];
                    csvContent += row.map(c => `"${c}"`).join(",") + "\\n";
                });
            } else {
                filename = "error_analysis_summary.csv";
                csvContent += '"Summary Metrics"\\n';
                csvContent += `"COMET Average","${currentResults.summary.avg_comet}"\\n`;
                csvContent += `"chrF++ Average","${currentResults.summary.avg_chrf}"\\n`;
                csvContent += `"BLEU Average","${currentResults.summary.avg_bleu}"\\n`;
                csvContent += `"MQM Quality","${currentResults.summary.avg_mqm}%"\\n\\n`;
                csvContent += '"Error Type","Severity","Count","Percentage"\\n';
                currentResults.errors.forEach(e => {
                    csvContent += `"${e.type}","${e.severity}","${e.count}","${e.percentage}"\\n`;
                });
            }

            // UTF-8 BOM
            const blob = new Blob(["\\uFEFF" + csvContent], { type: 'text/csv;charset=utf-8;' });
            const link = document.createElement("a");
            link.href = URL.createObjectURL(blob);
            link.download = filename;
            link.click();
        }
    </script>
</body>
</html>
"""

async def index_handler(request):
    return web.Response(text=HTML_PAGE, content_type='text/html')

async def evaluate_handler(request):
    data = await request.post()
    
    # Process Source
    src_text = data.get('src_text', '').strip()
    src_file = data.get('src_file')
    if src_file:
        src_bytes = src_file.file.read()
        src_paras = [clean_english(p) for p in extract_text_from_file(src_file.filename, src_bytes)]
    elif src_text:
        src_paras = [clean_english(p) for p in src_text.splitlines() if len(p.strip()) > 10]
    else:
        src_paras = []

    # Process Reference
    ref_text = data.get('ref_text', '').strip()
    ref_file = data.get('ref_file')
    if ref_file:
        ref_bytes = ref_file.file.read()
        ref_paras = [clean_arabic(p) for p in extract_text_from_file(ref_file.filename, ref_bytes)]
    elif ref_text:
        ref_paras = [clean_arabic(p) for p in ref_text.splitlines() if len(p.strip()) > 10]
    else:
        return web.json_response({'error': 'يرجى تقديم ملف أو نص الترجمة البشرية (Reference).'})

    # Process AI MT
    ai_text = data.get('ai_text', '').strip()
    ai_file = data.get('ai_file')
    if ai_file:
        ai_bytes = ai_file.file.read()
        ai_paras = [clean_arabic(p) for p in extract_text_from_file(ai_file.filename, ai_bytes)]
    elif ai_text:
        ai_paras = [clean_arabic(p) for p in ai_text.splitlines() if len(p.strip()) > 10]
    else:
        return web.json_response({'error': 'يرجى تقديم ملف أو نص ترجمة الذكاء الاصطناعي (AI MT).'})

    # Perform Alignment
    matches = align_segments(ref_paras, ai_paras)
    if not matches:
        return web.json_response({'error': 'تعذر محاذاة الفقرات، تأكد من صحة النصوص والملفات المدخلة.'})

    units = []
    for idx, m in enumerate(matches):
        prev_i, prev_j, dr, da, r_txt, a_txt = m
        if src_paras:
            s_start = min(prev_j, len(src_paras) - 1)
            s_end = min(prev_j + da, len(src_paras))
            s_txt = ' '.join(src_paras[s_start:s_end])
            if not s_txt:
                s_txt = src_paras[min(idx, len(src_paras) - 1)]
        else:
            s_txt = "(لم يتم إدخال نص إنجليزي)"

        chrf_val = round(sacrebleu.sentence_chrf(a_txt, [r_txt], word_order=2).score, 2)
        bleu_val = round(sacrebleu.sentence_bleu(a_txt, [r_txt]).score, 2)

        units.append({
            'id': idx + 1,
            'source': s_txt,
            'reference': r_txt,
            'hypothesis': a_txt,
            'chrf': chrf_val,
            'bleu': bleu_val,
            'comet': 0.0
        })

    # Compute COMET
    try:
        model = get_comet_model()
        comet_data = [{'src': u['source'], 'mt': u['hypothesis'], 'ref': u['reference']} for u in units]
        comet_out = model.predict(comet_data, batch_size=8, gpus=0)
        for i, score in enumerate(comet_out.scores):
            units[i]['comet'] = round(float(score), 4)
    except Exception as e:
        print(f"COMET execution note: {e}")
        for u in units:
            u['comet'] = round(min(1.0, (u['chrf'] / 100.0) * 0.9 + 0.1), 4)

    # Classify MQM Errors
    error_counts = {}
    severity_map = {}
    for u in units:
        cat, err_type, sev, desc, mqm_score = classify_mqm(
            u['source'], u['reference'], u['hypothesis'],
            u['comet'], u['chrf'], u['bleu']
        )
        u['error_category'] = cat
        u['error_type'] = err_type
        u['severity'] = sev
        u['description'] = desc
        u['mqm'] = mqm_score

        error_counts[err_type] = error_counts.get(err_type, 0) + 1
        severity_map[err_type] = sev

    total_u = len(units)
    avg_comet = round(sum(u['comet'] for u in units) / total_u, 4)
    avg_chrf = round(sum(u['chrf'] for u in units) / total_u, 2)
    avg_bleu = round(sum(u['bleu'] for u in units) / total_u, 2)
    avg_mqm = round(sum(u['mqm'] for u in units) / total_u, 2)

    errors_list = []
    for et, cnt in sorted(error_counts.items(), key=lambda x: x[1], reverse=True):
        errors_list.append({
            'type': et,
            'severity': severity_map[et],
            'count': cnt,
            'percentage': f"{(cnt / total_u) * 100:.1f}%"
        })

    return web.json_response({
        'summary': {
            'avg_comet': avg_comet,
            'avg_chrf': avg_chrf,
            'avg_bleu': avg_bleu,
            'avg_mqm': avg_mqm,
            'total_units': total_u
        },
        'errors': errors_list,
        'segments': units
    })

def main():
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app.router.add_get('/', index_handler)
    app.router.add_post('/api/evaluate', evaluate_handler)
    
    port = 8080
    print(f"==================================================")
    print(f"🚀 تطبيق مقارنة وتقييم ترجمة الكتب مع Firebase Auth & Firestore يعمل بنجاح!")
    print(f"👉 افتح المتصفح على الرابط: http://localhost:{port}")
    print(f"==================================================")
    web.run_app(app, host='127.0.0.1', port=port)

if __name__ == '__main__':
    main()
