import zipfile
import xml.etree.ElementTree as ET
import csv
import re

def get_docx_paras(path):
    with zipfile.ZipFile(path) as z:
        tree = ET.fromstring(z.read('word/document.xml'))
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    paras = []
    for p in tree.iterfind('.//w:p', ns):
        texts = [node.text for node in p.iterfind('.//w:t', ns) if node.text]
        p_text = ''.join(texts).strip()
        if p_text:
            paras.append(p_text)
    return paras

en_all = get_docx_paras('strange case of Dr. Jekyll and Mr. Hyde, The - Robert Louis Stevenson.docx')
ref_all = get_docx_paras('القضية الغريبة للدكتور جيكل ومستر هايد - روبرت لويس ستيفنسون.docx')
ai_all = get_docx_paras('arabic translation using ai.docx')

en_ch1 = en_all[37:65]
ref_ch1 = ref_all[119:147]
ai_ch1 = ai_all[2:30]

def clean_ref(text):
    # Remove Arabic footnote markers like ،١ or ١ etc.
    text = re.sub(r'[،,]?[٠-٩]+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

units = []

# Unit 0
units.append({
    'source': clean_text(en_ch1[0]),
    'reference': clean_ref(ref_ch1[0]),
    'hypothesis': clean_text(ai_ch1[0] + " " + ai_ch1[1])
})

# Unit 1
units.append({
    'source': clean_text(en_ch1[1]),
    'reference': clean_ref(ref_ch1[1]),
    'hypothesis': clean_text(ai_ch1[2])
})

# Unit 2
units.append({
    'source': clean_text(en_ch1[2]),
    'reference': clean_ref(ref_ch1[2]),
    'hypothesis': clean_text(ai_ch1[3])
})

# Unit 3
units.append({
    'source': clean_text(en_ch1[3]),
    'reference': clean_ref(ref_ch1[3]),
    'hypothesis': clean_text(ai_ch1[4])
})

# Unit 4: merge EN 4+5, Ref 4, AI 5+6
units.append({
    'source': clean_text(en_ch1[4] + " " + en_ch1[5]),
    'reference': clean_ref(ref_ch1[4]),
    'hypothesis': clean_text(ai_ch1[5] + " " + ai_ch1[6])
})

# Units 5 to 25
for i in range(5, 26):
    en_idx = i + 1
    ref_idx = i
    ai_idx = i + 2
    units.append({
        'source': clean_text(en_ch1[en_idx]),
        'reference': clean_ref(ref_ch1[ref_idx]),
        'hypothesis': clean_text(ai_ch1[ai_idx])
    })

print(f"Total aligned units: {len(units)}")

output_file = 'jekyll_hyde_ch1.csv'
with open(output_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.DictWriter(f, fieldnames=['source', 'reference', 'hypothesis'])
    writer.writeheader()
    for u in units:
        writer.writerow(u)

print(f"Successfully generated {output_file} with {len(units)} rows.")

for idx, u in enumerate(units):
    print(f"\n--- Row {idx+1} ---")
    print(f"SRC: {u['source'][:80]}...")
    print(f"REF: {u['reference'][:80]}...")
    print(f"HYP: {u['hypothesis'][:80]}...")
