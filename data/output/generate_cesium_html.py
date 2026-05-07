#!/usr/bin/env python3
"""把 locator 结果转为 CesiumJS 可视化 HTML."""
import json
import sys
from pathlib import Path

try:
    import pyproj
except ImportError:
    print("需要 pyproj: pip install pyproj")
    sys.exit(1)

# ── 路径 ──────────────────────────────────────────────────────────
RESULT_JSON = Path(__file__).parent / "locator_result_D37795450_济矿运河煤矿.json"
DATA_8373_JSON = Path(__file__).parent / "data_8373_济矿运河煤矿.json"
OUTPUT_HTML = Path(__file__).parent / "cesium_visualization.html"

# ── 坐标转换 ──────────────────────────────────────────────────────
# CGCS2000 / 3-degree Gauss-Kruger zone 39 (带号格式)
CRS_SRC = pyproj.CRS.from_proj4(
    "+proj=tmerc +lat_0=0 +lon_0=117 +k=1 +x_0=39500000 +y_0=0 "
    "+ellps=GRS80 +units=m +no_defs"
)
CRS_WGS84 = pyproj.CRS.from_epsg(4326)
transformer = pyproj.Transformer.from_crs(CRS_SRC, CRS_WGS84, always_xy=True)

# ── 传感器颜色 ────────────────────────────────────────────────────
SENSOR_COLORS = {
    "瓦斯": "#FF4444",
    "一氧化碳": "#FF8800",
    "风速": "#00DDFF",
    "温度": "#FFDD00",
    "烟雾": "#888888",
    "粉尘": "#8B4513",
    "断电": "#AA44FF",
    "馈电": "#AA44FF",
    "开停": "#AA44FF",
    "人员定位": "#44FF44",
    "风门": "#CCCCCC",
    "电流": "#CCCCCC",
    "电源状态": "#CCCCCC",
    "负压": "#CCCCCC",
}

# ── 读取结果 ──────────────────────────────────────────────────────
with open(RESULT_JSON, "r", encoding="utf-8") as f:
    data = json.load(f)

results = data.get("results", [])
unmatched = data.get("unmatched_devices", [])

# ── 转换坐标 ──────────────────────────────────────────────────────
entities = []

for r in results:
    coords = r.get("coordinates", {})
    x = coords.get("x")
    y = coords.get("y")
    z = coords.get("z")
    if x is None or y is None:
        continue
    try:
        lon, lat = transformer.transform(float(x), float(y))
        height = float(z) if z is not None else 0
    except Exception:
        continue

    sensor = r.get("sensor_type", "其他")
    color = SENSOR_COLORS.get(sensor, "#FFFFFF")
    conf = r.get("confidence", "低")
    size = 8 if conf == "高" else (6 if conf == "中" else 4)

    entities.append({
        "id": r.get("id", ""),
        "description": r.get("description", ""),
        "matched_name": r.get("matched_name", ""),
        "matched_type": r.get("matched_type", ""),
        "sensor_type": sensor,
        "confidence": conf,
        "mark_type": r.get("mark_type", ""),
        "sysaliasname": r.get("sysaliasname", ""),
        "lon": round(lon, 6),
        "lat": round(lat, 6),
        "height": round(height, 2),
        "color": color,
        "size": size,
    })

# 未匹配设备也给个列表用于右侧面板显示
unmatched_list = [
    {
        "id": u.get("id", ""),
        "description": u.get("description", ""),
        "sensor_type": u.get("sensor_type", ""),
        "reason": u.get("reason", ""),
    }
    for u in unmatched
]

# ── 读取并转换巷道折线 ────────────────────────────────────────────
tunnel_polylines = []
if DATA_8373_JSON.exists():
    with open(DATA_8373_JSON, "r", encoding="utf-8") as f:
        raw8373 = json.load(f)
    for cand in raw8373.get("candidates", []):
        line = cand.get("line", [])
        if len(line) < 2:
            continue
        positions = []
        for pt in line:
            x = pt.get("x")
            y = pt.get("y")
            z = pt.get("z")
            if x is None or y is None:
                continue
            try:
                lon, lat = transformer.transform(float(x), float(y))
                height = float(z) if z is not None else 0
                positions.append({"lon": round(lon, 6), "lat": round(lat, 6), "height": round(height, 2)})
            except Exception:
                continue
        if len(positions) >= 2:
            tunnel_polylines.append({
                "name": cand.get("name", ""),
                "type": cand.get("type", ""),
                "positions": positions,
            })

# ── 生成 HTML ─────────────────────────────────────────────────────
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>煤矿设备定位 — CesiumJS 可视化</title>
<script src="https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Cesium.js"></script>
<link href="https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Widgets/widgets.css" rel="stylesheet">
<style>
  html, body, #cesiumContainer {{ width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; font-family: "Microsoft YaHei", sans-serif; }}
  #infoPanel {{
    position: absolute; top: 10px; left: 10px;
    background: rgba(0,0,0,0.75); color: #fff;
    padding: 14px; border-radius: 8px; max-width: 320px;
    font-size: 13px; line-height: 1.6; pointer-events: auto;
    max-height: 90vh; overflow-y: auto;
  }}
  #infoPanel h2 {{ margin: 0 0 8px; font-size: 15px; }}
  #infoPanel .stat {{ display: flex; justify-content: space-between; margin: 2px 0; }}
  #infoPanel .legend {{ display: flex; align-items: center; margin: 3px 0; }}
  #infoPanel .dot {{ width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; display: inline-block; }}
  #infoPanel .unmatched {{ color: #ff6666; margin-top: 8px; }}
  #infoPanel details {{ margin-top: 6px; }}
  #infoPanel summary {{ cursor: pointer; color: #88ccff; }}
  .cesium-infoBox {{ max-width: 360px; }}
</style>
</head>
<body>
<div id="cesiumContainer"></div>
<div id="infoPanel">
  <h2>济矿运河煤矿 — 设备定位</h2>
  <div class="stat"><span>用户</span><span>D37795450</span></div>
  <div class="stat"><span>设备总数</span><span>{data["summary"]["total"]}</span></div>
  <div class="stat"><span>匹配成功</span><span style="color:#44ff44">{data["summary"]["matched"]}</span></div>
  <div class="stat"><span>未匹配</span><span style="color:#ff4444">{data["summary"]["unmatched"]}</span></div>
  <div class="stat"><span>巷道/工作面</span><span style="color:#ffaa00">{len(tunnel_polylines)}条</span></div>
  <hr style="border-color:rgba(255,255,255,0.2);margin:10px 0;">
  <div><b>传感器类型图例</b></div>
  <div class="legend"><span class="dot" style="background:#FF4444"></span>瓦斯</div>
  <div class="legend"><span class="dot" style="background:#FF8800"></span>一氧化碳</div>
  <div class="legend"><span class="dot" style="background:#00DDFF"></span>风速</div>
  <div class="legend"><span class="dot" style="background:#FFDD00"></span>温度</div>
  <div class="legend"><span class="dot" style="background:#888888"></span>烟雾</div>
  <div class="legend"><span class="dot" style="background:#8B4513"></span>粉尘</div>
  <div class="legend"><span class="dot" style="background:#AA44FF"></span>断电/馈电/开停</div>
  <div class="legend"><span class="dot" style="background:#44FF44"></span>人员定位</div>
  <div class="legend"><span class="dot" style="background:#FFFFFF"></span>其他</div>
  <hr style="border-color:rgba(255,255,255,0.2);margin:10px 0;">
  <div><b>点大小 = 置信度（大=高，中=中，小=低）</b></div>
  <details>
    <summary>未匹配设备 ({len(unmatched_list)}个)</summary>
    <div style="max-height:200px;overflow-y:auto;font-size:11px;">
      {"".join(f'<div class="unmatched">{u["id"]}<br>{u["description"]} ({u["sensor_type"]}) — {u["reason"]}</div>' for u in unmatched_list)}
    </div>
  </details>
</div>

<script>
// 不强制 Ion token，使用默认底图
Cesium.Ion.defaultAccessToken = undefined;

const viewer = new Cesium.Viewer('cesiumContainer', {{
  terrainProvider: new Cesium.EllipsoidTerrainProvider(),
  imageryProvider: new Cesium.SingleTileImageryProvider({{
    url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
    rectangle: Cesium.Rectangle.fromDegrees(-180, -90, 180, 90)
  }}),
  baseLayerPicker: false,
  geocoder: false,
  homeButton: false,
  sceneModePicker: false,
  navigationHelpButton: false,
  animation: false,
  timeline: false,
  shouldAnimate: false,
}});

// 关闭大气层/雾效果以便清晰看点
viewer.scene.skyAtmosphere.show = false;
viewer.scene.fog.enabled = false;

const entities = {json.dumps(entities, ensure_ascii=False, indent=2)};
const tunnelPolylines = {json.dumps(tunnel_polylines, ensure_ascii=False, indent=2)};

// 添加巷道/工作面折线
const tunnelEntityMap = {{}}; // name -> entity id

tunnelPolylines.forEach((tp, idx) => {{
  const cartesians = tp.positions.map(p => Cesium.Cartesian3.fromDegrees(p.lon, p.lat, p.height));
  const ent = viewer.entities.add({{
    name: tp.name,
    polyline: {{
      positions: cartesians,
      width: tp.type === 'tunnel' ? 8 : 6,
      material: tp.type === 'tunnel'
        ? Cesium.Color.RED
        : Cesium.Color.LIME,
      clampToGround: false,
    }},
    label: {{
      text: tp.name,
      font: '11px sans-serif',
      fillColor: Cesium.Color.fromCssColorString('#cccccc'),
      outlineColor: Cesium.Color.BLACK,
      outlineWidth: 2,
      style: Cesium.LabelStyle.FILL_AND_OUTLINE,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      pixelOffset: new Cesium.Cartesian2(0, -4),
      show: false,
      scaleByDistance: new Cesium.NearFarScalar(500, 1.5, 8000, 0.5),
    }},
    description: `
      <table style="font-size:13px">
        <tr><td><b>名称</b></td><td>${{tp.name}}</td></tr>
        <tr><td><b>类型</b></td><td>${{tp.type}}</td></tr>
        <tr><td><b>折点数</b></td><td>${{tp.positions.length}}</td></tr>
      </table>
    `,
  }});
  tunnelEntityMap[tp.name] = ent;

  // 兜底：如果折线渲染失败，用密集小红点串模拟出线
  cartesians.forEach((pos, i) => {{
    if (i % 2 === 0) {{ // 隔一个点打一个，避免太密
      viewer.entities.add({{
        position: pos,
        point: {{
          pixelSize: 4,
          color: Cesium.Color.RED,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 1,
          scaleByDistance: new Cesium.NearFarScalar(1000, 1.0, 8000, 0.5),
        }},
      }});
    }}
  }});

  // 折线中点加醒目球 + 标签，兜底防止折线看不见
  if (cartesians.length > 0) {{
    const midIdx = Math.floor(cartesians.length / 2);
    viewer.entities.add({{
      position: cartesians[midIdx],
      point: {{
        pixelSize: 8,
        color: Cesium.Color.fromCssColorString('#FF0000'),
        outlineColor: Cesium.Color.YELLOW,
        outlineWidth: 2,
        scaleByDistance: new Cesium.NearFarScalar(1000, 1.5, 8000, 0.5),
      }},
      label: {{
        text: tp.name,
        font: '11px sans-serif',
        fillColor: Cesium.Color.YELLOW,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -6),
        show: true,
        scaleByDistance: new Cesium.NearFarScalar(500, 1.0, 6000, 0.4),
      }},
    }});
  }}
}});

// 添加点实体
entities.forEach(e => {{
  viewer.entities.add({{
    position: Cesium.Cartesian3.fromDegrees(e.lon, e.lat, e.height),
    point: {{
      pixelSize: e.size,
      color: Cesium.Color.fromCssColorString(e.color),
      outlineColor: Cesium.Color.WHITE,
      outlineWidth: 1,
      scaleByDistance: new Cesium.NearFarScalar(1000, 2.0, 50000, 0.5),
    }},
    label: {{
      text: e.matched_name,
      font: '12px sans-serif',
      fillColor: Cesium.Color.fromCssColorString(e.color),
      outlineColor: Cesium.Color.BLACK,
      outlineWidth: 2,
      style: Cesium.LabelStyle.FILL_AND_OUTLINE,
      verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
      pixelOffset: new Cesium.Cartesian2(0, -8),
      show: false,  // 默认隐藏，避免太乱
    }},
    description: `
      <table style="font-size:13px">
        <tr><td><b>ID</b></td><td>${{e.id}}</td></tr>
        <tr><td><b>描述</b></td><td>${{e.description}}</td></tr>
        <tr><td><b>匹配巷道/工作面</b></td><td>${{e.matched_name}} (${{e.matched_type}})</td></tr>
        <tr><td><b>传感器</b></td><td>${{e.sensor_type}}</td></tr>
        <tr><td><b>置信度</b></td><td>${{e.confidence}}</td></tr>
        <tr><td><b>坐标</b></td><td>lon=${{e.lon}}, lat=${{e.lat}}, h=${{e.height}}</td></tr>
        <tr><td><b>系统</b></td><td>${{e.sysaliasname}}</td></tr>
        <tr><td><b>mark_type</b></td><td>${{e.mark_type}}</td></tr>
      </table>
    `,
  }});
}});

// 自动飞行到数据范围
if (entities.length > 0) {{
  const lons = entities.map(e => e.lon);
  const lats = entities.map(e => e.lat);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  viewer.camera.flyTo({{
    destination: Cesium.Rectangle.fromDegrees(minLon - 0.005, minLat - 0.005, maxLon + 0.005, maxLat + 0.005),
    duration: 2,
  }});
}}

// 点击显示标签（临时高亮）
const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
handler.setInputAction(click => {{
  const picked = viewer.scene.pick(click.position);
  if (Cesium.defined(picked) && Cesium.defined(picked.id)) {{
    // 隐藏所有标签
    viewer.entities.values.forEach(ent => {{ if(ent.label) ent.label.show = false; }});
    // 显示当前标签
    picked.id.label.show = true;
  }}
}}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

console.log("加载完成，共", entities.length, "个设备点");
</script>
</body>
</html>
'''

with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
    f.write(html)

print(f"已生成: {OUTPUT_HTML}")
print(f"  实体数: {len(entities)}")
if entities:
    print(f"  范围: lon {min(e['lon'] for e in entities):.4f} ~ {max(e['lon'] for e in entities):.4f}")
    print(f"        lat {min(e['lat'] for e in entities):.4f} ~ {max(e['lat'] for e in entities):.4f}")
