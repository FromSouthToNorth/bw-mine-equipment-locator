#!/usr/bin/env python3
import os
import json
import time
import argparse
import urllib.request

# Cross-platform file lock compatibility
try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


def _flock(fd, op):
    if _HAS_FCNTL:
        fcntl.flock(fd, op)


def _lock_and_read(cache_path):
    """Read cache file with shared lock (prevents reading during write)."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            if _HAS_FCNTL:
                _flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                if _HAS_FCNTL:
                    _flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        return {}


def _lock_and_write(cache_path, data):
    """Write cache file with exclusive lock (prevents concurrent write corruption)."""
    with open(cache_path, "w") as f:
        if _HAS_FCNTL:
            _flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2)
        finally:
            if _HAS_FCNTL:
                _flock(f.fileno(), fcntl.LOCK_UN)


def load_all_cached_data():
    """Load all cached data with file lock."""
    cache_path = get_cache_path()
    return _lock_and_read(cache_path)


def load_cached_tokens(cache_key, expected_mineName=None):
    """Load tokens from cache if they exist and are not expired.

    Args:
        cache_key: Cache key (e.g., "dingtalk:123456:minekey" or "user:zhangsan")
        expected_mineName: If provided, verify cached mineName matches.
                          Returns None if mismatch (forces re-fetch).
    """
    all_data = load_all_cached_data()
    user_data = all_data.get(cache_key)

    if not user_data:
        # Backward compatibility: try legacy key format
        # Old dingtalk format: "dingtalk:dingtalkId" (without minekey)
        if cache_key.startswith("dingtalk:") and cache_key.count(":") == 2:
            parts = cache_key.split(":")
            legacy_key = f"{parts[0]}:{parts[1]}"
            user_data = all_data.get(legacy_key)
        # Old username format: "zhangsan" (without "user:" prefix)
        elif cache_key.startswith("user:"):
            legacy_key = cache_key[5:]
            user_data = all_data.get(legacy_key)

    if not user_data:
        return None

    timestamp = user_data.get("timestamp", 0)
    if time.time() - timestamp >= CACHE_TTL:
        return None  # Expired

    tokens = user_data.get("tokens")
    if tokens and expected_mineName:
        cached_mineName = tokens.get("mineName", "")
        if cached_mineName != expected_mineName:
            print(f"[缓存校验] 缓存项目「{cached_mineName}」与请求项目「{expected_mineName}」不一致，强制重新获取")
            return None

    return tokens


def save_tokens_to_cache(cache_key, tokens, metadata=None):
    """Save tokens to cache with current timestamp, using exclusive file lock."""
    cache_path = get_cache_path()
    all_data = load_all_cached_data()
    all_data[cache_key] = {
        "timestamp": time.time(),
        "tokens": tokens
    }
    if metadata:
        all_data[cache_key].update(metadata)
    _lock_and_write(cache_path, all_data)


def fetch_project_choices(dingtalkId, minekey):
    """Fetch project choices from the API endpoint (DingTalk mode)."""
    import urllib.parse
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?dingtalkId={dingtalkId}&minekey={urllib.parse.quote(minekey)}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            data = response_data.get("data", [])
            chooses = []
            for item in data:
                mineName = item.get("MineName") or item.get("mineName") or item.get("minekey", "")
                item_minekey = item.get("minekey", "")
                chooses.append({"mineName": mineName, "minekey": item_minekey})
            return {"code": 100, "chooses": chooses}
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch project choices: {str(e)}")


def fetch_tokens_by_username(username, dingtalkId=None):
    """Fetch tokens from the API endpoint using username."""
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?username={username}"
    if dingtalkId:
        api_url += f"&dingtalkId={dingtalkId}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            tokens = response_data.get("data", {})
            tokens["mineName"] = username
            return tokens
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch tokens: {str(e)}")


def fetch_tokens_by_dingtalk(dingtalkId, mineName):
    """Fetch tokens from the API endpoint using dingtalkId and mineName."""
    import urllib.parse
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?dingtalkId={dingtalkId}&mineName={urllib.parse.quote(mineName)}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            tokens = response_data.get("data", {})
            tokens["mineName"] = mineName
            return tokens
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch tokens: {str(e)}")


def display_project_choices(chooses):
    """Display project choices for user confirmation."""
    print("\n===== 可选项目列表 =====")
    for i, choice in enumerate(chooses, 1):
        minekey = choice.get("minekey", "")
        mineName = choice.get("mineName", "")
        print(f"  [{i}] 项目名称：{mineName} (minekey: {minekey})")
    print("========================\n")


def get_user_confirmation(chooses):
    """Get user confirmation for project selection."""
    while True:
        try:
            choice = input("请选择项目编号 (或输入 q 退出): ").strip()
            if choice.lower() == 'q':
                return None
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(chooses):
                return chooses[choice_idx]
            else:
                print(f"请输入 1-{len(chooses)} 之间的数字")
        except ValueError:
            print("请输入有效的数字")


def get_tokens_by_username(username, dingtalkId=None, force_refresh=False, interactive=True):
    """
    Mode 1: Get tokens using username only.

    Flow:
    1. Check cache (key: user:username)
    2. If not in cache or force_refresh, fetch tokens directly with username
    """
    cache_key = f"user:{username}"

    if not force_refresh:
        cached = load_cached_tokens(cache_key)
        if cached:
            return cached

    print(f"正在获取令牌 (用户名：{username})...")
    tokens = fetch_tokens_by_username(username, dingtalkId)
    save_tokens_to_cache(cache_key, tokens, {"mode": "username", "username": username})
    return tokens


def get_tokens_by_dingtalk(dingtalkId, minekey, mineName=None, force_refresh=False, interactive=True):
    """
    Mode 2: Get tokens using dingtalkId + minekey (DingTalk mode).

    Cache key: dingtalk:{dingtalkId}:{minekey} — each project independently cached.

    Flow:
    1. Check cache (key: dingtalk:dingtalkId:minekey)
    2. If cache hit, verify mineName consistency (project mismatch → re-fetch)
    3. If not in cache or force_refresh or project mismatch, call API to get choices
    4. If choices returned, let user confirm which project
    5. Use confirmed mineName to fetch actual tokens
    6. Cache with project-specific key
    """
    # 项目级缓存键：每个项目独立缓存，互不干扰
    cache_key = f"dingtalk:{dingtalkId}:{minekey}"

    if not force_refresh:
        expected = mineName if mineName else minekey
        cached = load_cached_tokens(cache_key, expected_mineName=expected)
        if cached:
            return cached

    # Step 1: Fetch project choices using dingtalkId + minekey
    print(f"正在查询项目列表 (钉钉 ID: {dingtalkId}, minekey: {minekey})...")
    response_data = fetch_project_choices(dingtalkId, minekey)

    chooses = response_data.get("chooses", [])

    if not chooses:
        # No choices returned, try to fetch tokens directly with minekey as mineName
        print("未返回项目列表，尝试直接使用 minekey 获取令牌...")
        tokens = fetch_tokens_by_dingtalk(dingtalkId, minekey)
        save_tokens_to_cache(cache_key, tokens, {"mode": "dingtalk", "dingtalkId": dingtalkId, "minekey": minekey, "mineName": minekey})
        return tokens

    # Step 2: Display choices and get user confirmation
    if interactive:
        display_project_choices(chooses)
        selected = get_user_confirmation(chooses)
        if not selected:
            raise Exception("用户取消选择")
        confirmed_mineName = selected.get("mineName", selected.get("minekey", minekey))
    else:
        # Non-interactive mode: use the first choice or the provided mineName
        if mineName:
            for choice in chooses:
                if choice.get("mineName") == mineName or choice.get("minekey") == mineName:
                    confirmed_mineName = choice.get("mineName", mineName)
                    break
            else:
                confirmed_mineName = mineName
        else:
            confirmed_mineName = chooses[0].get("mineName", chooses[0].get("minekey", minekey))

    print(f"已选择项目：{confirmed_mineName}")

    # Step 3: Fetch tokens with confirmed mineName
    # Use the confirmed mineName as the cache key component for future lookups
    confirmed_cache_key = f"dingtalk:{dingtalkId}:{confirmed_mineName}"
    tokens = fetch_tokens_by_dingtalk(dingtalkId, confirmed_mineName)
    save_tokens_to_cache(confirmed_cache_key, tokens, {"mode": "dingtalk", "dingtalkId": dingtalkId, "minekey": minekey, "mineName": confirmed_mineName})
    return tokens


def main():
    parser = argparse.ArgumentParser(description="管理 BW 令牌 - 支持用户名模式和钉钉模式两种方式。")

    # Mode 1: Username only
    parser.add_argument("--username", help="用户名 (模式 1: 仅用户名模式)")

    # Mode 2: DingTalk mode
    parser.add_argument("--dingtalkId", help="钉钉 ID (模式 2: 钉钉模式，与 minekey 配合使用)")
    parser.add_argument("--minekey", help="项目标识 (模式 2: 钉钉模式必需；模式 1: 可选)")

    # Common options
    parser.add_argument("--mineName", help="项目名称 (用于非交互模式确认)")
    parser.add_argument("--force-refresh", action="store_true", help="强制刷新令牌，即使缓存存在")
    parser.add_argument("--output", choices=["json", "text"], default="json", help="输出格式")
    parser.add_argument("--no-interactive", action="store_true", help="非交互模式 (不显示选择列表)")

    args = parser.parse_args()

    # Validate mode
    if args.username and args.dingtalkId:
        print("错误：不能同时使用用户名模式和钉钉模式，请选择其中一种。", file=os.sys.stderr)
        os.sys.exit(1)

    if not args.username and not args.dingtalkId:
        print("错误：必须指定 --username (模式 1) 或 --dingtalkId (模式 2)。", file=os.sys.stderr)
        os.sys.exit(1)

    # DingTalk mode requires minekey and mineName
    if args.dingtalkId and not args.minekey:
        print("错误：钉钉模式必须提供 --minekey 参数。", file=os.sys.stderr)
        os.sys.exit(1)

    # Behavior rule: DingTalk conversation must include dingtalkId and project name
    if args.dingtalkId:
        if not args.minekey and not args.mineName:
            print("错误：钉钉对话时，必须携带钉钉 ID 和项目名称（--minekey 或 --mineName）。", file=os.sys.stderr)
            os.sys.exit(1)

    # Behavior rule: DingTalk mode requires mineName when using non-interactive mode
    if args.dingtalkId and args.no_interactive and not args.mineName:
        print("错误：钉钉模式下使用非交互模式时，必须提供 --mineName 参数确认项目。", file=os.sys.stderr)
        os.sys.exit(1)

    try:
        if args.username:
            # Mode 1: Username only
            tokens = get_tokens_by_username(
                args.username,
                dingtalkId=args.dingtalkId,
                force_refresh=args.force_refresh,
                interactive=not args.no_interactive
            )
        else:
            # Mode 2: DingTalk mode (dingtalkId + minekey)
            tokens = get_tokens_by_dingtalk(
                args.dingtalkId,
                args.minekey,
                mineName=args.mineName,
                force_refresh=args.force_refresh,
                interactive=not args.no_interactive
            )

        if args.output == "json":
            print(json.dumps(tokens, indent=2))
        else:
            print(f"bw_token: {tokens.get('bw_token', '')}")
            print(f"bw_token_bwmes: {tokens.get('bw_token_bwmes', '')}")
    except Exception as e:
        print(f"错误：{str(e)}", file=os.sys.stderr)
        os.sys.exit(1)


if __name__ == "__main__":
    main()


CACHE_FILE = "bw_tokens.json"
CACHE_TTL = 86400  # 24 hours in seconds

def get_cache_path():
    """Get the cache file path in the current workspace."""
    workspace = os.getcwd()
    return os.path.join(workspace, CACHE_FILE)

def _lock_and_read(cache_path):
    """Read cache file with shared lock (prevents reading during write)."""
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception:
        return {}

def _lock_and_write(cache_path, data):
    """Write cache file with exclusive lock (prevents concurrent write corruption)."""
    with open(cache_path, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def load_all_cached_data():
    """Load all cached data with file lock."""
    cache_path = get_cache_path()
    return _lock_and_read(cache_path)

def load_cached_tokens(cache_key, expected_mineName=None):
    """Load tokens from cache if they exist and are not expired.

    Args:
        cache_key: Cache key (e.g., "dingtalk:123456:minekey" or "user:zhangsan")
        expected_mineName: If provided, verify cached mineName matches.
                          Returns None if mismatch (forces re-fetch).
    """
    all_data = load_all_cached_data()
    user_data = all_data.get(cache_key)

    if not user_data:
        # Backward compatibility: try legacy key format
        # Old dingtalk format: "dingtalk:dingtalkId" (without minekey)
        if cache_key.startswith("dingtalk:") and cache_key.count(":") == 2:
            parts = cache_key.split(":")
            legacy_key = f"{parts[0]}:{parts[1]}"
            user_data = all_data.get(legacy_key)
        # Old username format: "zhangsan" (without "user:" prefix)
        elif cache_key.startswith("user:"):
            legacy_key = cache_key[5:]
            user_data = all_data.get(legacy_key)

    if not user_data:
        return None

    timestamp = user_data.get("timestamp", 0)
    if time.time() - timestamp >= CACHE_TTL:
        return None  # Expired

    tokens = user_data.get("tokens")
    if tokens and expected_mineName:
        cached_mineName = tokens.get("mineName", "")
        if cached_mineName != expected_mineName:
            print(f"[缓存校验] 缓存项目「{cached_mineName}」与请求项目「{expected_mineName}」不一致，强制重新获取")
            return None

    return tokens

def save_tokens_to_cache(cache_key, tokens, metadata=None):
    """Save tokens to cache with current timestamp, using exclusive file lock."""
    cache_path = get_cache_path()
    all_data = load_all_cached_data()
    all_data[cache_key] = {
        "timestamp": time.time(),
        "tokens": tokens
    }
    if metadata:
        all_data[cache_key].update(metadata)
    _lock_and_write(cache_path, all_data)

def fetch_project_choices(dingtalkId, minekey):
    """Fetch project choices from the API endpoint (DingTalk mode)."""
    import urllib.parse
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?dingtalkId={dingtalkId}&minekey={urllib.parse.quote(minekey)}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            data = response_data.get("data", [])
            chooses = []
            for item in data:
                mineName = item.get("MineName") or item.get("mineName") or item.get("minekey", "")
                item_minekey = item.get("minekey", "")
                chooses.append({"mineName": mineName, "minekey": item_minekey})
            return {"code": 100, "chooses": chooses}
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch project choices: {str(e)}")

def fetch_tokens_by_username(username, dingtalkId=None):
    """Fetch tokens from the API endpoint using username."""
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?username={username}"
    if dingtalkId:
        api_url += f"&dingtalkId={dingtalkId}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            tokens = response_data.get("data", {})
            tokens["mineName"] = username
            return tokens
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch tokens: {str(e)}")

def fetch_tokens_by_dingtalk(dingtalkId, mineName):
    """Fetch tokens from the API endpoint using dingtalkId and mineName."""
    import urllib.parse
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?dingtalkId={dingtalkId}&mineName={urllib.parse.quote(mineName)}"

    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if response_data.get("code") == 100:
            tokens = response_data.get("data", {})
            tokens["mineName"] = mineName
            return tokens
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch tokens: {str(e)}")

def display_project_choices(chooses):
    """Display project choices for user confirmation."""
    print("\n===== 可选项目列表 =====")
    for i, choice in enumerate(chooses, 1):
        minekey = choice.get("minekey", "")
        mineName = choice.get("mineName", "")
        print(f"  [{i}] 项目名称：{mineName} (minekey: {minekey})")
    print("========================\n")

def get_user_confirmation(chooses):
    """Get user confirmation for project selection."""
    while True:
        try:
            choice = input("请选择项目编号 (或输入 q 退出): ").strip()
            if choice.lower() == 'q':
                return None
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(chooses):
                return chooses[choice_idx]
            else:
                print(f"请输入 1-{len(chooses)} 之间的数字")
        except ValueError:
            print("请输入有效的数字")

def get_tokens_by_username(username, dingtalkId=None, force_refresh=False, interactive=True):
    """
    Mode 1: Get tokens using username only.

    Flow:
    1. Check cache (key: user:username)
    2. If not in cache or force_refresh, fetch tokens directly with username
    """
    cache_key = f"user:{username}"

    if not force_refresh:
        cached = load_cached_tokens(cache_key)
        if cached:
            return cached

    print(f"正在获取令牌 (用户名：{username})...")
    tokens = fetch_tokens_by_username(username, dingtalkId)
    save_tokens_to_cache(cache_key, tokens, {"mode": "username", "username": username})
    return tokens

def get_tokens_by_dingtalk(dingtalkId, minekey, mineName=None, force_refresh=False, interactive=True):
    """
    Mode 2: Get tokens using dingtalkId + minekey (DingTalk mode).

    Cache key: dingtalk:{dingtalkId}:{minekey} — each project independently cached.

    Flow:
    1. Check cache (key: dingtalk:dingtalkId:minekey)
    2. If cache hit, verify mineName consistency (project mismatch → re-fetch)
    3. If not in cache or force_refresh or project mismatch, call API to get choices
    4. If choices returned, let user confirm which project
    5. Use confirmed mineName to fetch actual tokens
    6. Cache with project-specific key
    """
    # 项目级缓存键：每个项目独立缓存，互不干扰
    cache_key = f"dingtalk:{dingtalkId}:{minekey}"

    if not force_refresh:
        expected = mineName if mineName else minekey
        cached = load_cached_tokens(cache_key, expected_mineName=expected)
        if cached:
            return cached

    # Step 1: Fetch project choices using dingtalkId + minekey
    print(f"正在查询项目列表 (钉钉 ID: {dingtalkId}, minekey: {minekey})...")
    response_data = fetch_project_choices(dingtalkId, minekey)

    chooses = response_data.get("chooses", [])

    if not chooses:
        # No choices returned, try to fetch tokens directly with minekey as mineName
        print("未返回项目列表，尝试直接使用 minekey 获取令牌...")
        tokens = fetch_tokens_by_dingtalk(dingtalkId, minekey)
        save_tokens_to_cache(cache_key, tokens, {"mode": "dingtalk", "dingtalkId": dingtalkId, "minekey": minekey, "mineName": minekey})
        return tokens

    # Step 2: Display choices and get user confirmation
    if interactive:
        display_project_choices(chooses)
        selected = get_user_confirmation(chooses)
        if not selected:
            raise Exception("用户取消选择")
        confirmed_mineName = selected.get("mineName", selected.get("minekey", minekey))
    else:
        # Non-interactive mode: use the first choice or the provided mineName
        if mineName:
            for choice in chooses:
                if choice.get("mineName") == mineName or choice.get("minekey") == mineName:
                    confirmed_mineName = choice.get("mineName", mineName)
                    break
            else:
                confirmed_mineName = mineName
        else:
            confirmed_mineName = chooses[0].get("mineName", chooses[0].get("minekey", minekey))

    print(f"已选择项目：{confirmed_mineName}")

    # Step 3: Fetch tokens with confirmed mineName
    # Use the confirmed mineName as the cache key component for future lookups
    confirmed_cache_key = f"dingtalk:{dingtalkId}:{confirmed_mineName}"
    tokens = fetch_tokens_by_dingtalk(dingtalkId, confirmed_mineName)
    save_tokens_to_cache(confirmed_cache_key, tokens, {"mode": "dingtalk", "dingtalkId": dingtalkId, "minekey": minekey, "mineName": confirmed_mineName})
    return tokens

def main():
    parser = argparse.ArgumentParser(description="管理 BW 令牌 - 支持用户名模式和钉钉模式两种方式。")

    # Mode 1: Username only
    parser.add_argument("--username", help="用户名 (模式 1: 仅用户名模式)")

    # Mode 2: DingTalk mode
    parser.add_argument("--dingtalkId", help="钉钉 ID (模式 2: 钉钉模式，与 minekey 配合使用)")
    parser.add_argument("--minekey", help="项目标识 (模式 2: 钉钉模式必需；模式 1: 可选)")

    # Common options
    parser.add_argument("--mineName", help="项目名称 (用于非交互模式确认)")
    parser.add_argument("--force-refresh", action="store_true", help="强制刷新令牌，即使缓存存在")
    parser.add_argument("--output", choices=["json", "text"], default="json", help="输出格式")
    parser.add_argument("--no-interactive", action="store_true", help="非交互模式 (不显示选择列表)")

    args = parser.parse_args()

    # Validate mode
    if args.username and args.dingtalkId:
        print("错误：不能同时使用用户名模式和钉钉模式，请选择其中一种。", file=os.sys.stderr)
        os.sys.exit(1)

    if not args.username and not args.dingtalkId:
        print("错误：必须指定 --username (模式 1) 或 --dingtalkId (模式 2)。", file=os.sys.stderr)
        os.sys.exit(1)

    # DingTalk mode requires minekey and mineName
    if args.dingtalkId and not args.minekey:
        print("错误：钉钉模式必须提供 --minekey 参数。", file=os.sys.stderr)
        os.sys.exit(1)

    # Behavior rule: DingTalk conversation must include dingtalkId and project name
    if args.dingtalkId:
        if not args.minekey and not args.mineName:
            print("错误：钉钉对话时，必须携带钉钉 ID 和项目名称（--minekey 或 --mineName）。", file=os.sys.stderr)
            os.sys.exit(1)

    # Behavior rule: DingTalk mode requires mineName when using non-interactive mode
    if args.dingtalkId and args.no_interactive and not args.mineName:
        print("错误：钉钉模式下使用非交互模式时，必须提供 --mineName 参数确认项目。", file=os.sys.stderr)
        os.sys.exit(1)

    try:
        if args.username:
            # Mode 1: Username only
            tokens = get_tokens_by_username(
                args.username,
                dingtalkId=args.dingtalkId,
                force_refresh=args.force_refresh,
                interactive=not args.no_interactive
            )
        else:
            # Mode 2: DingTalk mode (dingtalkId + minekey)
            tokens = get_tokens_by_dingtalk(
                args.dingtalkId,
                args.minekey,
                mineName=args.mineName,
                force_refresh=args.force_refresh,
                interactive=not args.no_interactive
            )

        if args.output == "json":
            print(json.dumps(tokens, indent=2))
        else:
            print(f"bw_token: {tokens.get('bw_token', '')}")
            print(f"bw_token_bwmes: {tokens.get('bw_token_bwmes', '')}")
    except Exception as e:
        print(f"错误：{str(e)}", file=os.sys.stderr)
        os.sys.exit(1)

if __name__ == "__main__":
    main()
