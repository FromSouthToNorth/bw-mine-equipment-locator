#!/usr/bin/env python3
"""
Strategy API Caller - 调用策略接口的脚本

支持三个接口：
1. GetStrategyData - 返回数据
2. GetStrategyJsonData - 返回 JSON 数据策略接口
3. ExecuteStrategyCom - 执行策略接口

Usage:
    python3 strategy_api.py <action> [options]
    
    action: get_data | get_json | execute
    
    Options:
        --id <strategy_id>      策略 ID (必需)
        --param <name=value>    策略参数 (可多次使用)
        --query-type <1|2>      查询类型 (可选，默认 1)
        --order <name:dir>      排序参数 (可选，例如 "id:desc")
        --output <json|text>    输出格式 (默认 json)
        --username <name>       用户名 (用于获取 token)

Examples:
    python3 strategy_api.py get_json --id 123 --param "timeout=30" --param "retry=3"
    python3 strategy_api.py execute --id 456 --username admin
"""

import sys
import json
import argparse
import requests
from pathlib import Path
from datetime import datetime, timedelta

# API 基础配置
BASE_URL = "http://192.168.133.110:33382/net/api/poininfoSmartValid"
TOKEN_CACHE_FILE = "bw_tokens.json"

# 接口映射
ENDPOINTS = {
    "get_data": "GetStrategyData",
    "get_json": "GetStrategyJsonData",
    "execute": "ExecuteStrategyCom"
}


def get_cached_token(username: str = "default") -> str:
    """从缓存文件获取 bw_token"""
    # 优先从工作区根目录查找
    workspace_root = Path(__file__).parent.parent.parent
    cache_path = workspace_root / TOKEN_CACHE_FILE
    
    if not cache_path.exists():
        # 尝试从当前工作目录获取
        cache_path = Path.cwd() / TOKEN_CACHE_FILE
    
    if not cache_path.exists():
        # 尝试从用户 home 目录查找
        cache_path = Path.home() / ".openclaw" / "workspace" / TOKEN_CACHE_FILE
    
    if not cache_path.exists():
        raise FileNotFoundError(f"Token cache file not found. Searched: {workspace_root / TOKEN_CACHE_FILE}, {Path.cwd() / TOKEN_CACHE_FILE}, {Path.home() / '.openclaw/workspace/' / TOKEN_CACHE_FILE}")
    
    with open(cache_path, 'r', encoding='utf-8') as f:
        cache = json.load(f)
    
    if username not in cache:
        raise KeyError(f"No cached token for username: {username}")
    
    entry = cache[username]
    timestamp = entry.get("timestamp", 0)
    now = datetime.now().timestamp()
    
    # 检查 token 是否过期 (24 小时)
    if now - timestamp > 86400:
        raise ValueError(f"Token expired for username: {username}. Please refresh.")
    
    tokens = entry.get("tokens", {})
    return tokens.get("bw_token", "")


def call_strategy_api(action: str, strategy_id: int, parameters: list = None, 
                      query_type: int = 1, orders: list = None, 
                      token: str = None, username: str = "default") -> dict:
    """
    调用策略接口
    
    Args:
        action: 接口类型 (get_data | get_json | execute)
        strategy_id: 策略 ID
        parameters: 策略参数列表 [{"name": "xxx", "value": "xxx"}]
        query_type: 查询类型 (1 或 2)
        orders: 排序参数 [{"name": "id", "value": "desc"}]
        token: bw_token (如果不提供则从缓存获取)
        username: 用户名 (用于获取缓存 token)
    
    Returns:
        API 响应字典
    """
    if action not in ENDPOINTS:
        raise ValueError(f"Unknown action: {action}. Valid: {list(ENDPOINTS.keys())}")
    
    endpoint = ENDPOINTS[action]
    url = f"{BASE_URL}/{endpoint}"
    
    # 获取 token
    if not token:
        token = get_cached_token(username)
    
    # 构建请求头
    headers = {
        "caller": "openclaw",
        "token": token,
        "Content-Type": "application/json"
    }
    
    # 构建请求体
    payload = {
        "id": strategy_id,
        "parameter": parameters or [],
        "queryType": query_type,
        "orders": orders or [{"name": "id", "value": "asc"}]
    }
    
    # 发送请求
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        # 验证返回码
        code = result.get("code", 0)
        if code != 100:
            print(f"[WARNING] API returned code {code}: {result.get('mesg', 'Unknown error')}", file=sys.stderr)
        
        return result
        
    except requests.exceptions.RequestException as e:
        return {
            "code": 0,
            "mesg": f"Request failed: {str(e)}",
            "data": None
        }


def parse_parameters(param_strings: list) -> list:
    """解析参数列表 ["name=value"] -> [{"name": "name", "value": "value"}]"""
    parameters = []
    for param in param_strings:
        if "=" in param:
            name, value = param.split("=", 1)
            parameters.append({"name": name.strip(), "value": value.strip()})
        else:
            print(f"[WARNING] Invalid parameter format: {param} (expected name=value)", file=sys.stderr)
    return parameters


def parse_orders(order_strings: list) -> list:
    """解析排序列表 ["name:dir"] -> [{"name": "name", "value": "dir"}]"""
    orders = []
    for order in order_strings:
        if ":" in order:
            name, direction = order.split(":", 1)
            orders.append({"name": name.strip(), "value": direction.strip().lower()})
        else:
            orders.append({"name": order.strip(), "value": "asc"})
    return orders


def main():
    parser = argparse.ArgumentParser(description="Strategy API Caller")
    parser.add_argument("action", choices=["get_data", "get_json", "execute"],
                        help="API action to call")
    parser.add_argument("--id", type=int, required=True, help="策略 ID")
    parser.add_argument("--param", action="append", default=[], 
                        help="策略参数 (name=value)，可多次使用")
    parser.add_argument("--query-type", type=int, default=1, choices=[1, 2],
                        help="查询类型 (1 或 2)")
    parser.add_argument("--order", action="append", default=[],
                        help="排序参数 (name:dir)，可多次使用")
    parser.add_argument("--token", type=str, help="bw_token (不提供则从缓存获取)")
    parser.add_argument("--username", type=str, default="default",
                        help="用户名 (用于获取缓存 token)")
    parser.add_argument("--output", choices=["json", "text"], default="json",
                        help="输出格式")
    
    args = parser.parse_args()
    
    # 解析参数
    parameters = parse_parameters(args.param)
    orders = parse_orders(args.order) if args.order else [{"name": "id", "value": "asc"}]
    
    # 调用 API
    result = call_strategy_api(
        action=args.action,
        strategy_id=args.id,
        parameters=parameters,
        query_type=args.query_type,
        orders=orders,
        token=args.token,
        username=args.username
    )
    
    # 输出结果
    if args.output == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        code = result.get("code", 0)
        mesg = result.get("mesg", "")
        data = result.get("data")
        print(f"Code: {code}")
        print(f"Message: {mesg}")
        print(f"Data: {json.dumps(data, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
