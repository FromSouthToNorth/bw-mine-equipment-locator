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


def _infer_sensor_type(description: str) -> str:
    """从 description 关键词推断传感器类型（兜底）。"""
    d = description
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
    return None


# ── sensor_type 巷道偏好 ──────────────────────────────────────────
_SENSOR_TUNNEL_PREF = {
    "瓦斯": ["回风巷", "进风巷", "切巷", "工作面", "顺槽", "石门", "大巷"],
    "一氧化碳": ["隅角", "皮带", "硐室", "石门", "滚筒"],
    "风速": ["测风站", "总回风", "回风巷", "一翼回风"],
    "温度": ["硐室", "压风机", "工作面", "机电"],
    "烟雾": ["皮带", "运输", "机头", "机尾", "滚筒"],
    "粉尘": ["采煤", "掘进", "转载", "破碎", "装煤"],
    "馈电": ["配电", "变电", "开关", "馈电"],
    "断电": ["配电", "变电", "开关", "馈电"],
    "开停": ["配电", "变电", "开关", "风机"],
    "人员定位": ["井口", "交叉口", "大巷", "入口"],
}


# ── mark_type → 子系统大类映射 ───────────────────────────────────
# 注意：mark_type 是系统大类（B14=安全监测、B15=人员定位、B16=工业视频），
# 与 sensor_type（瓦斯/风速/烟雾/温度等具体传感器类型）是完全不同的概念。
_MARK_TYPE_TO_SYSTEM = {
    "B14": "安全监测系统",
    "B15": "人员定位系统",
    "B16": "工业视频系统",
}


def _candidate_matches_sensor_pref(name: str, sensor_type: str) -> bool:
    prefs = _SENSOR_TUNNEL_PREF.get(sensor_type, [])
    return any(p in name for p in prefs)


# ── T 标识位置规则 (AQ 1029-2019) ─────────────────────────────────
_T_POSITION_RULES = {
    "T0": (0.00, 0.05),    # 上隅角 → 工作面回风端 0-5%
    "T1": (0.00, 0.05),    # 掘进迎头 → 距起点 0-5%
    "T2": (0.85, 1.00),    # 掘进回风流 → 距终点 10-15%
    "T3": (0.30, 0.50),    # 混合风流 → 风机附近
    "T4": (0.90, 1.00),    # 掘进回风巷口 → 距终点 0-10%
}


# ── 地点类型语义过滤 ──────────────────────────────────────────────
_LOCATION_SEMANTICS = {
    "洗煤厂": {"allow": ["洗煤厂"], "penalty": -10},
    "中央变电所": {"allow": ["变电", "配电"], "penalty": -10},
    "避难硐室": {"allow": ["硐室"], "penalty": -10},
    "井口": {"allow": ["井口", "井筒", "副井", "主井"], "penalty": -10},
    "地面": {"allow": ["地面", "洗煤厂", "空压机房"], "penalty": -10},
}


# ── 巷道类型 × sensor_type 坐标规则 ──────────────────────────────
_TUNNEL_TYPE_RULES = {
    "26-工作面回风巷(辅运顺槽)": {
        "瓦斯": {"from": "end", "meters": 10, "tolerance": 3},
        "风速": {"from": "mid", "meters": 0, "station": True},
        "一氧化碳": {"from": "end", "meters": 10, "tolerance": 3},
    },
    "27-工作面进风巷(胶运顺槽)": {
        "风速": {"from": "mid", "meters": 0, "station": True},
        "烟雾": {"from": "start", "meters": 3, "tolerance": 1},
        "粉尘": {"from": "start", "meters": 3, "tolerance": 1},
    },
    "28-工作面切眼": {
        "瓦斯": {"from": "start", "meters": 5, "tolerance": 2},
        "一氧化碳": {"from": "start", "meters": 5, "tolerance": 2},
    },
    "3-煤仓": {
        "瓦斯": {"from": "start", "meters": 2, "tolerance": 1},
    },
    "25-工作面停采线": {
        "瓦斯": {"from": "mid", "meters": 0},
    },
    "29-回采工作面巷道": {
        "瓦斯": {"from": "mid", "meters": 0},
        "粉尘": {"from": "start", "meters": 5, "tolerance": 2},
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
    "瓦斯":   0.3,   # 距底板 ≥0.3m，距顶板 ≤0.2m
    "风速":   0.2,   # 距顶板 ≤0.3m，风速传感器较矮
    "烟雾":   0.2,   # 距顶板 ≤0.3m
    "粉尘":   1.5,   # 距底板 1.5-2m
    "一氧化碳": 0.2,  # 距顶板 ≤0.3m
    "温度":   0.2,   # 距顶板 ≤0.3m（硐室）或设备上方
}


# ── AQ 1029-2019 精确距离规则 (米) ────────────────────────────────
_AQ1029_DISTANCE_RULES = [
    # (keyword, sensor_type, distance_from, meters)
    ("T1", None, "start", 5),
    ("T2", None, "end", 12),
    ("风速", None, "mid", 0),
    ("烟雾", None, "start", 3),
    ("粉尘", None, "start", 3),
    ("温度", "硐室", "mid", 0),
]


def _extract_t_keyword(description: str) -> str:
    """从描述中提取 T 标识，如 T1/T2/T22。"""
    m = re.search(r'T(\d+)', description)
    if m:
        return f"T{m.group(1)}"
    return None


def extract_workface_code(description: str) -> str:
    """提取工作面/地点编码，如 C8302、9209、F1302、-490、-725。"""
    # 优先匹配字母+数字格式（如 C8302、F1302）
    m = re.search(r'([A-Z]\d{3,4})', description)
    if m:
        return m.group(1)
    # 匹配 -490、-725 等水平标高
    m = re.search(r'(-\d{3,4})', description)
    if m:
        return m.group(1)
    # 匹配纯数字工作面编号（4位数字如 5318、9209）
    m = re.search(r'(?<![A-Z])(\d{4})(?![A-Z])', description)
    if m:
        return m.group(1)
    return None


def _semantic_penalty(description: str, candidate_name: str) -> int:
    """语义惩罚：若描述含某地点关键词但候选不匹配允许列表，返回惩罚值。"""
    for keyword, rule in _LOCATION_SEMANTICS.items():
        if keyword in description:
            if not any(allow in candidate_name for allow in rule["allow"]):
                return rule["penalty"]
    return 0


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
                     coalbed_map: dict = None) -> dict:
    """
    在 candidates 中找最佳匹配项。
    评分规则：LCS + sensor_type 加权 + 编码匹配 + 巷道类型匹配 + 语义过滤 + coalbed 验证。
    返回 {"name": ..., "lcs": ..., "score": ..., "candidate": ...} 或 None。
    """
    best = None
    best_score = 0
    code_indices = set(code_to_candidates.get(device_code, [])) if code_to_candidates and device_code else set()
    device_coalbed = coalbed_map.get(device_code, "") if coalbed_map and device_code else ""

    for cand in candidates:
        name = cand.get("name") or ""
        if not name:
            continue
        lcs_len = longest_common_substring_len(cleaned, name)
        score = lcs_len

        # sensor_type 巷道偏好加权
        if sensor_type and lcs_len >= 2 and _candidate_matches_sensor_pref(name, sensor_type):
            score += 2

        # 编码匹配加权（最高优先级）
        if device_code and device_code in name:
            score += 5

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
                score -= 1  # 轻微惩罚，不绝对排除（同名巷道可能跨煤层）

        # 语义过滤惩罚
        sem_penalty = _semantic_penalty(cleaned, name)
        score += sem_penalty

        if score >= 2 and score > best_score:
            best_score = score
            best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand}
        elif score == best_score and best is not None and score >= 2:
            # 平局：优先编码匹配，然后 LCS 更长，然后名称更长
            idx = candidates.index(cand)
            best_idx = candidates.index(best["candidate"])
            if idx in code_indices and best_idx not in code_indices:
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand}
            elif lcs_len > best["lcs"]:
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand}
            elif len(name) > len(best["name"]):
                best = {"name": name, "lcs": lcs_len, "score": score, "candidate": cand}
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
                      sensor_type: str = None, tunnel_type: str = None, step: float = 5.0) -> list:
    """
    按固定步长（默认 5m）分配距起点的距离。
    优先使用精确米数规则，次选百分比规则。
    若区间放不下所有设备，则在该区间内均匀分布。
    """
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
    else:
        if sensor_type == "风速":
            lo, hi = line_length * 0.4, line_length * 0.6
        elif sensor_type in ("烟雾", "粉尘"):
            lo, hi = line_length * 0.0, line_length * 0.2
        elif sensor_type == "温度":
            lo, hi = line_length * 0.3, line_length * 0.7
        elif sensor_type == "人员定位":
            lo, hi = line_length * 0.5, line_length * 0.5
        else:
            lo, hi = line_length * 0.1, line_length * 0.9

    return _distribute_in_zone(lo, hi, count, step)


def _classify_keyword(description: str) -> str:
    """按关键词分类：T1/T2/T0/T3/T4 / 迎头 / 回风流 / default"""
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
    coalbed_map = {}

    def _add_code_index(name: str, idx: int):
        code = extract_workface_code(name)
        if code:
            code_to_candidates.setdefault(code, []).append(idx)

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
    return candidates, code_to_candidates, coalbed_map


# ── 主流程 ────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="煤矿设备定位")
    parser.add_argument("username", help="用户名（如 F18795450）")
    parser.add_argument("--load", metavar="PATH",
                        help="从本地文件加载 8373 数据，跳过 API 调用")
    args = parser.parse_args()
    username = args.username

    # ── Step 1: Token ──
    print(f"[1/2] 获取 token (username={username})...", file=sys.stderr)
    tokens, mine_name = get_token_and_mine_name(username)
    print(f"  → mineName: {mine_name}", file=sys.stderr)

    # ── Step 2: 获取 8373 数据 ──
    # 策略 8373: get_json → {devices, tunnels, workfaces}, get_data → 工作面列表
    if args.load:
        load_path = Path(args.load)
        print(f"[2/2] 从文件加载 8373 数据: {load_path}", file=sys.stderr)
        with open(load_path, "r", encoding="utf-8") as f:
            items3 = json.load(f)
        devices = []
        candidates = []
        code_to_candidates = {}
        coalbed_map = {}
        if isinstance(items3, dict):
            devices = items3.get("devices", [])
            candidates, code_to_candidates, coalbed_map = _extract_candidates(items3)
        elif isinstance(items3, list):
            devices, candidates = classify_items(items3)
    else:
        print(f"[2/2] 获取策略 8373...", file=sys.stderr)
        resp_dev = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_json")
        raw_data = resp_dev.get("data", {})

        # get_json 返回 dict {devices, tunnels, workfaces} → 直接提取
        code_to_candidates = {}
        coalbed_map = {}
        if isinstance(raw_data, dict) and "devices" in raw_data:
            devices = raw_data.get("devices", [])
            candidates, code_to_candidates, coalbed_map = _extract_candidates(raw_data)
        else:
            # get_json 返回扁平数组 → classify_items 分类
            device_items = extract_items(raw_data)
            devices, _ = classify_items(device_items)
            # 额外调用 get_data 获取工作面候选
            print(f"  → 获取工作面/巷道 (get_data)...", file=sys.stderr)
            resp_cand = call_strategy_api(8373, username, f"MineName={mine_name}", action="get_data")
            cand_items = extract_items(resp_cand.get("data"))
            candidates, code_to_candidates, coalbed_map = _extract_candidates(cand_items)

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

    # 第一遍：匹配所有设备
    match_entries = []  # [(device, match_info, cleaned), ...]
    unmatched = []

    for device in devices:
        desc = device.get("description", "")
        cleaned = strip_prefix(desc)
        sensor_type = device.get("sensor_type") or _infer_sensor_type(desc)
        # 编码从原始描述提取（避免被前缀剥离误删）
        device_code = extract_workface_code(desc)
        match = find_best_match(cleaned, candidates, sensor_type=sensor_type,
                                 device_code=device_code, code_to_candidates=code_to_candidates,
                                 coalbed_map=coalbed_map)
        if match is None:
            reason = "得分过低"
            if _semantic_penalty(cleaned, "") != 0:
                reason = "语义冲突（无合适候选）"
            unmatched.append({
                "id": device.get("id", ""),
                "description": desc,
                "mark_type": device.get("mark_type", ""),
                "sensor_type": sensor_type,
                "sysaliasname": device.get("sysaliasname", ""),
                "reason": reason,
            })
            continue
        match_entries.append((device, match, cleaned, sensor_type))

    # 按 (matched_name, keyword, sensor_type) 分组
    groups = {}
    for device, match, cleaned, sensor_type in match_entries:
        keyword = _classify_keyword(cleaned)
        group_key = (match["name"], keyword, sensor_type)
        groups.setdefault(group_key, []).append((device, match, cleaned, sensor_type))

    def _calc_confidence(match: dict, cleaned: str, sensor_type: str = None) -> str:
        score = match.get("score", 0)
        lcs = match.get("lcs", 0)
        device_code = extract_workface_code(cleaned)
        code_match = device_code and device_code in match["name"]
        cand_type = match["candidate"].get("type", "")

        # 检查巷道类型是否与传感器类型匹配
        type_match = False
        if cand_type and sensor_type and cand_type in _TUNNEL_TYPE_RULES:
            type_match = sensor_type in _TUNNEL_TYPE_RULES[cand_type]

        if code_match and lcs >= 3 and type_match:
            return "高"
        if (code_match and lcs >= 3) or (score >= 5 and type_match):
            return "中"
        if lcs >= 3:
            return "低"
        return "极低"

    # 每组分配距离并计算坐标
    results = []
    matched_count = 0
    for group_key, entries in groups.items():
        name, keyword, sensor_type = group_key
        candidate = entries[0][1]["candidate"]
        line = candidate.get("line", [])
        tunnel_type = candidate.get("type", "")
        total_len = _polyline_length(line)
        distances = _assign_distances(len(entries), keyword, total_len,
                                       sensor_type=sensor_type, tunnel_type=tunnel_type)
        for (device, match, cleaned, sensor_type), dist in zip(entries, distances):
            matched_count += 1
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
                "sysaliasname": device.get("sysaliasname", ""),
                "coordinates": coords,
            })

    # ── 输出 ──
    output = {
        "username": username,
        "mine_name": mine_name,
        "summary": {
            "total": len(devices),
            "matched": matched_count,
            "unmatched": len(unmatched),
        },
        "results": results,
        "unmatched_devices": unmatched,
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
