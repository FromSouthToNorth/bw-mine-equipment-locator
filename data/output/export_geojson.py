import json
from pathlib import Path

# 读取数据
with open('cesium_cad_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

output_dir = Path('geojson')
output_dir.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════
# 1. 巷道折线 (LineString, 3D)
# ═══════════════════════════════════════════════════════════
tunnel_features = []
for t in data['tunnelLines']:
    tunnel_features.append({
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": t['coords']  # [lng, lat, z] 列表
        },
        "properties": {
            "name": t['name'],
            "category": "tunnel",
            "point_count": len(t['coords']),
            "z_min": min(c[2] for c in t['coords']),
            "z_max": max(c[2] for c in t['coords']),
        }
    })

tunnels_geojson = {
    "type": "FeatureCollection",
    "name": "tunnels",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
    "features": tunnel_features
}

with open(output_dir / "tunnels.geojson", 'w', encoding='utf-8') as f:
    json.dump(tunnels_geojson, f, ensure_ascii=False, indent=2)

print(f"✓ tunnels.geojson: {len(tunnel_features)} 条折线")

# ═══════════════════════════════════════════════════════════
# 2. 设备点 (Point, 3D)
# ═══════════════════════════════════════════════════════════
device_features = []
for d in data['devicePoints']:
    device_features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [d['lng'], d['lat'], d['z']]
        },
        "properties": {
            "id": d['id'],
            "name": d['description'],
            "sensor_type": d['sensor_type'],
            "matched_tunnel": d['matched'],
            "confidence": d['confidence'],
            "score": d['score'],
            "category": "device",
        }
    })

devices_geojson = {
    "type": "FeatureCollection",
    "name": "devices",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
    "features": device_features
}

with open(output_dir / "devices.geojson", 'w', encoding='utf-8') as f:
    json.dump(devices_geojson, f, ensure_ascii=False, indent=2)

print(f"✓ devices.geojson: {len(device_features)} 个点")

# ═══════════════════════════════════════════════════════════
# 3. CAD 点 - 有效匹配 (Point, 3D)
# ═══════════════════════════════════════════════════════════
cad_match_features = []
for p in data['matchedCad']:
    cad_match_features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [p['lng'], p['lat'], p['z']]
        },
        "properties": {
            "content": p['content'],
            "nearest_tunnel": p['nearest'],
            "distance": p['dist'],
            "category": "cad_matched",
        }
    })

with open(output_dir / "cad_matched.geojson", 'w', encoding='utf-8') as f:
    json.dump({
        "type": "FeatureCollection",
        "name": "cad_matched",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": cad_match_features
    }, f, ensure_ascii=False, indent=2)

print(f"✓ cad_matched.geojson: {len(cad_match_features)} 个点")

# ═══════════════════════════════════════════════════════════
# 4. CAD 点 - 异常标注 (Point, 3D)
# ═══════════════════════════════════════════════════════════
cad_anomaly_features = []
for p in data['anomalyCad']:
    cad_anomaly_features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [p['lng'], p['lat'], p['z']]
        },
        "properties": {
            "content": p['content'],
            "nearest_tunnel": p['nearest'],
            "should_be": p.get('should_be', ''),
            "distance": p['dist'],
            "category": "cad_anomaly",
        }
    })

with open(output_dir / "cad_anomaly.geojson", 'w', encoding='utf-8') as f:
    json.dump({
        "type": "FeatureCollection",
        "name": "cad_anomaly",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": cad_anomaly_features
    }, f, ensure_ascii=False, indent=2)

print(f"✓ cad_anomaly.geojson: {len(cad_anomaly_features)} 个点")

# ═══════════════════════════════════════════════════════════
# 5. CAD 点 - 规划区域 (Point, 3D)
# ═══════════════════════════════════════════════════════════
cad_plan_features = []
for p in data['ignoredPlan']:
    cad_plan_features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [p['lng'], p['lat'], p['z']]
        },
        "properties": {
            "content": p['content'],
            "nearest_tunnel": p['nearest'],
            "distance": p['dist'],
            "category": "cad_planning",
        }
    })

with open(output_dir / "cad_planning.geojson", 'w', encoding='utf-8') as f:
    json.dump({
        "type": "FeatureCollection",
        "name": "cad_planning",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": cad_plan_features
    }, f, ensure_ascii=False, indent=2)

print(f"✓ cad_planning.geojson: {len(cad_plan_features)} 个点")

# ═══════════════════════════════════════════════════════════
# 6. 合并为一个总览文件
# ═══════════════════════════════════════════════════════════
all_features = []
all_features.extend(tunnel_features)
all_features.extend(device_features)
all_features.extend(cad_match_features)
all_features.extend(cad_anomaly_features)
all_features.extend(cad_plan_features)

with open(output_dir / "all_layers.geojson", 'w', encoding='utf-8') as f:
    json.dump({
        "type": "FeatureCollection",
        "name": "all_layers",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": all_features
    }, f, ensure_ascii=False, indent=2)

print(f"✓ all_layers.geojson: {len(all_features)} 个要素")
print(f"\n导出完成: {output_dir.absolute()}")
