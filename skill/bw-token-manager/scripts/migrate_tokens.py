#!/usr/bin/env python3
"""
BW Token Manager - 迁移工具

用于将旧格式的缓存迁移到新格式，或提供向后兼容支持。

旧格式:
{
  "zhangsan": { "timestamp": ..., "tokens": {...} }
}

新格式:
{
  "user:zhangsan": { "timestamp": ..., "tokens": {...}, "mode": "username" },
  "dingtalk:123456789": { "timestamp": ..., "tokens": {...}, "mode": "dingtalk" }
}
"""

import json
import time
from pathlib import Path

CACHE_FILE = "bw_tokens.json"
CACHE_TTL = 86400  # 24 hours

def get_cache_path():
    workspace = Path.cwd()
    return workspace / CACHE_FILE

def migrate_old_to_new(dry_run=True):
    """
    将旧格式缓存迁移到新格式
    
    Args:
        dry_run: 如果为 True，仅显示将要执行的迁移，不实际修改
    """
    cache_path = get_cache_path()
    
    if not cache_path.exists():
        print("缓存文件不存在，无需迁移。")
        return
    
    with open(cache_path, "r") as f:
        cache = json.load(f)
    
    migrations = []
    new_cache = {}
    
    for key, value in cache.items():
        # 检查是否是旧格式（没有前缀的简单键）
        if ":" not in key and isinstance(value, dict) and "tokens" in value:
            # 判断是用户名模式还是钉钉模式
            tokens = value.get("tokens", {})
            mode = value.get("mode", "username")
            
            if mode == "dingtalk" or "dingtalkId" in value:
                dingtalkId = value.get("dingtalkId", key)
                new_key = f"dingtalk:{dingtalkId}"
            else:
                new_key = f"user:{key}"
            
            migrations.append((key, new_key, value))
            new_cache[new_key] = value
        else:
            # 已经是新格式，保留
            new_cache[key] = value
    
    if not migrations:
        print("缓存已经是新格式，无需迁移。")
        return
    
    print(f"\n===== 迁移计划 ({'模拟运行' if dry_run else '执行中'}) =====\n")
    for old_key, new_key, value in migrations:
        print(f"  {old_key} → {new_key}")
    
    if dry_run:
        print(f"\n共 {len(migrations)} 项需要迁移。")
        print("使用 --execute 参数执行实际迁移。")
    else:
        with open(cache_path, "w") as f:
            json.dump(new_cache, f, indent=2)
        print(f"\n迁移完成！共迁移 {len(migrations)} 项。")


def create_compatibility_wrapper():
    """
    创建一个向后兼容的包装器模块，供其他技能使用
    """
    wrapper_code = '''#!/usr/bin/env python3
"""
BW Token Manager - 向后兼容包装器

为其他技能提供统一的令牌获取接口，自动处理新旧缓存格式。
"""

import json
import time
from pathlib import Path

CACHE_FILE = "bw_tokens.json"
CACHE_TTL = 86400  # 24 hours


def get_token(username=None, dingtalkId=None, workspace=None):
    """
    获取令牌（兼容新旧缓存格式）
    
    Args:
        username: 用户名（用户名模式）
        dingtalkId: 钉钉 ID（钉钉模式）
        workspace: 工作区路径（可选，默认当前目录）
    
    Returns:
        dict: tokens 包含 bw_token, bw_token_bwmes, mineName
        None: 如果缓存不存在或已过期
    """
    workspace_path = Path(workspace) if workspace else Path.cwd()
    cache_path = workspace_path / CACHE_FILE
    
    if not cache_path.exists():
        return None
    
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
    
    return None


def get_bw_token(username=None, dingtalkId=None, workspace=None):
    """
    仅获取 bw_token 字符串（方便快速调用）
    """
    tokens = get_token(username, dingtalkId, workspace)
    if tokens:
        return tokens.get("bw_token", "")
    return None


if __name__ == "__main__":
    # 测试示例
    tokens = get_token(username="zhangsan")
    if tokens:
        print(f"bw_token: {tokens.get('bw_token', '')[:50]}...")
        print(f"mineName: {tokens.get('mineName', '')}")
    else:
        print("未找到有效令牌")
'''
    
    workspace = Path.cwd()
    wrapper_path = workspace / "skills" / "bw-token-manager" / "scripts" / "token_compat.py"
    
    with open(wrapper_path, "w") as f:
        f.write(wrapper_code)
    
    print(f"向后兼容包装器已创建：{wrapper_path}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="BW Token Manager 迁移工具")
    parser.add_argument("--migrate", action="store_true", help="执行缓存格式迁移")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行迁移（默认）")
    parser.add_argument("--create-wrapper", action="store_true", help="创建向后兼容包装器")
    
    args = parser.parse_args()
    
    if args.create_wrapper:
        create_compatibility_wrapper()
    elif args.migrate:
        migrate_old_to_new(dry_run=args.dry_run)
    else:
        # 默认显示迁移计划
        migrate_old_to_new(dry_run=True)
