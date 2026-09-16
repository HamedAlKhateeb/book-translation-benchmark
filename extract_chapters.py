import os
import zipfile
from html.parser import HTMLParser

class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
    def handle_data(self, d):
        self.result.append(d)
    def get_text(self):
        return ''.join(self.result)

def clean_html(html_str):
    p = TextExtractor()
    p.feed(html_str)
    text = p.get_text()
    lines = [line.strip() for line in text.splitlines()]
    return '\n'.join(line for line in lines if line)

base_dir = os.path.dirname(os.path.abspath(__file__))
ar_out_dir = os.path.join(base_dir, 'الفصل_الثاني_لكل_كتاب')
en_out_dir = os.path.join(ar_out_dir, 'English_Versions')

os.makedirs(ar_out_dir, exist_ok=True)
os.makedirs(en_out_dir, exist_ok=True)

# 1. Arabic Books (Chapter 2)
arabic_books = [
    {
        'epub': 'العناصر مقدمة قصيرة جدا.epub',
        'name': '1 - العناصر مقدمة قصيرة جدا - الفصل الثاني (الثورة كيف غير الأكسجين العالم)',
        'entry': 'EPUB/Content/chapter-1-3.xhtml'
    },
    {
        'epub': 'تغطية الإسلام إدوارد سعيد.epub',
        'name': '2 - تغطية الإسلام - الفصل الثاني (قصة إيران)',
        'entry': 'EPUB/Content/chapter-1-5.xhtml'
    },
    {
        'epub': 'الفلسفة_بنظرة_علمية.epub',
        'name': '3 - الفلسفة بنظرة علمية - الفصل الثاني (الإنسان وبيئته)',
        'entry': 'EPUB/Content/chapter-1-4.xhtml'
    },
    {
        'epub': 'دروس مبسطة في علم الاقتصاد.epub',
        'name': '4 - دروس مبسطة في علم الاقتصاد - الفصل الثاني (تطوير مبادئ علم الاقتصاد)',
        'entry': 'EPUB/Content/chapter-1-4.xhtml'
    }
]

for b in arabic_books:
    epub_path = os.path.join(base_dir, b['epub'])
    if not os.path.exists(epub_path):
        print(f"Warning: {epub_path} not found!")
        continue
    with zipfile.ZipFile(epub_path, 'r') as z:
        if 'entry' in b:
            raw = z.read(b['entry']).decode('utf-8', errors='ignore')
        else:
            raw = '\n\n'.join(z.read(e).decode('utf-8', errors='ignore') for e in b['entries'])
        
        txt = clean_html(raw)
        
        out_txt = os.path.join(ar_out_dir, f"{b['name']}.txt")
        with open(out_txt, 'w', encoding='utf-8') as f_out:
            f_out.write(txt)
            
        out_html = os.path.join(ar_out_dir, f"{b['name']}.html")
        with open(out_html, 'w', encoding='utf-8') as f_out:
            f_out.write(raw)

# 2. English Books (Chapter 2)
eng_dir = os.path.join(base_dir, 'English versions')
english_books = [
    {
        'epub': 'The_Elements_A_Very_Short_Introductio_z_library_sk,_1lib_sk,.epub',
        'name': '1 - The Elements - Chapter 2 (Revolution - How oxygen changed the world)',
        'entry': 'index_split_009.html'
    },
    {
        'epub': 'Covering_Islam_How_the_Media_and_the_z_library_sk,_1lib_sk,.epub',
        'name': '2 - Covering Islam - Chapter 2 (The Iran Story)',
        'entries': [
            'index_split_012.html',
            'index_split_013.html',
            'index_split_014.html',
            'index_split_015.html',
            'index_split_016.html'
        ]
    },
    {
        'epub': 'An_Outline_of_Philosophy_Russell,_Be_z_library_sk,_1lib_sk,.epub',
        'name': '3 - An Outline of Philosophy - Chapter 2 (Man and His Environment)',
        'entry': 'OEBPS/Text/9781134027477_010.xhtml'
    },
    {
        'epub': 'Lessons_for_the_Young_Economists_Mur_z_library_sk,_1lib_sk,.epub',
        'name': '4 - Lessons for the Young Economist - Chapter 2 (How We Develop Economic Principles)',
        'entries': [
            'OEBPS/11-chap02.xhtml',
            'OEBPS/12-page27.xhtml',
            'OEBPS/13-page28.xhtml',
            'OEBPS/14-page29.xhtml'
        ]
    }
]

for b in english_books:
    epub_path = os.path.join(eng_dir, b['epub'])
    if not os.path.exists(epub_path):
        print(f"Warning: {epub_path} not found!")
        continue
    with zipfile.ZipFile(epub_path, 'r') as z:
        if 'entry' in b:
            raw = z.read(b['entry']).decode('utf-8', errors='ignore')
        else:
            raw = '\n\n'.join(z.read(e).decode('utf-8', errors='ignore') for e in b['entries'])
        
        txt = clean_html(raw)
        
        out_txt = os.path.join(en_out_dir, f"{b['name']}.txt")
        with open(out_txt, 'w', encoding='utf-8') as f_out:
            f_out.write(txt)
            
        out_html = os.path.join(en_out_dir, f"{b['name']}.html")
        with open(out_html, 'w', encoding='utf-8') as f_out:
            f_out.write(raw)

print("Extraction of Arabic and English Chapter 2 completed successfully!")
