#!/usr/bin/env python3
import os
import json
import time
import argparse
import urllib.request

CACHE_FILE = "bw_tokens.json"
CACHE_TTL = 86400  # 24 hours in seconds

def get_cache_path():
    """Get the cache file path in the current workspace."""
    workspace = os.getcwd()  # Use current working directory (workspace)
    return os.path.join(workspace, CACHE_FILE)

def load_all_cached_data():
    """Load all cached data (per-username tokens)."""
    cache_path = get_cache_path()
    if not os.path.exists(cache_path):
        return {}
    
    try:
        with open(cache_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def load_cached_tokens(username):
    """Load tokens for a specific username from cache if they exist and are not expired."""
    all_data = load_all_cached_data()
    user_data = all_data.get(username)
    
    if not user_data:
        return None
    
    timestamp = user_data.get("timestamp", 0)
    if time.time() - timestamp < CACHE_TTL:
        return user_data.get("tokens")
    else:
        return None

def save_tokens_to_cache(username, tokens):
    """Save tokens for a specific username to cache with current timestamp."""
    cache_path = get_cache_path()
    all_data = load_all_cached_data()
    all_data[username] = {
        "timestamp": time.time(),
        "tokens": tokens
    }
    with open(cache_path, "w") as f:
        json.dump(all_data, f, indent=2)

def fetch_tokens_from_api(username):
    """Fetch tokens from the API endpoint."""
    api_url = f"http://192.168.133.110:33382/bwRuleNode/getUserToken?username={username}"
    
    try:
        with urllib.request.urlopen(api_url, timeout=10) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        
        if response_data.get("code") == 100:
            return response_data.get("data", {})
        else:
            raise Exception(f"API returned error: {response_data.get('mesg', 'Unknown error')}")
    except Exception as e:
        raise Exception(f"Failed to fetch tokens: {str(e)}")

def get_tokens(username, force_refresh=False):
    """Get tokens for a specific username, using cache if available and not expired."""
    if not force_refresh:
        cached = load_cached_tokens(username)
        if cached:
            return cached
    
    tokens = fetch_tokens_from_api(username)
    save_tokens_to_cache(username, tokens)
    return tokens

def main():
    parser = argparse.ArgumentParser(description="Manage bw tokens - fetch and cache from API (per-username cache).")
    parser.add_argument("username", help="Username to fetch tokens for")
    parser.add_argument("--force-refresh", action="store_true", help="Force refresh tokens even if cache exists")
    parser.add_argument("--output", choices=["json", "text"], default="json", help="Output format")
    
    args = parser.parse_args()
    
    try:
        tokens = get_tokens(args.username, args.force_refresh)
        
        if args.output == "json":
            print(json.dumps(tokens, indent=2))
        else:
            print(f"bw_token: {tokens.get('bw_token', '')}")
            print(f"bw_token_bwmes: {tokens.get('bw_token_bwmes', '')}")
    except Exception as e:
        print(f"Error: {str(e)}", file=os.sys.stderr)
        os.sys.exit(1)

if __name__ == "__main__":
    main()
