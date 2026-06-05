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
import math
from datetime import datetime
from pathlib import Path


# ── 路径 ──────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # 仓库根目录
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
    """调用 bw-token-manager 获取 token 和 mineName。
    兼容新旧两种 CLI：新版用 --username，旧版用位置参数。
    若上游 skill 不可用（如 Windows 上 fcntl 缺失），直接调用 API 获取。"""
    # 先尝试新版接口 (--username)
    result = subprocess.run(
        [_PYTHON_EXE, str(TOKEN_MANAGER), "--username", username, "--output", "json"],
        capture_output=True, text=True, cwd=PROJECT_ROOT
    )
    if result.returncode != 0:
        # fallback 到旧版接口（位置参数）
        result = subprocess.run(
            [_PYTHON_EXE, str(TOKEN_MANAGER), username, "--output", "json"],
            capture_output=True, text=True, cwd=PROJECT_ROOT
        )
    if result.returncode == 0:
        try:
            tokens = json.loads(result.stdout.strip())
            if tokens.get("bw_token"):
                mine_name = tokens.get("mineName") or username
                return tokens, mine_name
        except json.JSONDecodeError:
            pass
    # 上游 skill 不可用 → 直接调用 API
    print(f"[locator] Token manager unavailable, fetching token directly via API...", file=sys.stderr)
    import urllib.request
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?username={username}"
    try:
        with urllib.request.urlopen(api_url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("code") == 100:
            tokens = data.get("data", {})
            tokens["mineName"] = tokens.get("mineName") or username
            if not tokens.get("bw_token"):
                raise RuntimeError("bw_token not found in API response")
            return tokens, tokens["mineName"]
        else:
            raise RuntimeError(f"API error: {data.get('mesg', 'Unknown')}")
    except Exception as e:
        raise RuntimeError(f"Failed to get token via API: {e}")


def call_strategy_api(strategy_id: int, username: str, param: str = None,
                      action: str = "get_data", param_file: str = None) -> dict:
    """调用 strategy_api.py，返回完整响应 dict。

    对于 execute 操作且数据量大时，strategy_api.py CLI 不支持 --param-from-file，
    此时自动回退到直接 API 调用。"""
    # execute + param_file → 直接调用 API（上游 CLI 不支持大参数文件）
    if action == "execute" and param_file:
        return _call_execute_strategy_api_directly(strategy_id, username, param_file)

    cmd = [
        _PYTHON_EXE, str(STRATEGY_API), action,
        "--id", str(strategy_id),
        "--username", username,
        "--output", "json",
    ]
    if param:
        cmd.extend(["--param", param])

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"Strategy API (id={strategy_id}) failed: {result.stderr.strip()}")
    return json.loads(result.stdout.strip())


def _call_execute_strategy_api_directly(strategy_id: int, username: str, param_file: str) -> dict:
    """直接调用 ExecuteStrategyCom API，绕过 strategy_api.py CLI 限制。

    param_file 格式: data=文件路径
    API 期望的请求体: {"id": 8385, "parameter": [{"name": "data", "value": "<json_string>"}], ...}
    """
    import urllib.request

    # 获取 token
    tokens, _ = get_token_and_mine_name(username)
    token = tokens.get("bw_token", "")
    if not token:
        raise RuntimeError("bw_token not available for execute API")

    # 解析 param_file
    if param_file.startswith("data="):
        file_path = param_file[5:]
    else:
        file_path = param_file

    with open(file_path, "r", encoding="utf-8") as f:
        data_content = f.read()

    # 构建 API 请求体（包装策略ID和参数）
    payload = {
        "id": strategy_id,
        "parameter": [{"name": "data", "value": data_content}],
        "queryType": 1,
        "orders": [{"name": "id", "value": "asc"}]
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    url = f"http://192.168.133.110:33382/net/api/poininfoSmartValid/ExecuteStrategyCom"
    req = urllib.request.Request(
        url,
        data=payload_json.encode("utf-8"),
        headers={
            "caller": "openclaw",
            "token": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    code = result.get("code", 0)
    if code != 100:
        print(f"[WARNING] Execute API returned code {code}: {result.get('mesg', 'Unknown')}", file=sys.stderr)

    return result


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
    - 无以上字段但有 line/type → 无名称巷道（被跳过）

    返回 (devices, candidates, unnamed_tunnel_skipped)
    """
    devices = []
    candidates = []
    unnamed_tunnel_skipped = 0
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
            name = (item.get("name") or "").strip()
            if not name:
                unnamed_tunnel_skipped += 1
                continue
            candidates.append({
                "name": name,
                "type": "tunnel",
                "line": item.get("line", []),
            })
        elif "line" in item:
            # 有几何数据但无 name/description/workFaceName — 无名称巷道
            unnamed_tunnel_skipped += 1
    return devices, candidates, unnamed_tunnel_skipped


# ── 匹配逻辑 ──────────────────────────────────────────────────────
PREFIX_PATTERNS = [
    # 通道编码固定 6 字符: 3 位数字 + 1 大写字母 + 2 位数字 (如 001A01, 029D06)
    r"^\d+号分站(?:模拟量|开关量|多态量)\d{3}[A-Z]\d{2}",
    # 其他前缀通道编码: 6 位+ 数字 + 1 大写字母 + 2 位数字 (如 999602059P10)
    r"^其他\d{6,}[A-Z]\d{2}",
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
    if "烟雾" in d or "YW" in d:
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
    # 斜井/立井等价：副井/主井/回风井 与 副斜井/副立井 等在口语中常混用
    "副井": ["副斜井", "副立井"],
    "主井": ["主斜井", "主立井"],
    "回风井": ["回风斜井", "回风立井"],
}


def _expand_aliases(text: str) -> list[str]:
    """将 text 中的巷道别名扩展为多个字符串变体，返回列表。
    每种变体将别名替换为一种可能的全称（或保留原缩写），
    用于 LCS 计算时取最大匹配值，避免 `|` 连接符被计入公共子串导致虚高。"""
    if not text:
        return [text]
    # 按长度降序处理，避免短别名破坏长别名
    items = sorted(_TUNNEL_ALIAS_MAP.items(), key=lambda x: len(x[0]), reverse=True)
    variants = [text]
    for abbr, full_forms in items:
        if not any(abbr in v for v in variants):
            continue
        new_variants = []
        for v in variants:
            if abbr in v:
                for form in [abbr] + full_forms:
                    new_variants.append(v.replace(abbr, form, 1))
            else:
                new_variants.append(v)
        variants = new_variants
    # 去重并限制数量，避免组合爆炸
    seen = set()
    result = []
    for v in variants:
        if v not in seen and len(result) < 32:
            seen.add(v)
            result.append(v)
    return result


def _candidate_matches_sensor_pref(name: str, sensor_type: str) -> bool:
    """返回候选名是否命中 sensor_type 偏好关键词（True/False，用于匹配加分判断）。"""
    prefs = _SENSOR_TUNNEL_PREF.get(sensor_type, [])
    return any(p in name for p in prefs)


def _count_sensor_pref_matches(name: str, sensor_type: str) -> int:
    """返回候选名命中 sensor_type 偏好关键词的数量（用于平局时的质量判断）。"""
    prefs = _SENSOR_TUNNEL_PREF.get(sensor_type, [])
    return sum(1 for p in prefs if p in name)


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
    "露天矿",   # 露天矿监控/充电桩/南帮工作面等（露天采矿区，非井下）
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
    "化验楼",   # 地面化验楼设施
    "产品仓",   # 地面洗选仓储
    "原煤仓",   # 地面原煤仓储
    "工业区域", # 地面工业广场/工业区域设施（压风机房、制氮车间等）
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


# ── description 语义分类（地面/井下）────────────────────────────────
# 补充 area 过滤的不足：当 area 未标记为地面但描述明显是地面设施时，
# 同样跳过井下候选。
_DESCRIPTION_SURFACE_PATTERNS = [
    "调度楼",
    "办公楼",
    "宿舍楼",
    "培训楼",
    "培训室",
    "化验室",
    "会议室",
    "职工食堂",
    "澡堂",
    "灯房",
    "煤场",
    "工业广场",
    "门卫",
    "机修车间",
    "加工车间",
]

# ── 系统生成巷道名称检测 ───────────────────────────────────────────
# 系统生成巷道名称：形如 "巷道136" 或纯数字 "146"（人工命名巷道不会是纯数字）
_GENERIC_TUNNEL_NAME_PATTERN = re.compile(r'^巷道\d+$|^\d+$')


def _is_generic_tunnel_name(name: str) -> bool:
    """检测系统生成的巷道名称（如 '巷道136'），无实际语义含义。"""
    if not name:
        return False
    return bool(_GENERIC_TUNNEL_NAME_PATTERN.match(name.strip()))


def _is_surface_description(description: str) -> bool:
    """根据 description 判断设备是否属于地面设施。"""
    if not description:
        return False
    for pattern in _DESCRIPTION_SURFACE_PATTERNS:
        if pattern in description:
            return True
    return False


def _is_shaft_mouth(description: str) -> bool:
    """检测是否为井口设备（主井井口、副井井口、回风井井口、风井井口等）。
    这类设备 area 虽标记为井上，但实际是井下斜井的入口点，
    应匹配到对应斜井起始位置，而非被 AREA_SURFACE 过滤掉。
    """
    if not description:
        return False
    # 匹配 "主井井口"、"副井井口"、"回风井井口"、"风井井口" 等
    return bool(re.search(r'(主井|副井|回风井|风井)井口', description))


# ── 地点类型语义过滤 ──────────────────────────────────────────────
_LOCATION_SEMANTICS = {
    "洗煤厂": {"allow": ["洗煤厂"], "penalty": -10},
    "选煤楼": {"allow": ["选煤楼"], "penalty": -10},
    "中央变电所": {"allow": ["变电", "配电"], "penalty": -10},
    "避难硐室": {"allow": ["硐室"], "penalty": -10},
    "硐室": {"allow": ["硐室"], "penalty": -10},
    "井口": {"allow": ["井口", "井筒", "副井", "主井", "斜井"], "penalty": -10},
    "地面": {"allow": ["地面", "洗煤厂", "空压机房"], "penalty": -10},
    "通风机": {"allow": ["通风", "风机", "通风机"], "penalty": -10},
    "主扇": {"allow": ["通风", "风机", "主扇"], "penalty": -10},
    # 新增：排矸/联巷/泵站/泵房/运输巷等地点语义
    "排矸": {"allow": ["排矸"], "penalty": -10},
    "联巷": {"allow": ["联巷", "联络巷"], "penalty": -10},
    "联络巷": {"allow": ["联巷", "联络巷"], "penalty": -10},
    "泵站": {"allow": ["泵站", "泵房"], "penalty": -10},
    "泵房": {"allow": ["泵站", "泵房"], "penalty": -10},
    "瓦斯泵站": {"allow": ["瓦斯泵站", "瓦斯泵房", "瓦斯抽放泵站", "移动式瓦斯泵站", "瓦斯抽放"], "penalty": -10},
    "瓦斯泵房": {"allow": ["瓦斯泵站", "瓦斯泵房", "瓦斯抽放泵站", "移动式瓦斯泵站", "瓦斯抽放"], "penalty": -10},
    "运输巷": {"allow": ["运输巷", "运输大巷"], "penalty": -10},
    "充电硐室": {"allow": ["充电硐室", "充电站"], "penalty": -10},
    "煤仓": {"allow": ["煤仓"], "penalty": -10},
    "探巷": {"allow": ["探巷"], "penalty": -10},
    "集控仓": {"allow": ["仓", "硐室", "室"], "penalty": -10},
    "皮带": {"allow": ["皮带", "运输", "输送机", "机头", "机尾", "转载", "胶运", "顺槽"], "penalty": -10},
    "机头": {"allow": ["机头", "皮带", "运输", "输送机", "顺槽", "胶运"], "penalty": -10},
    "压带轮": {"allow": ["皮带", "运输", "输送机", "顺槽", "机轨"], "penalty": -10},
    "卸料器": {"allow": ["皮带", "运输", "输送机", "煤仓", "转载", "顺槽"], "penalty": -10},
    "运输大巷": {"allow": ["运输", "大巷", "皮带", "轨道", "顺槽", "胶运"], "penalty": -10},
    "候车室": {"allow": ["候车室", "车场"], "penalty": -10},
    "机轨": {"allow": ["机轨", "机轨运输"], "penalty": -10},
    # 地面工业设施：描述含此类设施词但候选不含对应设施时拒绝匹配
    "压风机房": {"allow": ["压风机房", "压风机"], "penalty": -10},
    "制氮车间": {"allow": ["制氮", "车间"], "penalty": -10},
    "车间": {"allow": ["车间"], "penalty": -10},
}


# ── 设备功能词与巷道类型的可疑匹配检测 ──────────────────────────────
# 当描述中的设备功能词表明设备应位于特定类型巷道/硐室，但候选不符时，
# 标记为 suspicious（可疑）。编码命中+LCS 高无法掩盖这种语义错配。
# 此类匹配不会自动拒绝（宁缺毋滥的底线由 _has_hard_semantic_conflict 保障），
# 但会在结果中打上 suspicious 标记，由上层（Claude）提示用户逐条确认。
_FUNCTIONAL_SEMANTIC_CHECKS = [
    # (描述关键词, 候选必须含其一, 可疑原因)
    ("水泵房", ["水泵房", "硐室"], "设备为水泵房但候选非硐室/水泵房"),
    ("水泵", ["水泵房", "硐室", "变电", "配电"], "设备含水泵但候选非硐室/水泵房/变电"),
    ("远控开关", ["硐室", "变电", "配电", "水泵房"], "设备含远控开关但候选非配电/硐室"),
    ("配电点", ["硐室", "变电", "配电", "水泵房"], "设备含配电点但候选非配电/硐室"),
]


def _check_functional_conflict(cleaned: str, candidate_name: str) -> tuple:
    """检查设备功能词与候选巷道类型是否冲突。
    返回 (is_suspicious, reason)。"""
    if not cleaned or not candidate_name:
        return False, None
    for func_kw, required_kws, reason in _FUNCTIONAL_SEMANTIC_CHECKS:
        if func_kw in cleaned:
            if not any(rkw in candidate_name for rkw in required_kws):
                return True, reason
    return False, None


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
_MATCH_LAYER_LOW = 3       # lcs≥2 且 score≥2（标记人工复核）
_MATCH_LAYER_REJECT = 4     # 拒绝


# ── 未匹配详细原因 ────────────────────────────────────────────────
REJECT_NO_CANDIDATE = "NO_CANDIDATE"           # 无可行候选
REJECT_CODE_MISMATCH = "CODE_MISMATCH"         # 编码存在但候选中无匹配
REJECT_SEMANTIC_CONFLICT = "SEMANTIC_CONFLICT"  # 语义惩罚阻断所有候选
REJECT_LOW_LCS = "LOW_LCS"                     # LCS 得分过低 (score<2 或 lcs<2)
REJECT_PREFIX_MISMATCH = "PREFIX_MISMATCH"     # 前缀模糊匹配失败
REJECT_AREA_SURFACE = "AREA_SURFACE"           # area 语义为地面，不匹配井下候选
SKIP_LOW_CONFIDENCE = "LOW_CONFIDENCE"         # 置信度低，宁缺毋滥已过滤


def _filter_low_confidence(results: list, include_low: bool = False) -> tuple:
    """将低置信度匹配从回写列表中过滤出来。

    Returns:
        (writeback_results, skipped_results)
        writeback_results: 应回写的结果（高+中，或全部如果 include_low=True）
        skipped_results: 被过滤掉的结果（仅当 not include_low 时非空）
    """
    if include_low:
        return results, []
    writeback = []
    skipped = []
    for r in results:
        if r.get("confidence") == "低":
            skipped.append(r)
        else:
            writeback.append(r)
    return writeback, skipped


# ── 坐标分配规则表 ──────────────────────────────────────────────────
# 各表对应 _assign_distances 的 5→7 步回退策略，修改时请同步更新 _RULES_REGISTRY。

# 关键词→区间比例（多设备分布区间，单设备用 _KEYWORD_SINGLE_RATIO）
_KEYWORD_ZONE_RULES = {
    "迎头":      (0.00, 0.15),  # AQ 1029-2019 6.3.1
    "回风流":    (0.85, 1.00),  # AQ 1029-2019 6.3.1
    "井口":      (0.00, 0.10),  # MT/T 1198-2023 §5.1.2 / DB51T1412-2011 5.1.8.1
    "入口":      (0.00, 0.10),  # 巷道入口处
    "井底":      (0.90, 1.00),  # DB51T1412-2011 5.1.8.1
    "岔口":      (0.10, 0.25),  # MT/T 1198-2023 §5.1.3 / DB51T1412-2011 5.1.8.2
    "硐室":      (0.40, 0.60),  # MT/T 1198-2023 §5.2.4 / DB51T1412-2011 5.1.8.5
    "充电站":    (0.40, 0.60),  # MT/T 1198-2023 §5.1.4
    "机头":      (0.00, 0.10),  # MT/T 1201.6-2023 A.1
    "机尾":      (0.90, 1.00),  # MT/T 1201.6-2023 A.1
    "转载点":    (0.00, 0.15),  # MT/T 1201.6-2023 A.1
    "中部":      (0.40, 0.60),  # MT/T 1201.6-2023 A.1#16
    "超前支护":  (0.00, 0.15),  # MT/T 1201.6-2023 A.1#3
    "T2处":      (0.85, 1.00),  # 回风口附近
    "支架":      (0.30, 0.70),  # MT/T 1201.6-2023 A.1#1
    "煤仓":      (0.30, 0.70),  # MT/T 1201.6-2023 A.1#20
    "车场":      (0.30, 0.70),  # 车场区域全景
    "地面":      (0.30, 0.70),  # 地面设施居中
}

# 关键词→精确比例（单设备 count≤1 时使用，终值 = line_length × ratio）
_KEYWORD_SINGLE_RATIO = {
    "迎头":      0.0,    # 掘进迎头起点
    "回风流":    1.0,    # 回风流终点
    "井口":      0.05,   # 井口处
    "入口":      0.0,    # 巷道入口起点
    "井底":      0.95,   # 井底处
    "硐室":      0.5,    # 硐室中部
    "充电站":    0.5,    # 充电站中部
    "机头":      0.0,    # 机头起点
    "机尾":      1.0,    # 机尾终点
    "转载点":    0.05,   # 转载点起点附近
    "中部":      0.5,    # 中部点
    "超前支护":  0.05,   # 超前支护起点附近
    "T2处":      0.95,   # T2处终点附近
    "支架":      0.5,    # 支架中部
    "煤仓":      0.5,    # 煤仓中部
    "车场":      0.5,    # 车场中部
    "地面":      0.5,    # 地面中部
}

# sensor_type 默认区间（多设备回退，单设备用 _SENSOR_SINGLE_RATIO）
_SENSOR_DEFAULT_ZONES = {
    "风速":      (0.40, 0.60),  # 测风站
    "烟雾":      (0.00, 0.20),  # 产尘点（皮带机头）
    "粉尘":      (0.00, 0.20),  # 产尘点
    "温度":      (0.30, 0.70),  # 设备上方
    "人员定位":  (0.40, 0.60),  # DB51T1412-2011 5.1.8.2: 长巷道中部
}
_DEFAULT_ZONE = (0.10, 0.90)

# sensor_type 精确比例（单设备 count≤1 回退）
_SENSOR_SINGLE_RATIO = {
    "风速":      0.5,
    "烟雾":      0.0,
    "粉尘":      0.0,
    "温度":      0.5,
    "人员定位":  0.5,
}

# 关键词分类表（按匹配优先级降序，长字符串在前避免短字符串误匹配）
_CLASSIFY_KEYWORD_TABLE = [
    ("机头及转载点", "机头"),
    ("机尾及转载机", "机尾"),
    ("皮带中部",     "中部"),
    ("输送机中部",   "中部"),
    ("T2传感器",     "T2处"),
    ("T2处",         "T2处"),
    ("避难硐室",     "硐室"),
    ("空压机房",     "硐室"),
    ("空压机站",     "硐室"),
    ("提升机房",     "硐室"),
    ("通风机房",     "硐室"),
    ("灌浆站",       "硐室"),
    ("注浆站",       "硐室"),
    ("充填站",       "硐室"),
    ("瓦斯抽采",     "硐室"),
    ("瓦斯泵",       "硐室"),
    ("水泵房",       "硐室"),
    ("变电所",       "硐室"),
    ("绞车房",       "硐室"),
    ("调度室",       "硐室"),
    ("坑木场",       "硐室"),
    ("制氮",         "硐室"),
    ("注氮",         "硐室"),
    ("工业广场",     "地面"),
    ("迎头",         "迎头"),
    ("回风流",       "回风流"),
    ("入口处",       "入口"),
    ("井口",         "井口"),
    ("井底",         "井底"),
    ("充电站",       "充电站"),
    ("机头",         "机头"),
    ("机尾",         "机尾"),
    ("转载点",       "转载点"),
    ("超前支护",     "超前支护"),
    ("支架",         "支架"),
    ("硐室",         "硐室"),
    ("煤仓",         "煤仓"),
    ("车场",         "车场"),
    ("煤场",         "地面"),
]


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
    """提取工作面/地点编码，如 C8302、9209、F1302、-490、-725，支持中文数字（九采区→9）。

    优先级:字母+数字 > 水平标高 > 4 位纯数字 > 中文数字采区 > 3 位纯数字。
    中文数字降为兜底:形如 `一采区5402095P80F1302皮顺` 应当返回 `F1302` 而非 `1`。"""
    # 1. 字母+数字格式（如 C8302、F1302）— 最具体,最高优先级
    m = re.search(r'([A-Z]\d{3,4})', description)
    if m:
        return m.group(1)
    # 2. -490、-725 等水平标高
    m = re.search(r'(-\d{3,4})', description)
    if m:
        return m.group(1)
    # 3-6. 纯数字编号：排除分站编号、电压值、距离值等常见误提取上下文
    # 先收集所有 3-5 位数字，按长度降序排列，排除重叠范围和问题上下文后返回
    all_matches = []
    for m in re.finditer(r'(?<![A-Z])\d{3,5}(?![A-Z])', description):
        all_matches.append((m.group(), m.start(), m.end()))
    # 按长度降序、位置升序排列
    all_matches.sort(key=lambda x: (-len(x[0]), x[1]))
    excluded_ranges = []
    for num, start, end in all_matches:
        # 若与已排除的更长数字范围重叠，跳过（避免 1140v 被跳过后 114 被匹配）
        if any(start < e and end > s for s, e in excluded_ranges):
            continue
        prefix = description[max(0, start-3):start]
        suffix = description[end:min(len(description), end+3)]
        # 排除分站编号上下文（如 130分站、分站130）
        if '分站' in prefix or '分站' in suffix:
            excluded_ranges.append((start, end))
            continue
        # 排除电压值（如 1140v、660V、380伏）
        if re.match(r'[vV伏]', description[end:end+1]):
            excluded_ranges.append((start, end))
            continue
        # 排除距离值（如 600米、300m）— B15 人员定位常用格式
        if re.match(r'[米mM]', description[end:end+1]):
            excluded_ranges.append((start, end))
            continue
        # 排除传感器通道编号（如 "传感器903"、"传感器温度903"、"温度传感器903"）
        if '传感器' in description[max(0, start-5):start]:
            excluded_ranges.append((start, end))
            continue
        return num
    # 7. 中文数字 + 采区/煤层/盘区/水平 (兜底)
    for cn_char, digit in _CN_NUMERALS.items():
        pattern = re.escape(cn_char) + r'(采区|煤层|盘区|水平)'
        if re.search(pattern, description):
            return digit
    return None


def extract_explicit_distance(description: str) -> float or None:
    """从描述中提取显式距离（米），如 '2730米' → 2730.0, '10米_人数' → 10.0。
    支持中文小数记法：'50米2' = 50.2米, '10米5' = 10.5米。
    用于精确位置计算，优先级高于所有分类规则。
    对方向偏移型（'东80米'）仍提取距离，但标注为方向型需要二次处理。
    """
    # 0. 中文小数：'50米2' = 50.2米（米后面一位数字是小数，后接结束符或_/)处）
    m = re.search(r'(\d{2,})\s*米\s*(\d)\s*(?:_|\)|处|$)', description)
    if m:
        return float(f"{m.group(1)}.{m.group(2)}")
    # 1. 匹配 'NN米' 模式，距离数字 ≥2 位（避免匹配楼层号如 "10米2"中 "10米"）
    m = re.search(r'(\d{2,})\s*米', description)
    if m:
        return float(m.group(1))
    # 2. 匹配 'N米_'/'N米)' 等 1 位数字但当距离用（如 '口5米_', '10米_', '15米处', '20米)'）
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
            # 若描述已明确包含候选名主体(LCS>=50%)，豁免设备类型关键词的惩罚
            # 避免"中央回风大巷皮带机头CO"因"皮带/机头"被错误惩罚
            if keyword in ("皮带", "机头", "压带轮", "卸料器"):
                lcs = max(longest_common_substring_len(cv, candidate_name) for cv in _expand_aliases(description))
                if lcs >= len(candidate_name) * 0.5:
                    continue
            # 特殊放宽："皮带机头硐室"等特定硐室地点，不应被运输设备关键词泛化拦截
            if keyword in ("皮带", "机头", "压带轮", "卸料器") and "硐室" in description:
                continue
            # 皮带/机头→辅运豁免：皮带输送机属辅运系统，"辅运"巷道不应被运输设备关键词惩罚
            if keyword in ("皮带", "机头") and "辅运" in candidate_name:
                continue
            if not any(allow in candidate_name for allow in rule["allow"]):
                return rule["penalty"]
    return 0


# 地点锚定词正则 — 描述中出现这些地点限定词时，候选必须包含相同的地点词，否则硬性拒绝
_AREA_ANCHOR_RE = re.compile(
    r'([一二三四五六七八九十百千\d]+采区|'  # 六采区、一采区等
    r'[东西南北]翼|'  # 西翼、北翼、东翼、南翼
    r'暗斜井|'  # 金河煤矿特定巷道
    r'新平硐|新平峒|'  # 金河煤矿新平硐区域（峒=硐异体字）
    r'哈拉沟|马蹄沟|马蹄坡|'  # 特定地名（矿特有，后续按需扩展）
    r'交岔点)',  # 巷道交岔点（特定地点）
    re.UNICODE,
)


def _has_hard_semantic_conflict(description: str, candidate_name: str) -> bool:
    """硬性语义冲突检测。
    当描述中有明确的地点限定词或功能词，而候选中缺少对应词或含冲突词时，
    直接拒绝匹配，避免编码/LCS 命中掩盖语义错误（宁缺毋滥）。
    """
    if not description or not candidate_name:
        return False

    # 0. 路标豁免：描述包含该巷道的路标 → 不触发语义冲突
    if _LANDMARKS and candidate_name in _LANDMARKS:
        for landmark_name in _LANDMARKS[candidate_name]:
            if landmark_name in description:
                return False

    # 0b. 传感器路标豁免：描述包含传感器路标 → 不触发语义冲突
    if _SENSOR_LANDMARKS and candidate_name in _SENSOR_LANDMARKS:
        sl_ratio, _, _ = _find_sensor_landmark_ratio(description, candidate_name)
        if sl_ratio is not None:
            return False

    # 1. 地点锚定词冲突（双向）
    # 描述含地点限定词 → 候选必须含相同词
    for match in _AREA_ANCHOR_RE.finditer(description):
        anchor = match.group(0)
        if anchor and anchor not in candidate_name:
            return True
    # 候选含地点限定词 → 描述也必须含相同词
    for match in _AREA_ANCHOR_RE.finditer(candidate_name):
        anchor = match.group(0)
        if anchor and anchor not in description:
            return True

    # 2. 功能互斥冲突
    # 底抽 vs 回风/进风/皮顺/胶运
    if "底抽" in description and "底抽" not in candidate_name:
        if any(kw in candidate_name for kw in ["回风", "进风", "皮顺", "胶运"]):
            return True
    # 回风 vs 进风/底抽/胶运
    if "回风" in description and "回风" not in candidate_name:
        if any(kw in candidate_name for kw in ["进风", "底抽", "胶运"]):
            return True
    # 进风 vs 回风/底抽/皮顺
    if "进风" in description and "进风" not in candidate_name:
        if any(kw in candidate_name for kw in ["回风", "底抽", "皮顺"]):
            return True
    # 溜子/刮板输送机应在工作面/切眼内，不应匹配到停采线/回风/进风/施工/联巷等边界巷道
    if "溜子" in description and "溜子" not in candidate_name:
        if any(kw in candidate_name for kw in ["停采线", "回风", "进风", "施工", "联巷", "绕道", "石门"]):
            return True
    # 转载机应在工作面/顺槽内，不应匹配到停采线/施工/联巷
    if "转载机" in description and "转载机" not in candidate_name:
        if any(kw in candidate_name for kw in ["停采线", "施工", "联巷", "绕道"]):
            return True

    # 石门 vs 非石门：描述含"石门"但候选不含"石门"时拒绝（石门是特定巷道类型）
    if "石门" in description and "石门" not in candidate_name:
        return True

    # 硐室同名异址：描述含"X硐室"但候选是"Y硐室"（X≠Y）
    # 避免"机头硐室"匹配到"永久避难硐室"，"单轨吊充电检修硐室"匹配到"架空乘人器硐室"
    desc_cave = re.search(r'(.+?)硐室', description)
    cand_cave = re.search(r'(.+?)硐室', candidate_name)
    if desc_cave and cand_cave:
        desc_prefix = desc_cave.group(1)
        cand_prefix = cand_cave.group(1)
        if (desc_prefix and cand_prefix
                and len(desc_prefix) >= 2 and len(cand_prefix) >= 2
                and desc_prefix != cand_prefix
                and cand_prefix not in desc_prefix
                and desc_prefix not in cand_prefix):
            # 豁免：前缀公共子串足够长（如"架空乘人装置"与"架空乘人器"LCS=4）
            prefix_lcs = longest_common_substring_len(desc_prefix, cand_prefix)
            if prefix_lcs < 3:
                return True

    # 4. 同名异址冲突：描述含"X联络巷"但候选是"Y联络巷"（X≠Y）
    # 避免"中部通风联络巷"匹配到"辅运联络巷"，"大巷联络巷"匹配到"主斜井机尾联络巷"
    # 允许描述前缀包含候选前缀（如"天宝公司-辅运"包含"辅运"）
    desc_contact = re.search(r'(.+?)(?:联络巷|联巷)', description)
    cand_contact = re.search(r'(.+?)(?:联络巷|联巷)', candidate_name)
    if desc_contact and cand_contact:
        desc_prefix = desc_contact.group(1)
        cand_prefix = cand_contact.group(1)
        if (desc_prefix and cand_prefix
                and len(desc_prefix) >= 2 and len(cand_prefix) >= 2
                and desc_prefix != cand_prefix
                and cand_prefix not in desc_prefix
                and desc_prefix not in cand_prefix):
            return True

    # 5. 通用巷道类型名冲突：描述和候选含同一通用类型名但限定语不同 → REJECT
    # 避免"保安煤矿皮带大巷"匹配到"沿9号煤皮带大巷"(LCS=4仅在通用"皮带大巷"上)，
    # 或"保安煤矿轨道斜巷"匹配到"9号煤轨道斜巷"等。类似联络巷同名异址规则。
    for _tt_pattern in [r'(.+?)大巷', r'(.+?)斜巷', r'(.+?)顺槽',
                        r'(.+?)(?:底抽巷|高抽巷)', r'(.+?)回风巷',
                        r'(.+?)进风巷', r'(.+?)运输巷']:
        desc_tt = re.search(_tt_pattern, description)
        cand_tt = re.search(_tt_pattern, candidate_name)
        if desc_tt and cand_tt:
            desc_prefix_tt = desc_tt.group(1).strip()
            cand_prefix_tt = cand_tt.group(1).strip()
            if (desc_prefix_tt and cand_prefix_tt
                    and len(desc_prefix_tt) >= 2 and len(cand_prefix_tt) >= 2
                    and desc_prefix_tt != cand_prefix_tt
                    and cand_prefix_tt not in desc_prefix_tt
                    and desc_prefix_tt not in cand_prefix_tt):
                return True

    # 7. 反向编码约束：候选含 specific code（如 8301/6301/15103）但描述完全不含 → REJECT
    # 避免"三部强力皮带"靠"皮带"短 LCS 蹭到"8301皮带顺槽"等带工作面编码的候选。
    # specific code = 3+位纯数字、字母+数字（C8302/F1302）、负数水平（-650）
    cand_codes = re.findall(r'(?:-?\d{3,}|[A-Za-z]\d{2,})', candidate_name)
    cand_codes = [c for c in cand_codes if (any(ch.isalpha() for ch in c) or len(c.lstrip('-')) >= 3)]
    if cand_codes:
        desc_has_any = False
        for code in cand_codes:
            if any(ch.isalpha() for ch in code) or code.startswith('-'):
                if code in description:
                    desc_has_any = True
                    break
            else:
                if re.search(r'(?<!\d)' + re.escape(code) + r'(?!\d)', description):
                    desc_has_any = True
                    break
        if not desc_has_any:
            return True

    # 8. 地点语义硬性冲突：_LOCATION_SEMANTICS 中的规则直接拒绝匹配
    for keyword, rule in _LOCATION_SEMANTICS.items():
        if keyword in description:
            # 若描述已明确包含候选名主体(LCS>=50%)，豁免设备类型关键词的硬性拒绝
            if keyword in ("皮带", "机头", "压带轮", "卸料器"):
                lcs = max(longest_common_substring_len(cv, candidate_name) for cv in _expand_aliases(description))
                if lcs >= len(candidate_name) * 0.5:
                    continue
            # 特殊放宽："皮带机头硐室"等特定硐室地点，不应被运输设备关键词泛化拦截
            if keyword in ("皮带", "机头", "压带轮", "卸料器") and "硐室" in description:
                continue
            # 皮带/机头→辅运豁免：皮带输送机属辅运系统，"辅运"巷道不应被运输设备关键词惩罚
            if keyword in ("皮带", "机头") and "辅运" in candidate_name:
                continue
            if not any(allow in candidate_name for allow in rule["allow"]):
                return True

    # 9. 轨顺/皮顺→联络巷冲突：描述说"轨顺"/"皮顺"但未说"联络巷/联巷"，
    # 候选却是"X轨顺联络巷"/"X皮顺联络巷"时拒绝。
    # 轨顺/皮顺是主要运输巷道，联络巷是连接巷道 → 不同地点
    for token in ("轨顺", "皮顺"):
        if token in description and "联络巷" not in description and "联巷" not in description:
            if re.search(f'{token}联[络]?巷', candidate_name):
                return True

    # 10. 支架工作面约束：描述含"工作面\d+[#]?架"(液压支架编号)但候选不含工作面关键词
    # 液压支架位置必须在回采工作面/切眼/停采线上，不应匹配到普通巷道或联络巷
    stent_match = re.search(r'工作面\s*\d+\s*[#]?\s*架', description)
    if stent_match and not any(kw in candidate_name for kw in ["工作面", "切眼", "停采"]):
        return True

    # 11. 轨顺/皮顺类型互斥：描述说"皮顺"(皮带顺槽)但候选是"轨顺"(轨道顺槽)，反之亦然
    # 皮带顺槽和轨道顺槽是完全不同的巷道类型，即使编码相同也不应匹配
    if "皮顺" in description and "轨顺" in candidate_name:
        return True
    if "轨顺" in description and "皮顺" in candidate_name:
        return True
    # 12. 轨顺/皮顺→切眼冲突：描述说"轨顺"/"皮顺"（运输顺槽）但候选是"切眼"（工作面），不同巷道类型
    if any(t in description for t in ("轨顺", "皮顺", "轨道顺槽", "皮带顺槽")) and "切眼" in candidate_name and "切眼" not in description:
        return True

    return False


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


def _code_in_name(code: str, name: str) -> bool:
    """编码是否在候选名中。
    纯数字编码使用边界匹配 (?<!\\d)code(?!\\d)，避免单字符 '7' 误命中 '7300皮顺'。
    字母数字/负数编码使用直接子串匹配。
    """
    if not code or not name:
        return False
    if any(c.isalpha() for c in code) or code.startswith('-'):
        return code in name
    if code.isdigit():
        return bool(re.search(r'(?<!\d)' + re.escape(code) + r'(?!\d)', name))
    return code in name


def _is_specific_code(code: str) -> bool:
    """判断 device_code 是否足够具体以触发硬过滤（缺失即拒绝）。
    含字母（C8302/F1302）、负数水平（-490）、3+ 位纯数字（9308/7302/920）视为具体。
    单字符数字（来自'七采区'->7）视为非具体，仅做软加分。"""
    if not code:
        return False
    if any(c.isalpha() for c in code):
        return True
    digits = code.lstrip('-')
    return digits.isdigit() and len(digits) >= 3


def _is_generic_code(code: str, code_to_candidates: dict, candidates: list) -> bool:
    """判断 device_code 是否为通用区域前缀（如 1460 对应变电所/错车场/运输大巷/水泵房等）。
    通用前缀在多个不同地点类型中出现，不应作为强匹配依据。
    含字母或负数的编码、5 位及以上纯数字（工作面编号）视为特定编码；
    3-4 位纯数字且对应超过 3 个不同候选名的视为通用前缀。"""
    if not code or not code.isdigit():
        return False
    # 5 位及以上纯数字通常是特定工作面/巷道编号（如 17216、16120），不视为通用前缀
    if len(code) >= 5:
        return False
    indices = code_to_candidates.get(code, [])
    if len(indices) <= 3:
        return False
    unique_names = {candidates[i].get("name", "") for i in indices}
    return len(unique_names) > 3


def _score_candidates(cleaned: str, candidates: list, sensor_type: str = None,
                      device_code: str = None, code_to_candidates: dict = None,
                      prefix_to_candidates: dict = None,
                      coalbed_map: dict = None, mark_type: str = None) -> list:
    """
    对 candidates 逐一打分并分层，返回完整排序列表。
    每项为 {"candidate": cand, "name": str, "lcs": int, "score": int, "layer": int, "pref_count": int, "idx": int}。
    """
    scored = []
    code_indices = set(code_to_candidates.get(device_code, [])) if code_to_candidates and device_code else set()
    prefix_indices = set()
    if prefix_to_candidates and device_code:
        prefix_indices = set(prefix_to_candidates.get(device_code, []))
    device_coalbed = coalbed_map.get(device_code, "") if coalbed_map and device_code else ""
    cleaned_variants = _expand_aliases(cleaned)

    # 清理上设备残留在 candidate 上的 _sensor_landmark（避免跨设备共享污染）
    for c in candidates:
        c.pop("_sensor_landmark", None)

    # 特定编码硬过滤：device_code 足够具体（3+位数字/含字母/负数水平），
    # 但任何候选名/tunnelId 都不包含该编码 → 全部标 REJECT，由上层归为 CODE_MISMATCH。
    # 注意：若编码仅出现在被语义冲突阻断的候选中，仍视为缺失（宁缺毋滥）。
    specific_code_missing = False
    if device_code and _is_specific_code(device_code):
        has_anywhere = False
        for c in candidates:
            name = c.get("name") or ""
            if (_code_in_name(device_code, name) or device_code in (c.get("tunnelId") or "")):
                if not _has_hard_semantic_conflict(cleaned, name):
                    has_anywhere = True
                    break
        if not has_anywhere and prefix_to_candidates and device_code:
            for idx in prefix_to_candidates.get(device_code, []):
                if 0 <= idx < len(candidates):
                    name = candidates[idx].get("name") or ""
                    if not _has_hard_semantic_conflict(cleaned, name):
                        has_anywhere = True
                        break
        if not has_anywhere:
            specific_code_missing = True

    for cand in candidates:
        name = cand.get("name") or ""
        if not name:
            continue
        lcs_len = max(
            longest_common_substring_len(cv, name)
            for cv in cleaned_variants
        )
        score = round(lcs_len * 10 / len(name)) if name else 0

        # 路标匹配加分：描述包含该巷道的路标 → 强加分，帮助低 LCS 匹配
        # 支持组合路标部分匹配（如 "CH4（T2)" 中的 "CH4" 匹配 "总回风CH4"）
        if _LANDMARKS and name in _LANDMARKS:
            norm_clean = _norm_lm(cleaned)
            for landmark_name in _LANDMARKS[name]:
                norm_lm = _norm_lm(landmark_name)
                matched = norm_lm in norm_clean
                if not matched and '、' in norm_lm:
                    matched = any(p in norm_clean for p in norm_lm.split('、') if len(p) >= 2)
                # T 标识路标：无 T 设备允许部分匹配（如 CH4 匹配 CH4(T2)）
                if not matched and _has_t_id(norm_lm):
                    sensor_part = _strip_t_id(norm_lm)
                    if sensor_part and sensor_part in norm_clean:
                        matched = True
                if matched:
                    score += 8  # 强加分，使路标匹配的设备得分超过语义惩罚
                    break

        # 传感器路标匹配加分
        if _SENSOR_LANDMARKS and name in _SENSOR_LANDMARKS:
            sl_ratio, sl_sensor_type, sl_confidence = _find_sensor_landmark_ratio(cleaned, name, sensor_type, t_keyword=None)
            if sl_ratio is not None:
                if sl_confidence == "exact":
                    score += 10  # 标识+类型都匹配 → 最强加分
                elif sl_confidence == "partial":
                    score += 5   # 仅标识匹配 → 中等加分
                elif sl_confidence == "type_match":
                    score += 3   # sensor_type 一致但无标识 → 弱加分
                # 将传感器路标信息附加到 candidate，供后续定位使用
                cand["_sensor_landmark"] = {
                    "ratio": sl_ratio,
                    "sensor_type": sl_sensor_type,
                    "confidence": sl_confidence,
                }

        # sensor_type 巷道偏好加权
        if sensor_type and lcs_len >= 2 and _candidate_matches_sensor_pref(name, sensor_type):
            score += 2

        # 编码精确命中（纯数字使用边界匹配，避免 '7' 误中 '7300皮顺'）
        # 通用前缀（如 1460 对应变电所/错车场/运输大巷/水泵房等多个地点）降级处理
        code_hit = _code_in_name(device_code, name) if device_code else False
        if code_hit:
            if code_to_candidates and candidates and _is_generic_code(device_code, code_to_candidates, candidates):
                score += 1  # 通用前缀只给 +1，避免短名称候选（如 1460变电所）靠编码压倒语义匹配
            else:
                score += 5

        # 前缀模糊编码匹配（仅纯数字编码做前缀匹配，且需是更长候选码的前缀）
        prefix_hit = False
        if (not code_hit and device_code and len(device_code) >= 2
                and device_code.lstrip('-').isdigit()):
            codes_in_name = re.findall(r'\d{3,4}', name)
            for code_in_name in codes_in_name:
                if code_in_name.startswith(device_code) and code_in_name != device_code:
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

        # 总回风加分：描述含"总回风"且候选名含"回风"时加分
        if "总回风" in cleaned and "回风" in name:
            score += 3

        # 皮带→辅运加分：描述含"皮带"且候选名含"辅运"时加分
        # 皮带输送机属于辅助运输系统，应匹配到辅运类巷道
        if "皮带" in cleaned and "辅运" in name:
            score += 8

        # coalbed 验证惩罚（跨煤层不匹配）
        if device_coalbed and cand.get("coalbed"):
            if cand["coalbed"] != device_coalbed:
                score -= 1

        # 语义过滤惩罚
        sem_penalty = _semantic_penalty(cleaned, name, mark_type)
        score += sem_penalty

        # 特定编码强制约束：specific code 必须在候选名或 tunnelId 中出现
        # 避免 "1460排矸" 匹配到不含 1460 的 "排矸"（短名称依赖+LCS 膨胀）
        if device_code and _is_specific_code(device_code):
            if not _code_in_name(device_code, name) and device_code not in (cand.get("tunnelId") or ""):
                score -= 20

        # 通用前缀检测（用于分层判定）
        is_generic = (code_to_candidates and candidates
                      and _is_generic_code(device_code, code_to_candidates, candidates))

        # 硬性语义冲突：地点/功能词不一致时直接拒绝，宁缺毋滥
        has_conflict = _has_hard_semantic_conflict(cleaned, name)

        # 额外语义检查：描述含"<code>工作面"且候选类型是普通巷道（非工作面类型）
        # 避免"6302工作面33#架"错误匹配到"6302轨顺联络巷"(type=0-普通巷道)
        # 当上游数据缺少工作面时保持宁缺毋滥，不降级匹配到普通巷道
        if not has_conflict:
            wf_m = re.search(r'(\d{3,})\s*工作面', cleaned)
            if wf_m:
                wf_code = wf_m.group(1)
                cand_type = cand.get("type", "")
                if wf_code in name and cand_type.split("-")[0] == "0":
                    has_conflict = True

        # 分层判定
        idx = candidates.index(cand)
        if has_conflict:
            layer = _MATCH_LAYER_REJECT
        elif specific_code_missing:
            # 具体编码在候选池中完全缺失 → 强制 REJECT
            layer = _MATCH_LAYER_REJECT
        elif not is_generic and (code_hit or idx in code_indices):
            # 特定编码精确命中进入 EXACT；通用前缀不升层，避免短名称候选靠编码压倒语义
            layer = _MATCH_LAYER_EXACT if lcs_len >= 1 else _MATCH_LAYER_LCS_PREF
        elif (prefix_hit or idx in prefix_indices or score >= (7 if lcs_len < 3 else 5)) and lcs_len >= 2:
            # LCS=2 的匹配需更高分值（≥7）才给予中置信度（含 sensor_type 偏好加分后达标），
            # 避免"轨道"等短 LCS 蹭 sensor_type 偏好蒙上中匹配。
            layer = _MATCH_LAYER_LCS_PREF
        elif lcs_len >= 2 and score >= 2:
            layer = _MATCH_LAYER_LOW
        # 功能词匹配豁免：描述含明确功能关键词且候选对应时，降低 LCS 门槛
        # 如"皮带"→"辅运"（皮带机属于辅助运输系统）
        elif score >= 2 and "皮带" in cleaned and "辅运" in name:
            layer = _MATCH_LAYER_LOW
        else:
            layer = _MATCH_LAYER_REJECT

        pref_count = _count_sensor_pref_matches(name, sensor_type) if sensor_type else 0

        # 功能词-巷道类型可疑检测
        suspicious, suspicious_reason = _check_functional_conflict(cleaned, name)

        scored.append({
            "candidate": cand, "name": name, "lcs": lcs_len,
            "score": score, "layer": layer, "pref_count": pref_count, "idx": idx,
            "suspicious": suspicious, "suspicious_reason": suspicious_reason,
        })
    return scored


def find_best_match(cleaned: str, candidates: list, sensor_type: str = None,
                     device_code: str = None, code_to_candidates: dict = None,
                     prefix_to_candidates: dict = None,
                     coalbed_map: dict = None, mark_type: str = None):
    """
    在 candidates 中找最佳匹配项（分层策略）。
    评分规则：LCS(别名扩展后) + sensor_type 加权 + 编码匹配(含前缀模糊) + 巷道类型匹配 + 语义过滤 + coalbed 验证。
    返回 (best_dict, all_scored_list)。best_dict 为最佳匹配或 None。
    """
    scored = _score_candidates(
        cleaned, candidates, sensor_type=sensor_type,
        device_code=device_code, code_to_candidates=code_to_candidates,
        prefix_to_candidates=prefix_to_candidates,
        coalbed_map=coalbed_map, mark_type=mark_type,
    )
    filtered = [s for s in scored if s["layer"] != _MATCH_LAYER_REJECT]
    if not filtered:
        return None, scored

    # 优先选 EXACT 层（编码精确命中）候选，避免高 LCS 但无编码命中的候选击败它们。
    # 若无 EXACT 候选，再在 LCS_PREF/LOW 中按分数选最高。
    exact_pool = [s for s in filtered if s["layer"] == _MATCH_LAYER_EXACT]
    pool = exact_pool if exact_pool else filtered

    best_score = max(s["score"] for s in pool)
    tied = [s for s in pool if s["score"] == best_score]

    code_indices = set(code_to_candidates.get(device_code, [])) if code_to_candidates and device_code else set()
    best = max(tied, key=lambda s: (s["idx"] in code_indices, s["lcs"], -len(s["name"]), s["pref_count"]))

    best_out = {
        "name": best["name"], "lcs": best["lcs"], "score": best["score"],
        "candidate": best["candidate"], "layer": best["layer"],
        "_pref_count": best["pref_count"],
        "suspicious": best.get("suspicious", False),
        "suspicious_reason": best.get("suspicious_reason"),
    }
    return best_out, scored


def _format_top_candidates(scored: list, n: int = 3) -> list:
    """从 scored 列表中提取 Top-N 候选的精简信息。"""
    if not scored:
        return []
    sorted_scored = sorted(scored, key=lambda s: s["score"], reverse=True)
    top = []
    for s in sorted_scored[:n]:
        layer_cn = "高" if s["layer"] == _MATCH_LAYER_EXACT else (
            "中" if s["layer"] == _MATCH_LAYER_LCS_PREF else (
            "低" if s["layer"] == _MATCH_LAYER_LOW else "拒绝"))
        top.append({
            "name": s["name"],
            "score": s["score"],
            "confidence": layer_cn,
            "tunnel_type": s["candidate"].get("type", ""),
            "lcs": s["lcs"],
        })
    return top


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


# ── 折线几何工具（模块级，供路标和传感器路标复用）────────────────────

def _poly_len_2d(line):
    """计算折线总长度（2D）。"""
    length = 0.0
    for i in range(len(line) - 1):
        x1, y1 = line[i].get('x'), line[i].get('y')
        x2, y2 = line[i + 1].get('x'), line[i + 1].get('y')
        if None in (x1, y1, x2, y2):
            continue
        length += math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
    return length


def _project_ratio_2d(px, py, line):
    """计算点到折线的投影比例和距离。"""
    best_dist = float('inf')
    best_ratio = 0.0
    cum_len = 0.0
    total_len = _poly_len_2d(line)
    if total_len <= 0:
        return 0.0, float('inf')

    for i in range(len(line) - 1):
        x1, y1 = line[i].get('x'), line[i].get('y')
        x2, y2 = line[i + 1].get('x'), line[i + 1].get('y')
        if None in (x1, y1, x2, y2):
            continue

        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq == 0:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / seg_len_sq))

        nx = x1 + t * dx
        ny = y1 + t * dy
        dist = math.sqrt((px - nx) ** 2 + (py - ny) ** 2)
        seg_len = math.sqrt(seg_len_sq)

        if dist < best_dist:
            best_dist = dist
            best_ratio = (cum_len + t * seg_len) / total_len

        cum_len += seg_len

    return best_ratio, best_dist


# ── CAD 路标定位系统 ─────────────────────────────────────────────────
# 从 CAD 标注数据提取有意义的地点名称，计算其在巷道折线上的投影比例，
# 当设备描述包含路标名称时，直接定位到路标位置（替代默认区间规则）。

_LANDMARKS = {}      # {tunnel_name: {landmark_name: ratio, ...}}
_LANDMARK_IDS = {}   # {tunnel_name: {landmark_name: cad_item_id}} — 路标→CAD原始ID映射

# ── 路标匹配归一化工具 ──────────────────────────────────────────────
_TRAIL_PUNCT = '。，.,'

def _norm_lm(s: str) -> str:
    """归一化路标名：全角括号→半角，移除空格，去除末尾标点"""
    return s.replace('（', '(').replace('）', ')').replace(' ', '').rstrip(_TRAIL_PUNCT)

def _has_t_id(name: str) -> bool:
    """检查名称是否包含 T 标识（T0/T1/T2/T4）"""
    return any(t in name for t in ('T0', 'T1', 'T2', 'T4'))

def _strip_t_id(name: str) -> str:
    """移除 T 标识，返回传感器主体部分（如 CH4(T2) → CH4）"""
    return re.sub(r'T[0124]', '', name).strip('()（）')

# ── CAD 传感器标识组合系统 ────────────────────────────────────────────
# 将 CAD 图纸上打散的传感器标识（如 CH+4→CH4, T+CH+4→TCH4）聚合成完整标识，
# 作为传感器路标辅助设备匹配和精确定位。

# Unicode 下标数字 → 普通数字
_SUBSCRIPT_DIGITS = {
    '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
    '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
}

# ── 传感器标识发现系统 ────────────────────────────────────────────────
# 从输入数据（设备描述 + CAD 标注）动态学习传感器标识→类型映射，
# 避免硬编码列表遗漏新标识（如 YW、YWD 等）。
#
# _SENSOR_ID_MAP: 运行时动态构建的 {标识: sensor_type} 映射
# _BASE_SENSOR_ID_MAP: 硬编码 fallback（化学式、已知缩写）
# _UNKNOWN_SENSOR_IDS: 尚未能推断 sensor_type 的标识集合（等待设备数据补充）

_SENSOR_ID_MAP = {}          # 动态 {sensor_id: sensor_type}（自学习 + fallback）
_UNKNOWN_SENSOR_IDS = set()  # 已发现但未推断出类型的标识（暂时不参与定位）

# 硬编码基础映射（fallback — 化学式和已知标准缩写）
# 运行时 _build_sensor_id_map() 会从这里拷贝然后叠加自学习结果
_BASE_SENSOR_ID_MAP = {
    # 化学拆分模式
    "CH4": "瓦斯",    "CO": "一氧化碳",    "CO2": "二氧化碳",
    "O2": "氧气",     "H2": "氢气",         "NO2": "二氧化氮",
    "SO2": "二氧化硫", "NO": "一氧化氮",    "SO": "二氧化硫前缀",
    # T 前缀模式
    "TCH4": "瓦斯",   "TCO": "一氧化碳",   "TCO2": "二氧化碳",
    "TO2": "氧气",    "TH2": "氢气",        "Tt": "温度",
    "Tw": "瓦斯",     "Tv": "风速",         "TD": "断电",
    "Tf": "粉尘",
    # 拼音/英文缩写（预置常见缩写）
    "YW": "烟雾",     "YWD": "烟雾",
    # 中文传感器标识
    "烟雾": "烟雾",   "风筒": "风筒",       "风速": "风速",
    "温度": "温度",   "粉尘": "粉尘",       "瓦斯": "瓦斯",
    "一氧化碳": "一氧化碳", "二氧化碳": "二氧化碳",
    "氧气": "氧气",   "氢气": "氢气",
}

# ── 传感器标识片段聚合常量 ───────────────────────────────────────────
# 化学前缀：能自己独立或与数字组合成传感器标识
_CHEM_PREFIXES = {"CH", "CO", "O", "H", "NO", "SO"}
# 化学后缀（数字）：与化学前缀组合用
_CHEM_SUFFIXES = {"2", "4"}
# T 前缀标记（传感器编号，不含 sensor_type 语义）
_T_PREFIX = "T"

# ── 旧别名（向后兼容）─────────────────────────────────────────────
_SENSOR_ID_TO_TYPE = _SENSOR_ID_MAP  # 引用动态映射（外部引用自动更新）
_SENSOR_LANDMARKS = {}  # {tunnel_name: {sensor_id: {"ratio": float, "sensor_type": str, "x": x, "y": y}}}


def _normalize_subscript(text: str) -> str:
    """将 Unicode 下标数字替换为普通数字。如 CO₂ → CO2, H₂ → H2。"""
    if not text:
        return text
    result = []
    for ch in text:
        result.append(_SUBSCRIPT_DIGITS.get(ch, ch))
    return ''.join(result)


def _build_sensor_id_map(devices: list, cad_data: list = None):
    """从设备描述和 CAD 标注自动学习传感器标识→类型映射。

    启动时调用，以 _BASE_SENSOR_ID_MAP 为基础，从数据中自动发现
    新的传感器标识缩写（如 YW→烟雾），避免人工补充硬编码列表。

    来源：
    1. devices: 从 sensor_type 明确标注的设备描述中提取缩写词
    2. cad_data: 从 CAD 标注内容中识别孤立标识（尝试与 sensor_type 关联）
    3. _BASE_SENSOR_ID_MAP: 作为硬编码 fallback

    Args:
        devices: 设备列表 [{id, description, sensor_type, ...}]
        cad_data: CAD 标注列表 [{content, ...}]
    """
    global _SENSOR_ID_MAP, _UNKNOWN_SENSOR_IDS
    _SENSOR_ID_MAP.update(_BASE_SENSOR_ID_MAP)
    _UNKNOWN_SENSOR_IDS.clear()

    if not devices:
        return

    # ── 来源1：从设备描述提取缩写词 ──
    # sensor_type 明确标注的设备 → 从描述/area 提取可能的缩写词
    # 模式：2-4字符的大写字母组合（如 YW、YWD、CH4、CO2）
    import re
    abbrev_pat = re.compile(r'([A-Z]{2,4})')  # 2-4 连续大写字母
    chem_pat = re.compile(r'([A-Z][a-z]?\d*)')  # 化学式：字母+可选数字

    st_to_ids = {}  # {sensor_type: [id1, id2, ...]}
    for dev in devices:
        st = dev.get("sensor_type", "") or ""
        if not st:
            continue
        # 从 description 和 area 提取候选缩写
        for field in [dev.get("description", ""), dev.get("area", "")]:
            text = str(field or "")
            # 大写缩写 (YW, YWD, CJ...)
            for m in abbrev_pat.finditer(text):
                aid = m.group(1)
                if aid not in _SENSOR_ID_MAP or _SENSOR_ID_MAP[aid] == aid:
                    st_to_ids.setdefault(st, set()).add(aid)
            # 化学式 (CH4, CO2, O2...)
            for m in chem_pat.finditer(text):
                aid = m.group(1)
                if len(aid) >= 2 and aid not in _SENSOR_ID_MAP:
                    # 排除纯数字和噪声词
                    if not aid.isdigit() and aid not in {"瓦斯", "甲烷", "分站", "位置"}:
                        st_to_ids.setdefault(st, set()).add(aid)

    # 将提取的缩写→类型关系加入动态映射
    for st, aids in st_to_ids.items():
        for aid in aids:
            existing = _SENSOR_ID_MAP.get(aid)
            if existing and existing != aid:
                continue  # 已有明确的类型映射，跳过
            _SENSOR_ID_MAP[aid] = st

    # ── 来源2：从 CAD 标注识别未映射的传感器标识 ──
    if cad_data:
        for item in cad_data:
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            norm = _normalize_subscript(content)
            # 1-4字符的字母/字母数字组合（过滤纯数字、噪声坐标）
            if re.match(r'^[A-Za-z][A-Za-z0-9]{0,3}$', norm):
                if norm not in _SENSOR_ID_MAP and not norm.isdigit():
                    _UNKNOWN_SENSOR_IDS.add(norm)

    # 暂时忽略 _UNKNOWN_SENSOR_IDS（后续可以从 sensor_type 反查补充）


def _is_sensor_fragment(content: str) -> bool:
    """判断内容是否为可能的传感器标识片段。

    通过两阶段检查：
    1. 化学模式：化学前缀 + 数字后缀（CH/CO/O/H/NO/SO + 2/4）
    2. 映射数据库：已经识别出的传感器标识（来自 _SENSOR_ID_MAP 或 _UNKNOWN_SENSOR_IDS）
    3. T 前缀标记
    4. 温度/其他后缀
    """
    if not content:
        return False
    content = _normalize_subscript(content.strip())
    # 化学前缀/后缀（聚合基础组件）
    if content in _CHEM_PREFIXES or content in _CHEM_SUFFIXES:
        return True
    # T 前缀标记（传感器编号前缀）
    if content == _T_PREFIX:
        return True
    # 温度后缀
    if content in {"t", "温度"}:
        return True
    # 已是完整标识（来自动态映射）
    if content in _SENSOR_ID_MAP:
        return True
    # 干净的模式匹配：2-4字符含字母的标识（非纯数字，非噪声坐标）
    if re.match(r'^[A-Za-z]{2,4}\d*$', content):
        _UNKNOWN_SENSOR_IDS.add(content)
        return True
    return False


def _can_combine(contents: list, new_content: str) -> bool:
    """判断 new_content 是否可以与当前片段列表组合成完整标识。
    使用动态 _SENSOR_ID_MAP + 化学组合规则验证组合有效性。
    """
    all_c = contents + [new_content]
    n = len(all_c)

    # 化学模式：化学前缀 + 数字
    if n == 2:
        c1, c2 = all_c[0], all_c[1]
        if c1 in _CHEM_PREFIXES and c2 in _CHEM_SUFFIXES:
            combined = c1 + c2
            return combined in _SENSOR_ID_MAP or combined in _UNKNOWN_SENSOR_IDS

    # T 前缀 + 化学前缀/温度（中间态，可继续聚合数字）
    if n == 2:
        c1, c2 = all_c[0], all_c[1]
        if c1 == _T_PREFIX and (c2 in _CHEM_PREFIXES or c2 in {"t", "温度"}):
            return True
            # T + 温度后缀 → 直接检查完整标识
            if c2 in {"t", "温度"}:
                combined = "T" + c2
                return combined in _SENSOR_ID_TO_TYPE

    # 规则3: T + 化学前缀 + 数字
    if n == 3:
        c1, c2, c3 = all_c[0], all_c[1], all_c[2]
        if c1 == _T_PREFIX and c2 in _CHEM_PREFIXES and c3 in _CHEM_SUFFIXES:
            combined = "T" + c2 + c3
            return combined in _SENSOR_ID_MAP

    return False


def _group_sensor_fragments(cad_items: list, max_spacing: float = 10.0) -> list:
    """
    将相邻的 CAD 标注点聚合成传感器完整标识。

    聚合规则：
    1. 化学拆分: CH + 4 → CH4, CO + 2 → CO2, O + 2 → O2, H + 2 → H2
    2. T 前缀: T + CH + 4 → TCH4, T + CO → TCO, T + t → Tt(温度)

    返回: 组合后的标识列表，每项为 dict(combined_id, sensor_type, x, y, components)
    """
    if not cad_items:
        return []

    # 按 x 坐标排序
    items = sorted(cad_items, key=lambda item: (item.get('x', 0), item.get('y', 0)))

    used = set()
    groups = []

    def _try_combine(start_idx, start_item):
        """从起始点开始贪心组合，返回组合结果或 None。"""
        if start_idx in used:
            return None
        content = _normalize_subscript(start_item.get('content', '').strip())
        if not _is_sensor_fragment(content):
            return None
        if content not in {"CH", "CO", "O", "H", "NO", "SO", "T"}:
            return None

        group = [start_item]
        local_used = {start_idx}
        cx, cy = start_item.get('x', 0), start_item.get('y', 0)

        while True:
            best_j = None
            best_dist = max_spacing
            current_contents = [_normalize_subscript(it.get('content', '').strip()) for it in group]

            for j, other in enumerate(items):
                if j in used or j in local_used:
                    continue
                other_content = _normalize_subscript(other.get('content', '').strip())
                if not _is_sensor_fragment(other_content):
                    continue
                ox, oy = other.get('x', 0), other.get('y', 0)
                dist = math.sqrt((cx - ox) ** 2 + (cy - oy) ** 2)
                if dist < best_dist and _can_combine(current_contents, other_content):
                    best_j = j
                    best_dist = dist

            if best_j is None:
                break

            group.append(items[best_j])
            local_used.add(best_j)
            cx = sum(it.get('x', 0) for it in group) / len(group)
            cy = sum(it.get('y', 0) for it in group) / len(group)

        # 组合组内片段
        combined_id = "".join(_normalize_subscript(it.get('content', '').strip()) for it in group)
        sensor_type = _SENSOR_ID_TO_TYPE.get(combined_id)
        if sensor_type:
            for idx in local_used:
                used.add(idx)
            avg_x = sum(it.get('x', 0) for it in group) / len(group)
            avg_y = sum(it.get('y', 0) for it in group) / len(group)
            return {
                "combined_id": combined_id,
                "sensor_type": sensor_type,
                "x": avg_x,
                "y": avg_y,
                "components": [_normalize_subscript(it.get('content', '').strip()) for it in group],
            }
        return None

    # 第一轮：优先处理 T 标注（尝试 T + CH + 4 等三三组合）
    for i, item in enumerate(items):
        content = _normalize_subscript(item.get('content', '').strip())
        if content == "T":
            result = _try_combine(i, item)
            if result:
                groups.append(result)

    # 第二轮：处理剩余化学前缀标注（CH + 4, CO + 2 等）
    for i, item in enumerate(items):
        if i in used:
            continue
        content = _normalize_subscript(item.get('content', '').strip())
        if content in {"CH", "CO", "O", "H", "NO", "SO"}:
            result = _try_combine(i, item)
            if result:
                groups.append(result)

    return groups


def _find_sensor_landmark_ratio(description: str, tunnel_name: str,
                                 sensor_type: str = None, t_keyword: str = None):
    """
    检查设备描述是否包含当前巷道上的传感器路标。

    匹配优先级：
    1. exact: 描述包含传感器标识且 sensor_type 一致（最强）
    2. partial: 描述包含传感器标识但 sensor_type 不一致
    3. type_match: 描述不含标识但 sensor_type 与路标类型一致

    Args:
        t_keyword: 设备描述中的 T 标识（如 "T1"）。若提供，则只匹配
                   同样包含该 T 标识的路标。

    返回: (ratio, matched_sensor_type, confidence)
    """
    if not _SENSOR_LANDMARKS or tunnel_name not in _SENSOR_LANDMARKS:
        return None, None, None

    tunnel_sensors = _SENSOR_LANDMARKS[tunnel_name]
    best_ratio = None
    best_sensor_type = None
    best_confidence = None
    best_len = 0

    # 1. 直接标识匹配（传感器标识在设备描述中）
    for sensor_id, info in tunnel_sensors.items():
        if sensor_id in description:
            matched_st = info.get("sensor_type")
            # T 标识精确过滤：设备有 T 标识时，路标名也必须包含该 T 标识
            if t_keyword and t_keyword not in sensor_id:
                continue
            # 长度优先：更长的标识更具体（TCH4 > CH4）
            if len(sensor_id) > best_len:
                best_len = len(sensor_id)
                best_ratio = info.get("ratio")
                best_sensor_type = matched_st
                if sensor_type and matched_st == sensor_type:
                    best_confidence = "exact"
                else:
                    best_confidence = "partial"

    # 2. sensor_type 类型匹配（描述不含标识，但类型一致）
    if best_confidence is None and sensor_type:
        type_matches = []
        for sensor_id, info in tunnel_sensors.items():
            if info.get("sensor_type") == sensor_type:
                type_matches.append((info.get("ratio"), info.get("sensor_type")))
        if type_matches:
            # 选择比例最接近 0.5 的（巷道中间最可能安装该类型传感器）
            type_matches.sort(key=lambda x: abs(x[0] - 0.5))
            best_ratio, best_sensor_type = type_matches[0]
            best_confidence = "type_match"

    return best_ratio, best_sensor_type, best_confidence

def _build_landmarks(cad_data: list, tunnels: list, max_dist: float = 100.0) -> dict:
    """
    从 CAD 数据构建路标表。

    对每个有意义的 CAD 标注点：
    1. 过滤噪声（高程数字、图签文字、报警阈值等）
    2. 计算到最近巷道折线的投影比例（0-1）
    3. 只保留投影距离 <= max_dist 的路标

    返回 {tunnel_name: {landmark_name: ratio, ...}}
    """
    import re

    # 噪声内容集合（同 cesium_cad_data.json 的过滤逻辑）
    # 注意：传感器位置标注（如 CH4(T1)、风筒传感器）已从噪声中移除，
    # 保留为有效路标用于精确定位。T 标识设备通过 T 感知匹配使用路标（见下方路标定位阶段）。
    _NOISE_CONTENTS = {
        '值＜1.0%.', '23ppm。', '烟雾报警值：有烟', 'CO上报值：24ppm，上解值：',
        '报警值≥1.0%,断电值≥1.5%,复电', '报警值≥1.0%,断电值≥1.0%,复电',
        '风筒开停报警值：无风。', '断电控制器',
        '闭锁开关', '双风机双电源开关', '传感器', '分站', '光缆',
        '信号传输线', '局部通风机', '乏风', '环网交换机', '16°', '15°', '掘进迎头位置',
        '图号', '资料来源', '制图', '审核', '日期', '比例尺', '图例', '说明', '总工程师',
        '安全生产部', '天宝公司', '吴冬红', '2024年10月', '1:1000', '1.煤、岩巷',
        'T2报警值≥1.0%,断电值≥1.0%,复电值＜1.0%.',
        '2.断电范围:掘进巷道内全部非本质型安全电气设备',
        '红沙梁矿井安全监控布置图', '窑街煤电集团酒泉天宝煤业有限公司',
    }

    # 巷道/地点关键词
    _TUNNEL_KWS = ['井', '巷', '硐室', '石门', '大巷', '联络', '工作面', '车场', '水仓', '变电所', '泵站', '交岔点', '落底点']

    # 构建隧道映射
    tunnel_map = {}
    for t in tunnels:
        if t.get('line') and t.get('name'):
            tunnel_map[t['name']] = t['line']

    landmarks = {}
    landmark_ids = {}
    for item in cad_data:
        coords = item.get('coordinates', {})
        if not coords.get('x') or not coords.get('y'):
            continue

        content = item.get('content', '').strip()
        if not content:
            continue

        # 过滤噪声
        if re.match(r'^[+\-]?\d+(\.\d+)?$', content) or re.match(r'^(X|Y|Z)=', content):
            continue
        if content in _NOISE_CONTENTS:
            continue

        # 清理前缀；安装地点标注用于语义匹配但不作为设备定位路标
        clean = content[5:] if content.startswith('安装地点：') else content
        is_install_label = content.startswith('安装地点：')
        if clean.startswith('(兼') and clean.endswith(')'):
            clean = clean[1:-1]

        # 只保留有巷道关键词的 或 传感器位置标注
        # 使用动态 _SENSOR_ID_MAP 自动识别所有已知传感器标识
        is_sensor_pos = any(sid in clean for sid in _SENSOR_ID_MAP)
        if not any(kw in clean for kw in _TUNNEL_KWS) and not is_sensor_pos:
            continue

        x, y = coords['x'], coords['y']

        # 找最近巷道（优先语义匹配：CAD 内容含巷道名时，若距离合理则优先）
        best_dist = float('inf')
        best_name = None
        best_ratio = 0.0
        semantic_name = None
        semantic_dist = float('inf')
        semantic_ratio = 0.0
        for name, line in tunnel_map.items():
            ratio, dist = _project_ratio_2d(x, y, line)
            # 语义匹配：CAD 内容含该巷道名
            if dist <= max_dist and name in clean:
                if dist < semantic_dist:
                    semantic_dist = dist
                    semantic_name = name
                    semantic_ratio = ratio
            # 空间最近（兜底）
            if dist < best_dist:
                best_dist = dist
                best_name = name
                best_ratio = ratio

        # 语义匹配优先于空间最近
        if semantic_name:
            best_name = semantic_name
            best_dist = semantic_dist
            best_ratio = semantic_ratio

        if best_dist <= max_dist and best_name:
            if best_name not in landmarks:
                landmarks[best_name] = {}
            # 跳过：安装地点标签（描述性文字，非传感器位置标记）
            if is_install_label:
                continue
            # 跳过：隧道名自身作为路标（隧道名已是候选，用作其他巷道路标会产生误匹配）
            if clean in tunnel_map:
                continue
            # 去重：如果同一路标在同一巷道有多个，保留比例最合理的（中间优先）
            existing = landmarks[best_name].get(clean)
            if existing is None or abs(best_ratio - 0.5) < abs(existing - 0.5):
                landmarks[best_name][clean] = round(best_ratio, 4)
                if best_name not in landmark_ids:
                    landmark_ids[best_name] = {}
                landmark_ids[best_name][clean] = item.get('id')

    # ── 传感器标识组合：将打散的标注点聚合成完整标识 ──
    global _SENSOR_LANDMARKS
    _SENSOR_LANDMARKS.clear()

    # 收集未参与普通路标的标注点中的传感器片段
    sensor_items = []
    used_contents = set()
    for name, lms in landmarks.items():
        used_contents.update(lms.keys())

    for item in cad_data:
        coords = item.get('coordinates', {})
        if not coords.get('x') or not coords.get('y'):
            continue
        content = item.get('content', '').strip()
        if not content:
            continue
        # 跳过噪声（但保留传感器数字后缀 2/4）
        clean = _normalize_subscript(content)
        if clean not in {"2", "4"}:
            if re.match(r'^[+\-]?\d+(\.\d+)?$', clean) or re.match(r'^(X|Y|Z)=', clean):
                continue
        if content in _NOISE_CONTENTS:
            continue
        # 跳过已作为普通路标的
        if content in used_contents:
            continue
        # 只保留传感器片段
        if not _is_sensor_fragment(content):
            continue
        sensor_items.append({
            'content': content,
            'x': coords['x'],
            'y': coords['y'],
        })

    # 组合打散标识
    sensor_groups = _group_sensor_fragments(sensor_items, max_spacing=10.0)

    # 将组合结果投影到巷道
    for sg in sensor_groups:
        x, y = sg['x'], sg['y']
        sensor_id = sg['combined_id']
        stype = sg['sensor_type']

        best_dist = float('inf')
        best_name = None
        best_ratio = 0.0
        for name, line in tunnel_map.items():
            ratio, dist = _project_ratio_2d(x, y, line)
            if dist <= max_dist:
                if dist < best_dist:
                    best_dist = dist
                    best_name = name
                    best_ratio = ratio

        if best_name:
            if best_name not in _SENSOR_LANDMARKS:
                _SENSOR_LANDMARKS[best_name] = {}
            # 同巷道同标识去重（保留比例最合理的）
            existing = _SENSOR_LANDMARKS[best_name].get(sensor_id)
            if existing is None or abs(best_ratio - 0.5) < abs(existing['ratio'] - 0.5):
                _SENSOR_LANDMARKS[best_name][sensor_id] = {
                    'ratio': round(best_ratio, 4),
                    'sensor_type': stype,
                    'x': x,
                    'y': y,
                }

    if sensor_groups:
        total_sl = sum(len(v) for v in _SENSOR_LANDMARKS.values())
        print(f"  → 传感器路标: {len(sensor_groups)} 组组合 → {total_sl} 个 ({len(_SENSOR_LANDMARKS)} 条巷道)", file=sys.stderr)

    global _LANDMARK_IDS
    _LANDMARK_IDS = landmark_ids
    return landmarks


def _find_landmark_ratio(description: str, tunnel_name: str,
                         t_keyword: str = None, sensor_type: str = None) -> tuple:
    """
    检查设备描述是否包含当前巷道上的路标。
    如果包含，返回路标在折线上的投影比例（0-1）；否则返回 None。
    当多个路标匹配时，优先选择最长的（最具体）路标名称。

    Args:
        t_keyword: 设备描述中的 T 标识（如 "T1"）。若提供，则只匹配
                   同样包含该 T 标识的路标（避免 "CH4" 路标覆盖 "CH4(T1)" 设备）。
        sensor_type: 设备传感器类型。用于 type_match 回退：当路标名是传感器缩写
                     （如 YW→烟雾）但未在描述中出现时，按类型匹配。
    """
    if not _LANDMARKS or tunnel_name not in _LANDMARKS:
        return None, None

    tunnel_landmarks = _LANDMARKS[tunnel_name]
    best_match = None
    best_name = None
    best_len = 0
    # 归一化：全角括号→半角，移除空格，去除末尾标点
    norm_desc = _norm_lm(description)

    def _is_better(lm_len, ratio, best_len, best_match):
        return lm_len > best_len or (lm_len == best_len and abs(ratio - 0.5) > abs(best_match - 0.5))

    for landmark_name, ratio in tunnel_landmarks.items():
        norm_name = _norm_lm(landmark_name)
        # 组合路标支持（如 "CO、烟雾"）：只要任一子串(≥2字符)在描述中即可匹配
        matched = norm_name in norm_desc
        if not matched and '、' in norm_name:
            matched = any(p in norm_desc for p in norm_name.split('、') if len(p) >= 2)
        # T 标识路标传感器部分匹配：无 T 设备允许匹配传感器部分（如 CH4 匹配 CH4(T2)）
        if not matched and _has_t_id(norm_name):
            sensor_part = _strip_t_id(norm_name)
            if sensor_part and sensor_part in norm_desc:
                matched = True
        if matched:
            lm_len = len(norm_name)
            # T 标识精确匹配：设备有 T 标识时，优先找同样含该 T 标识的路标
            if t_keyword and t_keyword in norm_name:
                if _is_better(lm_len, ratio, best_len, best_match):
                    best_match = ratio
                    best_name = landmark_name
                    best_len = lm_len
            # 无 T 设备 或 T 标识不匹配时：回退到不含 T 的路标（如 CH4）
            elif not t_keyword:
                if _is_better(lm_len, ratio, best_len, best_match):
                    best_match = ratio
                    best_name = landmark_name
                    best_len = lm_len
    # T 标识设备精确匹配失败时：回退到不含 T 的路标中最合适的
    if best_match is None and t_keyword:
        fb_match = None
        fb_name = None
        fb_len = 0
        for landmark_name, ratio in tunnel_landmarks.items():
            norm_name = _norm_lm(landmark_name)
            matched = norm_name in norm_desc
            if not matched and '、' in norm_name:
                matched = any(p in norm_desc for p in norm_name.split('、') if len(p) >= 2)
            if not matched and _has_t_id(norm_name):
                sensor_part = _strip_t_id(norm_name)
                if sensor_part and sensor_part in norm_desc:
                    matched = True
            if matched:
                # 跳过含 T 标识的路标（已在第一轮尝试）
                if _has_t_id(norm_name):
                    continue
                lm_len = len(norm_name)
                if _is_better(lm_len, ratio, fb_len, fb_match):
                    fb_match = ratio
                    fb_name = landmark_name
                    fb_len = lm_len
        best_match = fb_match
        best_name = fb_name
    # sensor_type 回退：路标名是传感器缩写（如 YW→烟雾、CH4→瓦斯）
    # 设备描述可能不含缩写但 sensor_type 与路标一致
    if best_match is None and sensor_type:
        for landmark_name, ratio in tunnel_landmarks.items():
            lm_st = _SENSOR_ID_TO_TYPE.get(landmark_name)
            if lm_st == sensor_type:
                lm_len = len(landmark_name)
                if _is_better(lm_len, ratio, best_len, best_match):
                    best_match = ratio
                    best_name = landmark_name
                    best_len = lm_len
    return best_match, best_name


# ──────────────────────────────────────────────────────────────────────

def _assign_distances(count: int, keyword: str, line_length: float,
                      sensor_type: str = None, tunnel_type: str = None, step: float = 1.0) -> list:
    """
    沿折线分配距起点的距离（米），分配策略优先级：
    路标定位 > T 标识规则 > 巷道类型×sensor_type > AQ1029 距离 > 关键词区间 > sensor_type 默认 > 兜底
    """
    # B16 工业视频同组多设备的标准间距（MT/T 1201.6-2023 附录 A）
    if sensor_type == "工业视频":
        if keyword == "支架":
            step = 75.0      # A.1#1: 两工业视频间距 ≤50架≈75m
        elif keyword == "中部":
            step = 500.0     # A.1#16: 主运输皮带中部每500m一台
        elif keyword == "架空乘人":
            step = 100.0     # A.1#23: 架空乘人装置中部每100m一台

    # ── 辅助函数 ──
    def _distribute_in_zone(lo: float, hi: float, count: int, step: float) -> list:
        """在 [lo, hi] 区间内按步长分配，放不下则均匀分布。"""
        zone = hi - lo
        if count <= 1:
            return [(lo + hi) / 2]
        distances = [lo + i * step for i in range(count)]
        if distances[-1] > hi + 1e-6:
            step_adj = zone / (count - 1) if zone > 0 else 0
            distances = [lo + i * step_adj for i in range(count)]
        return distances

    def _ratio_to_meters(lo_ratio: float, hi_ratio: float,
                         exact_lo: float = None, exact_hi: float = None) -> tuple:
        if line_length <= 0:
            return 0.0, 0.0
        lo, hi = line_length * lo_ratio, line_length * hi_ratio
        if exact_lo is not None:
            lo = min(lo, exact_lo)
        if exact_hi is not None:
            hi = min(hi, exact_hi)
        return lo, hi

    # 1. T 标识规则（AQ 1029-2019 §6.2.1/§6.3.1）
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

    # 2. 巷道类型 × sensor_type 规则（AQ 1029-2019 / MT/T 1201.6-2023 附录 A）
    if tunnel_type and tunnel_type in _TUNNEL_TYPE_RULES:
        rule = _TUNNEL_TYPE_RULES[tunnel_type].get(sensor_type) if sensor_type else None
        if rule:
            direction = rule["from"]
            meters = rule.get("meters", 0)
            tolerance = rule.get("tolerance", 0)
            if direction == "start":
                lo, hi = 0.0, min(line_length * 0.15, meters + tolerance) if tolerance else meters
                return _distribute_in_zone(lo, hi, count, step)
            elif direction == "end":
                lo, hi = max(line_length * 0.85, line_length - meters - tolerance) if tolerance else line_length - meters, line_length
                return _distribute_in_zone(lo, hi, count, step)
            elif direction == "mid":
                lo, hi = (max(0.0, line_length * 0.5 - meters), min(line_length, line_length * 0.5 + meters)) if meters > 0 else (line_length * 0.4, line_length * 0.6)
                return _distribute_in_zone(lo, hi, count, step)

    # 3. AQ1029 通用精确米数规则
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

    # 4. 单设备 / 空折线 → 精确位置（查表）
    if line_length <= 0 or count <= 1:
        ratio = _KEYWORD_SINGLE_RATIO.get(keyword)
        if ratio is not None:
            return [line_length * ratio] if line_length > 0 else [0.0]
        ratio = _SENSOR_SINGLE_RATIO.get(sensor_type, 0.5)
        return [line_length * ratio] if line_length > 0 else [0.0]

    # 5. 多设备 → 关键词区间分布（查表）
    zone = _KEYWORD_ZONE_RULES.get(keyword)
    if zone:
        return _distribute_in_zone(line_length * zone[0], line_length * zone[1], count, step)

    # 6. sensor_type 默认区间（查表）
    zone = _SENSOR_DEFAULT_ZONES.get(sensor_type)
    if zone:
        return _distribute_in_zone(line_length * zone[0], line_length * zone[1], count, step)

    # 7. 兜底：10-90%
    return _distribute_in_zone(line_length * 0.1, line_length * 0.9, count, step)


def _classify_keyword(description: str) -> str:
    """按关键词分类：T标识 / 迎头 / 回风流 / B15/B16关键词 / default

    分类规则顺序（优先级从高到低）：
    T标识 ≥ 隅角/混合风流 ≥ 岔口/交叉口/分流 ≥ _CLASSIFY_KEYWORD_TABLE ≥ default
    """
    d = description
    # T标识（最高优先级，AQ 1029-2019 §6.2.1/§6.3.1）
    t_kw = _extract_t_keyword(d)
    if t_kw and t_kw in _T_POSITION_RULES:
        return t_kw
    # 特殊词汇（无法用简单子串表达）
    if "隅角" in d:
        return "T0"
    if "混合风流" in d:
        return "T3"
    if any(kw in d for kw in ("岔口", "交叉口", "分流")):
        return "岔口"  # MT/T 1198 §5.1.3: 分流路口归为岔口
    # 表驱动匹配（_CLASSIFY_KEYWORD_TABLE 已按关键词长度降序）
    for keyword, result in _CLASSIFY_KEYWORD_TABLE:
        if keyword in d:
            return result
    return "default"


def _extract_candidates(items) -> tuple:
    """
    从策略数据提取候选匹配项（巷道+工作面）。
    支持 dict 格式 {tunnels:[], workfaces:[]} 和 list 格式（workface 对象数组）。

    返回 (candidates, code_to_candidates, prefix_to_candidates, coalbed_map,
           generic_tunnel_skipped, unnamed_tunnel_skipped, generic_tunnel_names) 其中：
    - code_to_candidates: 工作面编码 -> 候选索引列表
    - coalbed_map: 工作面编码 -> coalbed 映射
    - generic_tunnel_skipped: 被跳过的系统生成巷道名称数量
    - unnamed_tunnel_skipped: 被跳过的无名称巷道数量
    - generic_tunnel_names: 被排除的系统生成巷道名称列表
    """
    candidates = []
    code_to_candidates = {}
    prefix_to_candidates = {}
    coalbed_map = {}
    generic_tunnel_skipped = 0
    unnamed_tunnel_skipped = 0
    generic_tunnel_names = []

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
            tunnel_name = (t.get("name") or "").strip()
            if not tunnel_name:
                unnamed_tunnel_skipped += 1
                continue
            if _is_generic_tunnel_name(tunnel_name):
                generic_tunnel_skipped += 1
                generic_tunnel_names.append(tunnel_name)
                continue
            idx = len(candidates)
            tunnel_type = t.get("type", "")
            candidates.append({
                "name": tunnel_name,
                "type": tunnel_type,
                "category": "tunnel",
                "line": t.get("line", []),
                "id": t.get("id") or t.get("tunnelId", ""),
                "coalbed": t.get("coalbed", ""),
            })
            _add_code_index(tunnel_name, idx)
            _add_coalbed(tunnel_name, t.get("coalbed", ""))
        for w in items.get("workfaces", []):
            idx = len(candidates)
            wf_type = w.get("type", "")
            candidates.append({
                "name": w.get("workFaceName", ""),
                "type": wf_type,
                "category": "workface",
                "line": w.get("line", []),
                "id": w.get("id") or w.get("tunnelId", ""),
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
                    "id": item.get("id") or item.get("tunnelId", ""),
                    "tunnelId": item.get("tunnelId", ""),
                    "coalbed": item.get("coalbed", ""),
                })
                _add_code_index(name, idx)
                _add_coalbed(name, item.get("coalbed", ""))
    return candidates, code_to_candidates, prefix_to_candidates, coalbed_map, generic_tunnel_skipped, unnamed_tunnel_skipped, generic_tunnel_names


# ── 数据校验 ──────────────────────────────────────────────────────
# CGCS2000 3°带高斯投影坐标范围（中国经度 73°E~135°E，纬度 18°N~54°N）
_X_MIN, _X_MAX = 2.5e7, 4.6e7  # 25带~46带（500km偏移）
_Y_MIN, _Y_MAX = 2.0e6, 5.5e6  # 纬度范围覆盖
_Z_MIN, _Z_MAX = -3000.0, 3000.0


def _validate_devices(devices: list) -> list:
    """校验设备数据，返回清洗后的列表。跳过无效条目并输出警告。
    处理重复ID：相同id+相同description去重；相同id+不同description直接报错终止。"""
    if not isinstance(devices, list):
        raise ValueError("devices 必须是 list")
    if not devices:
        return []
    cleaned = []
    seen = {}          # (id, desc) -> True
    id_to_desc = {}    # id -> 第一次出现的description
    auto_id = 1
    skipped = 0
    dedup_skipped = 0
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
        raw_id = dev.get("id") or f"AUTO_{auto_id:03d}"
        desc = desc.strip()
        key = (raw_id, desc)
        if key in seen:
            dedup_skipped += 1
            continue
        seen[key] = True
        # 相同id不同description → 直接报错终止
        if raw_id in id_to_desc:
            first_desc = id_to_desc[raw_id]
            raise ValueError(
                f"设备ID重复且description不同，数据质量错误，请修复上游数据后再运行。\n"
                f"  重复ID: {raw_id}\n"
                f"  第一次: \"{first_desc}\"\n"
                f"  第{i+1}条: \"{desc}\""
            )
        id_to_desc[raw_id] = desc
        item = {
            "id": raw_id,
            "description": desc,
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
    if dedup_skipped:
        print(f"  → 去重 {dedup_skipped} 个重复设备", file=sys.stderr)
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
    """校验巷道数据，返回清洗后的列表。跳过无效条目并输出警告。"""
    if not isinstance(tunnels, list):
        raise ValueError("tunnels 必须是 list")
    cleaned = []
    skipped = 0
    for i, t in enumerate(tunnels):
        if not isinstance(t, dict):
            print(f"  ! 跳过 tunnels[{i}]: 非 dict 类型", file=sys.stderr)
            skipped += 1
            continue
        name = t.get("name", "")
        if not isinstance(name, str) or not name.strip():
            print(f"  ! 跳过 tunnels[{i}]: name 为空", file=sys.stderr)
            skipped += 1
            continue
        line = _validate_line(t.get("line", []), f"tunnels[{i}] '{name}'")
        item = {"name": name.strip(), "line": line}
        for field in ("id", "type", "coalbed"):
            val = t.get(field)
            if val is not None:
                if not isinstance(val, str):
                    print(f"  ! 跳过 tunnels[{i}] '{name}': {field} 类型错误", file=sys.stderr)
                    skipped += 1
                    break
                item[field] = val
        else:
            cleaned.append(item)
    if skipped:
        print(f"  → 跳过 {skipped} 个无效巷道", file=sys.stderr)
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


def _parse_device_ids_from_file(path: str) -> set:
    """从文件读取设备 ID 列表，返回 set。

    支持格式：
      - JSON 数组: ["ID1","ID2"]
      - 逗号分隔: ID1,ID2,ID3
      - 标签行:  json: ["ID1","ID2"]
                 text: ID1,ID2
                 userinput: ID1,ID2
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"设备 ID 文件不存在: {p}")
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return set()

    # 尝试 JSON 数组
    if raw.startswith("["):
        try:
            ids = json.loads(raw)
            if isinstance(ids, list):
                return set(str(i).strip() for i in ids if i)
        except json.JSONDecodeError:
            pass

    # 逐行解析：支持 json:/text:/userinput: 前缀
    ids = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("json:"):
            payload = line[5:].strip()
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, list):
                    ids.update(str(i).strip() for i in parsed if i)
            except json.JSONDecodeError:
                pass
        elif line.startswith("text:") or line.startswith("userinput:"):
            payload = line[line.index(":") + 1:].strip()
            for part in payload.split(","):
                part = part.strip()
                if part:
                    ids.add(part)
        else:
            # 裸逗号分隔
            for part in line.split(","):
                part = part.strip()
                if part:
                    ids.add(part)
    return ids


def _load_json_file(path: str) -> dict or list:
    """加载 JSON 文件，返回解析后的对象。

    自动识别 BW-MES API envelope `{code, data: {...}}` 并解包到内层 data，
    避免因外壳未剥离导致下游 devices/tunnels 全空。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {p}")
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, (dict, list)):
        raise ValueError(f"JSON 根必须是 dict 或 list")
    if (
        isinstance(data, dict)
        and "code" in data
        and "data" in data
        and isinstance(data["data"], (dict, list))
    ):
        inner = data["data"]
        if isinstance(inner, dict) and (
            "devices" in inner or "tunnels" in inner or "workfaces" in inner
        ):
            return inner
        if isinstance(inner, list):
            return inner
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
        if cand.get("name") == cached_name:
            return cand
        # 仅当 cached_id 非空时才按 ID 匹配，避免空字符串误匹配第一个候选
        if cached_id and cand.get("id") == cached_id:
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


# ── 匹配+坐标分配 ──────────────────────────────────────────────────
def _match_devices(devices: list, candidates: list,
                   code_to_candidates: dict, prefix_to_candidates: dict,
                   coalbed_map: dict) -> tuple:
    """
    匹配设备到候选巷道/工作面，分组分配坐标。
    返回 (results, unmatched, wind_warnings, matched_count)。
    """

    def _calc_confidence(match: dict, cleaned: str, sensor_type: str = None) -> str:
        layer = match.get("layer", _MATCH_LAYER_REJECT)
        if layer == _MATCH_LAYER_EXACT:
            return "高"
        if layer == _MATCH_LAYER_LCS_PREF:
            return "中"
        if layer == _MATCH_LAYER_LOW:
            return "低"
        return "极低"

    match_cache = _load_match_cache()
    cache_hits = 0
    match_entries = []
    unmatched = []

    # 第一遍：匹配所有设备
    for device in devices:
        desc = device.get("description", "")
        cleaned = strip_prefix(desc)
        mark_type = device.get("mark_type")
        sensor_type = device.get("sensor_type") or _infer_sensor_type(desc, mark_type)
        if mark_type == "B16" and sensor_type in ("海康", "大华", "宇视", "萤石"):
            sensor_type = "工业视频"
        # 从剥离前缀后的描述提取编码,避免通道编码（如 001A01）误判为位置编码
        device_code = extract_workface_code(cleaned)

        cached = match_cache.get(_make_cache_key(desc, mark_type))
        if cached:
            cand = _find_cached_candidate(candidates, cached)
            if cand:
                cand_name = cand.get("name", "")
                # 缓存语义校验：即使缓存命中，若存在硬性语义冲突也应忽略缓存重新匹配
                cache_valid = not _has_hard_semantic_conflict(cleaned, cand_name)
                if cache_valid:
                    # 额外类型检查：工作面设备不应匹配到非工作面类型候选
                    wf_m = re.search(r'(\d{3,})\s*工作面', cleaned)
                    if wf_m and wf_m.group(1) in cand_name:
                        cand_type = cand.get("type", "")
                        if cand_type.split("-")[0] == "0":
                            cache_valid = False
                if cache_valid:
                    cache_hits += 1
                    match = {
                        "name": cand_name, "lcs": 0,
                        "score": cached.get("score", 10), "candidate": cand,
                        "layer": _MATCH_LAYER_EXACT, "from_cache": True,
                    }
                    match_entries.append((device, match, cleaned, sensor_type,
                                          extract_explicit_distance(desc)))
                    continue

        if (_is_surface_area(device.get("area")) or _is_surface_description(desc)) and not _is_shaft_mouth(desc):
            unmatched.append({
                "id": device.get("id", ""), "description": desc,
                "mark_type": mark_type or "", "sensor_type": sensor_type,
                "sysaliasname": device.get("sysaliasname", ""),
                "reason": REJECT_AREA_SURFACE, "device_code": device_code,
                "area": device.get("area", ""),
                "candidates": [],
            })
            continue

        match, all_scored = find_best_match(cleaned, candidates, sensor_type=sensor_type,
                                             device_code=device_code,
                                             code_to_candidates=code_to_candidates,
                                             prefix_to_candidates=prefix_to_candidates,
                                             coalbed_map=coalbed_map, mark_type=mark_type)
        if match is None:
            reason = REJECT_LOW_LCS
            if not candidates:
                reason = REJECT_NO_CANDIDATE
            elif device_code:
                code_found = any(_code_in_name(device_code, c.get("name") or "") for c in candidates)
                if not code_found:
                    prefix_found = False
                    if device_code.lstrip('-').isdigit():
                        prefix_found = any(
                            any(cn.startswith(device_code) for cn in re.findall(r'\d{3,4}', c.get("name", "")))
                            for c in candidates
                        )
                    if not prefix_found:
                        reason = REJECT_CODE_MISMATCH
            if reason == REJECT_LOW_LCS and candidates:
                sem_blocked = all(
                    _semantic_penalty(cleaned, c.get("name", ""), mark_type=mark_type) <= -10
                    for c in candidates
                )
                hard_blocked = all(
                    _has_hard_semantic_conflict(cleaned, c.get("name", ""))
                    for c in candidates
                )
                if sem_blocked or hard_blocked:
                    reason = REJECT_SEMANTIC_CONFLICT
            unmatched.append({
                "id": device.get("id", ""), "description": desc,
                "mark_type": mark_type or "", "sensor_type": sensor_type,
                "sysaliasname": device.get("sysaliasname", ""),
                "reason": reason, "device_code": device_code,
                **({"area": device.get("area", "")} if device.get("area") else {}),
                "candidates": _format_top_candidates(all_scored, n=3),
            })
            continue

        if match.get("layer") == _MATCH_LAYER_EXACT:
            _add_to_cache(match_cache, desc, match, mark_type)
        match_entries.append((device, match, cleaned, sensor_type,
                              extract_explicit_distance(desc)))

    if cache_hits > 0:
        print(f"  → 缓存命中: {cache_hits} 个", file=sys.stderr)
    _save_match_cache(match_cache)

    # 第二遍：按 (matched_name, keyword) 分组 → 分配坐标
    groups = {}
    for device, match, cleaned, sensor_type, explicit_dist in match_entries:
        keyword = _classify_keyword(cleaned)
        groups.setdefault((match["name"], keyword), []).append(
            (device, match, cleaned, sensor_type, explicit_dist))

    results = []
    matched_count = 0
    for group_key, entries in groups.items():
        name, keyword = group_key
        candidate = entries[0][1]["candidate"]
        line = candidate.get("line", [])
        tunnel_type = candidate.get("type", "")
        total_len = _polyline_length(line)

        st_counts = {}
        for _, _, _, st, _ in entries:
            st_counts[st] = st_counts.get(st, 0) + 1
        representative_st = max(st_counts, key=st_counts.get) if st_counts else None

        # ── 路标定位：为没有显式距离的设备检查路标 ──
        # 先检查传感器路标（更精确），再检查普通路标
        lm_cad_ids = {}  # {device_id: cad_id} — per-device，避免共享 candidate 冲突
        if _SENSOR_LANDMARKS or _LANDMARKS:
            new_entries = []
            landmark_ratios = []
            for device, match, cleaned, st, ed in entries:
                if ed is None:
                    # T 标识精确过滤：提取设备 T 标识，路标匹配时要求路标名也包含该 T 标识
                    # 避免 "CH4" 路标（巷道中部 47%）覆盖 "CH4(T1)" 设备的 T1 规则（迎头 0-5m）
                    # 但 "CH4（T1)" 路标会被保留，实现 CAD 精确位置定位
                    t_kw = _extract_t_keyword(cleaned)
                    # 先检查传感器路标
                    sl_ratio, _, _ = _find_sensor_landmark_ratio(cleaned, name, st, t_keyword=t_kw)
                    if sl_ratio is not None:
                        ed = total_len * sl_ratio
                        landmark_ratios.append(sl_ratio)
                    else:
                        # 再检查普通路标
                        ratio, lm_name = _find_landmark_ratio(cleaned, name, t_keyword=t_kw, sensor_type=st)
                        if ratio is not None:
                            ed = total_len * ratio
                            landmark_ratios.append(ratio)
                            # 获取路标对应的 CAD 原始 ID（per-device 存储）
                            if lm_name and _LANDMARK_IDS and name in _LANDMARK_IDS:
                                cad_id = _LANDMARK_IDS[name].get(lm_name)
                                if cad_id:
                                    lm_cad_ids[device.get('id', '')] = cad_id
                new_entries.append((device, match, cleaned, st, ed))
            entries = new_entries
            # 同组多设备共用同一路标时，按 1m 步长分散
            if len(landmark_ratios) > 1:
                step = 1.0
                off_idx = 0
                for i, (device, match, cleaned, st, ed) in enumerate(entries):
                    if ed is not None and any(abs(ed - total_len * r) < 0.01 for r in landmark_ratios):
                        entries[i] = (device, match, cleaned, st, ed + off_idx * step)
                        off_idx += 1

        implicit_count = sum(1 for _, _, _, _, ed in entries if ed is None)
        explicit_entries = [(i, ed) for i, (_, _, _, _, ed) in enumerate(entries) if ed is not None]
        # 切眼交汇点：描述同时含轨顺/皮顺和切眼时→定位到折线起点（0%），即 junction 点
        is_junction = False
        for _, _, cleaned, _, _ in entries:
            if any(t in cleaned for t in ("轨顺", "皮顺", "轨道顺槽", "皮带顺槽")) and "切眼" in cleaned:
                is_junction = True
                break
        if is_junction:
            implicit_distances = [0.0] * implicit_count
        else:
            implicit_distances = _assign_distances(implicit_count, keyword, total_len,
                                                    sensor_type=representative_st, tunnel_type=tunnel_type)

        distances = [None] * len(entries)
        for idx, ed in explicit_entries:
            desc_clean = entries[idx][2]
            distances[idx] = _apply_keyword_offset(desc_clean, keyword, ed, total_len) if keyword else ed
        imp_idx = 0
        for i in range(len(distances)):
            if distances[i] is None:
                distances[i] = implicit_distances[imp_idx]
                imp_idx += 1

        for (device, match, cleaned, sensor_type, explicit_dist), dist in zip(entries, distances):
            matched_count += 1
            clamped = False
            if explicit_dist is not None and total_len > 0 and explicit_dist > total_len:
                pct = (explicit_dist / total_len) * 100
                print(f"  ! 显式距离超出: {explicit_dist:.0f}m > {total_len:.0f}m "
                      f"(={pct:.0f}% of line length) "
                      f"(matched={match['name']}, desc={device.get('description', '')[:60]})", file=sys.stderr)
                dist = total_len
                clamped = True
            ratio = dist / total_len if total_len > 0 else 0.5
            coords = _polyline_interpolate(line, ratio) if line else {"x": None, "y": None, "z": None}
            if coords.get("z") is not None and sensor_type in _SENSOR_INSTALL_HEIGHT:
                coords = dict(coords)
                coords["z"] = round(coords["z"] + _SENSOR_INSTALL_HEIGHT[sensor_type], 4)

            result_entry = {
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
                "line_total_length": round(total_len, 2),
                "distance_along_line": round(dist, 2),
                "line_percentage": round(ratio * 100, 1),
            }
            if match.get("suspicious"):
                result_entry["suspicious"] = True
                result_entry["suspicious_reason"] = match.get("suspicious_reason")
            # 保存传感器路标信息到结果
            if match.get("candidate") and match["candidate"].get("_sensor_landmark"):
                result_entry["_sensor_landmark"] = match["candidate"]["_sensor_landmark"]
            # 保存 CAD 路标原始 ID 到结果（per-device，避免共享 candidate 覆盖）
            dev_cad_id = lm_cad_ids.get(device.get('id', ''))
            if dev_cad_id:
                result_entry["_landmark_cad_id"] = dev_cad_id
            results.append(result_entry)

    wind_warnings = _check_wind_speed_spacing(groups)
    if wind_warnings:
        print(f"  ! 风速间距警告: {len(wind_warnings)} 条", file=sys.stderr)

    return results, unmatched, wind_warnings, matched_count


# ── 结构化分析 ──────────────────────────────────────────────────────
def _generate_analysis_report(data_path: str) -> dict:
    """生成 8373 数据文件的结构化分析报告。

    分析项目包括设备总数、巷道/工作面数、mark_type 分布、sensor_type 分布、
    地面/井下拆分、系统命名巷道排除、潜在难匹配设备等。

    Args:
        data_path: data_8373_*.json 文件路径

    Returns:
        包含分析结果的 dict
    """
    import os
    from collections import Counter

    data = _load_json_file(data_path)

    devices = []
    candidates = []
    if isinstance(data, dict):
        if "devices" in data and isinstance(data["devices"], list):
            devices = data["devices"]
        if "candidates" in data and isinstance(data["candidates"], list):
            candidates = data["candidates"]
        elif "tunnels" in data or "workfaces" in data:
            cand_res = _extract_candidates(data)
            candidates = cand_res[0] if cand_res else []
    elif isinstance(data, list):
        devices, candidates, _ = classify_items(data)

    total_devices = len(devices)
    tunnels = [c for c in candidates if c.get("category") == "tunnel"]
    workfaces = [c for c in candidates if c.get("category") == "workface"]

    # ── cadData 分析 ──
    cad_data = []
    cad_type_distribution = Counter()
    cad_dwg_distribution = Counter()
    cad_tunnel_name_matches = 0
    cad_device_desc_matches = 0
    if isinstance(data, dict) and "cadData" in data and isinstance(data["cadData"], list):
        cad_data = data["cadData"]
        cad_type_distribution = Counter(item.get("typeName", "(无)") for item in cad_data)
        cad_dwg_distribution = Counter(item.get("dwgID", "(无)") for item in cad_data)
        # 统计 content 与巷道名/设备描述的文本关联
        tunnel_names = {c.get("name", "") for c in tunnels if c.get("name")}
        device_descs = {d.get("description", "") for d in devices if d.get("description")}
        for item in cad_data:
            content = str(item.get("content", ""))
            for tn in tunnel_names:
                if tn and tn in content:
                    cad_tunnel_name_matches += 1
                    break
            for desc in device_descs:
                if desc and (content in desc or desc in content):
                    cad_device_desc_matches += 1
                    break
        # ── 构建 CAD 路标，填充 _SENSOR_LANDMARKS 和 _LANDMARKS ──
        _LANDMARKS.clear()
        _LANDMARKS.update(_build_landmarks(cad_data, data.get("tunnels", [])))

        # ── CAD 内容分类统计 ──
        import re as _re
        _noise_contents = {
            '值＜1.0%.', '23ppm。', '烟雾报警值：有烟', 'CO上报值：24ppm，上解值：',
            '报警值≥1.0%,断电值≥1.5%,复电', '报警值≥1.0%,断电值≥1.0%,复电',
            '风筒开停报警值：无风。', 'T2报警值≥1.0%,断电值≥1.0%,复电值＜1.0%.',
            '图号', '资料来源', '制图', '审核', '日期', '比例尺', '图例', '说明',
            '总工程师', '安全生产部', '天宝公司', '吴冬红', '2024年10月', '1:1000',
            '2.断电范围:掘进巷道内全部非本质型安全电气设备',
            '红沙梁矿井安全监控布置图', '窑街煤电集团酒泉天宝煤业有限公司',
        }
        _sensor_kws = {'CH4', 'CO', '烟雾', '风筒', '风速', '温度', '粉尘'}
        _location_kw = '安装地点'
        _station_kws = {'分站', '地面中心站'}
        _chamber_kws = {'硐室', '等候室'}
        _tunnel_kws_list = ['井', '巷', '石门', '大巷', '联络', '工作面', '车场', '水仓', '变电所', '泵站', '交岔点', '落底点']
        _equip_kws = {'断电控制器', '双风机', '闭锁开关', '环网交换机', '局部通风机'}
        cad_stats = {
            'total': len(cad_data),
            'noise': 0, 'numeric': 0, 'coordinate': 0,
            'sensor_type': 0, 'location': 0, 'station': 0,
            'chamber': 0, 'tunnel_name': 0, 'equipment': 0, 'other': 0,
            'noise_detail': Counter(), 'sensor_detail': Counter(),
            'location_detail': Counter(), 'tunnel_detail': Counter(),
            'station_detail': Counter(), 'chamber_detail': Counter(),
            'equip_detail': Counter(),
        }
        for item in cad_data:
            c = str(item.get('content', '')).strip()
            if not c:
                continue
            # 纯数字 / 坐标
            stripped = c.replace('.', '').replace('-', '').replace(' ', '').replace('°', '')
            if stripped.isdigit():
                cad_stats['numeric'] += 1
                continue
            if c.startswith('Z=') or c.startswith('X=') or c.startswith('Y='):
                cad_stats['coordinate'] += 1
                continue
            # 噪声（阈值 / 图签）
            if c in _noise_contents or '报警值' in c or '断电值' in c or '复电值' in c or 'ppm' in c or (c.endswith('°') and stripped.isdigit()):
                cad_stats['noise'] += 1
                cad_stats['noise_detail'][c] += 1
                continue
            # 传感器类型
            if any(kw in c for kw in _sensor_kws):
                cad_stats['sensor_type'] += 1
                cad_stats['sensor_detail'][c] += 1
                continue
            # 安装地点
            if _location_kw in c:
                cad_stats['location'] += 1
                cad_stats['location_detail'][c] += 1
                continue
            # 分站
            if any(kw in c for kw in _station_kws):
                cad_stats['station'] += 1
                cad_stats['station_detail'][c] += 1
                continue
            # 硐室
            if any(kw in c for kw in _chamber_kws):
                cad_stats['chamber'] += 1
                cad_stats['chamber_detail'][c] += 1
                continue
            # 设备
            if any(kw in c for kw in _equip_kws):
                cad_stats['equipment'] += 1
                cad_stats['equip_detail'][c] += 1
                continue
            # 巷道名（含巷道关键词但不含上述类别）
            if any(kw in c for kw in _tunnel_kws_list):
                cad_stats['tunnel_name'] += 1
                cad_stats['tunnel_detail'][c] += 1
                continue
            # 其他
            cad_stats['other'] += 1

    # mark_type 分布
    mark_types = Counter(d.get("mark_type") or "UNKNOWN" for d in devices)

    # sensor_type 分布
    sensor_types = Counter()
    missing_sensor = []
    for d in devices:
        st = d.get("sensor_type")
        if st:
            sensor_types[st] += 1
        else:
            inferred = _infer_sensor_type(d.get("description", ""), d.get("mark_type"))
            if inferred:
                missing_sensor.append(d)

    # area 分布 + 地面识别
    areas = Counter()
    surface_count = 0
    for d in devices:
        area = d.get("area") or "(无)"
        areas[area] += 1
        if _is_surface_area(area) or _is_surface_description(d.get("description", "")):
            surface_count += 1

    # 巷道分析
    generic_tunnel_names = sorted(set(
        c["name"] for c in tunnels if _is_generic_tunnel_name(c.get("name", ""))
    ))
    named_tunnels = [c for c in tunnels if not _is_generic_tunnel_name(c.get("name", ""))]

    # 编码提取分析 — 潜在难匹配设备
    hard_to_match = []
    for d in devices:
        if not extract_workface_code(d.get("description", "")):
            hard_to_match.append(d)

    # originData 分析（8385 当前存储的定位标注数据）
    origin_data = data.get("originData", []) if isinstance(data, dict) else []
    origin_has_coord = 0
    origin_no_coord = 0
    for od in origin_data:
        coord_str = od.get("coordinates") or ""
        if coord_str and coord_str != "{}":
            origin_has_coord += 1
        else:
            origin_no_coord += 1

    # 煤层分布
    coalbeds = Counter(c.get("coalbed") or "(无)" for c in candidates)

    # ── mark_type × sensor_type 交叉表（预计算，供后续使用）──
    cross = {}
    for d in devices:
        mt = d.get("mark_type", "UNKNOWN")
        st = d.get("sensor_type") or _infer_sensor_type(d.get("description", ""), d.get("mark_type"))
        if mt not in cross:
            cross[mt] = {}
        cross[mt][st] = cross[mt].get(st, 0) + 1
    all_sts = sorted({st for row in cross.values() for st in row}, key=lambda s: -sum(r.get(s, 0) for r in cross.values()))

    # ── 各 area 的 Mark Type / Sensor Type 构成（预计算）──
    area_details = {}
    for d in devices:
        area = d.get("area") or "(无)"
        mt = d.get("mark_type", "UNKNOWN")
        st = d.get("sensor_type") or _infer_sensor_type(d.get("description", ""), d.get("mark_type"))
        if area not in area_details:
            area_details[area] = {"mt": {}, "st": {}}
        area_details[area]["mt"][mt] = area_details[area]["mt"].get(mt, 0) + 1
        area_details[area]["st"][st] = area_details[area]["st"].get(st, 0) + 1

    # ── 编码提取成功率统计（预计算）──
    code_extracted = 0
    for d in devices:
        if extract_workface_code(d.get("description", "")):
            code_extracted += 1
    code_pct = (code_extracted / total_devices * 100) if total_devices else 0

    # ── 可用巷道分组（预计算）──
    tunnel_by_coalbed = {}
    for c in named_tunnels:
        cb = c.get("coalbed") or "(无)"
        if cb not in tunnel_by_coalbed:
            tunnel_by_coalbed[cb] = []
        tunnel_by_coalbed[cb].append(c)

    # ════════════════════════════════════════════════════════════
    # 输出文本报告 — 严格固定格式，每个区块必定出现，行数固定
    # ════════════════════════════════════════════════════════════
    def _fmt_kv(k, v, w=20):
        return f"  {k:<{w}}{v}"

    def _fmt_list(items, label_fn=None, empty="  (无)"):
        """输出固定 10 行的列表，空数据时补占位符"""
        if not items:
            for i in range(1, 11):
                print(f"  {i:>2}. (无)", file=sys.stderr)
            return
        for i, item in enumerate(items[:10], 1):
            txt = label_fn(item) if label_fn else str(item)
            print(f"  {i:>2}. {txt}", file=sys.stderr)
        if len(items) > 10:
            print(f"      ...及其他 {len(items) - 10} 条", file=sys.stderr)
        else:
            for i in range(len(items) + 1, 11):
                print(f"  {i:>2}. (无)", file=sys.stderr)

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  8373 数据分析报告", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    # 【概况】固定 4 行
    print(f"\n【概况】", file=sys.stderr)
    print(_fmt_kv("设备总数:", total_devices), file=sys.stderr)
    print(_fmt_kv("巷道数 (候选):", f"{len(tunnels)} (系统命名排除: {len(generic_tunnel_names)}, 具名可用: {len(named_tunnels)})"), file=sys.stderr)
    print(_fmt_kv("工作面数 (候选):", len(workfaces)), file=sys.stderr)
    print(_fmt_kv("系统巷道排除示例:", ', '.join(generic_tunnel_names[:5]) if generic_tunnel_names else "无"), file=sys.stderr)

    # 【CAD 数据】
    print(f"\n【CAD 数据】", file=sys.stderr)
    if cad_data:
        print(_fmt_kv("CAD 标注点总数:", len(cad_data)), file=sys.stderr)
        print(_fmt_kv("图纸数量:", f"{len(cad_dwg_distribution)} 张"), file=sys.stderr)
        print(_fmt_kv("图纸类型分布:", " | ".join(f"{t}:{c}" for t, c in cad_type_distribution.most_common())), file=sys.stderr)
        print(_fmt_kv("dwgID 分布:", " | ".join(f"{d}:{c}" for d, c in cad_dwg_distribution.most_common(3))), file=sys.stderr)
        # 噪声 vs 有效
        print(_fmt_kv("  ├─ 纯数字(高程):", f"{cad_stats['numeric']} ({cad_stats['numeric']/cad_stats['total']*100:.1f}%)"), file=sys.stderr)
        print(_fmt_kv("  ├─ 坐标标注:", f"{cad_stats['coordinate']} ({cad_stats['coordinate']/cad_stats['total']*100:.1f}%)"), file=sys.stderr)
        print(_fmt_kv("  ├─ 噪声(阈值/图签):", f"{cad_stats['noise']} ({cad_stats['noise']/cad_stats['total']*100:.1f}%)"), file=sys.stderr)
        print(_fmt_kv("  └─ 有效路标:", f"{cad_stats['total']-cad_stats['numeric']-cad_stats['coordinate']-cad_stats['noise']} ({(cad_stats['total']-cad_stats['numeric']-cad_stats['coordinate']-cad_stats['noise'])/cad_stats['total']*100:.1f}%)"), file=sys.stderr)
        # 路标分类
        total_lm = sum(len(v) for v in _LANDMARKS.values())
        if total_lm > 0:
            print(_fmt_kv("路标覆盖:", f"{total_lm} 个路标 / {len(_LANDMARKS)} 条巷道"), file=sys.stderr)
        # 内容分类详情
        print(_fmt_kv("  传感器标注:", f"{cad_stats['sensor_type']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['sensor_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        print(_fmt_kv("  安装地点:", f"{cad_stats['location']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['location_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        print(_fmt_kv("  巷道名:", f"{cad_stats['tunnel_name']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['tunnel_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        print(_fmt_kv("  分站:", f"{cad_stats['station']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['station_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        print(_fmt_kv("  硐室:", f"{cad_stats['chamber']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['chamber_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        print(_fmt_kv("  设备:", f"{cad_stats['equipment']} 条"), file=sys.stderr)
        for c, cnt in cad_stats['equip_detail'].most_common(5):
            print(_fmt_kv(f"    {c}", f"x{cnt}"), file=sys.stderr)
        if cad_stats['other'] > 0:
            print(_fmt_kv("  其他:", cad_stats['other']), file=sys.stderr)
        # 传感器路标统计（组合模式）
        if _SENSOR_LANDMARKS:
            total_sl = sum(len(v) for v in _SENSOR_LANDMARKS.values())
            print(_fmt_kv("传感器路标组合:", f"{total_sl} 个 ({len(_SENSOR_LANDMARKS)} 条巷道)"), file=sys.stderr)
            st_counts = {}
            for tunnel_sensors in _SENSOR_LANDMARKS.values():
                for info in tunnel_sensors.values():
                    st = info.get("sensor_type", "未知")
                    st_counts[st] = st_counts.get(st, 0) + 1
            print(_fmt_kv("  类型分布:", " | ".join(f"{st}:{cnt}" for st, cnt in sorted(st_counts.items(), key=lambda x: -x[1]))), file=sys.stderr)
    else:
        print(_fmt_kv("状态:", "⚠ 无 cadData — 数据文件中未包含 CAD 图纸标注信息"), file=sys.stderr)
        print(_fmt_kv("说明:", "CAD 数据由 8373 API 的 originData/cadData 提供，用于辅助定位验证"), file=sys.stderr)
        print(_fmt_kv("图纸数量:", "0"), file=sys.stderr)
        print(_fmt_kv("标注点数:", "0"), file=sys.stderr)

    # 【设备过滤】固定 2 行
    print(f"\n【设备过滤】", file=sys.stderr)
    print(_fmt_kv("地面设备 (将跳过):", f"{surface_count}/{total_devices}"), file=sys.stderr)
    print(_fmt_kv("缺少 sensor_type:", f"{len(missing_sensor)}/{total_devices}"), file=sys.stderr)

    # 【Mark Type 分布】固定 4 行
    print(f"\n【Mark Type 分布】", file=sys.stderr)
    other_mt = sum(v for k, v in mark_types.items() if k not in ("B14", "B15", "B16"))
    for mt in ["B14", "B15", "B16"]:
        print(_fmt_kv(f"{mt} ({_MARK_TYPE_TO_SYSTEM.get(mt, '?')}):", mark_types.get(mt, 0)), file=sys.stderr)
    print(_fmt_kv("其他:", other_mt), file=sys.stderr)

    # 【Sensor Type 分布 (Top 10)】固定 11 行
    print(f"\n【Sensor Type 分布 (Top 10)】", file=sys.stderr)
    st_items = sensor_types.most_common(10)
    st_rest = sum(v for _k, v in sensor_types.most_common()[10:])
    _fmt_list(st_items, label_fn=lambda x: f"{x[0]}: {x[1]}")
    print(_fmt_kv("其他 sensor_type:", st_rest), file=sys.stderr)

    # 【Mark Type × Sensor Type 交叉分析 (Top 10)】固定 5 行 + 表头
    print(f"\n【Mark Type × Sensor Type 交叉分析 (Top 10)】", file=sys.stderr)
    top10_sts = all_sts[:10] if all_sts else ["(无)"]
    # 表头
    hdr = "        " + "".join(f"{s:>8}" for s in top10_sts)
    print(f"  {hdr}", file=sys.stderr)
    # B14 / B15 / B16 / 其他 固定 4 行
    for mt in ["B14", "B15", "B16"]:
        row = cross.get(mt, {})
        cells = "".join(f"{row.get(st, 0):>8}" for st in top10_sts)
        print(f"  {mt:>6} {cells}", file=sys.stderr)
    other_cross = {}
    for mt, row in cross.items():
        if mt not in ("B14", "B15", "B16"):
            for st, v in row.items():
                other_cross[st] = other_cross.get(st, 0) + v
    cells = "".join(f"{other_cross.get(st, 0):>8}" for st in top10_sts)
    print(f"  {'其他':>6} {cells}", file=sys.stderr)
    if len(all_sts) > 10:
        print(f"  ...及其他 {len(all_sts) - 10} 个 sensor_type", file=sys.stderr)
    else:
        print(f"  (无其他 sensor_type)", file=sys.stderr)

    # 【巷道煤层分布】固定列出所有煤层
    print(f"\n【巷道煤层分布】", file=sys.stderr)
    coalbed_items = coalbeds.most_common()
    if coalbed_items:
        for cb, cnt in coalbed_items:
            print(_fmt_kv(f"煤层 {cb}:", f"{cnt} 条巷道/工作面"), file=sys.stderr)
    else:
        print(_fmt_kv("煤层:", "(无)"), file=sys.stderr)

    # 【Area 分布 (Top 10)】固定 11 行
    print(f"\n【Area 分布 (Top 10)】", file=sys.stderr)
    area_items = areas.most_common(10)
    area_rest = sum(v for _k, v in areas.most_common()[10:])
    _fmt_list(area_items, label_fn=lambda x: f"{x[0]}: {x[1]} 台")
    print(_fmt_kv("其他区域:", f"{area_rest} 个"), file=sys.stderr)

    # 【区域设备构成 (Top 10)】固定 10 行
    print(f"\n【区域设备构成 (Top 10)】", file=sys.stderr)
    area_comp = []
    for area, cnt in areas.most_common(10):
        detail = area_details.get(area, {})
        mt_str = "  ".join(f"{k}={v}" for k, v in sorted(detail.get("mt", {}).items(), key=lambda x: -x[1])[:2])
        st_str = "  ".join(f"{k}={v}" for k, v in sorted(detail.get("st", {}).items(), key=lambda x: -x[1])[:2])
        area_comp.append(f"{area} ({cnt}台) | {mt_str} | {st_str}")
    _fmt_list(area_comp)

    # 【可用巷道列表 (每组 Top 10)】
    print(f"\n【可用巷道列表 ({len(named_tunnels)}条)】", file=sys.stderr)
    if tunnel_by_coalbed:
        for cb, tlist in sorted(tunnel_by_coalbed.items()):
            print(f"  煤层 {cb} ({len(tlist)}条):", file=sys.stderr)
            t_sorted = sorted(tlist, key=lambda x: x.get("name", ""))
            _fmt_list(t_sorted, label_fn=lambda c: f"{c['name']} ({c.get('type', '')})")
    else:
        print(f"  (无可用巷道)", file=sys.stderr)
        _fmt_list([])

    # 【工作面列表 (Top 10)】固定 11 行
    print(f"\n【工作面列表 ({len(workfaces)}个)】", file=sys.stderr)
    wf_items = sorted(workfaces, key=lambda x: x.get("workFaceName", ""))[:10]
    _fmt_list(wf_items, label_fn=lambda w: f"{w.get('workFaceName', '?')} ({w.get('type', '')})")

    # 【编码提取情况】固定 2 行
    print(f"\n【编码提取情况】", file=sys.stderr)
    print(_fmt_kv("成功提取编码:", f"{code_extracted}/{total_devices} ({code_pct:.1f}%)"), file=sys.stderr)
    print(_fmt_kv("未提取编码:", f"{len(hard_to_match)}/{total_devices} ({100-code_pct:.1f}%)"), file=sys.stderr)

    # 【originData 分析（8385 当前标注数据）】固定 3 行
    print(f"\n【originData 分析（8385 当前标注数据）】", file=sys.stderr)
    if origin_data:
        print(_fmt_kv("设备总数:", len(origin_data)), file=sys.stderr)
        print(_fmt_kv("已有坐标:", f"{origin_has_coord}/{len(origin_data)}"), file=sys.stderr)
        print(_fmt_kv("无坐标:", f"{origin_no_coord}/{len(origin_data)}"), file=sys.stderr)
    else:
        print(_fmt_kv("数据状态:", "(无 — 尚未标注过)"), file=sys.stderr)
        print(_fmt_kv("设备总数:", 0), file=sys.stderr)
        print(_fmt_kv("已有坐标:", "0/0"), file=sys.stderr)
        print(_fmt_kv("无坐标:", "0/0"), file=sys.stderr)

    # 【潜在难匹配设备 (Top 10)】固定 11 行
    print(f"\n【潜在难匹配设备 ({len(hard_to_match)} 台)】", file=sys.stderr)
    print(f"  这些设备 description 中未提取到工作面编码，只能依赖 LCS 文本匹配:", file=sys.stderr)
    _fmt_list(hard_to_match, label_fn=lambda d: f"{d.get('id', '?')}: {d.get('description', '')[:55]}")

    # 【典型设备样例】固定 3 个 mark_type × 3 条 = 9 行 + 3 行标题
    print(f"\n【典型设备样例 (每 Mark Type 前 3)】", file=sys.stderr)
    for mt in ["B14", "B15", "B16"]:
        samples = [d for d in devices if d.get("mark_type") == mt][:3]
        print(f"  {mt} ({_MARK_TYPE_TO_SYSTEM.get(mt, '?')}):", file=sys.stderr)
        for i in range(3):
            if i < len(samples):
                d = samples[i]
                print(f"    {i+1}. {d.get('id', '?')}: {d.get('description', '')[:55]}", file=sys.stderr)
            else:
                print(f"    {i+1}. (无)", file=sys.stderr)

    # 保存 JSON 分析报告
    report = {
        "total_devices": total_devices,
        "total_tunnels": len(tunnels),
        "total_workfaces": len(workfaces),
        "generic_tunnels": len(generic_tunnel_names),
        "generic_tunnel_examples": generic_tunnel_names[:10],
        "surface_devices": surface_count,
        "missing_sensor_type": len(missing_sensor),
        "mark_type_distribution": dict(mark_types),
        "sensor_type_distribution": dict(sensor_types),
        "cross_mark_sensor": cross,
        "area_distribution": dict(areas.most_common(20)),
        "area_details": {k: v for k, v in area_details.items()},
        "coalbed_distribution": dict(coalbeds),
        "code_extracted": code_extracted,
        "code_extracted_rate": round(code_pct, 1),
        "hard_to_match_count": len(hard_to_match),
        "hard_to_match_examples": [
            {"id": d.get("id", "?"), "description": d.get("description", "")[:80].strip()}
            for d in hard_to_match[:10]
        ],
        "named_tunnels_by_coalbed": {
            cb: [{"name": c["name"], "type": c.get("type", "")} for c in tlist]
            for cb, tlist in tunnel_by_coalbed.items()
        },
        "workface_list": [
            {"name": wf.get("workFaceName", "?"), "type": wf.get("type", "")}
            for wf in workfaces
        ],
        "cadData": {
            "has_cad_data": bool(cad_data),
            "total_points": len(cad_data),
            "dwg_count": len(cad_dwg_distribution),
            "type_distribution": dict(cad_type_distribution),
            "dwg_distribution": dict(cad_dwg_distribution),
            "content_tunnel_name_matches": cad_tunnel_name_matches,
            "content_device_desc_matches": cad_device_desc_matches,
        } if cad_data else {
            "has_cad_data": False,
            "note": "数据文件中未包含 CAD 图纸标注信息（cadData 字段）",
        },
    }
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(data_path)),
        f"analysis_{os.path.basename(data_path)}"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n分析报告已保存: {report_path}", file=sys.stderr)

    return report


def _audit_results(results: list) -> dict:
    """后验审查：从匹配结果中识别高风险匹配。
    返回 {"high": [...], "medium": [...], "counts": {...}}
    """
    high = []
    medium = []

    # 按 matched_name 分组，检查功能词漂移
    groups = {}
    for r in results:
        groups.setdefault(r.get("matched_name", ""), []).append(r)

    for r in results:
        desc = r.get("description", "")
        matched = r.get("matched_name", "")
        lcs = r.get("match_lcs", 0)
        score = r.get("match_score", 0)
        reasons = []

        # 规则1: 短名称依赖（LCS=1 但 score 高，说明靠短名称膨胀）
        if lcs == 1 and score >= 5:
            reasons.append("短名称依赖")

        # 规则2: 编码不一致（specific code 在描述中但候选名不含）
        cleaned = strip_prefix(desc)
        code = extract_workface_code(cleaned)
        if code and _is_specific_code(code):
            if not _code_in_name(code, matched):
                reasons.append("编码不一致")

        # 规则3: 语义冲突（描述与候选有硬性冲突但被匹配）
        if _has_hard_semantic_conflict(cleaned, matched):
            reasons.append("语义冲突被绕过")

        # 规则4: 功能词漂移（同一 matched_name 下描述含不同功能词）
        group = groups.get(matched, [])
        if len(group) > 1:
            func_words = set()
            for gr in group:
                gd = strip_prefix(gr.get("description", ""))
                for fw in ["底抽", "回风", "进风", "皮顺", "胶运"]:
                    if fw in gd:
                        func_words.add(fw)
            if len(func_words) > 1:
                reasons.append("功能词漂移")

        if reasons:
            item = {
                "id": r.get("id", ""),
                "description": desc,
                "matched_name": matched,
                "reasons": reasons,
                "lcs": lcs,
                "score": score,
                "confidence": r.get("confidence", ""),
            }
            if any(r in ("短名称依赖", "语义冲突被绕过") for r in reasons):
                high.append(item)
            else:
                medium.append(item)

    return {
        "high": high,
        "medium": medium,
        "counts": {
            "high": len(high),
            "medium": len(medium),
            "短名称依赖": sum(1 for h in high if "短名称依赖" in h["reasons"]),
            "编码不一致": sum(1 for h in high + medium if "编码不一致" in h["reasons"]),
            "语义冲突被绕过": sum(1 for h in high if "语义冲突被绕过" in h["reasons"]),
            "功能词漂移": sum(1 for h in high + medium if "功能词漂移" in h["reasons"]),
        },
    }


def _classify_low_confidence_reasons(low_conf_results: list) -> dict:
    """分类统计低置信度匹配的原因分布。"""
    counts = {}
    for r in low_conf_results:
        desc = r.get("description", "")
        matched = r.get("matched_name", "")
        lcs = r.get("match_lcs", 0)
        code = extract_workface_code(strip_prefix(desc))
        if lcs <= 2:
            counts["LCS过短(≤2)"] = counts.get("LCS过短(≤2)", 0) + 1
        elif code and not _code_in_name(code, matched):
            counts["编码不在候选名中"] = counts.get("编码不在候选名中", 0) + 1
        elif lcs <= 3:
            counts["LCS偏短(=3)"] = counts.get("LCS偏短(=3)", 0) + 1
        else:
            counts["无编码仅LCS"] = counts.get("无编码仅LCS", 0) + 1
    return counts


def _build_report(devices, results, unmatched, summary, audit_data,
                  wind_warnings, generic_tunnel_names, mine_name,
                  phase="match", include_low_in_writeback: bool = True) -> dict:
    """构建结构化报告数据，供 JSON 嵌入和 stderr 模板化输出使用。"""

    # ── 置信度样本（每级 Top 3）──
    conf_groups = {"高": [], "中": [], "低": []}
    for r in results:
        conf = r.get("confidence", "低")
        if conf in conf_groups:
            conf_groups[conf].append(r)
    confidence_samples = {}
    for conf in ["高", "中", "低"]:
        group = sorted(conf_groups[conf], key=lambda x: -x.get("match_score", 0))[:3]
        confidence_samples[conf] = [
            {
                "id": r.get("id", "?"),
                "description": r.get("description", "")[:50],
                "matched_name": r.get("matched_name", "?"),
                "match_score": r.get("match_score", 0),
                "line_percentage": r.get("line_percentage", 0),
            }
            for r in group
        ]

    # ── 可疑匹配样本（Top 10）──
    suspicious_items = [
        {
            "id": r.get("id", "?"),
            "description": r.get("description", "")[:50],
            "matched_name": r.get("matched_name", "?"),
            "match_score": r.get("match_score", 0),
            "suspicious_reason": r.get("suspicious_reason", ""),
        }
        for r in results if r.get("suspicious")
    ]
    suspicious_items = sorted(suspicious_items, key=lambda x: -x["match_score"])[:10]

    # ── 未匹配设备 Top 10 ──
    unmatched_top10 = []
    for u in unmatched[:10]:
        best_cand = ""
        if u.get("candidates"):
            cands = sorted(u["candidates"], key=lambda x: -x.get("score", 0))
            if cands:
                best_cand = f"{cands[0]['name']} (score={cands[0].get('score', 0)})"
        unmatched_top10.append({
            "id": u.get("id", "?"),
            "description": u.get("description", "")[:50],
            "reason": u.get("reason", "UNKNOWN"),
            "best_candidate": best_cand,
        })

    # ── Sensor Type 匹配率 ──
    sensor_total = {}
    for d in devices:
        st = d.get("sensor_type") or _infer_sensor_type(d.get("description", ""), d.get("mark_type"))
        sensor_total[st] = sensor_total.get(st, 0) + 1
    sensor_matched = {}
    for r in results:
        st = r.get("sensor_type", "")
        sensor_matched[st] = sensor_matched.get(st, 0) + 1
    sensor_match_rate = []
    for st in sorted(sensor_total, key=lambda s: -sensor_total[s]):
        matched_n = sensor_matched.get(st, 0)
        total_n = sensor_total[st]
        pct = (matched_n / total_n * 100) if total_n else 0
        sensor_match_rate.append({
            "sensor_type": st,
            "matched": matched_n,
            "total": total_n,
            "rate_pct": round(pct, 1),
        })

    # ── 空间分布 ──
    spatial = {"buckets": {}, "coordinate_range": {}}
    if results:
        buckets = {"0-20%": 0, "20-40%": 0, "40-60%": 0, "60-80%": 0, "80-100%": 0}
        xs, ys, zs = [], [], []
        for r in results:
            lp = r.get("line_percentage", 0)
            if lp <= 20:
                buckets["0-20%"] += 1
            elif lp <= 40:
                buckets["20-40%"] += 1
            elif lp <= 60:
                buckets["40-60%"] += 1
            elif lp <= 80:
                buckets["60-80%"] += 1
            else:
                buckets["80-100%"] += 1
            c = r.get("coordinates", {})
            if c.get("x") is not None:
                xs.append(c["x"]); ys.append(c["y"]); zs.append(c["z"])
        spatial["buckets"] = buckets
        if xs:
            spatial["coordinate_range"] = {
                "x": [round(min(xs), 2), round(max(xs), 2)],
                "y": [round(min(ys), 2), round(max(ys), 2)],
                "z": [round(min(zs), 2), round(max(zs), 2)],
            }

    # ── 巷道设备密度 Top 15 ──
    by_tunnel = {}
    for r in results:
        name = r.get("matched_name", "")
        by_tunnel[name] = by_tunnel.get(name, 0) + 1
    tunnel_density = [
        {"name": name, "count": cnt}
        for name, cnt in sorted(by_tunnel.items(), key=lambda x: -x[1])[:15]
    ]

    s = summary

    # ── 低置信度样本 (Top 5) —— 已包含在回写中，建议审查 ──
    low_conf_results = [r for r in results if r.get("confidence") == "低"]
    low_conf_results_sorted = sorted(low_conf_results, key=lambda x: -x.get("match_score", 0))
    low_confidence_samples = [
        {
            "id": r.get("id", "?"),
            "description": r.get("description", "")[:50],
            "matched_name": r.get("matched_name", "?"),
            "match_score": r.get("match_score", 0),
            "match_lcs": r.get("match_lcs", 0),
            "tunnel_type": r.get("tunnel_type", ""),
        }
        for r in low_conf_results_sorted[:5]
    ]

    # ── 回写计划 ──
    bc = s.get("by_confidence", {"高": 0, "中": 0, "低": 0})
    if include_low_in_writeback:
        writeback_count = bc.get("高", 0) + bc.get("中", 0) + bc.get("低", 0)
        held_back_count = 0
    else:
        writeback_count = bc.get("高", 0) + bc.get("中", 0)
        held_back_count = bc.get("低", 0)
    writeback_plan = {
        "writeback_count": writeback_count,
        "held_back_count": held_back_count,
        "by_tier": {"高": bc.get("高", 0), "中": bc.get("中", 0), "低": bc.get("低", 0)},
        "held_back_reasons": _classify_low_confidence_reasons(low_conf_results),
        "include_low_in_writeback": include_low_in_writeback,
    }

    return {
        "phase": phase,
        "mine_name": mine_name,
        "overview": {
            "total_devices": s["total"],
            "matched": s["matched"],
            "unmatched": s["unmatched"],
        },
        "match_summary": {
            "total": s["total"],
            "matched": s["matched"],
            "unmatched": s["unmatched"],
            "filtered_by_id": s.get("filtered_by_id", 0),
            "generic_tunnels_skipped": s.get("generic_tunnels_skipped", 0),
            "unnamed_tunnels_skipped": s.get("unnamed_tunnels_skipped", 0),
            "wind_spacing_warnings": s.get("wind_spacing_warnings", 0),
            "suspicious_count": s.get("suspicious_count", 0),
            "by_confidence": s.get("by_confidence", {"高": 0, "中": 0, "低": 0}),
            "by_mark_type": s.get("by_mark_type", {}),
            "by_sensor_type": s.get("by_sensor_type", {}),
            "by_reason": s.get("by_reason", {}),
            "generic_tunnel_names": generic_tunnel_names[:5],
        },
        "confidence_samples": confidence_samples,
        "low_confidence_samples": low_confidence_samples,
        "writeback_plan": writeback_plan,
        "suspicious_devices": {
            "total": s.get("suspicious_count", 0),
            "top_10": suspicious_items,
        },
        "unmatched_devices": {
            "total": len(unmatched),
            "by_reason": s.get("by_reason", {}),
            "top_10": unmatched_top10,
        },
        "audit": {
            "counts": audit_data.get("counts", {}),
            "high": audit_data.get("high", [])[:5],
            "medium": audit_data.get("medium", [])[:5],
        },
        "sensor_type_match_rate": sensor_match_rate,
        "spatial_distribution": spatial,
        "tunnel_density": tunnel_density,
        "warnings": [
            {"type": w.get("type", ""), "message": w.get("message", "")}
            for w in wind_warnings
        ],
    }


def _print_report_stderr(report: dict):
    """将结构化报告模板化输出到 stderr。每个区块必定出现，格式固定。"""

    def _p(text=""):
        print(text, file=sys.stderr)

    s = report["match_summary"]

    # ── 汇总 ──
    _p(f"\n{'='*60}")
    _p(f"  匹配汇总报告")
    _p(f"{'='*60}")

    _p(f"\n【概况】")
    _p(f"  总计: {s['total']}  匹配: {s['matched']} ✓  未匹配: {s['unmatched']}")
    bm = s.get("by_mark_type", {})
    _p(f"  类型: B14={bm.get('B14', 0)}  B15={bm.get('B15', 0)}  B16={bm.get('B16', 0)}")
    bs = s.get("by_sensor_type", {})
    if bs:
        sensor_fmt = "  ".join(f"{k}={v}" for k, v in sorted(bs.items(), key=lambda x: -x[1])[:8])
        _p(f"  Sensor: {sensor_fmt}")
    if s.get("filtered_by_id", 0):
        _p(f"  设备 ID 过滤: 已跳过 {s['filtered_by_id']} 台")
    br = s.get("by_reason", {})
    if br:
        reasons_fmt = "  ".join(f"{k}={v}" for k, v in sorted(br.items(), key=lambda x: -x[1]))
        _p(f"  未匹配原因: {reasons_fmt}")
    if s.get("wind_spacing_warnings", 0):
        _p(f"  风速间距警告: {s['wind_spacing_warnings']}")
    if s.get("generic_tunnels_skipped", 0):
        excluded = s.get("generic_tunnel_names", [])
        sample = excluded[:5]
        _p(f"  系统巷道已排除: {s['generic_tunnels_skipped']} 条"
           f" ({', '.join(sample)}{'...' if len(excluded) > 5 else ''})")
    if s.get("unnamed_tunnels_skipped", 0):
        _p(f"  无名称巷道已排除: {s['unnamed_tunnels_skipped']} 条")

    # ── CAD 路标统计 ──
    if s.get("landmark_count", 0) > 0:
        total_lm = sum(len(v) for v in _LANDMARKS.values())
        _p(f"  CAD 路标: {total_lm} 个路标 / {len(_LANDMARKS)} 条巷道"
           f"  → {s['landmark_count']} 台设备通过路标精确定位")
    elif _LANDMARKS:
        total_lm = sum(len(v) for v in _LANDMARKS.values())
        _p(f"  CAD 路标: {total_lm} 个路标 / {len(_LANDMARKS)} 条巷道"
           f"  → 无设备匹配到路标")
    elif s.get("cad_data_count", 0) > 0:
        _p(f"  CAD 数据: {s['cad_data_count']} 条标注点"
           f"  → 无可识别路标")

    # ── 置信度分布（独立区块，含百分比条）──
    bc = s.get("by_confidence", {})
    total_all = s["total"]
    high = bc.get("高", 0)
    medium = bc.get("中", 0)
    low = bc.get("低", 0)
    unmatched_count = s["unmatched"]

    def _pct_bar(n, total):
        if total <= 0:
            return "0.0%", ""
        pct = n / total * 100
        bar = "█" * min(20, max(0, round(pct / 5)))
        return f"{pct:.1f}%", bar

    _p(f"\n【置信度分布】")
    for label, count in [("高", high), ("中", medium), ("低", low)]:
        pct_str, bar = _pct_bar(count, total_all)
        _p(f"  {label}:   {count:>3} 条 ({pct_str})  {bar}")
    if unmatched_count:
        pct_str, bar = _pct_bar(unmatched_count, total_all)
        _p(f"  未匹配: {unmatched_count:>3} 台 ({pct_str})  {bar}")
    match_rate = (s["matched"] / total_all * 100) if total_all > 0 else 0
    _p(f"  匹配率: {match_rate:.1f}%")

    # ── 巷道设备密度（始终输出，无数据时显示"无"）──
    td = report.get("tunnel_density", [])
    _p(f"\n【巷道设备密度 (Top 15)】")
    if td:
        for item in td:
            _p(f"    {item['name']}: {item['count']} 台")
    else:
        _p(f"    (无)")

    # ── 置信度样本（每级固定 3 条，不足补"无"）──
    _p(f"\n【置信度样本 (Top 3 每级)】")
    for conf in ["高", "中", "低"]:
        samples = report["confidence_samples"].get(conf, [])
        _p(f"  {conf}:")
        for i in range(3):
            if i < len(samples):
                r = samples[i]
                _p(f"    {r['id']} → {r['matched_name']}  score={r['match_score']}  {r['line_percentage']}%  {r['description']}")
            else:
                _p(f"    (无)")

    # ── 可疑匹配设备（需人工确认）──
    sd = report.get("suspicious_devices", {})
    sd_total = sd.get("total", 0)
    _p(f"\n【可疑匹配设备 ({sd_total} 台)】")
    if sd_total > 0:
        _p(f"  ⚠ 以下设备匹配结果的语义可疑，建议逐条确认：")
        for item in sd.get("top_10", []):
            _p(f"  {item['id']} → {item['matched_name']}  score={item['match_score']}")
            _p(f"    原因: {item['suspicious_reason']}  {item['description']}")
        if sd_total > 10:
            _p(f"    ...及其他 {sd_total - 10} 台")
    else:
        _p(f"    (无)")

    # ── 未匹配设备（固定 10 条，不足补"无"）──
    um = report["unmatched_devices"]
    _p(f"\n【未匹配设备 ({um['total']} 台)】")
    top10 = um.get("top_10", [])
    if top10:
        for u in top10:
            best = f" 最佳候选: {u['best_candidate']}" if u['best_candidate'] else ""
            _p(f"  {u['id']}: {u['description']}")
            _p(f"    → 原因: {u['reason']}{best}")
    else:
        _p(f"    (无)")
    if um["total"] > 10:
        _p(f"    ...及其他 {um['total'] - 10} 台")

    # ── Sensor Type 匹配率（始终输出）──
    smr = report.get("sensor_type_match_rate", [])
    _p(f"\n【Sensor Type 匹配率】")
    if smr:
        for item in smr:
            _p(f"  {item['sensor_type']}: {item['matched']}/{item['total']} = {item['rate_pct']:.1f}%")
    else:
        _p(f"    (无)")

    # ── 空间分布（始终输出）──
    sp = report.get("spatial_distribution", {})
    _p(f"\n【空间分布】")
    buckets = sp.get("buckets", {})
    if buckets:
        bk_str = "  ".join(f"{k}={v}" for k, v in buckets.items())
        _p(f"  沿线百分比: {bk_str}")
    else:
        _p(f"  沿线百分比: (无)")
    cr = sp.get("coordinate_range", {})
    if cr:
        xr, yr, zr = cr["x"], cr["y"], cr["z"]
        _p(f"  坐标范围: X=[{xr[0]}, {xr[1]}]  Y=[{yr[0]}, {yr[1]}]  Z=[{zr[0]}, {zr[1]}]")
    else:
        _p(f"  坐标范围: (无)")

    # ── 审计摘要（始终输出，无风险时显示 0）──
    ac = report.get("audit", {}).get("counts", {})
    _p(f"\n{'='*60}")
    _p(f"  审查摘要")
    _p(f"{'='*60}")
    high_n = ac.get("high", 0)
    medium_n = ac.get("medium", 0)
    _p(f"  ⚠ 高风险匹配: {high_n}"
       f" (短名称依赖={ac.get('短名称依赖', 0)},"
       f" 编码不一致={ac.get('编码不一致', 0)},"
       f" 语义冲突被绕过={ac.get('语义冲突被绕过', 0)})")
    _p(f"  ℹ 中风险匹配: {medium_n}"
       f" (功能词漂移={ac.get('功能词漂移', 0)})")
    if high_n or medium_n:
        _p(f"  提示: 使用 --audit 查看详情")
    else:
        _p(f"  (无风险项)")

    # ── 高风险详情（固定 5 条，不足补"无"）──
    if high_n:
        _p(f"\n【高风险匹配详情 (Top 5)】")
        high_items = report["audit"].get("high", [])
        for i in range(5):
            if i < len(high_items):
                h = high_items[i]
                _p(f"  {h['id']} → {h['matched_name']}  reasons={'/'.join(h['reasons'])}  score={h['score']}  lcs={h['lcs']}")
            else:
                _p(f"  (无)")

    # ── 中风险详情（固定 5 条，不足补"无"）──
    if medium_n:
        _p(f"\n【中风险匹配详情 (Top 5)】")
        med_items = report["audit"].get("medium", [])
        for i in range(5):
            if i < len(med_items):
                m = med_items[i]
                _p(f"  {m['id']} → {m['matched_name']}  reasons={'/'.join(m['reasons'])}  score={m['score']}  lcs={m['lcs']}")
            else:
                _p(f"  (无)")

    # ── 回写计划 ──
    wp = report.get("writeback_plan", {})
    include_low = wp.get("include_low_in_writeback", True)
    if wp:
        _p(f"\n{'='*60}")
        _p(f"  回写计划")
        _p(f"{'='*60}")
        wb = wp.get("writeback_count", 0)
        hb = wp.get("held_back_count", 0)
        by_tier = wp.get("by_tier", {})
        low_count = by_tier.get('低', 0)
        if include_low and low_count > 0:
            _p(f"  待回写 8385: {wb} 条  (高={by_tier.get('高', 0)}, 中={by_tier.get('中', 0)}, 低={low_count} ⚠)")
            _p(f"  ⚠ 包含 {low_count} 条低置信度匹配，建议审查后确认")
        else:
            _p(f"  待回写 8385: {wb} 条  (高={by_tier.get('高', 0)}, 中={by_tier.get('中', 0)})")
        if hb > 0 and not include_low:
            _p(f"  暂缓回写:    {hb} 条  (低置信度 —— 宁缺毋滥)")
            hbr = wp.get("held_back_reasons", {})
            if hbr:
                reasons_fmt = "  ".join(f"{k}={v}" for k, v in sorted(hbr.items(), key=lambda x: -x[1]))
                _p(f"  暂缓原因: {reasons_fmt}")

    # ── 低置信度样本（已包含在回写中 ⚠）──
    lcs_samples = report.get("low_confidence_samples", [])
    low_total = report.get("writeback_plan", {}).get("by_tier", {}).get("低", 0)
    if lcs_samples and include_low:
        _p(f"\n【低置信度样本 (Top 5) —— 已包含在回写中 ⚠】")
        for s in lcs_samples:
            _p(f"  {s['id']} → {s['matched_name']}"
               f"  score={s['match_score']}  lcs={s['match_lcs']}"
               f"  {s['description']}")
        _p(f"  以上 {low_total} 条低置信度匹配将一并回写，建议人工审查确认")
    elif lcs_samples and not include_low:
        _p(f"\n【暂缓回写样本 (Top 5) —— 宁缺毋滥】")
        for s in lcs_samples:
            _p(f"  {s['id']} → {s['matched_name']}"
               f"  score={s['match_score']}  lcs={s['match_lcs']}"
               f"  {s['description']}")
    elif low_total == 0:
        _p(f"\n【低置信度匹配】")
        _p(f"  (无 —— 所有匹配置信度达标)")


# ── 回写前备份 ────────────────────────────────────────────────────
def _confirm_overwrite(to_writeback_count: int, held_back_count: int,
                        auto_yes: bool = False,
                        low_confidence_count: int = 0) -> bool:
    """回写前提示用户确认覆盖原有标注数据。

    Args:
        to_writeback_count: 待回写设备数
        held_back_count: 暂缓设备数
        auto_yes: True 时跳过提示直接确认（用于 --yes 参数）
        low_confidence_count: 低置信度匹配数量（强制提示，不受 held_back 影响）

    Returns:
        True 表示确认回写，False 表示取消
    """
    if auto_yes:
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  ⚠ 自动跳过确认（--yes 模式）", file=sys.stderr)
        print(f"  ⚠ 即将回写 {to_writeback_count} 条定位结果到策略 8385", file=sys.stderr)
        if low_confidence_count:
            print(f"  ⚠ 包含 {low_confidence_count} 条低置信度匹配（宁缺毋滥已关闭）", file=sys.stderr)
        print(f"  ⚠ 此操作将会覆盖 8385 中原有的标注数据！", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        return True

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  ⚠ 即将回写 {to_writeback_count} 条定位结果到策略 8385", file=sys.stderr)
    if low_confidence_count:
        print(f"  ⚠ 包含 {low_confidence_count} 条低置信度匹配（宁缺毋滥已关闭）", file=sys.stderr)
    print(f"  ⚠ 此操作将会覆盖 8385 中原有的标注数据！", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)

    try:
        answer = input("  确认覆盖原有数据并提交回写？(y/N): ").strip().lower()
        return answer in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False


def _backup_origin_data(data: dict, mine_name: str) -> str:
    """回写前备份当前 8385 originData 到 data/backup/。

    从 8373 响应中提取 originData（即 8385 当前存储的定位标注数据），
    保存到备份目录，以便回写出问题时恢复。

    Args:
        data: 包含 originData 字段的字典（output_full 或 locator 结果文件）
        mine_name: 矿名，用于备份文件命名

    Returns:
        备份文件路径，若无 originData 则返回空字符串
    """
    origin = data.get("originData", [])
    if not origin:
        print("  ! 无 originData 可备份，跳过", file=sys.stderr)
        return ""

    backup_dir = PROJECT_ROOT / "data" / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"data_8385_{mine_name}_{timestamp}.json"

    backup_obj = {"results": origin}
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump(backup_obj, f, ensure_ascii=False, indent=2)

    print(f"  → 8385 备份已保存: {backup_path} ({len(origin)} 条)", file=sys.stderr)
    return str(backup_path)


# ── 输出+回写 ──────────────────────────────────────────────────────
def _save_and_writeback(output: dict, username: str, output_mode: str = "full",
                        output_full: dict = None, html_mode: str = "auto",
                        match_only: bool = False, report: dict = None,
                        auto_yes: bool = False):
    """输出 JSON 到 stdout，保存到文件，可选回写策略 8385。

    Args:
        match_only: True 时跳过 8385 回写，仅输出+保存。
        report: 结构化报告数据，会注入 output_full['report'] 并用于 stderr 模板化输出。
    """
    print(json.dumps(output, ensure_ascii=False, indent=2))

    result_save_dir = PROJECT_ROOT / "data" / "output"
    result_save_dir.mkdir(parents=True, exist_ok=True)
    # 文件始终保存完整数据，不受 output_mode 裁剪影响
    save_data = output_full if output_full is not None else output
    mine_name = save_data.get("mine_name", "")
    result_save_path = result_save_dir / f"locator_result_{username}_{mine_name}.json"

    # 将 report 嵌入完整数据
    if report is not None:
        save_data["report"] = report

    with open(result_save_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {result_save_path}", file=sys.stderr)

    # ── 可选: 自动生成 CesiumJS 3D 可视化 ──
    if html_mode != "never":
        cesium_script = PROJECT_ROOT / "data" / "output" / "generate_cesium_html.py"
        if cesium_script.exists():
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("cesium_gen", str(cesium_script))
                cesium_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(cesium_mod)
                if hasattr(cesium_mod, "generate_html"):
                    data_8373_path = str(result_save_dir / f"data_8373_{mine_name}.json")
                    if not Path(data_8373_path).exists():
                        data_8373_path = None
                    html_path = cesium_mod.generate_html(
                        str(result_save_path),
                        data_8373_path=data_8373_path,
                    )
                    print(f"  → 3D 可视化: {html_path}", file=sys.stderr)
            except Exception as e:
                if html_mode == "always":
                    print(f"  ! HTML 可视化生成失败: {e}", file=sys.stderr)

    # ── 模板化 stderr 输出（由 _print_report_stderr 统一处理）──
    if report is not None:
        _print_report_stderr(report)

    if not match_only:
        import tempfile
        full = output_full if output_full is not None else output
        results_list = full.get("results", [])

        # ── 回写前备份当前 8385 originData ──
        mine_name = full.get("mine_name", "")
        if not full.get("originData"):
            try:
                resp = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_json")
                raw = resp.get("data", {})
                if isinstance(raw, dict) and "originData" in raw:
                    full["originData"] = raw["originData"]
            except Exception:
                pass
        _backup_origin_data(full, mine_name)

    # ── 置信度过滤：低置信度也回写，但报告给用户 ──
        to_writeback, held_back = _filter_low_confidence(results_list, include_low=True)

        print(f"\n[3/3] 准备回写定位结果到策略 8385...", file=sys.stderr)
        low_count = sum(1 for r in to_writeback if r.get("confidence") == "低")
        print(f"  待回写: {len(to_writeback)} 条 (含 {low_count} 条低置信度 ⚠)", file=sys.stderr)

        for r in to_writeback[:5]:
            cid = r.get("id", "?")
            cname = r.get("matched_name", "?")
            coords = r.get("coordinates", {})
            conf = r.get("confidence", "")
            print(f"    {cid} → {cname}  ({coords.get('x', 0):.2f}, {coords.get('y', 0):.2f}, {coords.get('z', 0):.2f})  [{conf}]", file=sys.stderr)
        if len(to_writeback) > 5:
            print(f"    ...及其他 {len(to_writeback) - 5} 条", file=sys.stderr)

        # ── 用户确认 ──
        low_count = sum(1 for r in to_writeback if r.get("confidence") == "低")
        if not _confirm_overwrite(len(to_writeback), len(held_back), auto_yes=auto_yes, low_confidence_count=low_count):
            print(f"  ⏸ 回写已取消。备份文件保留在 data/backup/ 中。", file=sys.stderr)
            print(f"  确认后可通过: --writeback <结果文件> 再次回写", file=sys.stderr)
            return

        writeback_data = dict(full)
        writeback_data["results"] = to_writeback
        writeback_data["total_low_confidence"] = low_count
        data_param = json.dumps(writeback_data, ensure_ascii=False)
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
                tf.write(data_param)
                tmp_path = tf.name
            resp = call_strategy_api(8385, username, action="execute", param_file=f"data={tmp_path}")
            code = resp.get('code', 'unknown')
            if code == 100:
                print(f"  → 8385 回写成功 (code=100, 实际写入 {len(to_writeback)} 条)", file=sys.stderr)
            else:
                msg = resp.get('msg', '') or resp.get('message', '')
                print(f"  ! 8385 回写异常: code={code}, msg={msg}", file=sys.stderr)
            os.unlink(tmp_path)
        except Exception as e:
            print(f"  ! 8385 回写失败: {e}", file=sys.stderr)
    else:
        # ── match-only 模式：提示用户关注低置信度项 ──
        full = output_full if output_full is not None else output
        results_list = full.get("results", [])
        _to_writeback, held_back = _filter_low_confidence(results_list, include_low=True)
        low_count = sum(1 for r in _to_writeback if r.get("confidence") == "低")
        if low_count:
            print(f"\n  ⏸ 匹配完成（{len(_to_writeback)} 条待回写, 含 {low_count} 条低置信度 ⚠）", file=sys.stderr)
            print(f"    宁缺毋滥已关闭，低置信度将一并回写，请审查后确认", file=sys.stderr)
        else:
            print(f"\n  ⏸ 匹配完成，等待确认后回写", file=sys.stderr)
        print(f"    使用 --writeback 或再次运行回写", file=sys.stderr)

def _writeback_from_file(result_path: str, username: str, auto_yes: bool = False):
    """从已保存的结果文件回写 8385，不重复匹配。
    """

    import tempfile
    print(f"[1/1] 从文件加载结果: {result_path}", file=sys.stderr)
    data = _load_json_file(result_path)
    results_list = data.get("results", [])

    # ── 回写前备份当前 8385 originData ──
    mine_name = data.get("mine_name", "")
    # 如果结果文件中没有 originData，主动从 8373 API 获取
    if not data.get("originData"):
        try:
            print(f"  → 结果文件无 originData，从 API 获取...", file=sys.stderr)
            resp = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_json")
            raw = resp.get("data", {})
            if isinstance(raw, dict) and "originData" in raw:
                data["originData"] = raw["originData"]
        except Exception as e:
            print(f"  ! API 获取 originData 失败: {e}", file=sys.stderr)
    _backup_origin_data(data, mine_name)
    # ── 置信度过滤：低置信度也回写，但报告给用户 ──
    to_writeback, held_back = _filter_low_confidence(results_list, include_low=True)

    print(f"  → 已加载 {len(results_list)} 条结果", file=sys.stderr)
    low_count = sum(1 for r in to_writeback if r.get("confidence") == "低")
    print(f"  待回写: {len(to_writeback)} 条 (含 {low_count} 条低置信度 ⚠)", file=sys.stderr)

    for r in to_writeback[:5]:
        cid = r.get("id", "?")
        cname = r.get("matched_name", "?")
        coords = r.get("coordinates", {})
        conf = r.get("confidence", "")
        print(f"    {cid} → {cname}  ({coords.get('x', 0):.2f}, {coords.get('y', 0):.2f}, {coords.get('z', 0):.2f})  [{conf}]", file=sys.stderr)
    if len(to_writeback) > 5:
        print(f"    ...及其他 {len(to_writeback) - 5} 条", file=sys.stderr)

    # ── 用户确认 ──
    low_count = sum(1 for r in to_writeback if r.get("confidence") == "低")
    if not _confirm_overwrite(len(to_writeback), len(held_back), auto_yes=auto_yes, low_confidence_count=low_count):
        print(f"  ⏸ 回写已取消。备份文件保留在 data/backup/ 中。", file=sys.stderr)
        return

    writeback_data = dict(data)
    writeback_data["results"] = to_writeback
    writeback_data["_held_back_low_confidence"] = len(held_back)
    writeback_data["total_low_confidence"] = low_count
    data_param = json.dumps(writeback_data, ensure_ascii=False)
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            tf.write(data_param)
            tmp_path = tf.name
        resp = call_strategy_api(8385, username, action="execute", param_file=f"data={tmp_path}")
        code = resp.get('code', 'unknown')
        if code == 100:
            print(f"  → 8385 回写成功 (code=100, 实际写入 {len(to_writeback)} 条)", file=sys.stderr)
        else:
            msg = resp.get('msg', '') or resp.get('message', '')
            print(f"  ! 8385 回写异常: code={code}, msg={msg}", file=sys.stderr)
        os.unlink(tmp_path)
    except Exception as e:
        print(f"  ! 8385 回写失败: {e}", file=sys.stderr)


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
    parser.add_argument("--output-mode", choices=["full", "summary", "unmatched", "json-summary", "audit"],
                        default="full",
                        help="输出模式: full=完整结果, summary=仅汇总, unmatched=仅未匹配(含候选), json-summary=汇总JSON")
    parser.add_argument("--analyze", metavar="PATH",
                        help="分析 8373 数据文件的结构化报告（仅分析，不匹配退出）")
    parser.add_argument("--html", choices=["auto", "always", "never"], default="auto",
                        help="CesiumJS 可视化: auto=自动生成路径, always=强制生成, never=跳过 (默认 auto)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过回写前的覆盖确认提示（用于脚本自动化）")
    parser.add_argument("--match-only", action="store_true",
                        help="仅匹配不回写（展示汇总后等用户确认，再单独 --writeback）")
    parser.add_argument("--writeback", metavar="RESULT_JSON",
                        help="从已保存的结果文件回写 8385，不重复匹配")
    parser.add_argument("--device-ids", metavar="IDS",
                        help="只匹配指定的设备 ID（逗号分隔），如 --device-ids ID1,ID2,ID3")
    parser.add_argument("--device-ids-file", metavar="PATH",
                        help="从文件读取设备 ID 列表（JSON 数组 / 逗号分隔 / 含 json:/text:/userinput: 前缀的标签行均可）")
    args = parser.parse_args()
    username = args.username

    if args.analyze:
        _generate_analysis_report(args.analyze)
        return

    if args.writeback:
        _writeback_from_file(args.writeback, username, auto_yes=args.yes)
        return

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
    generic_tunnel_skipped = 0
    unnamed_tunnel_skipped = 0
    generic_tunnel_names = []
    origin_data = []
    cad_data_count = 0

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
                if "originData" in data:
                    origin_data = data.get("originData", [])
                    print(f"  → 文件 originData: {len(origin_data)} 条", file=sys.stderr)
                if "tunnels" in data or "workfaces" in data:
                    # 先校验再提取
                    validated = {}
                    if "tunnels" in data:
                        validated["tunnels"] = _validate_tunnels(data["tunnels"])
                    if "workfaces" in data:
                        validated["workfaces"] = _validate_workfaces(data["workfaces"])
                    candidates, code_to_candidates, prefix_to_candidates, coalbed_map, g_skip, u_skip, g_names = _extract_candidates(validated)
                    generic_tunnel_skipped += g_skip
                    unnamed_tunnel_skipped += u_skip
                    generic_tunnel_names.extend(g_names)
                    print(f"  → 文件候选: {len(candidates)} 个", file=sys.stderr)
                # ── CAD 路标构建 ──
                if "cadData" in data:
                    cad_data = data["cadData"]
                    cad_data_count = len(cad_data)
                    _build_sensor_id_map(devices, cad_data)
                    _LANDMARKS.clear()
                    _LANDMARKS.update(_build_landmarks(cad_data, data.get("tunnels", [])))
                    total_lm = sum(len(v) for v in _LANDMARKS.values())
                    if total_lm > 0:
                        print(f"  → CAD 路标: {total_lm} 个 ({len(_LANDMARKS)} 条巷道), 原始标注: {cad_data_count}", file=sys.stderr)
                    else:
                        print(f"  → CAD 数据: {cad_data_count} 条标注点 (无可识别路标)", file=sys.stderr)
            elif isinstance(data, list):
                devices, candidates, u_skip = classify_items(data)
                unnamed_tunnel_skipped += u_skip
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
            candidates, code_to_candidates, prefix_to_candidates, coalbed_map, g_skip, u_skip, g_names = _extract_candidates(merged_cand)
            generic_tunnel_skipped += g_skip
            unnamed_tunnel_skipped += u_skip
            generic_tunnel_names.extend(g_names)
            print(f"  → 文件候选合计: {len(candidates)} 个", file=sys.stderr)

    # 2b. 从 API 补全缺失的数据
    need_api_devices = not devices
    need_api_candidates = not candidates

    if need_api_devices or need_api_candidates:
        print(f"[2/2] 从 API 补全数据...", file=sys.stderr)
        resp_dev = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_json")
        raw_data = resp_dev.get("data", {})

        # 捕获 originData（8385 当前存储的定位标注数据）
        if isinstance(raw_data, dict) and "originData" in raw_data:
            origin_data = raw_data["originData"]

        if isinstance(raw_data, dict) and "devices" in raw_data:
            if need_api_devices:
                devices = _validate_devices(raw_data.get("devices", []))
                print(f"  → API devices: {len(devices)} 个", file=sys.stderr)
            if need_api_candidates:
                candidates, code_to_candidates, prefix_to_candidates, coalbed_map, g_skip, u_skip, g_names = _extract_candidates(raw_data)
                generic_tunnel_skipped += g_skip
                unnamed_tunnel_skipped += u_skip
                generic_tunnel_names.extend(g_names)
                print(f"  → API 候选: {len(candidates)} 个", file=sys.stderr)
                # ── CAD 路标构建 ──
                if isinstance(raw_data, dict) and "cadData" in raw_data:
                    cad_data = raw_data["cadData"]
                    cad_data_count = len(cad_data)
                    _build_sensor_id_map(devices, cad_data)
                    _LANDMARKS.clear()
                    _LANDMARKS.update(_build_landmarks(cad_data, raw_data.get("tunnels", [])))
                    total_lm = sum(len(v) for v in _LANDMARKS.values())
                    if total_lm > 0:
                        print(f"  → CAD 路标: {total_lm} 个 ({len(_LANDMARKS)} 条巷道), 原始标注: {cad_data_count}", file=sys.stderr)
                    else:
                        print(f"  → CAD 数据: {cad_data_count} 条标注点 (无可识别路标)", file=sys.stderr)
        else:
            device_items = extract_items(raw_data)
            if need_api_devices:
                api_devices, _, u_skip = classify_items(device_items)
                unnamed_tunnel_skipped += u_skip
                devices = _validate_devices(api_devices)
                print(f"  → API devices: {len(devices)} 个", file=sys.stderr)
            if need_api_candidates:
                print(f"  → 获取工作面/巷道 (get_data)...", file=sys.stderr)
                resp_cand = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_data")
                cand_items = extract_items(resp_cand.get("data"))
                candidates, code_to_candidates, prefix_to_candidates, coalbed_map, g_skip, u_skip, g_names = _extract_candidates(cand_items)
                generic_tunnel_skipped += g_skip
                unnamed_tunnel_skipped += u_skip
                generic_tunnel_names.extend(g_names)
                print(f"  → API 候选: {len(candidates)} 个", file=sys.stderr)

    # 如果用了文件+API混合，保存合并结果
    if use_file and (args.load_devices or args.load_tunnels or args.load_workfaces):
        save_dir = PROJECT_ROOT / "data" / "output"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"data_8373_{mine_name}.json"
        merged = {"devices": devices, "candidates": candidates}
        if origin_data:
            merged["originData"] = origin_data
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  → 合并结果已保存: {save_path}", file=sys.stderr)
    elif not use_file:
        save_dir = PROJECT_ROOT / "data" / "output"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"data_8373_{mine_name}.json"
        merged = {"devices": devices, "candidates": candidates}
        if origin_data:
            merged["originData"] = origin_data
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"  → 已保存: {save_path}", file=sys.stderr)

    print(f"  → 设备: {len(devices)} 个, 候选名: {len(candidates)} 个", file=sys.stderr)

    # ── 两阶段流程硬约束 ──
    # 默认禁止一步完成匹配+回写，强制分步执行。
    # 只有显式 --yes 才能绕过（用于自动化/CI）。
    if args.load and not args.match_only and not args.yes:
        print("\n============================================================", file=sys.stderr)
        print("  错误: 直接匹配+回写已禁用（两阶段流程约束）", file=sys.stderr)
        print("============================================================", file=sys.stderr)
        print("请分步执行：", file=sys.stderr)
        print(f"  阶段 1（匹配）: python locator.py {username} --load {args.load} --match-only", file=sys.stderr)
        print(f"  阶段 2（回写）: python locator.py {username} --writeback data/output/locator_result_{username}_{mine_name}.json", file=sys.stderr)
        print("\n或使用 --yes 显式跳过确认（仅用于自动化/CI）：", file=sys.stderr)
        print(f"  python locator.py {username} --load {args.load} --yes", file=sys.stderr)
        print("============================================================", file=sys.stderr)
        sys.exit(1)

    # ── 设备 ID 过滤 ──
    filtered_by_id = 0
    if args.device_ids or args.device_ids_file:
        target_ids = set()
        if args.device_ids:
            for part in args.device_ids.split(","):
                part = part.strip()
                if part:
                    target_ids.add(part)
        if args.device_ids_file:
            target_ids.update(_parse_device_ids_from_file(args.device_ids_file))
        if target_ids:
            before = len(devices)
            devices = [d for d in devices if d.get("id") in target_ids]
            filtered_by_id = before - len(devices)
            print(f"  → 设备 ID 过滤: {filtered_by_id} 台跳过, {len(devices)} 台参与匹配", file=sys.stderr)
            if not devices:
                print("  过滤后无匹配设备，退出。", file=sys.stderr)
                return
        else:
            print(f"  ! 设备 ID 过滤: 未解析到有效 ID，全部参与匹配", file=sys.stderr)

    if not devices:
        print("没有设备数据，退出。", file=sys.stderr)
        return

    # ── Step 3: 匹配 → 坐标分配 ──
    results, unmatched, wind_warnings, matched_count = _match_devices(
        devices, candidates, code_to_candidates, prefix_to_candidates, coalbed_map)

    # ── Step 4: 输出 + 回写 ──
    by_confidence = {"高": 0, "中": 0, "低": 0}
    by_mark_type = {}
    by_sensor_type = {}
    for r in results:
        conf = r.get("confidence", "低")
        by_confidence[conf] = by_confidence.get(conf, 0) + 1
        mt = r.get("mark_type", "")
        by_mark_type[mt] = by_mark_type.get(mt, 0) + 1
        st = r.get("sensor_type", "")
        by_sensor_type[st] = by_sensor_type.get(st, 0) + 1

    by_reason = {}
    for u in unmatched:
        reason = u.get("reason", "UNKNOWN")
        by_reason[reason] = by_reason.get(reason, 0) + 1

    by_tunnel = {}
    suspicious_count = 0
    landmark_count = 0
    for r in results:
        name = r.get("matched_name", "")
        by_tunnel[name] = by_tunnel.get(name, 0) + 1
        if r.get("suspicious"):
            suspicious_count += 1
        if r.get("_sensor_landmark"):
            landmark_count += 1

    summary = {
        "total": len(devices),
        "matched": matched_count,
        "unmatched": len(unmatched),
        "filtered_by_id": filtered_by_id,
        "wind_spacing_warnings": len(wind_warnings),
        "generic_tunnels_skipped": generic_tunnel_skipped,
        "unnamed_tunnels_skipped": unnamed_tunnel_skipped,
        "by_confidence": by_confidence,
        "by_mark_type": by_mark_type,
        "by_sensor_type": by_sensor_type,
        "by_reason": by_reason,
        "by_tunnel": by_tunnel,
        "suspicious_count": suspicious_count,
        "landmark_count": landmark_count,
        "cad_data_count": cad_data_count,
        "generic_tunnel_names": generic_tunnel_names[:50],
    }

    # ── 构建结构化报告 + 模板化 stderr 输出 ──
    audit_data = _audit_results(results)
    report = _build_report(
        devices, results, unmatched, summary, audit_data,
        wind_warnings, generic_tunnel_names, mine_name, phase="match"
    )
    _print_report_stderr(report)

    all_warnings = list(wind_warnings)
    if generic_tunnel_skipped > 0:
        all_warnings.append({
            "type": "generic_tunnels_excluded",
            "count": generic_tunnel_skipped,
            "excluded_names": generic_tunnel_names[:30],
            "message": f"{generic_tunnel_skipped} 条系统生成巷道名称已排除: "
                       f"{', '.join(generic_tunnel_names[:10])}{'...' if len(generic_tunnel_names) > 10 else ''}",
        })
    if unnamed_tunnel_skipped > 0:
        all_warnings.append({
            "type": "unnamed_tunnels_excluded",
            "count": unnamed_tunnel_skipped,
            "message": f"{unnamed_tunnel_skipped} 条巷道缺少名称（name字段为空），"
                       f"无法作为候选参与设备匹配，已从候选池排除。",
        })

    output_full = {
        "username": username,
        "mine_name": mine_name,
        "summary": summary,
        "results": results,
        "unmatched_devices": unmatched,
        "warnings": all_warnings,
        "originData": origin_data,
    }

    # 更新 data_8373_*.json 也包含 originData 供后续使用
    if origin_data:
        try:
            save_dir = PROJECT_ROOT / "data" / "output"
            save_path = save_dir / f"data_8373_{mine_name}.json"
            if save_path.exists():
                existing = _load_json_file(str(save_path))
                if isinstance(existing, dict):
                    existing["originData"] = origin_data
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(existing, f, ensure_ascii=False, indent=2)
        except Exception:
            pass  # 非关键操作，失败不影响主流程

    output_mode = args.output_mode
    if output_mode == "full":
        output = output_full
    elif output_mode in ("summary", "json-summary"):
        output = {
            "username": username,
            "mine_name": mine_name,
            "summary": summary,
            "warnings": all_warnings,
        }
    elif output_mode == "unmatched":
        output = {
            "username": username,
            "mine_name": mine_name,
            "summary": summary,
            "unmatched_devices": unmatched,
        }
    elif output_mode == "audit":
        output = {
            "username": username,
            "mine_name": mine_name,
            "summary": summary,
            "audit": audit_data,
            "results": results,
            "unmatched_devices": unmatched,
        }
    else:
        output = output_full

    _save_and_writeback(output, username, output_mode, output_full, args.html,
                        args.match_only, report, auto_yes=args.yes)


if __name__ == "__main__":
    main()


# ── 规则来源注册表（集中登记，便于审计追查）─────────────────────────
# 修改 _assign_distances 或匹配逻辑中的数据表时，请同步更新此注册表。
# 每个条目对应一条规则及其行业标准来源。条款号以 PDF 原文为准。
_RULES_REGISTRY = {
    "安装高度": {
        "table": "_SENSOR_INSTALL_HEIGHT",
        "standard": "AQ 1029-2019 §6.1.1, DB51T1412-2011 §5.2.3, MT/T 1201.6-2023 §4.2",
        "desc": "各 sensor_type 相对于巷道底板的 z 偏移（含条款依据）",
    },
    "T 标识位置": {
        "table": "_T_POSITION_RULES",
        "standard": "AQ 1029-2019 §6.2.1, §6.3.1",
        "desc": "T0-T4 标识→折线区间比例（用于掘进/采煤工作面传感器定位）",
    },
    "巷道类型 × sensor_type": {
        "table": "_TUNNEL_TYPE_RULES",
        "standard": "AQ 1029-2019 各条, MT/T 1201.6-2023 附录 A",
        "desc": "特定巷道 type + sensor_type→精确米数/方向规则",
    },
    "AQ1029 距离规则（通用）": {
        "table": "_AQ1029_DISTANCE_RULES",
        "standard": "AQ 1029-2019 §6.3.1, §7.2.1, §7.6, §7.7.3, §7.8",
        "desc": "通用精确距离：keyword × sensor_type → start/end/mid 方向+米数",
    },
    "关键词区间（多设备）": {
        "table": "_KEYWORD_ZONE_RULES",
        "standard": "AQ 1029-2019, MT/T 1198-2023 §5, DB51T1412-2011 §5.1.8, MT/T 1201.6-2023 附录 A",
        "desc": "关键词→折线区间比例（如迎头0-15%、井口0-10%）→多设备分布",
    },
    "关键词精确位置（单设备）": {
        "table": "_KEYWORD_SINGLE_RATIO",
        "standard": "同上",
        "desc": "关键词→折线精确比例（单设备 count≤1 时使用）",
    },
    "sensor_type 默认区间": {
        "table": "_SENSOR_DEFAULT_ZONES + _SENSOR_SINGLE_RATIO",
        "standard": "AQ 1029-2019, DB51T1412-2011",
        "desc": "兜底：无 keyword 匹配时按 sensor_type 的默认区间/比例",
    },
    "sensor_type 巷道偏好（匹配加分）": {
        "table": "_SENSOR_TUNNEL_PREF",
        "standard": "AQ 1029-2019, MT/T 1198-2023, MT/T 1201.6-2023",
        "desc": "匹配阶段：sensor_type 偏好关键词命中→score +2",
    },
    "巷道类型匹配加分": {
        "table": "_TUNNEL_TYPE_MATCH_BONUS",
        "standard": "实际数据观察",
        "desc": "匹配阶段：description 关键词→候选 type 匹配→+1~+3",
    },
    "地点语义惩罚": {
        "table": "_LOCATION_SEMANTICS",
        "standard": "实际数据观察",
        "desc": "匹配阶段：洗煤厂/中央变电所/避难硐室/井口/地面/通风机/主扇 语义不匹配→-10",
    },
    "反向编码约束（候选含 code 但描述不含）": {
        "table": "_has_hard_semantic_conflict 规则 5",
        "standard": "实际数据观察",
        "desc": "候选名含 specific code（如 8301/6301/-650）但描述完全不含 → REJECT，避免短 LCS 蹭工作面/巷道编码候选",
    },
    "别名映射（LCS 前扩展）": {
        "table": "_TUNNEL_ALIAS_MAP",
        "standard": "煤矿术语习惯 + 实际数据观察",
        "desc": "匹配阶段：皮顺↔皮带顺槽、切巷↔切眼 等缩写/全称双向扩展",
    },
    "标记类型→系统大类": {
        "table": "_MARK_TYPE_TO_SYSTEM",
        "standard": "煤矿系统分类",
        "desc": "mark_type（B14/B15/B16）→系统名称",
    },
    "关键词分类表": {
        "table": "_CLASSIFY_KEYWORD_TABLE",
        "standard": "AQ 1029-2019, MT/T 1201.6-2023 附录 A, MT/T 1198-2023",
        "desc": "设备描述→位置关键词（按优先级降序排列）",
    },
    "工业视频多设备步长": {
        "table": "内联于 _assign_distances 函数",
        "standard": "MT/T 1201.6-2023 附录 A.1",
        "desc": "B16 组内间距：支架75m / 主运输皮带中部500m / 架空乘人100m",
    },
    "地面 area 过滤": {
        "table": "_AREA_SURFACE_PATTERNS",
        "standard": "实际数据观察",
        "desc": "area 含地面/洗选/磅房/化验楼/产品仓/原煤仓 等关键词时跳过井下候选",
    },
    "设备功能词-巷道类型可疑检测": {
        "table": "_FUNCTIONAL_SEMANTIC_CHECKS",
        "standard": "实际数据观察",
        "desc": "描述含水泵/远控开关/配电点等功能词但候选非硐室/变电/配电时→suspicious，需人工确认",
    },
    "轨顺/皮顺→联络巷冲突": {
        "table": "_has_hard_semantic_conflict 规则 9",
        "standard": "实际数据观察",
        "desc": "描述说\"轨顺\"/\"皮顺\"但未说\"联络巷\"，候选却是\"X轨顺联络巷\"/\"X皮顺联络巷\"时→REJECT。轨顺/皮顺是主要运输巷道，联络巷是连接巷道，不同地点。",
    },
    "支架工作面约束": {
        "table": "_has_hard_semantic_conflict 规则 10",
        "standard": "实际数据观察",
        "desc": "描述含\"工作面\\d+[#]?架\"（液压支架编号）但候选不含工作面/切眼/停采关键词时→REJECT。支架必须在回采工作面/切眼上。",
    },
    "工作面→非工作面类型拒绝": {
        "table": "_score_candidates 内联检查",
        "standard": "实际数据观察",
        "desc": "描述含\"<code>工作面\"且候选类型是 0-普通巷道时→REJECT。当上游 8373 数据缺少工作面子项时宁缺毋滥，不降级匹配到普通巷道。",
    },
    "轨顺/皮顺类型互斥": {
        "table": "_has_hard_semantic_conflict 规则 11",
        "standard": "实际数据观察",
        "desc": "描述含\"皮顺\"而候选含\"轨顺\"（或反之）时→REJECT。皮带顺槽和轨道顺槽是完全不同的巷道类型。",
    },
    "轨顺/皮顺→切眼冲突": {
        "table": "_has_hard_semantic_conflict 规则 12",
        "standard": "实际数据观察",
        "desc": "描述说\"轨顺\"/\"皮顺\"/\"轨道顺槽\"/\"皮带顺槽\"但候选是\"切眼\"（工作面切眼）时→REJECT。运输顺槽与工作面切眼完全不同，不应混淆。",
    },
    "切眼交汇点定位": {
        "table": "设备处理主循环内联",
        "standard": "实际数据观察",
        "desc": "描述同时含轨顺/皮顺（顺槽名）和切眼时→定位到切眼折线起点(0%)，即顺槽与切眼的交汇处。已验证 8301轨道顺槽终点 = 8301切眼起点。",
    },
}
