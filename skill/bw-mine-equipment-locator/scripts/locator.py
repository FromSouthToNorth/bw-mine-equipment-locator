#!/usr/bin/env python3
"""
煤矿设备定位 — 根据设备描述匹配巷道/工作面，计算 (x, y, z) 坐标

Usage:
    python3 locator.py <username>

Workflow:
    1. 调用 bw-token-manager 获取 token 和 mineName
    2. 调用策略 8373 获取设备、巷道、工作面数据
    3. 匹配设备描述到巷道/工作面 → 计算坐标
"""

import functools
import sys
import os
import re
import json
import shutil
import subprocess
import argparse
from datetime import datetime
from pathlib import Path


# ── 路径 ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # F:\gis\Point
TOKEN_MANAGER = PROJECT_ROOT / "skill" / "bw-token-manager" / "scripts" / "bw_token_manager.py"
STRATEGY_API = PROJECT_ROOT / "skill" / "bw-strategy-api-caller" / "scripts" / "strategy_api.py"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
MATCH_CACHE_PATH = CACHE_DIR / "match_cache.json"

# ── 解释器探测 ────────────────────────────────────────────────────
@functools.lru_cache(maxsize=1)
def _resolve_python_exe() -> str:
    """按优先级探测能 `import requests` 的 Python，找到就用。"""
    candidates = []
    env_override = os.environ.get("BW_LOCATOR_PYTHON")
    if env_override:
        candidates.append(env_override)
    candidates.append(sys.executable)                # 当前解释器优先
    for name in ("python3", "python", "py"):         # PATH 上的常见命令
        which = shutil.which(name)
        if which:
            candidates.append(which)
    # Windows 历史约定路径作兜底（不存在不报错）
    candidates.append(r"C:\Users\bw\AppData\Local\Programs\Python\Python310\python.exe")

    seen, errors = set(), []
    for exe in candidates:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        if not Path(exe).exists() and not shutil.which(exe):
            continue
        try:
            r = subprocess.run([exe, "-c", "import requests"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                return exe
            stderr_tail = r.stderr.decode(errors="ignore").strip().splitlines()[-1:] or [""]
            errors.append(f"{exe}: {stderr_tail[0][:100]}")
        except Exception as e:
            errors.append(f"{exe}: {e}")

    raise RuntimeError(
        "未找到带 requests 的 Python 解释器。请：\n"
        "  1) pip install requests，或\n"
        "  2) 设置环境变量 BW_LOCATOR_PYTHON=<python.exe 绝对路径>\n"
        "尝试过：\n  " + "\n  ".join(errors)
    )

_PYTHON_EXE = _resolve_python_exe()


# ── API 调用 ──────────────────────────────────────────────────────
def get_token_and_mine_name(username: str) -> tuple:
    """调用 bw-token-manager 获取 token 和 mineName。"""
    result = subprocess.run(
        [_PYTHON_EXE, str(TOKEN_MANAGER), username, "--output", "json"],
        capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    if result.returncode != 0:
        raise RuntimeError(f"Token manager failed: {result.stderr.strip()}")
    tokens = json.loads(result.stdout.strip())
    mine_name = tokens.get("mineName", "")
    if not tokens.get("bw_token"):
        raise RuntimeError("bw_token not found")
    if not mine_name:
        raise RuntimeError("mineName not found")
    return tokens, mine_name


def call_strategy_api(strategy_id: int, username: str, param: str = None,
                      action: str = "get_data", param_file: str = None) -> dict:
    """调用 strategy_api.py，返回完整响应 dict。"""
    cmd = [
        _PYTHON_EXE, str(STRATEGY_API), action,
        "--id", str(strategy_id),
        "--username", username,
        "--output", "json",
    ]
    if param:
        cmd.extend(["--param", param])
    if param_file:
        cmd.extend(["--param-from-file", param_file])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"Strategy API (id={strategy_id}) failed: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def extract_items(raw_data):
    """
    从 API data 字段提取实际数据。
    处理各种嵌套格式：
    - 扁平列表 → 直接返回
    - [{"result_json": "JSON string"}] → 解析 JSON 字符串
    - [{"": "JSON string"}] → 解析 JSON 字符串（旧格式）
    - dict → 直接返回（调用方按字段处理）
    """
    if isinstance(raw_data, list):
        if not raw_data:
            return []
        if len(raw_data) == 1 and isinstance(raw_data[0], dict):
            for v in raw_data[0].values():
                if isinstance(v, str) and v.strip():
                    stripped = v.strip()
                    if stripped.startswith("[") or stripped.startswith("{"):
                        try:
                            return json.loads(stripped)
                        except json.JSONDecodeError:
                            pass
        return raw_data
    if isinstance(raw_data, dict):
        return raw_data
    return []


def classify_items(items: list) -> tuple:
    """
    从扁平数组分类出 devices 和 candidates。
    适用于 get_json 返回格式（设备/巷道/工作面混排的平铺数组）。

    判断依据：
    - 有 description 字段 → device
    - 有 name 字段且无 workFaceName → tunnel candidate
    - 有 workFaceName 字段 → workface candidate
    """
    devices = []
    candidates = []
    for item in items:
        if "description" in item:
            devices.append(item)
        elif "workFaceName" in item:
            candidates.append({
                "name": item["workFaceName"],
                "type": "workface",
                "line": item.get("line", []),
                "id": item.get("id", ""),
                "tunnelId": item.get("tunnelId", ""),
            })
        elif "name" in item and "workFaceName" not in item:
            candidates.append({
                "name": item["name"],
                "type": "tunnel",
                "line": item.get("line", []),
            })
    return devices, candidates


# ── 匹配逻辑 ──────────────────────────────────────────────────────
PREFIX_PATTERNS = [
    r"^\d+号分站(?:模拟量|开关量|多态量)[A-Za-z0-9_]+",
    r"^其他\d+[A-Za-z0-9]*",
]


def strip_prefix(description: str) -> str:
    for pattern in PREFIX_PATTERNS:
        cleaned = re.sub(pattern, "", description)
        if cleaned != description:
            return cleaned
    return description


def _infer_sensor_type(description: str, mark_type: str = None) -> str:
    """从 description 关键词推断传感器/设备类型。mark_type 与 sensor_type 是完全不同的概念，不要混淆。

    `mark_type` 是系统大类：B14=安全监测、B15=人员定位、B16=工业视频。
    当描述无明确关键词、但 mark_type=B16 时，默认返回"工业视频"——
    依据 MT/T 1201.6-2023 附录 A：B16 视频系统的设备类型即"工业视频"。
    """
    d = description
    if "二氧化碳" in d or "CO2" in d:
        return "二氧化碳"
    if "氧气" in d or re.search(r'\bO2\b', d):
        return "氧气"
    if "负压" in d or "风压" in d:
        return "负压"
    if "风速" in d:
        return "风速"
    if "烟雾" in d:
        return "烟雾"
    if "粉尘" in d:
        return "粉尘"
    if "温度" in d:
        return "温度"
    if re.search(r'一氧化碳|CO[^2]', d):
        return "一氧化碳"
    if "甲烷" in d or "瓦斯" in d or "CH4" in d:
        return "瓦斯"
    if "开停" in d:
        return "开停"
    if "馈电" in d:
        return "馈电"
    if "断电" in d:
        return "断电"
    if "人数" in d or "人员" in d:
        return "人员定位"
    # B16 设备类型
    if any(kw in d for kw in ["工业视频", "摄像头", "视频监控", "视频监测"]):
        return "工业视频"
    # mark_type=B16 兜底
    if mark_type == "B16":
    if mark_type == "B16":
        return "工业视频"
    return None


# ── sensor_type 巷道偏好 ──────────────────────────────────────────
_SENSOR_TUNNEL_PREF = {
    "瓦斯": ["回风巷", "进风巷", "切巷", "工作面", "顺槽", "石门", "大巷", "采空", "排瓦斯", "高冒"],
    "一氧化碳": ["隅角", "皮带", "硐室", "石门", "滚筒", "采空", "封闭火区", "采煤工作面"],
    "风速": ["测风站", "总回风", "回风巷", "一翼回风", "采区回风", "盘区回风"],
    "温度": ["硐室", "压风机", "工作面", "机电", "中央变电", "采区变电"],
    "烟雾": ["皮带", "运输", "机头", "机尾", "滚筒", "胶带", "胶运"],
    "粉尘": ["采煤", "掘进", "转载", "破碎", "装煤", "综采", "综掘", "回采"],
    "馈电": ["配电", "变电", "开关", "馈电"],
    "断电": ["配电", "变电", "开关", "馈电"],
    "开停": ["配电", "变电", "开关", "风机"],
    "人员定位": [
        # MT/T 1198-2023 §5.1.2-5: 出入井口/交叉巷口/采区分区分流路口/采区采面出入口/充电站/副井/运输斜井
        "井口", "井底", "交叉口", "岔口", "分流", "联络巷",
        "大巷", "入口", "工作面", "采区", "采面",
        "运输巷", "回风巷", "进风巷", "副井", "运输斜井",
        "充电站",
        # MT/T 1198-2023 §5.2.3-5 + AQ 1119-2023 §3.10/3.13: 重点/准入/限制区域
        "硐室", "变电所", "水泵房", "重点", "准入", "限制",
    ],
    "氧气": ["工作面", "硐室", "采空"],
    "二氧化碳": ["采空", "封闭火区", "回风巷"],
    "负压": ["风机", "通风机", "风筒"],
    # B16 工业视频 — MT/T 1201.6-2023 附录 A.1（井工煤矿 54 处）
    # 注意：sensor_type 是设备类型（工业视频），不是厂商名（海康/大华/宇视）。
    "工业视频": [
        # 工作面/巷道
        "工作面", "顺槽", "运输巷", "回风巷", "进风巷", "大巷", "斜巷",
        "支架", "超前支护", "迎头",          # A.1#1,2,3,10
        # 输送/转载
        "皮带", "输送机", "转载点", "机头", "机尾",  # A.1#6,7,8,12-16,18
        # 硐室/房间
        "硐室", "变电所", "水泵房", "泵房", "绞车房", "调度", "提升", "通风",
        "空压", "瓦斯泵", "制氮", "灌浆", "避难",  # A.1#21,25-29,37-39,45-49
        # 进出口/车场
        "井口", "井底", "车场", "煤仓", "乘车", "副立井", "罐笼",  # A.1#20,22,35,36,42-44,54
        # 地面
        "工业广场", "煤场", "坑木场",  # A.1#51-53
    ],
}


# ── mark_type → 子系统大类映射 ───────────────────────────────────
# 注意：mark_type 是系统大类（B14=安全监测、B15=人员定位、B16=工业视频），
# 与 sensor_type（瓦斯/风速/烟雾/温度等具体传感器类型）是完全不同的概念。
_MARK_TYPE_TO_SYSTEM = {
    "B14": "安全监测系统",
    "B15": "人员定位系统",
    "B16": "工业视频系统",
}


# ── 巷道别名映射 ──────────────────────────────────────────────────
_TUNNEL_ALIAS_MAP = {
    # 核心缩写 -> 全称列表（只放单向映射，避免循环替换）
    "皮顺": ["皮带顺槽", "辅运顺槽", "胶带顺槽"],
    "胶顺": ["胶带顺槽", "皮带顺槽"],
    "辅顺": ["辅运顺槽", "皮带顺槽"],
    "运顺": ["运输顺槽", "胶带顺槽"],
    "回顺": ["回风顺槽", "回风巷"],
    "进顺": ["进风顺槽", "进风巷"],
    "胶运": ["胶带运输", "进风巷", "胶运顺槽"],
    "东大": ["东部", "东翼"],
    "西大": ["西部", "西翼"],
    "南大": ["南部", "南翼"],
    "北大": ["北部", "北翼"],
    "切巷": ["切眼"],
    "联络巷": ["联巷"],
    "石门": ["门"],
    "硐室": ["硐"],
    "工作面": ["面"],
    "回风": ["回风巷"],
    "进风": ["进风巷"],
}


def _expand_aliases(text: str) -> str:
    """将 text 中的巷道别名扩展为 别名|全称1|全称2 形式，以提升 LCS 匹配覆盖率。
    使用占位符避免递归替换问题。"""
    if not text:
        return text
    # 按长度降序处理，避免短别名破坏长别名
    items = sorted(_TUNNEL_ALIAS_MAP.items(), key=lambda x: len(x[0]), reverse=True)
    placeholders = []
    expanded = text
    for i, (abbr, full_forms) in enumerate(items):
        if abbr in expanded:
            ph = f"\x00{i}\x00"
            replacement = "|".join([abbr] + full_forms)
            expanded = expanded.replace(abbr, ph)
            placeholders.append((ph, replacement))
    # 还原占位符
    for ph, replacement in placeholders:
        expanded = expanded.replace(ph, replacement)
    return expanded


def _candidate_matches_sensor_pref(name: str, sensor_type: str) -> bool:
    prefs = _SENSOR_TUNNEL_PREF.get(sensor_type, [])
    return any(p in name for p in prefs)


# ── T 标识位置规则 (AQ 1029-2019) ─────────────────────────────────
_T_POSITION_RULES = {
    "T0": (0.00, 0.05),    # 上隅角/回风隅角，采煤工作面回风端（6.2.1 图1）
    "T1": (0.00, 0.05),    # 掘进迎头 → 距起点 ≤5m（6.3.1 图3）；采煤回风巷距工作面≤10m（6.2.1）
    "T2": (0.85, 1.00),    # 采煤进风巷距工作面≤10m（6.2.1 突出矿井）；掘进进风风门口（6.3.1）
    "T3": (0.30, 0.50),    # 混合风流处 → 风机附近（6.3.1）
    "T4": (0.90, 1.00),    # 掘进回风巷口（6.3.1）
}


# ── area 语义分类（地面/井下）────────────────────────────────────
# area 字段标记设备所属区域，地面设备不应匹配井下巷道/工作面。
# 匹配前判断 area 语义，地面设备直接跳过所有候选。
_AREA_SURFACE_PATTERNS = [
    "地面",     # 地面、地面机房硐室等
    "洗选",     # 洗选中心、洗选中心化验室
    "洗煤",     # 洗煤厂…
    "销售",     # 销售磅房、销售装车视频
    "磅房",     # 空磅房卡口
    "风机房",   # 地面风机房
    "材料大库房",
    "炸药库",
    "计算机资源",
    "档案室",
    "队组楼",
    "设备废料",
    "井上",
]


def _is_surface_area(area: str) -> bool:
    """根据 area 字段判断设备是否属于地面（非井下），
    地面设备不应匹配井下巷道/工作面候选。"""
    if not area:
        return False
    for pattern in _AREA_SURFACE_PATTERNS:
        if pattern in area:
            return True
    return False


# ── 地点类型语义过滤 ──────────────────────────────────────────────
_LOCATION_SEMANTICS = {
    "洗煤厂": {"allow": ["洗煤厂"], "penalty": -10},
    "中央变电所": {"allow": ["变电", "配电"], "penalty": -10},
    "避难硐室": {"allow": ["硐室"], "penalty": -10},
    "井口": {"allow": ["井口", "井筒", "副井", "主井"], "penalty": -10},
    "地面": {"allow": ["地面", "洗煤厂", "空压机房"], "penalty": -10},
}


# ── 巷道类型 × sensor_type 坐标规则 (AQ 1029-2019) ─────────────────
_TUNNEL_TYPE_RULES = {
    "26-工作面回风巷(辅运顺槽)": {
        # 6.2.1: 采煤工作面回风巷距工作面≤10m；6.4.1: 采区/一翼/总回风巷测风站设风速
        "瓦斯": {"from": "end", "meters": 10, "tolerance": 3},      # 距工作面≤10m
        "风速": {"from": "mid", "meters": 0, "station": True},     # 测风站(7.2.1)
        "一氧化碳": {"from": "end", "meters": 10, "tolerance": 3}, # 回风巷(7.1.2)
        "工业视频": {"from": "end", "meters": 17, "tolerance": 5},   # MT/T 1201.6 A.1#4,11: 回风口外15-20m
    },
    "27-工作面进风巷(胶运顺槽)": {
        "风速": {"from": "mid", "meters": 0, "station": True},     # 测风站(7.2.1)
        "烟雾": {"from": "start", "meters": 3, "tolerance": 1},   # 皮带机头(7.6)
        "粉尘": {"from": "start", "meters": 3, "tolerance": 1},   # 产尘点(7.8)
        "工业视频": {"from": "start", "meters": 12, "tolerance": 3}, # MT/T 1201.6 A.1#3: 进风超前支护距煤壁10-15m
    },
    "28-工作面切眼": {
        "瓦斯": {"from": "start", "meters": 5, "tolerance": 2},   # 距迎头≤5m(6.3.1 图3)
        "一氧化碳": {"from": "start", "meters": 5, "tolerance": 2},# 上隅角(7.1.2)
        "工业视频": {"from": "mid", "meters": 0},                    # MT/T 1201.6 A.1#1: 支架沿切眼均匀分布
    },
    "3-煤仓": {
        "瓦斯": {"from": "start", "meters": 2, "tolerance": 1},   # 煤仓上口(6.4.3)
    },
    "25-工作面停采线": {
        "瓦斯": {"from": "mid", "meters": 0},                      # 工作面中部
    },
    "29-回采工作面巷道": {
        "瓦斯": {"from": "mid", "meters": 0},                      # 工作面中部
        "粉尘": {"from": "start", "meters": 5, "tolerance": 2},   # 采煤机产尘点(7.8)
        "工业视频": {"from": "start", "meters": 12, "tolerance": 3}, # MT/T 1201.6 A.1#2: 回风超前支护距煤壁10-15m
    },
}


# ── 巷道类型匹配加分 ──────────────────────────────────────────────
_TUNNEL_TYPE_MATCH_BONUS = {
    "煤仓": ("3-煤仓", 3),
    "切眼": ("28-工作面切眼", 3),
    "回风": ("26-工作面回风巷(辅运顺槽)", 2),
    "进风": ("27-工作面进风巷(胶运顺槽)", 2),
    "停采": ("25-工作面停采线", 2),
    "硐室": ("0-普通巷道", 1),  # 硐室多为普通巷道，弱加分
}


# ── 传感器安装高度 (相对于巷道底板) ──────────────────────────────
# AQ 1029-2019 对传感器距底板/顶板高度有要求，折线 z 为基础高程
_SENSOR_INSTALL_HEIGHT = {
    "瓦斯":   0.3,   # 距顶板(顶梁)≤300mm，距侧壁≥200mm（6.1.1）
    "风速":   0.2,   # 距顶板 ≤0.3m，风速传感器较矮
    "烟雾":   0.2,   # 距顶板 ≤0.3m
    "粉尘":   1.5,   # 距底板 1.5-2m
    "一氧化碳": 0.2,  # 距顶板(顶梁)≤300mm，距侧壁≥200mm（7.1.1）
    "温度":   0.2,   # 距顶板(顶梁)≤300mm，距侧壁≥200mm（7.7.1）
    "氧气":   0.2,   # 距顶板 ≤0.3m，O2 与空气近，挂顶
    "二氧化碳": 0.5, # CO2 重于空气，距底板 0.3-1.5m
    "负压":   0.0,   # 风压表，贴风筒/风机出口
    "人员定位": 0.3,   # DB51T1412-2011 5.2.3: 读卡器靠近顶板及帮侧 300mm；分站距底板 ≥300mm。AQ 1119-2023 §5.1.3 + MT/T 1198-2023 §5.1.6 工艺约束：远离人员碰触位置、固定支撑良好
    "工业视频": 1.8,    # MT/T 1201.6-2023 §4.2 + 附录 A：以人眼高度近似（巷道/硐室通用）
}


# ── AQ 1029-2019 精确距离规则 (米) ────────────────────────────────
_AQ1029_DISTANCE_RULES = [
    # (keyword, sensor_type, distance_from, meters)
    ("T1", None, "start", 5),       # 掘进迎头 ≤5m（6.3.1 图3）
    ("T2", None, "end", 12),        # 掘进进风风门口（6.3.1）/ 回风流末端
    ("风速", None, "mid", 0),       # 测风站，前后10m无分支（7.2.1）
    ("烟雾", None, "start", 3),     # 通用场景；皮带机头/滚筒下风侧10-15m见7.6（待按description关键词细分）
    ("粉尘", None, "start", 3),     # 产尘点（7.8）
    ("温度", "硐室", "mid", 0),     # 机电硐室温度（7.7.3）
]


# ── 风速传感器最小间距 (AQ 1029-2019 7.2.1: 测风站前后10m无分支) ───
_WIND_SPEED_MIN_SPACING = 10.0  # 米


# ── 分层匹配层级 ──────────────────────────────────────────────────
_MATCH_LAYER_EXACT = 1      # 编码精确命中（最高置信度）
_MATCH_LAYER_LCS_PREF = 2   # LCS + 偏好命中（良好置信度）
_MATCH_LAYER_LOW = 3       # 低分（标记人工复核）
_MATCH_LAYER_REJECT = 4     # 拒绝


# ── 未匹配详细原因 ────────────────────────────────────────────────
REJECT_NO_CANDIDATE = "NO_CANDIDATE"           # 无可行候选
REJECT_CODE_MISMATCH = "CODE_MISMATCH"         # 编码存在但候选中无匹配
REJECT_SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"  # 语义惩罚阻断所有候选
REJECT_LOW_LCS = "LOW_LCS"                     # LCS 得分过低 (< 2)
REJECT_PREFIX_MISMATCH = "PREFIX_MISMATCH"     # 前缀模糊匹配失败
REJECT_AREA_SURFACE = "AREA_SURFACE"           # area 语义为地面，不匹配井下候选


def _extract_t_keyword(description: str) -> str:
    """从描述中提取 T 标识，如 T1/T2/T22。"""
    m = re.search(r'T(\d+)', description)
    if m:
        return f"T{m.group(1)}"
    return None


# ── 中文数字映射 ──────────────────────────────────────────────────
_CN_NUMERALS = {
    '零': '0', '一': '1', '二': '2', '三': '3', '四': '4',
    '五': '5', '六': '6', '七': '7', '八': '8', '九': '9',
    '十': '10', '〇': '0',
    '壹': '1', '贰': '2', '叁': '3', '肆': '4', '伍': '5',
    '陆': '6', '柒': '7', '捌': '8', '玖': '9',
}


def extract_workface_code(description: str) -> str:
    """提取工作面/地点编码，如 C8302、9209、F1302、-490、-725，支持中文数字（九采区→9）。"""
    # 0. 中文数字 + 采区/煤层/盘区/水平 模式
    for cn_char, digit in _CN_NUMERALS.items():
        pattern = re.escape(cn_char) + r'(采区|煤层|盘区|水平)'
        if re.search(pattern, description):
            return digit
    # 1. 优先匹配字母+数字格式（如 C8302、F1302）
    m = re.search(r'([A-Z]\d{3,4})', description)
    if m:
        return m.group(1)
    # 2. 匹配 -490、-725 等水平标高
    m = re.search(r'(-\d{3,4})', description)
    if m:
        return m.group(1)
    # 3. 匹配纯数字工作面编号（4位数字如 5318、9209）
    m = re.search(r'(?<![A-Z])(\d{4})(?![A-Z])', description)
    if m:
        return m.group(1)
    # 4. 匹配3位纯数字编号（如 920、518）
    m = re.search(r'(?<![A-Z])(\d{3})(?![A-Z])', description)
    if m:
        return m.group(1)
    return None


def extract_explicit_distance(description: str) -> float or None:
    """从描述中提取显式距离（米），如 '2730米' → 2730.0, '10米_人数' → 10.0。
    用于精确位置计算，优先级高于所有分类规则。
    对方向偏移型（'东80米'）仍提取距离，但标注为方向型需要二次处理。
    """
    # 匹配 'NN米' 模式，距离数字 ≥2 位（避免匹配楼层号如 "10米2"中 "10米"）
    m = re.search(r'(\d{2,})\s*米', description)
    if m:
        return float(m.group(1))
    # 匹配 'N米_'/'N米)' 等 1 位数字但当距离用（如 '口5米_', '10米_', '15米处', '20米)'）
    m = re.search(r'(\d+)\s*米[_\)处]', description)
    if m:
        return float(m.group(1))
    return None


def _semantic_penalty(description: str, candidate_name: str, mark_type: str = None) -> int:
    """语义惩罚：若描述含某地点关键词但候选不匹配允许列表，返回惩罚值。
    B16/B15 设备放宽部分惩罚，允许匹配到近似候选（粗略坐标）。"""
    # B16 视频设备：地表设施允许匹配到含相关关键字的候选
    if mark_type == "B16":
        if "地面" in description:
            if any(kw in candidate_name for kw in ["井", "房", "场", "库", "间", "通路", "车场"]):
                return 0
        if any(kw in description for kw in ["主井", "副井", "风井"]):
            if "井" in candidate_name:
                return 0
    # B15 人员定位：井口允许匹配到立井；硐室允许匹配到避难相关
    if mark_type == "B15":
        if "井口" in description:
            if "井" in candidate_name:
                return 0
        if "避难硐室" in description:
            if any(kw in candidate_name for kw in ["避难", "硐"]):
                return 0
    # 原有规则
    for keyword, rule in _LOCATION_SEMANTICS.items():
        if keyword in description:
            if not any(allow in candidate_name for allow in rule["allow"]):
                return rule["penalty"]
    return 0


def _apply_keyword_offset(description: str, keyword: str, explicit_dist: float, total_len: float) -> float:
    """当 keyword 有语义区间且描述含方向+距离时，从区间基准偏移而非从起点算。
    如"旧1501轨道斜巷第一变电所外西60米"→keyword=硐室(50%)，偏移-60m。"""
    zone_centers = {
        "硐室": 0.5, "充电站": 0.5,
        "井口": 0.05, "井底": 0.95, "岔口": 0.15,
    }
    if keyword not in zone_centers or total_len <= 0:
        return explicit_dist
    base_pos = total_len * zone_centers[keyword]
    m = re.search(r'[外内]?([东西南北])\s*(\d+)\s*米', description)
    if not m:
        return explicit_dist
    direction, dist_str = m.groups()
    factor = {"东": 1, "西": -1, "南": 1, "北": -1}.get(direction, 1)
    final_pos = base_pos + factor * float(dist_str)
    return max(0.0, min(total_len, final_pos))


def longest_common_substring_len(s1: str, s2: str) -> int:
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    max_len = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > max_len:
                    max_len = dp[i][j]
            else:
                dp[i][j] = 0
    return max_len


def find_best_match(cleaned: str, candidates: list, sensor_type: str = None,
                     device_code: str = None, code_to_candidates: dict = None,
                     prefix_to_candidates: dict = None,
                     coalbed_map: dict = None, mark_type: str = None) -> dict:
    """
    在 candidates 中找最佳匹配项（分层策略）。
    评分规则：LCS(别名扩展后) + sensor_type 加权 + 编码匹配(含前缀模糊) + 巷道类型匹配 + 语义过滤 + coalbed 验证。
    返回 {"name": ..., "lcs": ..., "score": ..., "candidate": ..., "layer": ...} 或 None。
    """
    best = None
    best_score = 0
    code_indices = set(code_to_candidates.get(device_code, [])) if code_to_candidates and device_code else set()
    prefix_indices = set()
    if prefix_to_candidates and device_code:
        prefix_indices = set(prefix_to_candidates.get(device_code, []))
    device_coalbed = coalbed_map.get(device_code, "") if coalbed_map and device_code else ""

    # 别名扩展后的 cleaned（用于 LCS）
    cleaned_expanded = _expand_aliases(cleaned)

    for cand in candidates:
        name = cand.get("name") or ""
        if not name:
            continue
        # LCS 使用别名扩展后的文本（归一化到候选名长度，避免长名靠字符多取胜）
        name_expanded = _expand_aliases(name)
        lcs_len = longest_common_substring_len(cleaned_expanded, name_expanded)
        score = round(lcs_len * 10 / len(name)) if name else 0

        # sensor_type 巷道偏好加权
        if sensor_type and lcs_len >= 2 and _candidate_matches_sensor_pref(name, sensor_type):
            score += 2

        # 编码匹配加权（最高优先级）
        code_hit = False
        if device_code and device_code in name:
            score += 5
            code_hit = True

        # 前缀模糊编码匹配（次高优先级）
        prefix_hit = False
        if not code_hit and device_code and len(device_code) >= 2:
            codes_in_name = re.findall(r'\d{3,4}', name)
            for code_in_name in codes_in_name:
                if code_in_name.startswith(device_code):
                    score += 3
                    prefix_hit = True
                    break

        # workface-tunnel 关联加权
        if cand.get("tunnelId") and device_code and device_code in (cand.get("tunnelId") or ""):
            score += 3

        # 巷道类型匹配加分
        cand_type = cand.get("type", "")
        for kw, (type_str, bonus) in _TUNNEL_TYPE_MATCH_BONUS.items():
            if kw in cleaned and cand_type == type_str:
                score += bonus
                break

        # coalbed 验证惩罚（跨煤层不匹配）
        if device_coalbed and cand.get("coalbed"):
            if cand["coalbed"] != device_coalbed:
                score -= 1

        # 语义过滤惩罚
        sem_penalty = _semantic_penalty(cleaned, name, mark_type)
        score += sem_penalty

        # 分层判定
        idx = candidates.index(cand)
        if code_hit or idx in code_indices:
            layer = _MATCH_LAYER_EXACT
        elif prefix_hit or idx in prefix_indices or score >= 5:
            layer = _MATCH_LAYER_LCS_PREF
        elif score >= 2:
            layer = _MATCH_LAYER_LOW
        else:
            layer = _MATCH_LAYER_REJECT

        if score >= 2 and score > best_score:
            best_score = score
            best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand, "layer": layer}
        elif score == best_score and best is not None and score >= 2:
            # 平局：优先编码匹配，然后 LCS 更长，然后名称更短（更精确）
            best_idx = candidates.index(best["candidate"])
            if idx in code_indices and best_idx not in code_indices:
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand, "layer": layer}
            elif lcs_len > best["lcs"]:
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand, "layer": layer}
            elif len(name) < len(best["name"]):
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand, "layer": layer}
    return best


def _polyline_length(line: list) -> float:
    """计算折线总长度（米）。"""
    total = 0.0
    for i in range(1, len(line)):
        dx = line[i]["x"] - line[i-1]["x"]
        dy = line[i]["y"] - line[i-1]["y"]
        dz = line[i]["z"] - line[i-1]["z"]
        total += (dx*dx + dy*dy + dz*dz) ** 0.5
    return total


def _polyline_interpolate(line: list, ratio: float) -> dict:
    """沿折线线性插值。ratio: 0.0=起点, 1.0=终点。"""
    if not line:
        return {"x": None, "y": None, "z": None}
    if len(line) == 1:
        return {"x": line[0]["x"], "y": line[0]["y"], "z": line[0]["z"]}
    total_len = _polyline_length(line)
    if total_len == 0:
        return {"x": line[0]["x"], "y": line[0]["y"], "z": line[0]["z"]}
    target = ratio * total_len
    accumulated = 0.0
    for i in range(1, len(line)):
        p0, p1 = line[i-1], line[i]
        dx = p1["x"] - p0["x"]
        dy = p1["y"] - p0["y"]
        dz = p1["z"] - p0["z"]
        seg_len = (dx*dx + dy*dy + dz*dz) ** 0.5
        if accumulated + seg_len >= target or i == len(line) - 1:
            t = (target - accumulated) / (seg_len or 1)
            return {
                "x": round(p0["x"] + dx * t, 4),
                "y": round(p0["y"] + dy * t, 4),
                "z": round(p0["z"] + dz * t, 4),
            }
        accumulated += seg_len
    return {"x": line[-1]["x"], "y": line[-1]["y"], "z": line[-1]["z"]}


def _assign_distances(count: int, keyword: str, line_length: float,
                      sensor_type: str = None, tunnel_type: str = None, step: float = 1.0) -> list:
    """
    按固定步长（默认 1m）分配距起点的距离。
    优先使用精确米数规则，次选百分比规则。
    若区间放不下所有设备，则在该区间内均匀分布。
    """
    # B16 工业视频同组多设备的标准间距（MT/T 1201.6-2023 附录 A）
    if sensor_type == "工业视频":
        if keyword == "支架":
            step = 75.0     # A.1#1: 两工业视频间距 ≤50架≈75m
        elif keyword == "中部":
            step = 500.0    # A.1#16: 主运输皮带中部每500m一台
        elif keyword == "架空乘人":
            step = 100.0    # A.1#23: 架空乘人装置中部每100m一台

    # 辅助函数：在区间内按步长分配，溢出则均匀分布
    def _distribute_in_zone(lo: float, hi: float, count: int, step: float) -> list:
        zone = hi - lo
        if count <= 1:
            return [(lo + hi) / 2]
        distances = [lo + i * step for i in range(count)]
        if distances[-1] > hi + 1e-6:
            step_adj = zone / (count - 1) if zone > 0 else 0
            distances = [lo + i * step_adj for i in range(count)]
        return distances

    # 辅助函数：将百分比区间转为米数，若折线足够长则使用精确米数
    def _ratio_to_meters(lo_ratio: float, hi_ratio: float, exact_lo: float = None, exact_hi: float = None) -> tuple:
        if line_length <= 0:
            return 0.0, 0.0
        lo = line_length * lo_ratio
        hi = line_length * hi_ratio
        if exact_lo is not None:
            lo = min(lo, exact_lo)
        if exact_hi is not None:
            hi = min(hi, exact_hi)
        return lo, hi

    # 1. T 标识规则（最高优先级）— 有精确米数时用精确米数
    if keyword in _T_POSITION_RULES:
        lo_ratio, hi_ratio = _T_POSITION_RULES[keyword]
        exact_lo, exact_hi = None, None
        if keyword == "T1":
            exact_lo, exact_hi = 0.0, 5.0
        elif keyword == "T2":
            exact_lo, exact_hi = max(0.0, line_length - 15.0), line_length
        elif keyword == "T4":
            exact_lo, exact_hi = max(0.0, line_length - 10.0), line_length
        lo, hi = _ratio_to_meters(lo_ratio, hi_ratio, exact_lo, exact_hi)
        return _distribute_in_zone(lo, hi, count, step)

    # 2. 巷道类型 × sensor_type 规则（次高优先级）
    if tunnel_type and tunnel_type in _TUNNEL_TYPE_RULES:
        type_rules = _TUNNEL_TYPE_RULES[tunnel_type]
        rule = type_rules.get(sensor_type) if sensor_type else None
        if rule:
            direction = rule["from"]
            meters = rule.get("meters", 0)
            tolerance = rule.get("tolerance", 0)
            if direction == "start" and line_length >= meters:
                lo, hi = 0.0, min(line_length * 0.15, meters + tolerance) if tolerance else meters
                return _distribute_in_zone(lo, hi, count, step)
            elif direction == "end" and line_length >= meters:
                lo, hi = max(line_length * 0.85, line_length - meters - tolerance) if tolerance else line_length - meters, line_length
                return _distribute_in_zone(lo, hi, count, step)
            elif direction == "mid":
                if meters > 0:
                    lo, hi = max(0.0, line_length * 0.5 - meters), min(line_length, line_length * 0.5 + meters)
                else:
                    lo, hi = line_length * 0.4, line_length * 0.6
                return _distribute_in_zone(lo, hi, count, step)

    # 3. 精确米数规则（通用）
    if line_length > 0:
        for kw_rule, st_rule, direction, meters in _AQ1029_DISTANCE_RULES:
            if keyword == kw_rule or (kw_rule is None and keyword == "default"):
                if st_rule is None or sensor_type == st_rule:
                    if direction == "start" and line_length >= meters:
                        lo, hi = 0.0, min(line_length * 0.15, meters + 5)
                    elif direction == "end" and line_length >= meters:
                        lo, hi = max(line_length * 0.85, line_length - meters - 5), line_length
                    elif direction == "mid":
                        lo, hi = line_length * 0.4, line_length * 0.6
                    else:
                        continue
                    return _distribute_in_zone(lo, hi, count, step)

    # 4. 回退到百分比规则
    if line_length <= 0 or count <= 1:
        if keyword == "迎头":
            return [0.0]
        elif keyword == "回风流":
            return [line_length]
        elif keyword in ("井口", "岔口"):
            return [line_length * 0.05]
        elif keyword == "井底":
            return [line_length * 0.95]
        elif keyword == "硐室":
            return [line_length * 0.5]
        elif keyword == "充电站":
            return [line_length * 0.5]  # MT/T 1198-2023 §5.1.4: 井下专用充电站，居中即可
        # B16 工业视频位置 — 煤矿工业视频安装及联网接入规范（2024-12）附录 A
        elif keyword == "机头":
            return [0.0]                      # 机头/机头及转载点 → 起点
        elif keyword == "机尾":
            return [line_length]              # 机尾/机尾及转载机 → 终点
        elif keyword == "转载点":
            return [line_length * 0.05]       # 转载点 → 起点附近
        elif keyword == "中部":
            return [line_length * 0.5]        # 皮带/输送机中部 → 中段
        elif keyword == "超前支护":
            return [line_length * 0.05]       # 超前支护 → 距工作面煤壁 10-15m，取起点附近
        elif keyword == "T2处":
            return [line_length * 0.95]       # T2处 → 回风顺槽外口/回风口附近
        elif keyword == "支架":
            return [line_length * 0.5]        # 支架 → 沿工作面均匀分布，取中段代表
        elif keyword == "煤仓":
            return [line_length * 0.5]        # 煤仓 → 上/下口落煤处，取中段
        elif keyword == "车场":
            return [line_length * 0.5]        # 车场 → 区域全景，取中段
        elif keyword == "地面":
            return [line_length * 0.5]        # 地面设施 → 居中
        elif sensor_type == "风速":
            return [line_length * 0.5]
        elif sensor_type in ("烟雾", "粉尘"):
            return [0.0]
        elif sensor_type == "温度":
            return [line_length * 0.5]
        elif sensor_type == "人员定位":
            return [line_length * 0.5]
        else:
            return [line_length * 0.5]

    if keyword == "迎头":
        lo, hi = 0.0, line_length * 0.15
    elif keyword == "回风流":
        lo, hi = line_length * 0.85, line_length
    elif keyword == "井口":
        lo, hi = line_length * 0.00, line_length * 0.10  # MT/T 1198-2023 §5.1.2 / DB51T1412-2011 5.1.8.1: 井口处
    elif keyword == "井底":
        lo, hi = line_length * 0.90, line_length * 1.00  # DB51T1412-2011 5.1.8.1: 井底处
    elif keyword == "岔口":
        lo, hi = line_length * 0.10, line_length * 0.25  # MT/T 1198-2023 §5.1.3 / DB51T1412-2011 5.1.8.2: 主要交叉巷口/分流路口
    elif keyword == "硐室":
        lo, hi = line_length * 0.40, line_length * 0.60  # MT/T 1198-2023 §5.2.4 / DB51T1412-2011 5.1.8.5: 准入区域（变电所/水泵房）出入口
    elif keyword == "充电站":
        lo, hi = line_length * 0.40, line_length * 0.60  # MT/T 1198-2023 §5.1.4: 井下专用充电站
    # B16 工业视频位置 — 煤矿工业视频安装及联网接入规范（2024-12）附录 A
    elif keyword == "机头":
        lo, hi = 0.0, line_length * 0.10                 # 机头 → 起点 0-10%
    elif keyword == "机尾":
        lo, hi = line_length * 0.90, line_length         # 机尾 → 终点 90-100%
    elif keyword == "转载点":
        lo, hi = 0.0, line_length * 0.15                 # 转载点 → 起点附近 0-15%
    elif keyword == "中部":
        lo, hi = line_length * 0.40, line_length * 0.60  # 中部 → 中段 40-60%
    elif keyword == "超前支护":
        lo, hi = 0.0, line_length * 0.15                 # 超前支护 → 距工作面煤壁 10-15m，取 0-15%
    elif keyword == "T2处":
        lo, hi = line_length * 0.85, line_length         # T2处 → 回风口附近 85-100%
    elif keyword == "支架":
        lo, hi = line_length * 0.30, line_length * 0.70  # 支架 → 沿工作面均匀分布 30-70%
    elif keyword == "煤仓":
        lo, hi = line_length * 0.30, line_length * 0.70  # 煤仓 → 上/下口落煤处 30-70%
    elif keyword == "车场":
        lo, hi = line_length * 0.30, line_length * 0.70  # 车场 → 区域全景 30-70%
    elif keyword == "地面":
        lo, hi = line_length * 0.30, line_length * 0.70  # 地面设施 → 居中 30-70%
    else:
        if sensor_type == "风速":
            lo, hi = line_length * 0.4, line_length * 0.6
        elif sensor_type in ("烟雾", "粉尘"):
            lo, hi = line_length * 0.0, line_length * 0.2
        elif sensor_type == "温度":
            lo, hi = line_length * 0.3, line_length * 0.7
        elif sensor_type == "人员定位":
            lo, hi = line_length * 0.4, line_length * 0.6  # DB51T1412-2011 5.1.8.2: 长巷道>1000m时中部增设
        else:
            lo, hi = line_length * 0.1, line_length * 0.9

    return _distribute_in_zone(lo, hi, count, step)


def _classify_keyword(description: str) -> str:
    """按关键词分类：T1/T2/T0/T3/T4 / 迎头 / 回风流 / B15关键词 / B16关键词 / default"""
    t_kw = _extract_t_keyword(description)
    if t_kw and t_kw in _T_POSITION_RULES:
        return t_kw
    if "迎头" in description:
        return "迎头"
    if "回风流" in description:
        return "回风流"
    if "隅角" in description or "上隅角" in description:
        return "T0"
    if "混合风流" in description:
        return "T3"
    # B15 人员定位关键词 (MT/T 1198-2023 §5.1-5.2，DB51T1412-2011 5.1.8)
    if "井口" in description:
        return "井口"
    if "井底" in description:
        return "井底"
    if "岔口" in description or "交叉口" in description or "分流" in description:
        return "岔口"  # MT/T 1198 §5.1.3: 分流路口归为岔口
    if "充电站" in description:
        return "充电站"  # MT/T 1198 §5.1.4
    if "硐室" in description:
        return "硐室"
    # B16 工业视频位置关键词 — 煤矿工业视频安装及联网接入规范（2024-12）附录 A
    # 机头/机尾/转载点/中部（输送机相关）
    if "机头及转载点" in description:
        return "机头"
    if "机尾及转载机" in description:
        return "机尾"
    if "机头" in description:
        return "机头"
    if "机尾" in description:
        return "机尾"
    if "转载点" in description:
        return "转载点"
    if "皮带中部" in description or "输送机中部" in description:
        return "中部"
    # 工作面相关
    if "超前支护" in description:
        return "超前支护"
    if "T2处" in description or "T2传感器" in description:
        return "T2处"
    if "支架" in description:
        return "支架"
    # 硐室/房间类（水泵房、变电所等已在 B15 中通过 "硐室" 处理）
    if "水泵房" in description:
        return "硐室"
    if "变电所" in description:
        return "硐室"
    if "绞车房" in description:
        return "硐室"
    if "避难硐室" in description:
        return "硐室"
    if "调度室" in description or "提升机房" in description:
        return "硐室"
    if "通风机房" in description or "空压机房" in description or "空压机站" in description:
        return "硐室"
    if "瓦斯泵" in description or "瓦斯抽采" in description:
        return "硐室"
    if "制氮" in description or "注氮" in description:
        return "硐室"
    if "灌浆站" in description or "注浆站" in description or "充填站" in description:
        return "硐室"
    if "坑木场" in description:
        return "硐室"
    # 其他特定位置
    if "煤仓" in description:
        return "煤仓"
    if "车场" in description:
        return "车场"
    if "工业广场" in description or "煤场" in description:
        return "地面"
    return "default"


def _extract_candidates(items) -> tuple:
    """
    从策略数据提取候选匹配项（巷道+工作面）。
    支持 dict 格式 {tunnels:[], workfaces:[]} 和 list 格式（workface 对象数组）。

    返回 (candidates, code_to_candidates, coalbed_map) 其中：
    - code_to_candidates: 工作面编码 -> 候选索引列表
    - coalbed_map: 工作面编码 -> coalbed 映射
    """
    candidates = []
    code_to_candidates = {}
    prefix_to_candidates = {}
    coalbed_map = {}

    def _add_code_index(name: str, idx: int):
        code = extract_workface_code(name)
        if code:
            code_to_candidates.setdefault(code, []).append(idx)
            # 前缀映射（3位及以上数字编码）
            if code.isdigit() and len(code) >= 3:
                for i in range(2, len(code)):
                    prefix = code[:i]
                    prefix_to_candidates.setdefault(prefix, []).append(idx)

    def _add_coalbed(name: str, coalbed: str):
        code = extract_workface_code(name)
        if code and coalbed:
            coalbed_map[code] = coalbed

    if isinstance(items, dict):
        for t in items.get("tunnels", []):
            idx = len(candidates)
            tunnel_type = t.get("type", "")
            candidates.append({
                "name": t.get("name", ""),
                "type": tunnel_type,
                "category": "tunnel",
                "line": t.get("line", []),
                "id": t.get("id", ""),
                "coalbed": t.get("coalbed", ""),
            })
            _add_code_index(t.get("name", ""), idx)
            _add_coalbed(t.get("name", ""), t.get("coalbed", ""))
        for w in items.get("workfaces", []):
            idx = len(candidates)
            wf_type = w.get("type", "")
            candidates.append({
                "name": w.get("workFaceName", ""),
                "type": wf_type,
                "category": "workface",
                "line": w.get("line", []),
                "id": w.get("id", ""),
                "tunnelId": w.get("tunnelId", ""),
                "coalbed": w.get("coalbed", ""),
            })
            _add_code_index(w.get("workFaceName", ""), idx)
            _add_coalbed(w.get("workFaceName", ""), w.get("coalbed", ""))
    elif isinstance(items, list):
        for item in items:
            name = item.get("workFaceName") or ""
            if name:
                idx = len(candidates)
                candidates.append({
                    "name": name,
                    "type": item.get("type", ""),
                    "category": "workface",
                    "line": item.get("line", []),
                    "id": item.get("id", ""),
                    "tunnelId": item.get("tunnelId", ""),
                    "coalbed": item.get("coalbed", ""),
                })
                _add_code_index(name, idx)
                _add_coalbed(name, item.get("coalbed", ""))
    return candidates, code_to_candidates, prefix_to_candidates, coalbed_map


# ── 数据校验 ──────────────────────────────────────────────────────
_X_MIN, _X_MAX = 3.7e7, 4.0e7
_Y_MIN, _Y_MAX = 3.5e6, 4.5e6
_Z_MIN, _Z_MAX = -2000.0, 2000.0


def _validate_devices(devices: list) -> list:
    """校验设备数据，返回清洗后的列表。跳过无效条目并输出警告。"""
    if not isinstance(devices, list):
        raise ValueError("devices 必须是 list")
    if not devices:
        return []
    cleaned = []
    auto_id = 1
    skipped = 0
    for i, dev in enumerate(devices):
        if not isinstance(dev, dict):
            print(f"  ! 跳过 devices[{i}]: 非 dict 类型", file=sys.stderr)
            skipped += 1
            continue
        desc = dev.get("description", "")
        if not isinstance(desc, str) or not desc.strip():
            print(f"  ! 跳过 devices[{i}] (id={dev.get('id', 'N/A')}): description 为空", file=sys.stderr)
            skipped += 1
            continue
        item = {
            "id": dev.get("id") or f"AUTO_{auto_id:03d}",
            "description": desc.strip(),
        }
        for field, expected in [("sensor_type", str), ("mark_type", str), ("sysaliasname", str), ("area", str)]:
            val = dev.get(field)
            if val is not None and not isinstance(val, expected):
                print(f"  ! 跳过 devices[{i}] {field}: 类型错误（期望 {expected.__name__}）", file=sys.stderr)
                skipped += 1
                break
            if val is not None:
                item[field] = val
        else:
            cleaned.append(item)
            if not dev.get("id"):
                auto_id += 1
    if skipped:
        print(f"  → 跳过 {skipped} 个无效设备", file=sys.stderr)
    return cleaned


def _validate_line(line: list, ctx: str) -> list:
    """校验折线数据，返回清洗后的列表。"""
    if not isinstance(line, list):
        raise ValueError(f"{ctx} line 必须是 list")
    if len(line) == 0:
        raise ValueError(f"{ctx} line 至少要有 1 个点")
    cleaned = []
    for j, pt in enumerate(line):
        if not isinstance(pt, dict):
            raise ValueError(f"{ctx} line[{j}] 必须是 dict")
        for axis in ("x", "y", "z"):
            val = pt.get(axis)
            if val is None:
                raise ValueError(f"{ctx} line[{j}] 缺少 {axis}")
            if not isinstance(val, (int, float)):
                raise ValueError(f"{ctx} line[{j}] {axis} 必须是数字")
        x, y, z = pt["x"], pt["y"], pt["z"]
        if not (_X_MIN <= x <= _X_MAX):
            raise ValueError(f"{ctx} line[{j}] x={x} 超出范围 [{_X_MIN}, {_X_MAX}]")
        if not (_Y_MIN <= y <= _Y_MAX):
            raise ValueError(f"{ctx} line[{j}] y={y} 超出范围 [{_Y_MIN}, {_Y_MAX}]")
        if not (_Z_MIN <= z <= _Z_MAX):
            raise ValueError(f"{ctx} line[{j}] z={z} 超出范围 [{_Z_MIN}, {_Z_MAX}]")
        cleaned.append({"x": float(x), "y": float(y), "z": float(z)})
    return cleaned


def _validate_tunnels(tunnels: list) -> list:
    """校验巷道数据，返回清洗后的列表。"""
    if not isinstance(tunnels, list):
        raise ValueError("tunnels 必须是 list")
    cleaned = []
    for i, t in enumerate(tunnels):
        if not isinstance(t, dict):
            raise ValueError(f"tunnels[{i}] 必须是 dict")
        name = t.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"tunnels[{i}] name 必填且非空")
        line = _validate_line(t.get("line", []), f"tunnels[{i}] '{name}'")
        item = {"name": name.strip(), "line": line}
        for field in ("id", "type", "coalbed"):
            val = t.get(field)
            if val is not None:
                if not isinstance(val, str):
                    raise ValueError(f"tunnels[{i}] {field} 必须是 str")
                item[field] = val
        cleaned.append(item)
    return cleaned


def _validate_workfaces(workfaces: list) -> list:
    """校验工作面数据，返回清洗后的列表。"""
    if not isinstance(workfaces, list):
        raise ValueError("workfaces 必须是 list")
    cleaned = []
    for i, w in enumerate(workfaces):
        if not isinstance(w, dict):
            raise ValueError(f"workfaces[{i}] 必须是 dict")
        name = w.get("workFaceName", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"workfaces[{i}] workFaceName 必填且非空")
        line = _validate_line(w.get("line", []), f"workfaces[{i}] '{name}'")
        item = {"workFaceName": name.strip(), "line": line}
        for field in ("id", "type", "tunnelId", "coalbed"):
            val = w.get(field)
            if val is not None:
                if not isinstance(val, str):
                    raise ValueError(f"workfaces[{i}] {field} 必须是 str")
                item[field] = val
        cleaned.append(item)
    return cleaned


def _load_json_file(path: str) -> dict or list:
    """加载 JSON 文件，返回解析后的对象。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, (dict, list)):
        raise ValueError(f"JSON 根必须是 dict 或 list")
    return data


# ── 匹配缓存 ──────────────────────────────────────────────────────
def _load_match_cache() -> dict:
    """加载已确认匹配缓存。返回 {description_key: cached_result}。"""
    if not MATCH_CACHE_PATH.exists():
        return {}
    try:
        with open(MATCH_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_match_cache(cache: dict):
    """保存已确认匹配缓存。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(MATCH_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _make_cache_key(description: str, mark_type: str = None) -> str:
    key = description.strip()
    if mark_type:
        key = f"{mark_type}:{key}"
    return key


def _find_cached_candidate(candidates: list, cached: dict) -> dict:
    """根据缓存信息在候选列表中查找对应 candidate。"""
    cached_name = cached.get("matched_name")
    cached_id = cached.get("candidate_id")
    for cand in candidates:
        if cand.get("name") == cached_name or cand.get("id") == cached_id:
            return cand
    return None


def _add_to_cache(cache: dict, description: str, match_result: dict, mark_type: str = None):
    """将高置信度匹配结果加入缓存。"""
    key = _make_cache_key(description, mark_type)
    cache[key] = {
        "matched_name": match_result.get("name"),
        "candidate_id": match_result.get("candidate", {}).get("id"),
        "score": match_result.get("score"),
        "timestamp": datetime.now().isoformat(),
    }


# ── 风速间距检查 ──────────────────────────────────────────────────
def _check_wind_speed_spacing(groups: dict) -> list:
    """
    检查组内风速传感器是否满足最小间距要求。
    返回警告列表。
    """
    warnings = []
    for group_key, entries in groups.items():
        name, keyword = group_key
        wind_entries = [(i, e) for i, e in enumerate(entries) if e[3] == "风速"]
        if len(wind_entries) <= 1:
            continue
        candidate = entries[0][1]["candidate"]
        line = candidate.get("line", [])
        total_len = _polyline_length(line)
        # 取组内代表 sensor_type 计算距离
        st_counts = {}
        for _, _, _, st, _ in entries:
            st_counts[st] = st_counts.get(st, 0) + 1
        representative_st = max(st_counts, key=st_counts.get) if st_counts else None
        distances = _assign_distances(len(entries), keyword, total_len,
                                       sensor_type=representative_st,
                                       tunnel_type=candidate.get("type", ""))
        wind_distances = [(i, distances[i]) for i, _ in wind_entries]
        wind_distances.sort(key=lambda x: x[1])
        for j in range(1, len(wind_distances)):
            prev_idx, prev_dist = wind_distances[j - 1]
            curr_idx, curr_dist = wind_distances[j]
            spacing = abs(curr_dist - prev_dist)
            if spacing < _WIND_SPEED_MIN_SPACING - 1e-6:
                device1 = entries[prev_idx][0]
                device2 = entries[curr_idx][0]
                warnings.append({
                    "type": "wind_speed_spacing",
                    "tunnel": name,
                    "device1_id": device1.get("id", ""),
                    "device1_desc": device1.get("description", ""),
                    "device2_id": device2.get("id", ""),
                    "device2_desc": device2.get("description", ""),
                    "distance": round(spacing, 2),
                    "required": _WIND_SPEED_MIN_SPACING,
                })
    return warnings


# ── 主流程 ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="煤矿设备定位")
    parser.add_argument("username", help="用户名（如 F18795450）")
    parser.add_argument("devices_file", nargs="?", metavar="DEVICES_FILE",
                        help="设备数据文件（自动识别为 --load-devices）")
    parser.add_argument("--load", metavar="PATH",
                        help="从本地文件加载完整 8373 数据（含 devices/tunnels/workfaces）")
    parser.add_argument("--load-devices", metavar="PATH",
                        help="从本地文件加载设备数据")
    parser.add_argument("--load-tunnels", metavar="PATH",
                        help="从本地文件加载巷道数据")
    parser.add_argument("--load-workfaces", metavar="PATH",
                        help="从本地文件加载工作面数据")
    args = parser.parse_args()
    username = args.username

    # 判断哪些数据需要从文件加载（裸文件参数自动识别为 --load-devices）
    args.load_devices = args.load_devices or args.devices_file
    file_devices = args.load_devices or (args.load and "devices")
    file_tunnels = args.load_tunnels or (args.load and "tunnels")
    file_workfaces = args.load_workfaces or (args.load and "workfaces")
    use_file = bool(args.load or args.load_devices or args.load_tunnels or args.load_workfaces)

    # ── Step 1: Token ──
    print(f"[1/2] 获取 token (username={username})...", file=sys.stderr)
    tokens, mine_name = get_token_and_mine_name(username)
    print(f"  → mineName: {mine_name}", file=sys.stderr)

    # ── Step 2: 加载数据（文件优先，缺失部分从 API 补全）──
    devices, candidates = [], []
    code_to_candidates, prefix_to_candidates, coalbed_map = {}, {}, {}

    # 2a. 从文件加载
    if use_file:
        # 处理 --load 单一文件（含 devices/tunnels/workfaces）
        if args.load:
            load_path = Path(args.load)
            print(f"[2/2] 从文件加载: {load_path}", file=sys.stderr)
            data = _load_json_file(str(load_path))
            if isinstance(data, dict):
                if "devices" in data:
                    devices = _validate_devices(data["devices"])
                    print(f"  → 文件 devices: {len(devices)} 个", file=sys.stderr)
                if "tunnels" in data or "workfaces" in data:
                    # 先校验再提取
                    validated = {}
                    if "tunnels" in data:
                        validated["tunnels"] = _validate_tunnels(data["tunnels"])
                    if "workfaces" in data:
                        validated["workfaces"] = _validate_workfaces(data["workfaces"])
                    candidates, code_to_candidates, prefix_to_candidates, coalbed_map = _extract_candidates(validated)
                    print(f"  → 文件候选: {len(candidates)} 个", file=sys.stderr)
            elif isinstance(data, list):
                devices, candidates = classify_items(data)
                devices = _validate_devices(devices)
                print(f"  → 文件 devices: {len(devices)} 个, 候选: {len(candidates)} 个", file=sys.stderr)

        # 处理单独的 --load-* 参数
        if args.load_devices:
            print(f"  → 加载设备: {args.load_devices}", file=sys.stderr)
            data = _load_json_file(args.load_devices)
            if isinstance(data, dict) and "devices" in data:
                devices = _validate_devices(data["devices"])
            elif isinstance(data, list):
                devices = _validate_devices(data)
            else:
                raise ValueError(f"--load-devices 文件必须含 devices 数组或本身就是设备数组")
            print(f"    devices: {len(devices)} 个", file=sys.stderr)

        # 收集所有来源的 tunnels 和 workfaces，合并后统一提取 candidates
        merged_tunnels, merged_workfaces = [], []
        if args.load_tunnels:
            print(f"  → 加载巷道: {args.load_tunnels}", file=sys.stderr)
            data = _load_json_file(args.load_tunnels)
            if isinstance(data, dict) and "tunnels" in data:
                merged_tunnels = _validate_tunnels(data["tunnels"])
            elif isinstance(data, list):
                merged_tunnels = _validate_tunnels(data)
            else:
                raise ValueError("--load-tunnels 文件必须含 tunnels 数组或本身就是巷道数组")
            print(f"    tunnels: {len(merged_tunnels)} 个", file=sys.stderr)

        if args.load_workfaces:
            print(f"  → 加载工作面: {args.load_workfaces}", file=sys.stderr)
            data = _load_json_file(args.load_workfaces)
            if isinstance(data, dict) and "workfaces" in data:
                merged_workfaces = _validate_workfaces(data["workfaces"])
            elif isinstance(data, list):
                merged_workfaces = _validate_workfaces(data)
            else:
                raise ValueError("--load-workfaces 文件必须含 workfaces 数组或本身就是工作面数组")
            print(f"    workfaces: {len(merged_workfaces)} 个", file=sys.stderr)

        if merged_tunnels or merged_workfaces:
            merged_cand = {}
            if merged_tunnels:
                merged_cand["tunnels"] = merged_tunnels
            if merged_workfaces:
                merged_cand["workfaces"] = merged_workfaces
            candidates, code_to_candidates, prefix_to_candidates, coalbed_map = _extract_candidates(merged_cand)
            print(f"  → 文件候选合计: {len(candidates)} 个", file=sys.stderr)

    # 2b. 从 API 补全缺失的数据
    need_api_devices = not devices
    need_api_candidates = not candidates

    if need_api_devices or need_api_candidates:
        print(f"[2/2] 从 API 补全数据...", file=sys.stderr)
        resp_dev = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_json")
        raw_data = resp_dev.get("data", {})

        if isinstance(raw_data, dict) and "devices" in raw_data:
            if need_api_devices:
                devices = _validate_devices(raw_data.get("devices", []))
                print(f"  → API devices: {len(devices)} 个", file=sys.stderr)
            if need_api_candidates:
                candidates, code_to_candidates, prefix_to_candidates, coalbed_map = _extract_candidates(raw_data)
                print(f"  → API 候选: {len(candidates)} 个", file=sys.stderr)
        else:
            device_items = extract_items(raw_data)
            if need_api_devices:
                api_devices, _ = classify_items(device_items)
                devices = _validate_devices(api_devices)
                print(f"  → API devices: {len(devices)} 个", file=sys.stderr)
            if need_api_candidates:
                print(f"  → 获取工作面/巷道 (get_data)...", file=sys.stderr)
                resp_cand = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_data")
                cand_items = extract_items(resp_cand.get("data"))
                candidates, code_to_candidates, prefix_to_candidates, coalbed_map = _extract_candidates(cand_items)
                print(f"  → API 候选: {len(candidates)} 个", file=sys.stderr)

    # 如果用了文件+API混合，保存合并结果
    if use_file and (args.load_devices or args.load_tunnels or args.load_workfaces):
        save_dir = PROJECT_ROOT / "data" / "output"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"data_8373_{mine_name}.json"
        merged = {"devices": devices, "candidates": candidates}
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  → 合并结果已保存: {save_path}", file=sys.stderr)
    elif not use_file:
        save_dir = PROJECT_ROOT / "data" / "output"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"data_8373_{mine_name}.json"
        merged = {"devices": devices, "candidates": candidates}
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  → 已保存: {save_path}", file=sys.stderr)

    print(f"  → 设备: {len(devices)} 个, 候选名: {len(candidates)} 个", file=sys.stderr)

    if not devices:
        print("没有设备数据，退出。", file=sys.stderr)
        return

    # ── Step 3: 匹配（两遍：先分组，再分配坐标偏移）──
    print(f"\n[匹配] 匹配中...", file=sys.stderr)

    # 加载缓存
    match_cache = _load_match_cache()
    cache_hits = 0

    # 第一遍：匹配所有设备
    match_entries = []  # [(device, match_info, cleaned, sensor_type), ...]
    unmatched = []

    for device in devices:
        desc = device.get("description", "")
        cleaned = strip_prefix(desc)
        mark_type = device.get("mark_type")
        sensor_type = device.get("sensor_type") or _infer_sensor_type(desc, mark_type)
        # 厂商名归一化：海康/大华/宇视/萤石 是品牌而非设备类型，B16 时统一为"工业视频"
        if mark_type == "B16" and sensor_type in ("海康", "大华", "宇视", "萤石"):
            sensor_type = "工业视频"
        # 编码从原始描述提取（避免被前缀剥离误删）
        device_code = extract_workface_code(desc)

        # 先查缓存
        cached = match_cache.get(_make_cache_key(desc, mark_type))
        if cached:
            cand = _find_cached_candidate(candidates, cached)
            if cand:
                cache_hits += 1
                match = {
                    "name": cand.get("name", ""),
                    "lcs": 0,
                    "score": cached.get("score", 10),
                    "candidate": cand,
                    "layer": _MATCH_LAYER_EXACT,
                    "from_cache": True,
                }
                explicit_dist = extract_explicit_distance(desc)
                match_entries.append((device, match, cleaned, sensor_type, explicit_dist))
                continue

        # area 语义过滤：地面设备不匹配井下候选
        area_val = device.get("area")
        if _is_surface_area(area_val):
            unmatched.append({
                "id": device.get("id", ""),
                "description": desc,
                "mark_type": mark_type or "",
                "sensor_type": sensor_type,
                "sysaliasname": device.get("sysaliasname", ""),
                "reason": REJECT_AREA_SURFACE,
                "device_code": device_code,
                "area": area_val,
            })
            continue

        # 正常匹配流程
        match = find_best_match(cleaned, candidates, sensor_type=sensor_type,
                                 device_code=device_code, code_to_candidates=code_to_candidates,
                                 prefix_to_candidates=prefix_to_candidates,
                                 coalbed_map=coalbed_map, mark_type=mark_type)
        if match is None:
            # 详细拒绝原因分析
            reason = REJECT_LOW_LCS
            if not candidates:
                reason = REJECT_NO_CANDIDATE
            elif device_code:
                code_found = any(device_code in (c.get("name") or "") for c in candidates)
                if not code_found:
                    # 再试前缀匹配
                    prefix_found = False
                    for c in candidates:
                        codes_in_name = re.findall(r'\d{3,4}', c.get("name", ""))
                        if any(cn.startswith(device_code) for cn in codes_in_name):
                            prefix_found = True
                            break
                    if not prefix_found:
                        reason = REJECT_CODE_MISMATCH
            if reason == REJECT_LOW_LCS:
                # 检查是否是语义冲突导致所有候选得分过低
                sem_blocked = True
                for cand in candidates:
                    if _semantic_penalty(cleaned, cand.get("name", ""), mark_type=mark_type) > -10:
                        sem_blocked = False
                        break
                if sem_blocked and candidates:
                    reason = REJECT_SEMANTIC_CONFLICT
            unmatched.append({
                "id": device.get("id", ""),
                "description": desc,
                "mark_type": mark_type or "",
                "sensor_type": sensor_type,
                "sysaliasname": device.get("sysaliasname", ""),
                "reason": reason,
                "device_code": device_code,
                **({"area": device.get("area", "")} if device.get("area") else {}),
            })
            continue

        # 高置信度匹配写入缓存
        if match.get("layer") == _MATCH_LAYER_EXACT:
            _add_to_cache(match_cache, desc, match, mark_type)

        explicit_dist = extract_explicit_distance(desc)
        match_entries.append((device, match, cleaned, sensor_type, explicit_dist))

    if cache_hits > 0:
        print(f"  → 缓存命中: {cache_hits} 个", file=sys.stderr)
    _save_match_cache(match_cache)

    # 按 (matched_name, keyword) 分组（同一巷道同一关键词的设备在同一组）
    groups = {}
    for device, match, cleaned, sensor_type, explicit_dist in match_entries:
        keyword = _classify_keyword(cleaned)
        group_key = (match["name"], keyword)
        groups.setdefault(group_key, []).append((device, match, cleaned, sensor_type, explicit_dist))

    def _calc_confidence(match: dict, cleaned: str, sensor_type: str = None) -> str:
        layer = match.get("layer", _MATCH_LAYER_REJECT)
        if layer == _MATCH_LAYER_EXACT:
            return "高"
        if layer == _MATCH_LAYER_LCS_PREF:
            return "中"
        if layer == _MATCH_LAYER_LOW:
            return "低"
        return "极低"

    # 每组分配距离并计算坐标
    results = []
    matched_count = 0
    for group_key, entries in groups.items():
        name, keyword = group_key
        candidate = entries[0][1]["candidate"]
        line = candidate.get("line", [])
        tunnel_type = candidate.get("type", "")
        total_len = _polyline_length(line)
        # 取组内出现最多的 sensor_type 作为代表
        st_counts = {}
        for _, _, _, st, _ in entries:
            st_counts[st] = st_counts.get(st, 0) + 1
        representative_st = max(st_counts, key=st_counts.get) if st_counts else None
        # 剔除有显式距离的设备（不参与默认分配），其余设备按规则分配
        implicit_count = sum(1 for _, _, _, _, ed in entries if ed is None)
        explicit_entries = [(i, ed) for i, (_, _, _, _, ed) in enumerate(entries) if ed is not None]
        implicit_distances = _assign_distances(implicit_count, keyword, total_len,
                                                sensor_type=representative_st, tunnel_type=tunnel_type)
        # 合并：按索引回填，显式距离按语义偏移
        distances = [None] * len(entries)
        for idx, ed in explicit_entries:
            desc_clean = entries[idx][2]  # cleaned description
            distances[idx] = _apply_keyword_offset(desc_clean, keyword, ed, total_len) if keyword else ed
        imp_idx = 0
        for i in range(len(distances)):
            if distances[i] is None:
                distances[i] = implicit_distances[imp_idx]
                imp_idx += 1

        for (device, match, cleaned, sensor_type, explicit_dist), dist in zip(entries, distances):
            matched_count += 1
            # 显式距离若超出折线总长，clamp 到终点并记录警告
            if explicit_dist is not None and total_len > 0 and explicit_dist > total_len:
                print(f"  ! 显式距离超出: {explicit_dist:.0f}m > {total_len:.0f}m "
                      f"(matched={match['name']}, desc={device.get('description', '')[:60]})", file=sys.stderr)
                dist = total_len
                clamped = True
            else:
                clamped = False
            ratio = dist / total_len if total_len > 0 else 0.5
            coords = _polyline_interpolate(line, ratio) if line else {"x": None, "y": None, "z": None}

            # z 坐标调整：根据 sensor_type 叠加安装高度
            if coords.get("z") is not None and sensor_type in _SENSOR_INSTALL_HEIGHT:
                coords = dict(coords)
                coords["z"] = round(coords["z"] + _SENSOR_INSTALL_HEIGHT[sensor_type], 4)

            results.append({
                "id": device.get("id", ""),
                "description": device.get("description", ""),
                "matched": True,
                "matched_name": match["name"],
                "tunnel_id": (match["candidate"].get("tunnelId") or match["candidate"].get("id", "")),
                "matched_type": match["candidate"].get("category", ""),
                "tunnel_type": match["candidate"].get("type", ""),
                "coalbed": match["candidate"].get("coalbed", ""),
                "match_lcs": match["lcs"],
                "match_score": match.get("score", 0),
                "confidence": _calc_confidence(match, cleaned, sensor_type),
                "mark_type": device.get("mark_type", ""),
                "sensor_type": sensor_type,
                "area": device.get("area", ""),
                "sysaliasname": device.get("sysaliasname", ""),
                **({"explicit_distance": round(explicit_dist, 1)} if explicit_dist is not None else {}),
                **({"distance_clamped": True} if clamped else {}),
                "coordinates": coords,
            })

    # 风速间距检查
    wind_warnings = _check_wind_speed_spacing(groups)
    if wind_warnings:
        print(f"  ! 风速间距警告: {len(wind_warnings)} 条", file=sys.stderr)

    # ── 输出 ──
    output = {
        "username": username,
        "mine_name": mine_name,
        "summary": {
            "total": len(devices),
            "matched": matched_count,
            "unmatched": len(unmatched),
            "wind_spacing_warnings": len(wind_warnings),
        },
        "results": results,
        "unmatched_devices": unmatched,
        "warnings": wind_warnings,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))

    result_save_dir = PROJECT_ROOT / "data" / "output"
    result_save_dir.mkdir(parents=True, exist_ok=True)
    result_save_path = result_save_dir / f"locator_result_{username}_{mine_name}.json"
    with open(result_save_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {result_save_path}", file=sys.stderr)

    print(f"\n=== 汇总 ===", file=sys.stderr)
    print(f"  总计: {len(devices)}  匹配: {matched_count} ✓  未匹配: {len(unmatched)}", file=sys.stderr)

    # ── Step 4: 回写定位结果到策略 8385 ──
    print(f"\n[3/3] 回写定位结果到策略 8385...", file=sys.stderr)
    import tempfile
    data_param = json.dumps(output, ensure_ascii=False)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            tf.write(data_param)
            tmp_path = tf.name
        resp = call_strategy_api(8385, username, action="execute",
                                 param_file=f"data={tmp_path}")
        print(f"  → 8385 回写结果: {resp.get('code', 'unknown')}", file=sys.stderr)
        os.unlink(tmp_path)
    except Exception as e:
        print(f"  ! 8385 回写失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
