---
name: bw-token-manager
description: 从本地 API 端点获取并缓存 bw_token 和 bw_token_bwmes 令牌。支持两种认证方式：用户名模式 或 钉钉模式。
---

# BW 令牌管理器

## 概述

本技能提供一个脚本，用于从 `http://192.168.133.110:33382/bwRuleNode/getUserToken` 获取 `bw_token` 和 `bw_token_bwmes` 令牌，并将它们缓存在当前工作区（`bw_tokens.json` 文件）中，缓存有效期为 24 小时。

### 认证方式

| 场景 | 必需参数 | 说明 |
|------|---------|------|
| **钉钉对话** | `--dingtalkId` + `--minekey` | 钉钉 ID 自动从对话上下文获取，minekey 为用户自然语言描述的项目标识 |
| **非钉钉对话** | `--username` | 普通模式下，仅需用户名即可 |

### 核心特性

- **环境感知**：自动检测钉钉会话环境，强制使用钉钉模式
- **钉钉 ID 自动获取**：钉钉对话时，自动从对话上下文提取 `dingtalkId`，无需用户手动提供
- **自然语言项目标识**：用户用自然语言描述项目名称（如"阳城"），即为 `minekey`
- **项目选择流程**：支持通过 `minekey` 查询可选项目列表，用户二次确认后获取令牌
- **智能缓存**：按模式分别缓存，有效期 24 小时
- **项目一致性校验**：缓存命中后，自动比对请求项目名与缓存的 `mineName`。不一致时强制重新获取，不返回错误项目的 token

### 行为准则

| 场景 | 必需参数 | 模式要求 | 说明 |
|------|---------|---------|------|
| **钉钉对话时** | `--dingtalkId`（自动）+ `--minekey`（用户描述） | **必须等待用户确认** | 钉钉模式下，dingtalkId 自动获取，minekey 为用户自然语言描述，**等待用户明确确认项目名称后**才能获取 token |
| **非钉钉对话** | `--username` | 无限制 | 普通模式下，仅需用户名即可 |

> ⚠️ **重要**：
> 1. **钉钉会话环境不允许用户名模式获取**，必须使用钉钉模式。
> 2. 钉钉对话时，`dingtalkId` 自动从当前对话的钉钉 ID 获取，无需用户提供。
> 3. **自然语言描述即项目标识**：用户说"获取阳城的 token"，"阳城"就是 `minekey`。
> 4. **禁止越权操作**：API 返回项目列表后，**必须等待用户明确回复确认**，不得自行尝试或猜测用户选择。
> 5. 用户确认后，方可使用 `--mineName` + `--no-interactive` 获取 token。

## 依赖管理（其他技能开发者必读）

### 📚 钉钉模式使用指南

**其他技能如何在钉钉模式下使用此技能？请查看：**

👉 **[DINGTALK_USAGE.md](./DINGTALK_USAGE.md)** - 完整的钉钉模式集成指南

### 缓存格式变更历史

| 日期 | 变更内容 | 影响 |
|------|---------|------|
| 2026-04-21 | 缓存键从 `username` 改为 `user:username`，钉钉模式从 `dingtalkId` 改为 `dingtalk:dingtalkId` | 旧技能可能需要调整缓存键格式 |

### 向后兼容方案

本技能提供**向后兼容层**，旧格式缓存仍可读取：

1. **自动兼容**：`bw_token_manager.py` 会自动尝试新旧两种键格式
2. **迁移工具**：运行 `python3 scripts/migrate_tokens.py --migrate` 可批量迁移缓存
3. **兼容包装器**：使用 `scripts/token_compat.py` 提供的函数，无需关心缓存格式

### 推荐调用方式

```python
# 方式 1: 使用兼容包装器（推荐）
from skills.bw_token_manager.scripts.token_compat import get_token
tokens = get_token(username="zhangsan")

# 钉钉模式
from skills.bw_token_manager.scripts.token_compat import get_token_dingtalk
tokens = get_token_dingtalk(dingtalkId="123456789", minekey="project-a")

# 方式 2: 直接读取缓存（兼容新旧格式）
import json
with open("bw_tokens.json") as f:
    cache = json.load(f)
# 尝试新格式，失败则尝试旧格式
tokens = cache.get("user:zhangsan", cache.get("zhangsan", {})).get("tokens")
```

## 快速开始

### 钉钉模式（dingtalkId 自动获取 + minekey 自然语言）

```bash
# 用户说"获取阳城的 token"，自动解析为：
python3 scripts/bw_token_manager.py --dingtalkId 01523459434302 --minekey 阳城
```

### 用户名模式（仅非钉钉对话使用）

```bash
python3 scripts/bw_token_manager.py --username <用户名>
```

### 非交互模式（指定项目名称）

在自动化脚本中使用时，可直接指定项目名称，跳过选择列表：

```bash
# 钉钉模式 - 非交互
python3 scripts/bw_token_manager.py --dingtalkId <钉钉 ID> --minekey <项目标识> --mineName <项目名称> --no-interactive
```

### 强制刷新缓存

忽略缓存，获取新鲜令牌：

```bash
# 用户名模式
python3 scripts/bw_token_manager.py --username <用户名> --force-refresh

# 钉钉模式
python3 scripts/bw_token_manager.py --dingtalkId <钉钉 ID> --minekey <项目标识> --force-refresh
```

### 文本格式输出

以纯文本格式（而非 JSON）输出令牌：

```bash
python3 scripts/bw_token_manager.py --username <用户名> --output text
```

## 参数说明

| 参数 | 说明 | 必需 | 模式 |
|------|------|------|------|
| `--username` | 用户名 | 非钉钉对话必需 | 用户名模式 |
| `--dingtalkId` | 钉钉 ID | **钉钉对话时自动获取** | 钉钉模式 |
| `--minekey` | 项目标识（用户自然语言描述） | 钉钉对话时必需 | 钉钉模式 |
| `--mineName` | 项目名称（用户确认后使用） | 钉钉对话时必需 | 通用 |
| `--force-refresh` | 强制刷新令牌，忽略缓存 | 否 | 通用 |
| `--output` | 输出格式 (json/text) | 否 | 通用 |
| `--no-interactive` | 非交互模式，不显示选择列表 | 否（**钉钉对话时用户确认后自动使用**） | 通用 |

> ⚠️ **行为准则**：
> - **钉钉会话环境不允许用户名模式获取**
> - 钉钉对话时，`dingtalkId` 自动从当前对话上下文获取
> - 用户自然语言描述的项目名称即为 `minekey`（如"阳城"）
> - 钉钉对话时，API 返回项目列表后必须等待用户确认，确认后自动使用 `--no-interactive` 获取 token

## 工作流程

### 模式 1: 用户名模式（仅非钉钉对话）

```
┌─────────────────────────────────────────────────────────────┐
│  1. 用户调用脚本，传入 --username                              │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  2. 检查缓存 (key: user:username)                             │
│     - 缓存有效 → 直接返回令牌                                 │
│     - 缓存失效 → 继续下一步                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  3. 调用 API (username 参数) 直接获取令牌                        │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  4. 缓存令牌并返回结果                                        │
└─────────────────────────────────────────────────────────────┘
```

### 模式 2: 钉钉模式（钉钉对话唯一方式）

```
┌─────────────────────────────────────────────────────────────┐
│  1. 用户自然语言描述（如"获取阳城的 token"）                    │
│     自动解析：dingtalkId=当前对话 ID, minekey="阳城"          │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  2. 检查缓存 (key: dingtalk:dingtalkId)                       │
│     - 缓存有效 → 直接返回令牌                                 │
│     - 缓存失效 → 继续下一步                                   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  3. 调用 API (dingtalkId + minekey) 获取可选项目列表 (chooses)  │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  4. 判断是否有 chooses 返回                                    │
│     - 无 chooses → 直接使用 minekey 获取令牌                    │
│     - 有 chooses → 显示列表，**等待用户确认** ⚠️               │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  5. **用户明确回复确认项目名称**（禁止越权猜测）               │
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

> ⚠️ **越权禁令**：
> - **钉钉会话环境不允许用户名模式获取**
> - 步骤 4→5 之间**必须等待用户明确回复**，不得自行尝试或假设用户选择

## 缓存详情

- **缓存文件**：`bw_tokens.json`（存储在当前工作目录/工作区）
- **缓存键**：
  - 用户名模式：`user:username`
  - 钉钉模式：`dingtalk:{dingtalkId}:{minekey}`（按用户+项目独立缓存）
- **有效期**：24 小时（86400 秒）
- **项目隔离**：不同项目使用不同缓存键，互不覆盖。例：
  - `dingtalk:01523459434302:义桥` → 义桥 token
  - `dingtalk:01523459434302:朱家峁` → 朱家峁 token
- **项目一致性校验**：
  - 缓存命中后，自动比对请求项目与缓存的 `mineName`
  - **不一致时强制重新获取**，不返回错误项目的 token
- **并发安全**：使用 `fcntl` 文件锁，防止多个 Agent 并发写入损坏 JSON
- **向后兼容**：自动识别旧格式 `dingtalk:dingtalkId` 缓存，无需手动迁移
- **格式**：
  ```json
  {
    "user:zhangsan": {
      "timestamp": 1234567890,
      "mode": "username",
      "username": "zhangsan",
      "tokens": {
        "bw_token": "...",
        "bw_token_bwmes": "...",
        "mineName": "zhangsan"
      }
    },
    "dingtalk:123456789": {
      "timestamp": 1234567891,
      "mode": "dingtalk",
      "dingtalkId": "123456789",
      "tokens": {
        "bw_token": "...",
        "bw_token_bwmes": "...",
        "mineName": "生产环境"
      }
    }
  }
  ```

## 使用示例

### 示例 1: 用户名模式 - 基本使用

```bash
$ python3 scripts/bw_token_manager.py --username zhangsan

正在获取令牌 (用户名：zhangsan)...
{
  "bw_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "bw_token_bwmes": "bwmes_abc123...",
  "mineName": "zhangsan"
}
```

### 示例 2: 钉钉模式 - 项目选择

```bash
$ python3 scripts/bw_token_manager.py --dingtalkId 123456789 --minekey project-a

正在查询项目列表 (钉钉 ID: 123456789, minekey: project-a)...

===== 可选项目列表 =====
  [1] 项目名称：生产环境 (minekey: project-a-prod)
  [2] 项目名称：测试环境 (minekey: project-a-test)
  [3] 项目名称：开发环境 (minekey: project-a-dev)
========================

请选择项目编号 (或输入 q 退出): 1
已选择项目：生产环境
{
  "bw_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "bw_token_bwmes": "bwmes_abc123...",
  "mineName": "生产环境"
}
```

### 示例 3: 钉钉模式 - 交互模式（唯一允许的方式）

```bash
python3 scripts/bw_token_manager.py --dingtalkId 123456789 --minekey project-a
```

> ⚠️ 注意：钉钉模式**不能使用** `--no-interactive` 参数，必须让用户进行项目二次确认。

### 示例 4: 文本格式输出

```bash
$ python3 scripts/bw_token_manager.py --username zhangsan --output text

bw_token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
bw_token_bwmes: bwmes_abc123...
```

### 示例 5: 强制刷新缓存

```bash
# 用户名模式
python3 scripts/bw_token_manager.py --username zhangsan --force-refresh

# 钉钉模式
python3 scripts/bw_token_manager.py --dingtalkId 123456789 --minekey project-a --force-refresh
```

## 在其他技能中使用

### 方式 1: 使用兼容包装器（强烈推荐）

**自动处理新旧缓存格式，无需关心内部实现**：

```python
# 引入兼容包装器
from skills.bw_token_manager.scripts.token_compat import get_token, get_bw_token

# 用户名模式
tokens = get_token(username="zhangsan")
bw_token = get_bw_token(username="zhangsan")

# 钉钉模式
tokens = get_token(dingtalkId="123456789")

# 使用示例
if tokens:
    print(f"项目：{tokens.get('mineName')}")
    print(f"bw_token: {tokens.get('bw_token')}")
    print(f"bw_token_bwmes: {tokens.get('bw_token_bwmes')}")
```

**优势**：
- ✅ 自动兼容新旧缓存格式
- ✅ 自动验证过期时间
- ✅ 统一接口，无需记忆缓存键格式

### 方式 2: 读取缓存文件（向后兼容）

如果令牌已缓存且在有效期内，直接读取 `bw_tokens.json` 文件：

```python
import json
import os
from pathlib import Path

def get_cached_tokens(mode, identifier):
    """
    从缓存中获取令牌
    
    Args:
        mode: "user" 或 "dingtalk"
        identifier: 用户名 或 钉钉 ID
    
    Returns:
        tokens dict 或 None
    """
    workspace = Path.cwd()
    cache_file = workspace / "bw_tokens.json"
    
    if not cache_file.exists():
        return None
    
    with open(cache_file, "r") as f:
        cache = json.load(f)
    
    cache_key = f"{mode}:{identifier}"
    user_data = cache.get(cache_key)
    
    if not user_data:
        return None
    
    # 验证时间戳（24 小时有效期）
    import time
    if time.time() - user_data.get("timestamp", 0) > 86400:
        return None  # 缓存过期
    
    return user_data.get("tokens")

# 使用示例
tokens = get_cached_tokens("user", "zhangsan")
if tokens:
    print(f"项目：{tokens.get('mineName')}")
    print(f"bw_token: {tokens.get('bw_token')}")
    print(f"bw_token_bwmes: {tokens.get('bw_token_bwmes')}")
```

### 方式 3: 调用脚本获取令牌

如果缓存不存在或需要强制刷新，调用脚本获取：

```python
import subprocess
import json

def fetch_tokens(username=None, dingtalkId=None, minekey=None, force_refresh=False):
    """
    调用 bw_token_manager 脚本获取令牌
    
    Returns:
        tokens dict 或 None
    """
    cmd = ["python3", "skills/bw-token-manager/scripts/bw_token_manager.py"]
    
    if username:
        cmd.extend(["--username", username])
    elif dingtalkId and minekey:
        cmd.extend(["--dingtalkId", dingtalkId, "--minekey", minekey])
    else:
        raise ValueError("必须提供 username 或 (dingtalkId + minekey)")
    
    if force_refresh:
        cmd.append("--force-refresh")
    
    cmd.extend(["--output", "json", "--no-interactive"])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise Exception(f"获取令牌失败：{result.stderr}")
    
    return json.loads(result.stdout)

# 使用示例 - 用户名模式
tokens = fetch_tokens(username="zhangsan")

# 使用示例 - 钉钉模式（注意：非交互模式不能用于钉钉对话，仅用于自动化脚本）
tokens = fetch_tokens(dingtalkId="123456789", minekey="project-a")
```

### 方式 4: 封装为工具类

创建可复用的令牌管理工具类：

```python
# skills/bw-token-manager/scripts/token_utils.py
import json
import time
import subprocess
from pathlib import Path

class BWTokenManager:
    def __init__(self, workspace=None):
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self.cache_file = self.workspace / "bw_tokens.json"
    
    def get_tokens(self, username=None, dingtalkId=None, minekey=None, force_refresh=False):
        """获取令牌（优先缓存，过期则刷新）"""
        if not force_refresh:
            cached = self.get_cached_tokens(username, dingtalkId)
            if cached:
                return cached
        
        return self.fetch_fresh_tokens(username, dingtalkId, minekey)
    
    def get_cached_tokens(self, username=None, dingtalkId=None):
        """从缓存获取令牌"""
        if not self.cache_file.exists():
            return None
        
        with open(self.cache_file, "r") as f:
            cache = json.load(f)
        
        if username:
            cache_key = f"user:{username}"
        elif dingtalkId:
            cache_key = f"dingtalk:{dingtalkId}"
        else:
            return None
        
        user_data = cache.get(cache_key)
        if not user_data:
            return None
        
        if time.time() - user_data.get("timestamp", 0) > 86400:
            return None  # 缓存过期
        
        return user_data.get("tokens")
    
    def fetch_fresh_tokens(self, username=None, dingtalkId=None, minekey=None):
        """调用脚本获取新鲜令牌"""
        cmd = ["python3", str(self.workspace / "skills/bw-token-manager/scripts/bw_token_manager.py")]
        
        if username:
            cmd.extend(["--username", username])
        elif dingtalkId and minekey:
            cmd.extend(["--dingtalkId", dingtalkId, "--minekey", minekey])
        else:
            raise ValueError("必须提供 username 或 (dingtalkId + minekey)")
        
        cmd.extend(["--output", "json", "--no-interactive", "--force-refresh"])
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            raise Exception(f"获取令牌失败：{result.stderr}")
        
        return json.loads(result.stdout)

# 使用示例
manager = BWTokenManager()
tokens = manager.get_tokens(username="zhangsan")
```

### 方式 5: 在其他技能的 SKILL.md 中声明依赖

```markdown
## 依赖

本技能依赖 `bw-token-manager` 技能获取令牌。

### 获取令牌

```bash
# 用户名模式
python3 skills/bw-token-manager/scripts/bw_token_manager.py --username <用户名> --output json

# 钉钉模式
python3 skills/bw-token-manager/scripts/bw_token_manager.py --dingtalkId <钉钉 ID> --minekey <项目标识>
```

### 读取缓存

```python
import json
with open("bw_tokens.json", "r") as f:
    tokens = json.load(f).get("user:zhangsan", {}).get("tokens")
```
```

## API 端点

- **令牌获取**：`http://192.168.133.110:33382/bwRuleNode/getUserToken`
- **查询参数**：
  - **用户名模式**：`username`（必需）
  - **钉钉模式**：`dingtalkId` + `minekey`（查询项目列表），然后 `dingtalkId` + `mineName`（获取令牌）

## 注意事项

- **钉钉会话环境不允许用户名模式获取**，必须使用钉钉模式
- **钉钉 ID 自动获取**：钉钉对话时，自动从当前对话上下文提取 `dingtalkId`
- **自然语言描述即项目标识**：用户说"获取阳城的 token"，"阳城"就是 `minekey`
- **项目确认**：API 返回 `chooses` 属性时，必须让用户二次确认选择哪个项目
- **缓存策略**：
  - 用户名模式：按 `user:username` 缓存（仅非钉钉对话）
  - 钉钉模式：按 `dingtalk:dingtalkId` 缓存（同一钉钉用户共享缓存，不包含 minekey）
- **跨平台兼容**：支持 Linux 和 Windows

## 资源

### scripts/bw_token_manager.py
主 Python 脚本，负责处理 API 调用、项目选择、缓存和输出。
