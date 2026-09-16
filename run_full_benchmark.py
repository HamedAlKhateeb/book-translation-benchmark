import os
import sys
import re
import csv
import zipfile
import functools
from bs4 import BeautifulSoup
from rapidfuzz import fuzz
import sacrebleu

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

# Monkeypatch for Python 3.14 compatibility with COMET
class _HashedSeq(list):
    __slots__ = 'hashvalue'
    def __init__(self, tup, hash=hash):
        self[:] = tup
        self.hashvalue = hash(tup)
    def __hash__(self):
        return self.hashvalue
functools._HashedSeq = _HashedSeq

from comet import load_from_checkpoint

# Text cleaning helpers
def clean_ar(t):
    t = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', t)
    t = re.sub(r'[،,]?[٠-٩]+', '', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def clean_en(t):
    t = re.sub(r'\s+', ' ', t).strip()
    return t

def get_elements_from_html(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')
    res = []
    for el in soup.find_all(['p']):
        txt = el.get_text().strip()
        if len(txt) > 20:
            res.append(txt)
    return res

def get_elements_from_docx(path):
    with zipfile.ZipFile(path) as z:
        tree = BeautifulSoup(z.read('word/document.xml'), 'xml')
    res = []
    for p in tree.find_all('p'):
        txt = p.get_text().strip()
        if txt and txt != 'HTML' and not txt.startswith('HTML+') and len(txt) > 20:
            res.append(txt)
    return res

def align_ref_ai(ref, ai, max_merge=4):
    n_ref, n_ai = len(ref), len(ai)
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
                r_txt = ' '.join(ref[i:i+dr])
                for da in range(1, max_merge + 1):
                    if j + da > n_ai:
                        continue
                    a_txt = ' '.join(ai[j:j+da])
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
        r_txt = ' '.join(ref[prev_i:prev_i+dr])
        a_txt = ' '.join(ai[prev_j:prev_j+da])
        matches.append((prev_i, prev_j, dr, da, r_txt, a_txt))
        curr_i, curr_j = prev_i, prev_j

    matches.reverse()
    return matches

def classify_mqm_error(src, ref, hyp, comet_score, chrf_score, bleu_score):
    """
    Classify error using MQM taxonomy based on semantic, lexical, and structural analysis.
    """
    hyp_words = hyp.split()
    word_count = max(1, len(hyp_words))
    
    # Check untranslated English words or residual tags
    english_words = re.findall(r'[a-zA-Z]{3,}', hyp)
    english_words = [w for w in english_words if w.lower() not in ['html', 'docx', 'pdf', 'url']]
    
    src_len = len(src.split())
    hyp_len = len(hyp_words)
    ref_len = len(ref.split())
    
    length_ratio_ref = hyp_len / max(1, ref_len)
    
    # 1. Critical / Major Omission
    if length_ratio_ref < 0.65 and ref_len > 25:
        cat = 'الدقة (Accuracy)'
        err_type = 'حذف / سقط في المعنى (Omission)'
        sev = 'جسيم (Major)'
        desc = f'سقط جزء كبير من الفقرة في ترجمة الذكاء الاصطناعي بنسبة نقص {int((1-length_ratio_ref)*100)}%'
        penalty = 5
    elif english_words:
        cat = 'الدقة (Accuracy)'
        err_type = 'نص غير مترجم (Untranslated)'
        sev = 'طفيف (Minor)'
        desc = f'بقاء كلمات إنجليزية دون تعريب: {", ".join(english_words[:3])}'
        penalty = 1
    # 2. Major Mistranslation (Very low COMET and low chrF)
    elif comet_score < 0.72 or (comet_score < 0.76 and chrf_score < 30):
        cat = 'الدقة (Accuracy)'
        err_type = 'ترجمة غير دقيقة / معنى مغاير (Mistranslation)'
        sev = 'جسيم (Major)'
        desc = 'انحراف دلالي واضح عن قصد النص الأصلي والمصطلحات المعتمدة في الترجمة البشرية'
        penalty = 5
    # 3. Terminology Inappropriateness
    elif chrf_score < 42 and comet_score < 0.83:
        cat = 'المصطلحات (Terminology)'
        err_type = 'مصطلح غير دقيق (Inappropriate Term)'
        sev = 'طفيف (Minor)'
        desc = 'استخدام مصطلحات عامة أو معجمية تفتقر للاتساق الدقيق مع المصطلح العلمي/الفكري المعتمد'
        penalty = 1
    # 4. Style: Literalness / Calque
    elif bleu_score < 15 and chrf_score < 48:
        cat = 'الأسلوب (Style)'
        err_type = 'حرفية زائدة وركاكة أسلوبية (Literalness / Awkward Phrasing)'
        sev = 'طفيف (Minor)'
        desc = 'الترجمة حرفية تتبع بناء الجملة الإنجليزية وتقل فيها بلاغة التركيب العربي مقارنة بالأصل البشري'
        penalty = 1
    # 5. Fluency: Grammar / Syntax
    elif ' الذي ' in hyp and ' التي ' in ref and ' أن ' not in hyp:
        cat = 'السلاسة (Fluency)'
        err_type = 'قواعد ونحو وتركيب (Grammar / Syntax)'
        sev = 'طفيف (Minor)'
        desc = 'خلل تركيبي في مطابقة الضمائر أو أدوات الربط في السياق العربي'
        penalty = 1
    # 6. Addition
    elif length_ratio_ref > 1.45 and ref_len > 25:
        cat = 'الدقة (Accuracy)'
        err_type = 'إضافة غير مبررة (Addition)'
        sev = 'طفيف (Minor)'
        desc = 'إسهاب وحشو زائد في صياغة الفكرة لم يرد في النص الأصلي'
        penalty = 1
    # 7. Excellent / No significant error
    else:
        cat = 'لا يوجد (None)'
        err_type = 'ترجمة صحيحة وسليمة (Accurate & Fluent)'
        sev = 'منعدم (None)'
        desc = 'ترجمة دقيقة وسلسة ومكافئة دلالياً للترجمة البشرية الاحترافية'
        penalty = 0

    # MQM Score calculation (Standard MQM penalty formula normalized)
    # Penalties scaled per 100 words
    penalty_points = penalty * (100.0 / max(20, word_count))
    mqm_score = max(0.0, min(100.0, 100.0 - penalty_points))
    
    return cat, err_type, sev, desc, round(mqm_score, 2)

def main():
    print("Loading COMET model...")
    ckpt_path = r'C:\Users\hamed\.cache\huggingface\hub\models--Unbabel--wmt22-comet-da\snapshots\2760a223ac957f30acfb18c8aa649b01cf1d75f2\checkpoints\model.ckpt'
    comet_model = load_from_checkpoint(ckpt_path)
    print("COMET model loaded successfully.")

    books_config = [
        {
            'id': 1,
            'title': 'العناصر: مقدمة قصيرة جدًّا',
            'title_en': 'The Elements: A Very Short Introduction',
            'src_path': '2nd chapter for every book/1 - The Elements - Chapter 2 (Revolution - How oxygen changed the world).html',
            'ref_path': 'الترجمة المجمعة/1 - العناصر مقدمة قصيرة جدا - الفصل الثاني (الثورة كيف غير الأكسجين العالم).html',
            'ai_path': 'الترجمة المجمعة/1-the-elements-chapter-2-revolution-how-oxygen-changed-the-world.html',
            'ai_is_docx': False
        },
        {
            'id': 2,
            'title': 'تغطية الإسلام',
            'title_en': 'Covering Islam',
            'src_path': '2nd chapter for every book/2 - Covering Islam - Chapter 2 (The Iran Story).html',
            'ref_path': 'الترجمة المجمعة/2 - تغطية الإسلام - الفصل الثاني (قصة إيران).html',
            'ai_path': 'الترجمة المجمعة/covering islam edward .docx',
            'ai_is_docx': True
        },
        {
            'id': 3,
            'title': 'الفلسفة بنظرة علمية',
            'title_en': 'An Outline of Philosophy',
            'src_path': '2nd chapter for every book/3 - An Outline of Philosophy - Chapter 2 (Man and His Environment).html',
            'ref_path': 'الترجمة المجمعة/3 - الفلسفة بنظرة علمية - الفصل الثاني (الإنسان وبيئته).html',
            'ai_path': 'الترجمة المجمعة/3-an-outline-of-philosophy-chapter-2-man-and-his-environment.html',
            'ai_is_docx': False
        },
        {
            'id': 4,
            'title': 'دروس مبسطة في علم الاقتصاد',
            'title_en': 'Lessons for the Young Economist',
            'src_path': '2nd chapter for every book/4 - Lessons for the Young Economist - Chapter 2 (How We Develop Economic Principles).html',
            'ref_path': 'الترجمة المجمعة/4 - دروس مبسطة في علم الاقتصاد - الفصل الثاني (تطوير مبادئ علم الاقتصاد).html',
            'ai_path': 'الترجمة المجمعة/4-lessons-for-the-young-economist-chapter-2-how-we-develop-economic-principles.html',
            'ai_is_docx': False
        }
    ]

    all_rows = []
    book_summaries = []

    for cfg in books_config:
        print(f"\nProcessing Book {cfg['id']}: {cfg['title']}...")
        src_paras = [clean_en(p) for p in get_elements_from_html(cfg['src_path'])]
        ref_paras = [clean_ar(p) for p in get_elements_from_html(cfg['ref_path'])]
        ai_paras = [clean_ar(p) for p in (get_elements_from_docx(cfg['ai_path']) if cfg['ai_is_docx'] else get_elements_from_html(cfg['ai_path']))]

        # Handle header in source if needed
        if cfg['id'] == 3 and 'MAN AND HIS ENVIRONMENT' in src_paras[0]:
            src_paras = src_paras[1:]

        matches = align_ref_ai(ref_paras, ai_paras)
        print(f"  Aligned {len(matches)} units.")

        # Map source to matches
        units = []
        for idx, m in enumerate(matches):
            prev_i, prev_j, dr, da, r_txt, a_txt = m
            
            # Map corresponding source segment
            s_start = min(prev_j, len(src_paras) - 1)
            s_end = min(prev_j + da, len(src_paras))
            s_txt = ' '.join(src_paras[s_start:s_end])
            if not s_txt:
                s_txt = src_paras[min(idx, len(src_paras) - 1)]

            units.append({
                'book_id': cfg['id'],
                'book_title': cfg['title'],
                'chapter_num': 2,
                'segment_id': idx + 1,
                'source': s_txt,
                'reference': r_txt,
                'hypothesis': a_txt
            })

        # Compute chrF++ and BLEU
        for u in units:
            u['chrf++'] = round(sacrebleu.sentence_chrf(u['hypothesis'], [u['reference']], word_order=2).score, 2)
            u['bleu'] = round(sacrebleu.sentence_bleu(u['hypothesis'], [u['reference']]).score, 2)

        # Compute COMET in batches
        comet_data = [
            {'src': u['source'], 'mt': u['hypothesis'], 'ref': u['reference']}
            for u in units
        ]
        print(f"  Computing COMET scores for {len(units)} segments...")
        comet_res = comet_model.predict(comet_data, batch_size=8, gpus=0)
        for i, score in enumerate(comet_res.scores):
            units[i]['comet'] = round(float(score), 4)

        # Compute MQM error classification
        for u in units:
            cat, err_type, sev, desc, mqm = classify_mqm_error(
                u['source'], u['reference'], u['hypothesis'],
                u['comet'], u['chrf++'], u['bleu']
            )
            u['error_category'] = cat
            u['error_type'] = err_type
            u['severity'] = sev
            u['error_description'] = desc
            u['mqm_score'] = mqm

        all_rows.extend(units)

        # Book summary
        avg_bleu = sum(u['bleu'] for u in units) / len(units)
        avg_chrf = sum(u['chrf++'] for u in units) / len(units)
        avg_comet = sum(u['comet'] for u in units) / len(units)
        avg_mqm = sum(u['mqm_score'] for u in units) / len(units)

        book_summaries.append({
            'book_id': cfg['id'],
            'book_title': cfg['title'],
            'segments_count': len(units),
            'avg_bleu': round(avg_bleu, 2),
            'avg_chrf': round(avg_chrf, 2),
            'avg_comet': round(avg_comet, 4),
            'avg_mqm': round(avg_mqm, 2)
        })

    # Write detailed CSV (UTF-8 with BOM for Excel compatibility)
    out_details_csv = 'evaluation_results_all_chapters.csv'
    fieldnames = [
        'book_id', 'book_title', 'chapter_num', 'segment_id',
        'source', 'reference', 'hypothesis',
        'bleu', 'chrf++', 'comet', 'mqm_score',
        'error_category', 'error_type', 'severity', 'error_description'
    ]
    with open(out_details_csv, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_rows:
            writer.writerow(r)
    print(f"\nWritten {len(all_rows)} rows to {out_details_csv} with utf-8-sig.")

    # Error analysis and aggregation
    error_counts = {}
    severity_counts = {'منعدم (None)': 0, 'طفيف (Minor)': 0, 'جسيم (Major)': 0, 'حرج (Critical)': 0}
    for r in all_rows:
        e_type = r['error_type']
        error_counts[e_type] = error_counts.get(e_type, 0) + 1
        sev = r['severity']
        if sev in severity_counts:
            severity_counts[sev] += 1

    total_units = len(all_rows)
    error_summary_rows = []
    for e_type, cnt in sorted(error_counts.items(), key=lambda x: x[1], reverse=True):
        error_summary_rows.append({
            'نوع الخطأ': e_type,
            'التكرار (العدد)': cnt,
            'النسبة المئوية (%)': f"{(cnt / total_units) * 100:.2f}%"
        })

    # Write error summary CSV (UTF-8 with BOM)
    out_summary_csv = 'error_analysis_and_metrics_summary.csv'
    with open(out_summary_csv, 'w', newline='', encoding='utf-8-sig') as f:
        # Section 1: Book-level metrics comparison
        f.write("# مقارنة المعايير التقييمية (COMET, MQM, chrF++, BLEU) لكل فصل\n")
        w_book = csv.DictWriter(f, fieldnames=[
            'book_id', 'book_title', 'segments_count', 'avg_comet', 'avg_mqm', 'avg_chrf', 'avg_bleu'
        ])
        w_book.writeheader()
        for bs in book_summaries:
            w_book.writerow(bs)

        f.write("\n# إحصائيات وتوزيع أنواع الأخطاء (MQM Error Distribution)\n")
        w_err = csv.DictWriter(f, fieldnames=['نوع الخطأ', 'التكرار (العدد)', 'النسبة المئوية (%)'])
        w_err.writeheader()
        for es in error_summary_rows:
            w_err.writerow(es)

    print(f"Written summary to {out_summary_csv} with utf-8-sig.")

if __name__ == '__main__':
    main()
