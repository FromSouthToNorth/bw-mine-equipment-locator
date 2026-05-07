---
name: bw-strategy-api-caller
description: 调用策略接口的技能，支持 GetStrategyData、GetStrategyJsonData、ExecuteStrategyCom 三个接口。Use when you need to query strategy data, get JSON strategy configurations, or execute strategy commands via the internal API (192.168.133.110:33382). Requires bw_token from bw-token-manager.
---

# Strategy API Caller

## 概述

本技能用于调用内部策略接口，支持三种操作：

1. **GetStrategyData** - 返回策略数据
2. **GetStrategyJsonData** - 返回 JSON 格式的策略配置
3. **ExecuteStrategyCom** - 执行策略命令

## 前置条件

- 需要先通过 `bw-token-manager` 技能获取并缓存 `bw_token`
- Token 缓存文件：`bw_tokens.json` (位于工作区根目录)

## 快速开始

### 调用 GetStrategyJsonData (获取 JSON 策略)

```bash
python3 scripts/strategy_api.py get_json --id 123 --param "timeout=30" --param "retry=3"
```

### 调用 GetStrategyData (获取策略数据)

```bash
python3 scripts/strategy_api.py get_data --id 456 --query-type 2
```

### 调用 ExecuteStrategyCom (执行策略)

```bash
python3 scripts/strategy_api.py execute --id 789 --param "action=start"
```

## 参数说明

| 参数 | 说明 | 必需 | 默认值 |
|------|------|------|--------|
| `--id` | 策略 ID | 是 | - |
| `--param` | 策略参数 (name=value 格式，可多次使用) | 否 | [] |
| `--query-type` | 查询类型 (1 或 2) | 否 | 1 |
| `--order` | 排序参数 (name:dir 格式，可多次使用) | 否 | id:asc |
| `--username` | 用户名 (用于获取缓存 token) | 否 | default |
| `--token` | 直接指定 bw_token (不提供则从缓存读取) | 否 | - |
| `--output` | 输出格式 (json 或 text) | 否 | json |

## 请求格式

API 请求体结构：

```json
{
  "id": 策略 ID,
  "parameter": [
    {"name": "参数名", "value": "参数值"}
  ],
  "queryType": 1,
  "orders": [{"name": "id", "value": "desc"}]
}
```

请求头：

```
caller: openclaw
token: ${bw_token}
Content-Type: application/json
```

## 返回格式

```json
{
  "code": 100,
  "mesg": "",
  "data": {} 或 []
}
```

- `code: 100` 表示成功，其他值表示异常
- `mesg` 为错误信息或空字符串
- `data` 为返回的数据 (对象或数组)

## 完整示例

### 示例 1: 获取策略 JSON 配置，带多个参数

```bash
python3 scripts/strategy_api.py get_json \
  --id 1001 \
  --param "threshold=80" \
  --param "interval=60" \
  --param "enabled=true" \
  --order "id:desc" \
  --username admin
```

### 示例 2: 执行策略命令

```bash
python3 scripts/strategy_api.py execute \
  --id 2002 \
  --param "command=restart" \
  --param "force=true" \
  --output text
```

### 示例 3: 使用自定义 token

```bash
python3 scripts/strategy_api.py get_data \
  --id 3003 \
  --token "your_token_here" \
  --query-type 2
```

## 错误处理

脚本会自动处理以下错误：

- Token 缓存文件不存在
- Token 过期 (超过 24 小时)
- 网络请求失败
- API 返回非 100 状态码 (会输出警告到 stderr)

## 与其他技能联动

### 配合 bw-token-manager 使用

在调用策略接口前，先获取 token：

```bash
# 1. 获取并缓存 token
python3 ../bw-token-manager/scripts/bw_token_manager.py admin

# 2. 调用策略接口 (自动使用缓存的 token)
python3 strategy_api.py get_json --id 123 --username admin
```

## API 端点

- 基础 URL: `http://192.168.133.110:33382/net/api/poininfoSmartValid/`
- GetStrategyData: `{BASE_URL}/GetStrategyData`
- GetStrategyJsonData: `{BASE_URL}/GetStrategyJsonData`
- ExecuteStrategyCom: `{BASE_URL}/ExecuteStrategyCom`
