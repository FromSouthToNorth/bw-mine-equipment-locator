"""Extract monitoring-related sections from 检查实施清单 (pages 40-50) and 煤矿安全规程_2025 (pages 100-200)."""
import pymupdf

# ====== 煤矿安全监管监察检查实施清单_2022 ======
# Section 7 (监控与通信) starts at page 41 per TOC
doc1 = pymupdf.open(r'F:\gis\Point\data\pdf\standards\煤矿安全监管监察检查实施清单_2022.pdf')
print(f'检查实施清单 total pages: {len(doc1)}')
print('=' * 80)
print('SECTION: 检查实施清单 - 监控与通信 (pages 40-60)')
print('=' * 80)
for i in range(39, min(60, len(doc1))):
    page = doc1[i]
    text = page.get_text()
    if text.strip():
        print(f'--- Page {i+1} ---')
        print(text)
doc1.close()

# ====== 煤矿安全规程_2025 ======
# Need to find ventilation and monitoring sections
doc2 = pymupdf.open(r'F:\gis\Point\data\pdf\standards\煤矿安全规程_2025_应急管理部令第17号.pdf')
print(f'\n煤矿安全规程_2025 total pages: {len(doc2)}')
print('=' * 80)
print('SECTION: 煤矿安全规程_2025 - All pages (searching for 通风/监控 sections)')
print('=' * 80)

# First, find which pages contain 通风 and 监控
for i in range(len(doc2)):
    page = doc2[i]
    text = page.get_text()
    if '通风' in text or '监控' in text or '传感器' in text:
        print(f'--- Page {i+1} ---')
        print(text[:2000])
doc2.close()
