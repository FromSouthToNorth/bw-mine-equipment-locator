#!/usr/bin/env python3
"""把 locator 结果转为 CesiumJS 可视化 HTML。"""

import json
import sys
import argparse
from pathlib import Path

try:
    import pyproj
except ImportError:
    _HAS_PYPROJ = False
else:
    _HAS_PYPROJ = True


def generate_html(result_json_path: str, output_html: str = None,
                  data_8373_path: str = None) -> str:
    """从 locator 结果 JSON 生成 CesiumJS 可视化 HTML。

    Args:
        result_json_path: locator_result_*.json 路径
        output_html:      输出 HTML 路径（默认与 result 同目录）
        data_8373_path:   (可选) data_8373_*.json 路径，用于显示巷道折线

    Returns:
        str: 生成的 HTML 文件路径

    Raises:
        ImportError: pyproj 未安装
    """
    if not _HAS_PYPROJ:
        raise ImportError("需要 pyproj: pip install pyproj")

    RESULT_JSON = Path(result_json_path)
    OUTPUT_HTML = Path(output_html) if output_html else (
        RESULT_JSON.parent / f"cesium_{RESULT_JSON.stem}.html")
    DATA_8373_JSON = Path(data_8373_path) if data_8373_path else None

    # ── 坐标转换 ──────────────────────────────────────────────────────
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
        "人数": "#44FF44",
        "人员定位": "#44FF44",
        "风门": "#CCCCCC",
        "电流": "#CCCCCC",
        "电源状态": "#CCCCCC",
        "负压": "#CCCCCC",
        "工业视频": "#FF66AA",
    }

    # ── 读取结果 ──────────────────────────────────────────────────────
    with open(RESULT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = data.get("results", [])
    unmatched = data.get("unmatched_devices", [])
    summary = data.get("summary", {})
    mine_name = data.get("mine_name", "")
    username = data.get("username", "")

    # ── 转换坐标 ──────────────────────────────────────────────────────
    entities = []
    sensor_type_counts = {}
    tunnel_devices = {}

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

        sensor_type_counts[sensor] = sensor_type_counts.get(sensor, 0) + 1

        matched_name = r.get("matched_name", "")
        if matched_name:
            tunnel_devices.setdefault(matched_name, 0)
            tunnel_devices[matched_name] += 1

        entities.append({
            "id": r.get("id", ""),
            "description": r.get("description", ""),
            "matched_name": matched_name,
            "matched_type": r.get("matched_type", ""),
            "sensor_type": sensor,
            "confidence": conf,
            "mark_type": r.get("mark_type", ""),
            "sysaliasname": r.get("sysaliasname", ""),
            "line_total_length": r.get("line_total_length", 0),
            "distance_along_line": r.get("distance_along_line", 0),
            "line_percentage": r.get("line_percentage", 0),
            "lon": round(lon, 6),
            "lat": round(lat, 6),
            "height": round(height, 2),
            "color": color,
            "size": size,
        })

    # 未匹配设备
    unmatched_list = [
        {
            "id": u.get("id", ""),
            "description": u.get("description", ""),
            "sensor_type": u.get("sensor_type", ""),
            "reason": u.get("reason", ""),
        }
        for u in unmatched
    ]

    # 未匹配原因聚合
    unmatched_reason_counts = {}
    for u in unmatched:
        r = u.get("reason", "UNKNOWN")
        unmatched_reason_counts[r] = unmatched_reason_counts.get(r, 0) + 1

    # ── 读取并转换巷道折线 ────────────────────────────────────────────
    tunnel_polylines = []
    if DATA_8373_JSON and DATA_8373_JSON.exists():
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
                    "type": cand.get("category", ""),
                    "positions": positions,
                })

    # ── 传感器图例 HTML ──────────────────────────────────────────────
    legend_html = ""
    sorted_sensors = sorted(sensor_type_counts.items(), key=lambda x: -x[1])
    for sensor, cnt in sorted_sensors:
        color = SENSOR_COLORS.get(sensor, "#FFFFFF")
        legend_html += (
            f'<div class="legend"><span class="dot" style="background:{color}"></span>'
            f"{sensor} ({cnt})</div>\n"
        )

    # ── 置信度分布条 ──────────────────────────────────────────────────
    bc = summary.get("by_confidence", {})
    high = bc.get("高", 0)
    med = bc.get("中", 0)
    low = bc.get("低", 0)
    total_conf = high + med + low or 1
    high_pct = high / total_conf * 100
    med_pct = med / total_conf * 100
    low_pct = low / total_conf * 100
    conf_bar = (
        f'<div style="display:flex;height:16px;border-radius:4px;overflow:hidden;margin:6px 0;">'
        f'<div style="flex:{high_pct:.1f};background:#44ff44;text-align:center;font-size:10px;line-height:16px;">'
        f'{"高" + str(high) if high > 0 else ""}</div>'
        f'<div style="flex:{med_pct:.1f};background:#ffaa00;text-align:center;font-size:10px;line-height:16px;">'
        f'{"中" + str(med) if med > 0 else ""}</div>'
        f'<div style="flex:{low_pct:.1f};background:#ff6666;text-align:center;font-size:10px;line-height:16px;">'
        f'{"低" + str(low) if low > 0 else ""}</div></div>'
    )

    # ── 未匹配原因 HTML ──────────────────────────────────────────────
    unmatched_reason_html = ""
    for reason, cnt in sorted(unmatched_reason_counts.items(), key=lambda x: -x[1]):
        color_map = {
            "AREA_SURFACE": "#888",
            "LOW_LCS": "#ff6666",
            "NO_CANDIDATE": "#ff4444",
            "CODE_MISMATCH": "#ff8800",
            "SEMANTIC_CONFLICT": "#ffaa00",
        }
        rc = color_map.get(reason, "#fff")
        unmatched_reason_html += (
            f'<div style="display:flex;justify-content:space-between;margin:2px 0;font-size:12px;">'
            f'<span style="color:{rc}">{reason}</span><span>{cnt}</span></div>'
        )

    # ── 生成 HTML ─────────────────────────────────────────────────────
    title = f"{mine_name} — 设备定位" if mine_name else "煤矿设备定位可视化"

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<script src="https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Cesium.js"></script>
<link href="https://cesium.com/downloads/cesiumjs/releases/1.118/Build/Cesium/Widgets/widgets.css" rel="stylesheet">
<style>
  html, body, #cesiumContainer {{ width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; font-family: "Microsoft YaHei", sans-serif; }}
  #infoPanel {{
    position: absolute; top: 10px; left: 10px;
    background: rgba(0,0,0,0.78); color: #fff;
    padding: 14px; border-radius: 8px; max-width: 340px;
    font-size: 13px; line-height: 1.6; pointer-events: auto;
    max-height: 90vh; overflow-y: auto;
  }}
  #infoPanel h2 {{ margin: 0 0 8px; font-size: 15px; }}
  #infoPanel .stat {{ display: flex; justify-content: space-between; margin: 2px 0; }}
  #infoPanel .legend {{ display: flex; align-items: center; margin: 3px 0; }}
  #infoPanel .dot {{ width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; display: inline-block; flex-shrink: 0; }}
  #infoPanel .unmatched {{ color: #ff6666; margin-top: 8px; font-size: 11px; }}
  #infoPanel details {{ margin-top: 6px; }}
  #infoPanel summary {{ cursor: pointer; color: #88ccff; }}
  #infoPanel .table-wrap {{ max-height: 200px; overflow-y: auto; font-size: 11px; margin-top: 6px; }}
  #infoPanel table {{ width: 100%; border-collapse: collapse; }}
  #infoPanel td {{ padding: 2px 4px; border-bottom: 1px solid rgba(255,255,255,0.1); }}
  #infoPanel .filter-bar {{ display: flex; gap: 4px; flex-wrap: wrap; margin: 4px 0; }}
  #infoPanel .filter-btn {{ padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.3); background: transparent; color: #ccc; cursor: pointer; font-size: 11px; }}
  #infoPanel .filter-btn.active {{ background: rgba(255,255,255,0.2); color: #fff; border-color: #88ccff; }}
  .cesium-infoBox {{ max-width: 360px; }}
  #labelToggle {{ position: absolute; top: 10px; right: 10px; z-index: 100; padding: 6px 12px; background: rgba(0,0,0,0.7); color: #fff; border: 1px solid rgba(255,255,255,0.3); border-radius: 4px; cursor: pointer; font-size: 12px; }}
  #labelToggle:hover {{ background: rgba(0,0,0,0.9); }}
</style>
</head>
<body>
<div id="cesiumContainer"></div>
<button id="labelToggle">显示巷道名</button>
<div id="infoPanel">
  <h2>{title}</h2>
  <div class="stat"><span>用户</span><span>{username}</span></div>
  <div class="stat"><span>设备总数</span><span>{summary.get("total", 0)}</span></div>
  <div class="stat"><span>匹配成功</span><span style="color:#44ff44">{summary.get("matched", 0)}</span></div>
  <div class="stat"><span>未匹配</span><span style="color:#ff4444">{summary.get("unmatched", 0)}</span></div>
  <div class="stat"><span>巷道/工作面</span><span style="color:#ffaa00">{len(tunnel_polylines)}条</span></div>
  <hr style="border-color:rgba(255,255,255,0.2);margin:8px 0;">
  <div><b>置信度分布</b></div>
  {conf_bar}
  <hr style="border-color:rgba(255,255,255,0.2);margin:8px 0;">
  <div><b>传感器图例</b></div>
  {legend_html}
  <div style="margin-top:4px;font-size:11px;color:#aaa;">点大小: 大=高, 中=中, 小=低</div>
  <hr style="border-color:rgba(255,255,255,0.2);margin:8px 0;">
  <div><b>设备列表</b></div>
  <div class="filter-bar" id="filterBar">
    <button class="filter-btn active" data-filter="all">全部</button>
    <button class="filter-btn" data-filter="high">高置信</button>
    <button class="filter-btn" data-filter="mid">中置信</button>
    <button class="filter-btn" data-filter="low">低置信</button>
  </div>
  <div class="table-wrap" id="deviceTable"></div>
  <hr style="border-color:rgba(255,255,255,0.2);margin:8px 0;">
  <details>
    <summary>未匹配设备 ({len(unmatched_list)}个)</summary>
    {unmatched_reason_html}
    <div style="max-height:200px;overflow-y:auto;font-size:11px;margin-top:4px;">
      {"".join(f'<div class="unmatched">{u["id"]}<br>{u["description"]} ({u["sensor_type"]}) — {u["reason"]}</div>' for u in unmatched_list)}
    </div>
  </details>
</div>

<script>
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

viewer.scene.skyAtmosphere.show = false;
viewer.scene.fog.enabled = false;

const entities = {json.dumps(entities, ensure_ascii=False, indent=2)};
const tunnelPolylines = {json.dumps(tunnel_polylines, ensure_ascii=False, indent=2)};
let labelsVisible = false;

const labelToggle = document.getElementById('labelToggle');

// 添加巷道/工作面折线
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

  // 折线中点加标签兜底
  if (cartesians.length > 0) {{
    const midIdx = Math.floor(cartesians.length / 2);
    viewer.entities.add({{
      position: cartesians[midIdx],
      point: {{
        pixelSize: 6,
        color: Cesium.Color.fromCssColorString(tp.type === 'tunnel' ? '#FF0000' : '#00FF00'),
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
      show: false,
    }},
    description: `
      <table style="font-size:13px">
        <tr><td><b>ID</b></td><td>${{e.id}}</td></tr>
        <tr><td><b>描述</b></td><td>${{e.description}}</td></tr>
        <tr><td><b>匹配巷道/工作面</b></td><td>${{e.matched_name}} (${{e.matched_type}})</td></tr>
        <tr><td><b>传感器</b></td><td>${{e.sensor_type}}</td></tr>
        <tr><td><b>置信度</b></td><td>${{e.confidence}}</td></tr>
        <tr><td><b>沿巷道 %</b></td><td>${{e.line_percentage}}% (距离起点 ${{e.distance_along_line}}m / 总长 ${{e.line_total_length}}m)</td></tr>
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
    viewer.entities.values.forEach(ent => {{ if(ent.label) ent.label.show = false; }});
    picked.id.label.show = true;
  }}
}}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

// 巷道标签切换
labelToggle.addEventListener('click', () => {{
  labelsVisible = !labelsVisible;
  viewer.entities.values.forEach(ent => {{
    if (ent.label) {{
      // 只切换巷道标签 (中点兜底标签 show=true 的)
      if (ent.name) {{ ent.label.show = labelsVisible; }}
    }}
  }});
  labelToggle.textContent = labelsVisible ? '隐藏巷道名' : '显示巷道名';
}});

// ── 设备表格（可筛选） ──
function renderDeviceTable(filter) {{
  let filtered = entities;
  if (filter === 'high') filtered = entities.filter(e => e.confidence === '高');
  else if (filter === 'mid') filtered = entities.filter(e => e.confidence === '中');
  else if (filter === 'low') filtered = entities.filter(e => e.confidence === '低');

  const rows = filtered.map(e => {{
    const confColor = e.confidence === '高' ? '#44ff44' : (e.confidence === '中' ? '#ffaa00' : '#ff6666');
    return `<tr>
      <td style="color:${{e.color}};font-weight:bold">${{e.sensor_type}}</td>
      <td style="color:${{confColor}}">${{e.confidence}}</td>
      <td>${{e.matched_name}}</td>
      <td>${{e.mark_type}}</td>
    </tr>`;
  }}).join('');

  document.getElementById('deviceTable').innerHTML =
    `<table><tr style="color:#aaa"><td>传感器</td><td>置信度</td><td>匹配巷道</td><td>类型</td></tr>${{rows}}</table>`;
}}

renderDeviceTable('all');

// 筛选按钮
document.querySelectorAll('.filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderDeviceTable(btn.dataset.filter);
  }});
}});

console.log("加载完成，共", entities.length, "个设备点");
</script>
</body>
</html>'''

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"已生成: {OUTPUT_HTML}", file=sys.stderr)
    print(f"  实体数: {len(entities)}", file=sys.stderr)
    if entities:
        lons = [e['lon'] for e in entities]
        lats = [e['lat'] for e in entities]
        print(f"  范围: lon {min(lons):.4f} ~ {max(lons):.4f}", file=sys.stderr)
        print(f"        lat {min(lats):.4f} ~ {max(lats):.4f}", file=sys.stderr)

    return str(OUTPUT_HTML)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从 locator 结果生成 CesiumJS 可视化 HTML")
    parser.add_argument("result_json", help="locator_result_*.json 路径")
    parser.add_argument("--output", "-o", help="输出 HTML 路径")
    parser.add_argument("--data-8373", help="data_8373_*.json 路径（显示巷道路线）")
    args = parser.parse_args()
    path = generate_html(args.result_json, args.output, args.data_8373)
    print(f"已生成: {path}")
