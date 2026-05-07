# 煤矿设备定位 skill (bw-mine-equipment-locator)

根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

**输入：** 用户提供的 username（如 `F18795450`）  
**输出：** 每个设备匹配到巷道/工作面后的 (x, y, z) 坐标

### 快速运行

```bash
python3 skill/bw-mine-equipment-locator/scripts/locator.py F18795450
```

输出 JSON 到 stdout（含每个设备的 matched_name 和 coordinates），汇总到 stderr。

---

## 工作流

### Step 1: 获取 Token 和 mineName

调用 `bw-token-manager` skill，传入 `username`：

```bash
python skill/bw-token-manager/scripts/bw_token_manager.py <username>
```

- 向 `http://192.168.133.110:33382/bwRuleNode/getUserToken?username=<username>` 发起 GET 请求
- 返回并缓存 `bw_token`（24h 内有效），同时获取 `mineName`
- `bw_token` 会自动写入 `bw_tokens.json` 缓存文件，后续 step 从中自动读取

### Step 2: 获取策略 8373 数据（设备、巷道、工作面）

调用 `bw-strategy-api-caller` skill，传入 `mineName`（从 Step 1 获得）和 `username`。

两种接口可选：

**方式 A：`get_json`（推荐）— 返回平铺数组，locator 直接使用**
```bash
python skill/bw-strategy-api-caller/scripts/strategy_api.py get_json \
  --id 8373 --param "MineName=<mineName>" --username <username>
```
- 向 `GetStrategyJsonData` 发起 POST 请求
- 返回扁平数组，每条记录包含 `id`/`description`/`mark_type`（设备）或 `name`/`line`（巷道）或 `workFaceName`/`line`（工作面）
- **推荐 locator 使用此方式**

**注意：** `strategy_api.py` 依赖 `requests`。locator.py 会自动探测带 `requests` 的解释器（优先 `sys.executable`/`python3`/`python`），找不到就报错。可用 `BW_LOCATOR_PYTHON` 显式覆盖。

### Step 3: 匹配设备 → 计算坐标

对 Step 2 返回的每个 device：

1. 从 `description` 提取地点名称（先[剥离前缀](#前缀剥离)），按[匹配逻辑](#匹配逻辑)在**候选名称**中找到最佳匹配
2. **候选来源**（均来自 8373）：
   - `tunnels` 数组中的 `name`（主候选）
   - `workfaces` 数组中的 `workFaceName`（补充候选）
3. 按[坐标计算](#坐标计算)规则计算 (x, y, z)

### Step 4: 回写定位结果到策略 8385

匹配计算完成后，调用 `execute` 将结果写回策略 8385：

```bash
python skill/bw-strategy-api-caller/scripts/strategy_api.py execute \
  --id 8385 --param "data=<结果JSON>" --username <username>
```

- 向 `ExecuteStrategyCom` 发起 POST 请求
- `data` 参数值为 Step 3 输出的完整结果 JSON（每个设备的 matched_name + coordinates）
- 返回 `{"code": 100}` 表示成功

---

## 项目结构

```
F:\gis\Point\
├── CLAUDE.md              # 本文件
├── data/
│   ├── pdf/              # 煤矿安全规范等 PDF 文档
│   └── sql/
│       ├── example_8373.json  # 策略 8373 示例数据（设备+巷道+工作面）
│       └── index.sql      # 策略 8373 的 SQL 查询（表结构参考）
├── evals/
│   └── evals.json         # 测试评估 prompt
├── tests/
│   └── test_matching.py   # 离线匹配测试（LCS/前缀剥离/坐标计算）
└── skill/
    ├── bw-token-manager/           # Step 1: 获取 BW-MES API token, mineName
    │   └── scripts/bw_token_manager.py
    ├── bw-strategy-api-caller/      # Step 2: 调用策略 API
    │   └── scripts/strategy_api.py
    └── bw-mine-equipment-locator/  # Step 3: 设备定位完整流程
        ├── SKILL.md                # Skill 定义
        └── scripts/locator.py      # 自动化定位脚本
```

### 依赖

| Skill | 用途 | 调用时机 |
| ----- | ---- | -------- |
| `bw-token-manager` | 获取 BW-MES API token, mineName（用户输入名称如 `F18795450`） | Step 1 |
| `bw-strategy-api-caller` | 调用 `GetStrategyData`/`GetStrategyJsonData` 获取设备+巷道+工作面数据 | Step 2 |
| `bw-strategy-api-caller` | 调用 `ExecuteStrategyCom` 回写定位结果 | Step 4 |

### Python 解释器与依赖

唯一第三方依赖：`requests`（被 `strategy_api.py` 使用）。其余脚本纯标准库。

locator.py 启动时自动探测可用解释器，按优先级：
1. `BW_LOCATOR_PYTHON` 环境变量（显式覆盖）
2. 当前解释器 `sys.executable`
3. PATH 上的 `python3` / `python` / `py`
4. 历史 Windows 路径 `C:\Users\bw\AppData\Local\Programs\Python\Python310\python.exe`（兜底）

第一个能 `import requests` 的就被采用。全部失败时 stderr 会列出尝试过的解释器并提示 `pip install requests`。

---

## 核心数据（参考）

### 参考文档

`data/pdf/AQ 1029-2019 煤矿安全监控系统及检测仪器使用管理规范.pdf` 中的设备描述示例。

### 策略 8373 返回结构

**`get_json` 返回格式（扁平数组，设备+巷道+工作面混排）：**
```json
{
  "code": 100,
  "data": [
    { "id": "JKYHMK0030120001A01", "sysaliasname":"安全监测系统", "description": "1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯", "sensor_type": "瓦斯", "mark_type": "B14" },
    { "id": "...", "name": "-725东翼胶带大巷", "type": "0-普通巷道", "line": [...] },
    { "id": "...", "workFaceName": "5318工作面", "type": "25-工作面停采线", "line": [...] }
  ]
}
```

---

## 匹配逻辑

1. **设备描述**（如 `七采区避难硐室生存室甲烷`）包含地点名称
2. **匹配候选来源（均来自 8373）**：
   - **主候选：** `tunnels` 数组中的 `name`（巷道名，如 `程庄回风井`、`9209进风巷`）
   - **补充候选：** `workfaces` 数组中的 `workFaceName`（工作面名，如 `5318工作面`）
3. 对每个设备剥离前缀后，使用**最长公共子串（LCS）** 在所有候选中找最佳匹配
4. 匹配成功后，取该巷道/工作面折线（line）中的**折点坐标**计算设备位置

### 匹配规则

| 设备描述 | 匹配类型 | 匹配名称 |
| -------- | -------- | -------- |
| `七采区避难硐室生存室甲烷` | 巷道 | `七采区避难硐室` |
| `回风暗斜井一氧化碳` | 巷道 | `回风暗斜井` |
| `C8308轨顺粉尘` | 巷道 | `C8308轨顺` |
| `其他999602085J00暗斜井猴车下口基站人数` | 巷道 | `回风暗斜井` |

### 前缀剥离

设备描述中可能包含前缀（如 `1号分站模拟量001A019308`、`其他999602085J00`），**需忽略前缀后再匹配**：
- `1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯` → 剥离后匹配 `皮顺联络巷`
- `其他999602085J00暗斜井猴车下口基站人数` → 剥离后匹配 `暗斜井` → 命中最优候选 `回风暗斜井`

### 相似度计算

使用**最长公共子串（Longest Common Substring, LCS）** 算法：
- 计算 stripped description 与每个候选名称的 LCS 长度
- 取 LCS 最大的候选（要求至少 2 个字符）
- LCS 相同时选名称更长的（更具体）

### sensor_type 加权

8373 返回的设备含 `sensor_type`（如 `瓦斯`、`一氧化碳`、`风速`）字段。匹配时，在 LCS 得分基础上，对符合该传感器类型巷道偏好的候选额外加 2 分：

- `瓦斯` 优先 `回风巷`/`进风巷`/`切巷`/`工作面`
- `风速` 优先 `测风站`/`总回风巷`
- `烟雾` 优先 `皮带`/`运输巷`
- `温度` 优先 `硐室`/`工作面`
- `粉尘` 优先 `采煤`/`掘进`/`转载点`
- `馈电`/`断电`/`开停` 优先 `配电`/`变电`/`开关`
- `人员定位` 优先 `井口`/`交叉口`/`大巷`

字段缺失时，从 `description` 关键词自动推断。

---

## 坐标计算

匹配成功后，根据以下规则计算设备 (x, y, z) 坐标：

### 1. 沿折线分配距离（默认）

同一巷道匹配到多个设备时，沿 `line` 折线分配距离，默认均匀分布在 10%~90% 区间。

### 2. 关键词调整

| 关键词 | 位置 | 说明 |
| ------ | ---- | ---- |
| `迎头` | 起点附近 0-15% | 掘进头 |
| `回风流` | 终点附近 85-100% | 回风末端 |

### 3. sensor_type 调整

| sensor_type | 位置 | 说明 |
| ------------ | ---- | ---- |
| `风速` | 中段 40-60% | 测风站要求前后 10m 无分支 |
| `烟雾` / `粉尘` | 起点附近 0-20% | 机头/产尘点 |
| `温度`（机电硐室） | 中段 30-70% | 均匀分布于硐室内 |
| `人员定位` | 中点 50% | 基站放巷道交叉口/中点 |

**优先级**：`迎头` / `回风流` 关键词 > sensor_type > 默认均匀分布。

**示例：**
- `七采区避难硐室生存室甲烷` → 均匀分布（无关键词，sensor=瓦斯 走默认）
- `1号分站模拟量皮顺联络巷迎头激光甲烷` → 起点附近 0-15%
- `1号分站模拟量皮顺联络巷回风流激光甲烷` → 终点附近 85-100%
- `9209进风巷掘进面风速` → 中段 40-60%（sensor=风速）
