#!/usr/bin/env python3
"""
8385 回写脚本 — 从定位结果文件回写到策略 8385

用法:
    python writeback_8385.py <username> <result_file>

示例:
    python writeback_8385.py D99795450 locator_result_D99795450_窑街煤电金河煤矿.json
"""

import sys
import os
import json
import requests
from pathlib import Path

TOKEN_CACHE_FILE = "bw_tokens.json"
BASE_URL = "http://192.168.133.110:33382/net/api/poininfoSmartValid"

def get_token(username):
    """从缓存文件获取 bw_token"""
    # 搜索 token 缓存文件
    search_paths = [
        Path.cwd() / TOKEN_CACHE_FILE,
        Path(__file__).resolve().parent.parent.parent / TOKEN_CACHE_FILE,
        Path(__file__).resolve().parent / TOKEN_CACHE_FILE,
    ]
    cache_path = None
    for p in search_paths:
        if p.exists():
            cache_path = p
            break
    if not cache_path:
        raise FileNotFoundError(f"未找到 {TOKEN_CACHE_FILE}")

    with open(cache_path, "r", encoding="utf-8") as f:
        cache = json.load(f)

    if username not in cache:
        raise KeyError(f"缓存中无用户 {username} 的 token")

    entry = cache[username]
    tokens = entry.get("tokens", {})
    token = tokens.get("bw_token", "")
    if not token:
        raise ValueError(f"用户 {username} 的 token 为空")
    return token

def writeback(username, result_path):
    """执行 8385 回写"""
    # 1. 加载结果文件
    rp = Path(result_path)
    if not rp.exists():
        raise FileNotFoundError(f"结果文件不存在: {rp}")

    with open(rp, "r", encoding="utf-8") as f:
        data = json.load(f)

    results_list = data.get("results", [])
    mine_name = data.get("mine_name", "")

    print(f"用户: {username}")
    print(f"矿井: {mine_name}")
    print(f"结果条数: {len(results_list)}")

    # 2. 获取 token
    token = get_token(username)
    print(f"Token: {token[:20]}...")

    # 3. 构造请求
    url = f"{BASE_URL}/ExecuteStrategyCom"
    headers = {
        "caller": "openclaw",
        "token": token,
        "Content-Type": "application/json",
    }

    # 用临时文件传递 jsonData 参数
    data_param = json.dumps(data, ensure_ascii=False)
    payload = {
        "id": 8385,
        "parameter": [
            {"name": "data", "value": data_param}
        ],
        "queryType": 1,
        "orders": [{"name": "id", "value": "asc"}]
    }

    # 4. 发送请求
    print(f"\n正在回写 {len(results_list)} 条结果到策略 8385...")

    # 预览前 5 条
    for r in results_list[:5]:
        cid = r.get("id", "?")
        cname = r.get("matched_name", "?")
        coords = r.get("coordinates", {})
        print(f"  {cid} → {cname}  ({coords.get('x', 0):.2f}, {coords.get('y', 0):.2f}, {coords.get('z', 0):.2f})")
    if len(results_list) > 5:
        print(f"  ...及其他 {len(results_list) - 5} 条")

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        code = result.get("code", "unknown")
        if code == 100:
            print(f"\n✅ 回写成功 (code=100)")
        else:
            msg = result.get("msg", "") or result.get("message", "")
            print(f"\n⚠️  回写异常: code={code}, msg={msg}")
    except requests.exceptions.RequestException as e:
        print(f"\n❌ 请求失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    username = sys.argv[1]
    result_path = sys.argv[2]
    writeback(username, result_path)
