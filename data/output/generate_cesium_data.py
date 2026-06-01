import json
import math
import re
from collections import Counter

# 读取数据
with open('data/output/data_8373_窑街煤电天宝煤业.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

if 'data' in data and isinstance(data['data'], dict):
    raw = data['data']
else:
    raw = data

cadData = raw.get('cadData', [])
tunnels = raw.get('tunnels', [])
workfaces = raw.get('workfaces', [])

with open('data/output/locator_result_F11795450_窑街煤电天宝煤业.json', 'r', encoding='utf-8') as f:
    result = json.load(f)

results = result.get('results', [])

# 仿射变换 (x, y) -> (lat, lng)
control_points = []
for item in cadData:
    coords = item.get('coordinates', {})
    latlng = item.get('latLng', '')
    if coords.get('x') and coords.get('y') and latlng:
        parts = latlng.split('/')
        if len(parts) == 2:
            try:
                control_points.append((coords['x'], coords['y'], float(parts[1]), float(parts[0])))
            except ValueError:
                pass

n = len(control_points)
sx = sum(p[0] for p in control_points); sy = sum(p[1] for p in control_points)
slng = sum(p[2] for p in control_points); slat = sum(p[3] for p in control_points)
sxx = sum(p[0]**2 for p in control_points); syy = sum(p[1]**2 for p in control_points)
sxy = sum(p[0]*p[1] for p in control_points)
sxlng = sum(p[0]*p[2] for p in control_points); sylng = sum(p[1]*p[2] for p in control_points)
sxlat = sum(p[0]*p[3] for p in control_points); sylat = sum(p[1]*p[3] for p in control_points)

def solve3(m, v):
    a = [row[:] for row in m]; b = v[:]
    for i in range(3):
        mr = i
        for j in range(i+1, 3):
            if abs(a[j][i]) > abs(a[mr][i]): mr = j
        a[i], a[mr] = a[mr], a[i]; b[i], b[mr] = b[mr], b[i]
        p = a[i][i]
        for j in range(i, 3): a[i][j] /= p
        b[i] /= p
        for k in range(i+1, 3):
            f = a[k][i]
            for j in range(i, 3): a[k][j] -= f * a[i][j]
            b[k] -= f * b[i]
    x = [0, 0, 0]
    for i in range(2, -1, -1):
        x[i] = b[i]
        for j in range(i+1, 3): x[i] -= a[i][j] * x[j]
    return x

al, bl, cl = solve3([[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]], [sxlng, sylng, slng])
aa, ba, ca = solve3([[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, n]], [sxlat, sylat, slat])

def xy_to_ll(x, y):
    return aa*x + ba*y + ca, al*x + bl*y + cl

# 点到折线最近距离 + 插值 z
def point_to_polyline_info(px, py, line):
    min_dist = float('inf')
    best_z = line[0].get('z', 0) if line else 0
    best_t = 0

    for i in range(len(line) - 1):
        p1, p2 = line[i], line[i+1]
        x1, y1, z1 = p1.get('x'), p1.get('y'), p1.get('z', 0)
        x2, y2, z2 = p2.get('x'), p2.get('y'), p2.get('z', 0)
        if None in (x1, y1, x2, y2):
            continue
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx*dx + dy*dy
        if seg_len_sq == 0:
            dist = math.sqrt((px-x1)**2 + (py-y1)**2)
            t = 0
        else:
            t = max(0, min(1, ((px-x1)*dx + (py-y1)*dy) / seg_len_sq))
            nx = x1 + t * dx
            ny = y1 + t * dy
            dist = math.sqrt((px-nx)**2 + (py-ny)**2)
        if dist < min_dist:
            min_dist = dist
            best_z = z1 + t * (z2 - z1)
            best_t = t

    return min_dist, best_z

# 准备巷道/工作面
polylines = []
for t in tunnels:
    if t.get('line') and t.get('name'):
        polylines.append({'name': t['name'], 'line': t['line']})
for w in workfaces:
    if w.get('line') and w.get('workFaceName'):
        polylines.append({'name': w['workFaceName'], 'line': w['line']})

# 1. 巷道折线 —— 使用真实 z
tunnel_lines = []
for pl in polylines:
    coords = []
    for p in pl['line']:
        x, y, z = p.get('x'), p.get('y'), p.get('z', 0)
        if x is not None and y is not None:
            lat, lng = xy_to_ll(x, y)
            coords.append([round(lng, 6), round(lat, 6), round(z, 1)])
    if len(coords) >= 2:
        tunnel_lines.append({'name': pl['name'], 'coords': coords})

print(f'巷道折线: {len(tunnel_lines)} 条')
print(f'样例: {tunnel_lines[0]["name"]} —— 坐标 {tunnel_lines[0]["coords"][0]}')

# 2. 设备点 —— 使用真实 z
device_points = []
for r in results:
    coords = r.get('coordinates', {})
    x, y, z = coords.get('x'), coords.get('y'), coords.get('z', 0)
    if x and y:
        lat, lng = xy_to_ll(x, y)
        device_points.append({
            'lat': round(lat, 6), 'lng': round(lng, 6), 'z': round(z, 1),
            'id': r.get('id', ''),
            'description': r.get('description', ''),
            'matched': r.get('matched_name', ''),
            'sensor_type': r.get('sensor_type', ''),
            'confidence': r.get('confidence', ''),
            'score': r.get('score', 0)
        })

print(f'设备点: {len(device_points)} 个')
if device_points:
    print(f'样例: {device_points[0]["id"]} z={device_points[0]["z"]}')

# 3. CAD 点 —— 插值最近巷道的 z
noise_contents = {'值＜1.0%.', '23ppm。', '烟雾报警值：有烟', 'CO上报值：24ppm，上解值：',
    'CO、烟雾', 'CO、烟雾。', '报警值≥1.0%,断电值≥1.5%,复电', '报警值≥1.0%,断电值≥1.0%,复电',
    '风筒开停报警值：无风。', '断电控制器', 'CH4（T1)', 'CH4（T2)', 'CH4', 'CH4(T1)。',
    '面CH4(T1)。', '面风筒传感器。', '回风流CH4(T2)。', '风筒传感器。', '主风机开停',
    '副风机开停', '闭锁开关', '双风机双电源开关', '风筒', '传感器', '分站', '光缆',
    '信号传输线', '局部通风机', '乏风', '环网交换机', '16°', '15°', '掘进迎头位置',
    '图号', '资料来源', '制图', '审核', '日期', '比例尺', '图例', '说明', '总工程师',
    '安全生产部', '天宝公司', '吴冬红', '2024年10月', '1:1000', '1.煤、岩巷',
    'T2报警值≥1.0%,断电值≥1.0%,复电值＜1.0%.', '2.断电范围:掘进巷道内全部非本质型安全电气设备',
    '红沙梁矿井安全监控布置图', '窑街煤电集团酒泉天宝煤业有限公司'}

matched_cad = []; anomaly_cad = []; ignored_plan = []
tunnel_names = [p['name'] for p in polylines]

for item in cadData:
    coords = item.get('coordinates', {})
    if not coords.get('x') or not coords.get('y'): continue
    x, y = coords['x'], coords['y']
    c = item.get('content', '')

    best_dist = float('inf')
    best_name = None
    best_z = 0
    for pl in polylines:
        dist, z = point_to_polyline_info(x, y, pl['line'])
        if dist < best_dist:
            best_dist = dist
            best_name = pl['name']
            best_z = z

    is_noise = bool(re.match(r'^[+\-]?\d+(\.\d+)?$', c) or re.match(r'^(X|Y|Z)=', c) or c in noise_contents)

    lat, lng = xy_to_ll(x, y)
    # CAD 点高度 = 巷道插值 z + 5m 偏移
    pt = {'lat': round(lat, 6), 'lng': round(lng, 6), 'z': round(best_z + 5, 1),
          'content': c, 'dist': round(best_dist, 1), 'nearest': best_name}

    if best_dist <= 100:
        is_a = False
        for tn in tunnel_names:
            if tn != best_name and tn in c and len(tn) >= 3:
                is_a = True; pt['should_be'] = tn; break
        (anomaly_cad if is_a else matched_cad).append(pt)
    elif not is_noise:
        ignored_plan.append(pt)

print(f'有效CAD: {len(matched_cad)} 异常: {len(anomaly_cad)} 规划: {len(ignored_plan)}')
if matched_cad:
    print(f'有效CAD样例: {matched_cad[0]["content"]} z={matched_cad[0]["z"]} nearest={matched_cad[0]["nearest"]}')

# 保存
json_data = {
    'tunnelLines': tunnel_lines,
    'matchedCad': matched_cad,
    'anomalyCad': anomaly_cad,
    'ignoredPlan': ignored_plan,
    'devicePoints': device_points,
}
with open('data/output/cesium_cad_data.json', 'w', encoding='utf-8') as f:
    json.dump(json_data, f, ensure_ascii=False, indent=2)
print('数据已保存到 data/output/cesium_cad_data.json')
