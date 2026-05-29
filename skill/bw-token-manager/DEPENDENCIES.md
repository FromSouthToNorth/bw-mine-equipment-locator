# BW Token Manager - 依赖技能清单

本文档列出所有依赖 `bw-token-manager` 技能的技能，以及迁移状态。

## 依赖技能列表

| 技能名称 | 依赖方式 | 缓存键格式 | 迁移状态 | 备注 |
|---------|---------|-----------|---------|------|
| `bw-strategy-api-caller` | 直接读取 `bw_tokens.json` | 旧格式 (`username`) | ⚠️ 需要更新 | 使用 `cache[username]` 直接访问 |
| `bw-sync-manager` | 直接读取 `bw_tokens.json` | 待确认 | ⚠️ 需要检查 | - |
| `bw-nodered-migrator` | 文档引用 | N/A | ✅ 无需修改 | 仅文档引用 |
| `bw-download-skill` | 文档引用 | N/A | ✅ 无需修改 | 仅文档引用 |
| `bw-monitoring-alarm-overview` | 文档引用 | N/A | ✅ 无需修改 | 仅文档引用 |

## 迁移方案

### 方案 A: 使用兼容包装器（推荐）

修改依赖技能，使用 `token_compat.py` 提供的函数：

```python
# 旧代码（bw-strategy-api-caller）
with open(cache_path, 'r') as f:
    cache = json.load(f)
entry = cache[username]  # ❌ 旧格式

# 新代码
from skills.bw_token_manager.scripts.token_compat import get_token
tokens = get_token(username=username)  # ✅ 自动兼容
```

### 方案 B: 更新缓存键格式

修改依赖技能，使用新的缓存键格式：

```python
# 旧代码
entry = cache[username]

# 新代码
entry = cache.get(f"user:{username}")
```

### 方案 C: 执行缓存迁移

运行迁移工具，将旧缓存转换为新格式：

```bash
cd /home/xxzx/.openclaw/workspace
python3 skills/bw-token-manager/scripts/migrate_tokens.py --migrate --execute
```

## bw-strategy-api-caller 迁移示例

### 当前代码（需要更新）

```python
# strategy_api.py 第 70 行
entry = cache[username]
```

### 更新后代码

```python
# 方式 1: 使用兼容包装器（推荐）
from skills.bw_token_manager.scripts.token_compat import get_token

tokens = get_token(username=username)
if not tokens:
    raise ValueError(f"No valid token for username: {username}")
bw_token = tokens.get("bw_token", "")

# 方式 2: 兼容新旧格式
entry = cache.get(f"user:{username}", cache.get(username))
if not entry:
    raise KeyError(f"No cached token for username: {username}")
```

## 时间线

| 日期 | 事件 |
|------|------|
| 2026-04-21 | `bw-token-manager` 缓存格式变更，引入兼容层 |
| 2026-04-21 | 创建 `token_compat.py` 兼容包装器 |
| 2026-04-21 | 创建 `migrate_tokens.py` 迁移工具 |
| TBD | 更新 `bw-strategy-api-caller` 使用兼容包装器 |

## 测试清单

更新依赖技能后，需要测试：

- [ ] 用户名模式获取令牌
- [ ] 钉钉模式获取令牌
- [ ] 缓存过期后自动刷新
- [ ] 旧格式缓存兼容读取
- [ ] 新格式缓存正常写入

## 联系方式

如有问题，请联系技能维护者或参考 `bw-token-manager/SKILL.md`。
