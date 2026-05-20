@echo off
chcp 65001 >nul
echo ============================================
echo  提取低置信度匹配设备
echo  文件: locator_result_D99795450_窑街煤电金河煤矿.json
echo ============================================
echo.

cd /d "F:\gis\Point"

python -c "
import json
with open('data/output/locator_result_D99795450_窑街煤电金河煤矿.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

results = data.get('results', [])

# 查找低置信度 (匹配得分最低的20条)
lowest = sorted(results, key=lambda r: (r.get('match_score', 0), r.get('match_lcs', 0)))[:20]

# 也检查是否有 'confidence': '低' 的条目
low_conf = [r for r in results if r.get('confidence') == '低']

print(f'总匹配数: {len(results)}')
print(f'标注为\"低\"置信度的: {len(low_conf)} 条')
print(f'得分最低的20条:')
print()

for i, r in enumerate(lowest, 1):
    print(f'--- [{i}] ---')
    print(f'  ID: {r.get(\"id\",\"?\")}')
    print(f'  描述: {r.get(\"description\",\"\")}')
    print(f'  匹配到: {r.get(\"matched_name\",\"?\")}')
    print(f'  得分: {r.get(\"match_score\",0)}')
    print(f'  LCS: {r.get(\"match_lcs\",0)}')
    print(f'  置信度标签: {r.get(\"confidence\",\"?\")}')
    print(f'  类型: {r.get(\"sensor_type\",\"\")} / {r.get(\"mark_type\",\"\")}')
    print(f'  坐标: ({r.get(\"coordinates\",{}).get(\"x\",0):.2f}, {r.get(\"coordinates\",{}).get(\"y\",0):.2f}, {r.get(\"coordinates\",{}).get(\"z\",0):.2f})')
    print()

# 按置信度统计
from collections import Counter
conf_counts = Counter(r.get('confidence') for r in results)
print('=== 置信度分布 ===')
for conf, cnt in sorted(conf_counts.items()):
    print(f'  {conf}: {cnt}')
print()

# 显示摘要中的 by_confidence
print('=== 摘要中的 by_confidence ===')
print(json.dumps(data.get('summary',{}).get('by_confidence',{}), ensure_ascii=False, indent=2))
"
echo.
pause
