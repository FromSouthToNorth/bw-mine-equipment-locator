---
name: bw-mine-equipment-locator
description: 煤矿设备定位技能。根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备 (x, y, z) 坐标。当用户提到"设备定位"、"计算设备坐标"、"匹配设备到巷道"、"调用定位流程"或直接提供一个 username（如 F18795450）要求定位设备时触发。自动调用 bw-token-manager 和 bw-strategy-api-caller 两个子技能完成完整流程。
---

# 煤矿设备定位

根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

## 工作流总览

```
用户输入 username (如 F18795450)
    │
    ▼
Step 1: 获取 Token + mineName  ← bw-token-manager
    │
    ▼
Step 2: 获取策略 8373           ← bw-strategy-api-caller
        （设备、巷道、工作面全量数据）
    │
    ▼
Step 3: 匹配设备 description → 巷道/工作面
        · 候选名 = 8373.tunnels[] ∪ 8373.workfaces[]
        · LCS（最长公共子串）匹配
        · 坐标计算（几何中心 / 迎头 / 回风流）
    │
    ▼
Step 4: 回写定位结果到策略 8385  ← bw-strategy-api-caller execute
    │
    ▼
输出 JSON（每个设备的 matched_name + coordinates）
```

## 快速开始

```bash
cd F:/gis/Point
python3 skill/bw-mine-equipment-locator/scripts/locator.py <username>
```

### 输出示例

```json
{
  "username": "F06795450",
  "mine_name": "程庄煤矿",
  "summary": { "total": 1422, "matched": 1401, "unmatched": 21 },
  "results": [
    {
      "id": "CZMK000030120001A06",
      "description": "程庄风井总回风巷甲烷瓦斯程庄风井总回风巷甲烷2瓦斯",
      "matched": true,
      "matched_name": "程庄回风井",
      "tunnel_id": "TNL-001",
      "matched_type": "tunnel",
      "mark_type": "B14",
      "sensor_type": "瓦斯",
      "sysaliasname": "安全监测系统",
      "coordinates": { "x": 38452486.92, "y": 4206343.68, "z": 895.77 }
    }
  ]
}
```

汇总信息输出到 stderr。设备 ID 和 description 可直接查看。

## 数据源

| 策略 | 来源 | 内容 |
| ---- | ---- | ---- |
| 8373 | `bw-strategy-api-caller get_json --id 8373` | 设备、巷道、工作面全量数据。设备含 `description` + `mark_type`；巷道含 `name` + `line`；工作面含 `workFaceName` + `line` |
| 8385 | `bw-strategy-api-caller execute --id 8385 --param data=<JSON>` | 回写定位结果（匹配后的坐标）到系统 |

## 匹配逻辑

### 前缀剥离

设备描述中可能含分站编号、编码前缀，需先剥离：

| 原始描述 | 剥离后 |
| -------- | ------ |
| `1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯` | `皮顺联络巷迎头激光甲烷瓦斯` |
| `其他999602085J00暗斜井猴车下口基站人数` | `暗斜井猴车下口基站人数` |
| `14号分站开关量014D01回风暗斜井风门风门` | `回风暗斜井风门风门` |

### 名称匹配

使用**最长公共子串（LCS）** 在巷道名和工作面名中找最佳匹配。

**匹配候选来源（来自 8373）：**
1. `tunnels` 数组中的 `name`（如 `程庄回风井`、`9209进风巷`、`主井`）
2. `workfaces` 数组中的 `workFaceName`（如 `5318工作面`、`5319工作面`）

匹配策略：
- 对每个设备剥离前缀后的 description，计算与每个候选名的 LCS
- 取 LCS 最大的候选（要求至少 2 个字符）
- LCS 相同时选名称更长的（更具体）

## 坐标计算

### 几何中心（默认）

取 `line` 数组所有折点的平均值：

```
x = sum(p.x for p in line) / len(line)
y = sum(p.y for p in line) / len(line)
z = sum(p.z for p in line) / len(line)
```

### 关键词调整

| 关键词 | 位置 | 说明 |
| ------ | ---- | ---- |
| `迎头` | 起点附近 | 取 `line` 前 1-2 个折点均值 |
| `回风流` | 终点附近 | 取 `line` 后 1-2 个折点均值 |

## 依赖

| 子技能 | 用途 |
| ------ | ---- |
| `bw-token-manager` | 获取 BW-MES API token、mineName |
| `bw-strategy-api-caller` | 调用策略接口 GetStrategyData(8373) 获取设备+巷道+工作面数据；调用 ExecuteStrategyCom(8385) 回写定位结果 |

## 资源

### scripts/locator.py

自动化定位流程的 Python 脚本，包含：
- Token/MineName 获取（Step 1）
- 策略 8373 数据获取与解析（Step 2）
- 前缀剥离 + LCS 匹配（Step 3）
- 坐标计算（几何中心/迎头/回风流）
- JSON 结果输出

**Step 4（回写 8385）为手动步骤**，使用 locator.py 输出的结果 JSON 调用 `strategy_api.py execute`。详见 CLAUDE.md。
