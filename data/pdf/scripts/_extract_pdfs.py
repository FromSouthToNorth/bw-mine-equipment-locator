"""Extract first 20 pages from each PDF and output to stdout."""
import pymupdf
import os

files = [
    r'F:\gis\Point\data\pdf\standards\MT-T1004-2006_煤矿安全监控系统通用技术条件.pdf',
    r'F:\gis\Point\data\pdf\standards\MT-T1201.1-2023_煤矿感知数据联网接入规范.pdf',
    r'F:\gis\Point\data\pdf\standards\MT-T1201.4-2023_煤矿感知数据联网接入规范_水害防治.pdf',
    r'F:\gis\Point\data\pdf\standards\煤矿安全监管监察检查实施清单_2022.pdf',
    r'F:\gis\Point\data\pdf\standards\煤矿安全规程_2025_应急管理部令第17号.pdf',
]

for f in files:
    print('=' * 80)
    print(f'FILE: {os.path.basename(f)}')
    print('=' * 80)
    try:
        doc = pymupdf.open(f)
        max_pages = min(20, len(doc))
        for i in range(max_pages):
            page = doc[i]
            text = page.get_text()
            print(f'--- Page {i+1} ---')
            print(text[:2000])
        doc.close()
    except Exception as e:
        print(f'ERROR: {e}')
    print()
