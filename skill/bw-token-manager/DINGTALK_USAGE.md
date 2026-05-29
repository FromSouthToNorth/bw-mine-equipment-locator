# 钉钉模式使用指南

本文档说明其他技能如何在钉钉模式下使用 `bw-token-manager` 获取令牌。

## 核心概念

### 钉钉模式参数

| 参数 | 说明 | 必需 |
|------|------|------|
| `dingtalkId` | 钉钉用户 ID | 是 |
| `minekey` | 项目标识，用于查询可选项目列表 | 是 |
| `mineName` | 项目名称，用户确认后获得 | 自动获取 |

### 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│  1. 其他技能调用 token_compat.get_token_dingtalk()            │
│     参数：dingtalkId + minekey                                │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  2. 检查缓存 (key: dingtalk:dingtalkId)                       │
│     - 缓存有效 → 直接返回令牌                                 │
│     - 缓存失效 → 继续下一步                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  3. 调用 API (dingtalkId + minekey) 获取可选项目列表 (chooses)  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  4. 判断是否有 chooses 返回                                    │
│     - 无 chooses → 直接使用 minekey 获取令牌                    │
│     - 有 chooses → 进入项目选择流程                           │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  5. 项目选择流程（交互模式）                                   │
│     - 显示项目列表                                           │
│     - 等待用户输入确认                                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  6. 调用 API (dingtalkId + mineName) 获取令牌                  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  7. 缓存令牌并返回结果                                        │
└─────────────────────────────────────────────────────────────┘
```

## 使用方式

### 方式 1: 使用兼容包装器（推荐）

```python
# 引入兼容包装器
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk

# 获取令牌（优先缓存，过期则自动刷新）
tokens = get_token_dingtalk(
    dingtalkId="123456789",
    minekey="project-a"
)

# 使用令牌
print(f"项目：{tokens['mineName']}")
print(f"bw_token: {tokens['bw_token']}")
print(f"bw_token_bwmes: {tokens['bw_token_bwmes']}")
```

### 方式 2: 强制刷新令牌

```python
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk

# 忽略缓存，强制获取新鲜令牌
tokens = get_token_dingtalk(
    dingtalkId="123456789",
    minekey="project-a",
    force_refresh=True
)
```

### 方式 3: 调用脚本（适用于独立进程）

```python
import subprocess
import json

cmd = [
    "python3",
    "skills/bw-token-manager/scripts/bw_token_manager.py",
    "--dingtalkId", "123456789",
    "--minekey", "project-a",
    "--output", "json"
]

result = subprocess.run(cmd, capture_output=True, text=True)
tokens = json.loads(result.stdout)

print(f"bw_token: {tokens['bw_token']}")
```

### 方式 4: 读取缓存（仅适用于已有缓存）

```python
import json

with open("bw_tokens.json", "r") as f:
    cache = json.load(f)

# 新格式
tokens = cache.get("dingtalk:123456789", {}).get("tokens")

# 旧格式（向后兼容）
if not tokens:
    tokens = cache.get("123456789", {}).get("tokens")

if tokens:
    print(f"bw_token: {tokens['bw_token']}")
    print(f"项目：{tokens.get('mineName')}")
```

## 完整示例

### 示例 1: 在技能中集成钉钉模式令牌获取

```python
# skills/your-skill/scripts/your_script.py
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk

def main(dingtalkId, minekey):
    """
    你的技能主函数
    
    Args:
        dingtalkId: 钉钉用户 ID
        minekey: 项目标识
    """
    # 获取令牌
    tokens = get_token_dingtalk(dingtalkId, minekey)
    
    if not tokens:
        raise Exception("获取令牌失败")
    
    bw_token = tokens["bw_token"]
    mineName = tokens["mineName"]
    
    print(f"✓ 已获取 {mineName} 项目的令牌")
    
    # 使用令牌调用其他 API
    # ...

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dingtalkId", required=True)
    parser.add_argument("--minekey", required=True)
    args = parser.parse_args()
    
    main(args.dingtalkId, args.minekey)
```

### 示例 2: 在 SKILL.md 中声明依赖

```markdown
## 依赖

本技能依赖 `bw-token-manager` 获取令牌。

### 钉钉模式获取令牌

```bash
python3 skills/bw-token-manager/scripts/bw_token_manager.py \
  --dingtalkId <钉钉 ID> \
  --minekey <项目标识>
```

### Python 调用

```python
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk
tokens = get_token_dingtalk(dingtalkId, minekey)
```
```

### 示例 3: 带错误处理的完整实现

```python
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk

def fetch_data_with_token(dingtalkId, minekey, api_url):
    """
    使用令牌调用 API（带错误处理）
    """
    import requests
    
    try:
        # 获取令牌
        tokens = get_token_dingtalk(dingtalkId, minekey)
        
        if not tokens:
            raise Exception("无法获取令牌，请检查缓存或网络连接")
        
        bw_token = tokens["bw_token"]
        mineName = tokens["mineName"]
        
        # 调用 API
        headers = {"Authorization": f"Bearer {bw_token}"}
        response = requests.get(api_url, headers=headers)
        
        if response.status_code == 401:
            # 令牌过期，强制刷新
            tokens = get_token_dingtalk(dingtalkId, minekey, force_refresh=True)
            bw_token = tokens["bw_token"]
            headers["Authorization"] = f"Bearer {bw_token}"
            response = requests.get(api_url, headers=headers)
        
        response.raise_for_status()
        return response.json()
        
    except Exception as e:
        print(f"错误：{str(e)}")
        raise

# 使用
data = fetch_data_with_token("123456789", "project-a", "http://api.example.com/data")
```

## 缓存说明

### 缓存键格式

| 模式 | 缓存键 |
|------|--------|
| 钉钉模式 | `dingtalk:dingtalkId` |

### 缓存有效期

- **TTL**: 24 小时（86400 秒）
- **过期行为**: 自动调用 API 刷新

### 缓存文件位置

- **文件**: `bw_tokens.json`
- **位置**: 工作区根目录（`/home/xxzx/.openclaw/workspace/`）

## 注意事项

### ⚠️ 钉钉模式行为准则

1. **必须提供 dingtalkId**: 钉钉模式必须传入钉钉用户 ID
2. **必须提供 minekey**: 用于查询可选项目列表
3. **交互模式**: 钉钉对话时必须使用交互模式（让用户确认项目）
4. **非交互模式**: 自动化脚本可使用 `--no-interactive`，但仅限非钉钉对话场景

### ⚠️ 缓存策略

- 同一钉钉用户的所有项目共享同一个缓存键
- 如果需要多个项目的令牌同时有效，建议使用不同的 `dingtalkId` 或定期刷新

### ⚠️ 错误处理

```python
tokens = get_token_dingtalk(dingtalkId, minekey)
if not tokens:
    # 缓存不存在或已过期，且无法刷新
    # 可能需要检查网络连接或 API 状态
    pass
```

## 测试

### 测试用例

```python
def test_dingtalk_mode():
    # 测试 1: 首次获取（无缓存）
    tokens = get_token_dingtalk("123456789", "project-a")
    assert tokens is not None
    assert "bw_token" in tokens
    assert "mineName" in tokens
    
    # 测试 2: 使用缓存
    tokens2 = get_token_dingtalk("123456789", "project-a")
    assert tokens2["bw_token"] == tokens["bw_token"]
    
    # 测试 3: 强制刷新
    tokens3 = get_token_dingtalk("123456789", "project-a", force_refresh=True)
    # 令牌可能相同或不同（取决于 API）
    
    print("所有测试通过！")

if __name__ == "__main__":
    test_dingtalk_mode()
```

## 常见问题

### Q: 如何获取钉钉用户 ID？

A: 钉钉用户 ID 通常从钉钉会话上下文中获取，例如：
- 钉钉机器人消息中的 `senderId` 字段
- 钉钉开放平台 API 返回的用户信息

### Q: minekey 从哪里来？

A: `minekey` 是项目标识，通常：
- 由用户在对话中提供
- 从项目配置中读取
- 通过其他 API 查询获得

### Q: 如何处理多个项目？

A: 同一钉钉用户的多个项目共享缓存。如果需要同时使用多个项目的令牌：
1. 使用 `force_refresh=True` 每次获取最新令牌
2. 或者在获取后立即保存到变量/文件

### Q: 缓存文件在哪里？

A: 默认在工作区根目录：`/home/xxzx/.openclaw/workspace/bw_tokens.json`

## 相关文档

- [SKILL.md](./SKILL.md) - 技能完整文档
- [DEPENDENCIES.md](./DEPENDENCIES.md) - 依赖技能清单
- [token_compat.py](./scripts/token_compat.py) - 兼容包装器源码
