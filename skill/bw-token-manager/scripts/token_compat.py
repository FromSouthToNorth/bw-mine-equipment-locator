#!/usr/bin/env python3
"""
BW Token Manager - 向后兼容包装器

为其他技能提供统一的令牌获取接口，自动处理新旧缓存格式。
支持用户名模式和钉钉模式。
"""

import json
import time
import subprocess
from pathlib import Path

CACHE_FILE = "bw_tokens.json"
CACHE_TTL = 86400  # 24 hours


def get_token(username=None, dingtalkId=None, minekey=None, force_refresh=False, workspace=None):
    """
    获取令牌（兼容新旧缓存格式）
    
    Args:
        username: 用户名（用户名模式）
        dingtalkId: 钉钉 ID（钉钉模式）
        minekey: 项目标识（钉钉模式必需，用于查询项目列表）
        force_refresh: 是否强制刷新（默认 False，优先使用缓存）
        workspace: 工作区路径（可选，默认当前目录）
    
    Returns:
        dict: tokens 包含 bw_token, bw_token_bwmes, mineName
        None: 如果缓存不存在或已过期
    """
    workspace_path = Path(workspace) if workspace else Path.cwd()
    cache_path = workspace_path / CACHE_FILE
    
    # 优先从缓存读取（如果不强制刷新）
    if not force_refresh and cache_path.exists():
        with open(cache_path, "r") as f:
            cache = json.load(f)
        
        # 确定缓存键
        if username:
            cache_keys = [f"user:{username}", username]  # 新格式 + 旧格式
        elif dingtalkId:
            cache_keys = [f"dingtalk:{dingtalkId}", dingtalkId]  # 新格式 + 旧格式
        else:
            return None
        
        # 尝试所有可能的键格式
        for key in cache_keys:
            user_data = cache.get(key)
            if not user_data:
                continue
            
            timestamp = user_data.get("timestamp", 0)
            if time.time() - timestamp < CACHE_TTL:
                return user_data.get("tokens")
    
    # 缓存未命中或强制刷新，调用脚本获取
    return fetch_fresh_tokens(username, dingtalkId, minekey, workspace_path)


def fetch_fresh_tokens(username=None, dingtalkId=None, minekey=None, workspace=None):
    """
    调用 bw_token_manager.py 脚本获取新鲜令牌
    
    Args:
        username: 用户名（用户名模式）
        dingtalkId: 钉钉 ID（钉钉模式）
        minekey: 项目标识（钉钉模式必需）
        workspace: 工作区路径
    
    Returns:
        dict: tokens 或 None
    """
    cmd = [
        "python3",
        str(workspace / "skills" / "bw-token-manager" / "scripts" / "bw_token_manager.py"),
        "--output", "json",
        "--no-interactive",
        "--force-refresh"
    ]
    
    if username:
        cmd.extend(["--username", username])
    elif dingtalkId and minekey:
        cmd.extend(["--dingtalkId", dingtalkId, "--minekey", minekey])
    else:
        raise ValueError("必须提供 username 或 (dingtalkId + minekey)")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise Exception(f"获取令牌失败：{result.stderr}")
    
    return json.loads(result.stdout)


def get_bw_token(username=None, dingtalkId=None, minekey=None, force_refresh=False, workspace=None):
    """
    仅获取 bw_token 字符串（方便快速调用）
    """
    tokens = get_token(username, dingtalkId, minekey, force_refresh, workspace)
    if tokens:
        return tokens.get("bw_token", "")
    return None


def get_token_dingtalk(dingtalkId, minekey, force_refresh=False, workspace=None):
    """
    便捷函数：钉钉模式获取令牌
    
    Args:
        dingtalkId: 钉钉 ID（必需）
        minekey: 项目标识（必需，用于查询项目列表）
        force_refresh: 是否强制刷新
        workspace: 工作区路径
    
    Returns:
        dict: tokens 包含 bw_token, bw_token_bwmes, mineName
    
    Example:
        tokens = get_token_dingtalk("123456789", "project-a")
        print(f"项目：{tokens['mineName']}")
        print(f"令牌：{tokens['bw_token']}")
    """
    return get_token(dingtalkId=dingtalkId, minekey=minekey, force_refresh=force_refresh, workspace=workspace)


def get_token_username(username, force_refresh=False, workspace=None):
    """
    便捷函数：用户名模式获取令牌
    
    Args:
        username: 用户名（必需）
        force_refresh: 是否强制刷新
        workspace: 工作区路径
    
    Returns:
        dict: tokens 包含 bw_token, bw_token_bwmes, mineName
    
    Example:
        tokens = get_token_username("zhangsan")
        print(f"令牌：{tokens['bw_token']}")
    """
    return get_token(username=username, force_refresh=force_refresh, workspace=workspace)


if __name__ == "__main__":
    # 测试示例
    tokens = get_token(username="zhangsan")
    if tokens:
        print(f"bw_token: {tokens.get('bw_token', '')[:50]}...")
        print(f"mineName: {tokens.get('mineName', '')}")
    else:
        print("未找到有效令牌")
