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

完整规则在 `skill/bw-mine-equipment-locator/scripts/locator.py`，本节列出所有评分常量。代码是 source of truth，本节同步更新。

### 候选来源（均来自 8373）

| 来源 | 字段 | category |
| ---- | ---- | -------- |
| `tunnels[]` | `name` | tunnel |
| `workfaces[]` | `workFaceName` | workface |

### 前缀剥离

设备描述常带分站编号或编码前缀，匹配前需剥离（`PREFIX_PATTERNS`，locator.py:172-175）：

```
^\d+号分站(模拟量|开关量|多态量)[A-Za-z0-9_]+
^其他\d+[A-Za-z0-9]*
```

| 原始描述 | 剥离后 |
| -------- | ------ |
| `1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯` | `皮顺联络巷迎头激光甲烷瓦斯` |
| `其他999602085J00暗斜井猴车下口基站人数` | `暗斜井猴车下口基站人数` |

注：**编码提取在原始描述上做**（避免被前缀剥离误删），其他匹配在剥离后描述上做。

### sensor_type 推断

设备字段缺 `sensor_type` 时，从描述关键词按顺序推断（`_infer_sensor_type`，locator.py:186-209）：

`风速 > 烟雾 > 粉尘 > 温度 > 一氧化碳（CO/一氧化碳，排除 CO2） > 瓦斯（甲烷/CH4） > 开停 > 馈电 > 断电 > 人员定位（人数/人员）`

### 编码提取

`extract_workface_code`（locator.py:334-348），按优先级：
1. 字母+3-4 数字：`C8302`、`F1302`
2. 负号+3-4 数字（水平标高）：`-490`、`-725`
3. 4 位纯数字（前后无字母）：`5318`、`9209`

### 评分公式

`find_best_match`（locator.py:375-436）：

```
score = LCS_长度
      + 2  if  sensor_type 命中候选名巷道偏好且 LCS≥2
      + 5  if  device_code 在候选名内
      + 3  if  候选 tunnelId 含 device_code（workface 关联）
      + n  巷道类型匹配关键词加分
      - 1  coalbed 不一致
      - 10 _LOCATION_SEMANTICS 语义冲突
```

- 最低门槛：`score ≥ 2`
- 平局判定：编码命中 > LCS 长 > 候选名长

### sensor_type 巷道偏好（命中 +2）

`_SENSOR_TUNNEL_PREF`（locator.py:213-224）：

| sensor_type | 偏好关键字 |
| ----------- | ---------- |
| 瓦斯       | 回风巷, 进风巷, 切巷, 工作面, 顺槽, 石门, 大巷 |
| 一氧化碳   | 隅角, 皮带, 硐室, 石门, 滚筒 |
| 风速       | 测风站, 总回风, 回风巷, 一翼回风 |
| 温度       | 硐室, 压风机, 工作面, 机电 |
| 烟雾       | 皮带, 运输, 机头, 机尾, 滚筒 |
| 粉尘       | 采煤, 掘进, 转载, 破碎, 装煤 |
| 馈电 / 断电 | 配电, 变电, 开关, 馈电 |
| 开停       | 配电, 变电, 开关, 风机 |
| 人员定位   | 井口, 交叉口, 大巷, 入口 |

### 巷道类型匹配加分

`_TUNNEL_TYPE_MATCH_BONUS`（locator.py:292-299）：

| 描述含 | 候选 type | 加分 |
| ------ | --------- | ---- |
| 煤仓   | `3-煤仓`                       | +3 |
| 切眼   | `28-工作面切眼`                | +3 |
| 回风   | `26-工作面回风巷(辅运顺槽)`    | +2 |
| 进风   | `27-工作面进风巷(胶运顺槽)`    | +2 |
| 停采   | `25-工作面停采线`              | +2 |
| 硐室   | `0-普通巷道`                   | +1 |

### 地点语义惩罚（-10）

`_LOCATION_SEMANTICS`（locator.py:253-259）— 描述含此关键字时，候选必须含其一，否则扣 10：

| 描述含 | 候选必须含其一 |
| ------ | -------------- |
| 洗煤厂 | 洗煤厂 |
| 中央变电所 | 变电, 配电 |
| 避难硐室 | 硐室 |
| 井口   | 井口, 井筒, 副井, 主井 |
| 地面   | 地面, 洗煤厂, 空压机房 |

### mark_type → 系统大类

`_MARK_TYPE_TO_SYSTEM`（locator.py:230-234）：

| mark_type | 系统           |
| --------- | -------------- |
| B14       | 安全监测系统   |
| B15       | 人员定位系统   |
| B16       | 工业视频系统   |

`mark_type` 是系统大类，与 `sensor_type`（具体传感器）是不同维度。

### 置信度

`_calc_confidence`（locator.py:991-1009）：

| 条件 | confidence |
| ---- | ---------- |
| 编码匹配 + LCS≥3 + 巷道类型匹配 sensor_type | 高 |
| 编码匹配 + LCS≥3，或 score≥5 + type_match | 中 |
| LCS≥3 | 低 |
| 其他 | 极低 |

---

## 坐标计算

匹配成功后，沿命中巷道/工作面的 `line` 折线计算 (x, y, z)。

### 1. 分组键

`group_key = (matched_name, keyword)`（locator.py:984-989）。`keyword` 由 `_classify_keyword`（locator.py:593-606）决定：

- `T1/T2/T0/T3/T4`（从描述提取）
- `迎头` / `回风流`（描述含关键词）
- `default`（无）

→ 同巷道但不同关键词的设备分到不同组，不冲突。

### 2. 区间确定优先级

`_assign_distances`（locator.py:478-590）：

```
T 标识规则 > 巷道类型×sensor_type 规则 > AQ1029 距离规则 > 关键词区间 > sensor_type 默认百分比
```

#### 2a. T 标识区间

`_T_POSITION_RULES`（locator.py:243-249）：

| T 标识 | 比例区间 | 精确米数（若 line 够长） | 含义 |
| ------ | -------- | ----------------------- | ---- |
| T0     | 0-5%     | -                       | 上隅角（工作面回风端） |
| T1     | 0-5%     | 0 ~ 5m                  | 掘进迎头 |
| T2     | 85-100%  | length-15 ~ length      | 掘进回风流 |
| T3     | 30-50%   | -                       | 混合风流（风机附近） |
| T4     | 90-100%  | length-10 ~ length      | 掘进回风巷口 |

#### 2b. 巷道类型 × sensor_type 规则

`_TUNNEL_TYPE_RULES`（locator.py:263-288）：

| 巷道类型 | sensor | 方向 | 米数 | 容差 |
| -------- | ------ | ---- | ---- | ---- |
| 26-工作面回风巷(辅运顺槽) | 瓦斯 | end | 10 | 3 |
| 26-工作面回风巷(辅运顺槽) | 风速 | mid | 0  | （测风站） |
| 26-工作面回风巷(辅运顺槽) | 一氧化碳 | end | 10 | 3 |
| 27-工作面进风巷(胶运顺槽) | 风速 | mid | 0  | （测风站） |
| 27-工作面进风巷(胶运顺槽) | 烟雾 | start | 3 | 1 |
| 27-工作面进风巷(胶运顺槽) | 粉尘 | start | 3 | 1 |
| 28-工作面切眼            | 瓦斯 | start | 5 | 2 |
| 28-工作面切眼            | 一氧化碳 | start | 5 | 2 |
| 3-煤仓                   | 瓦斯 | start | 2 | 1 |
| 25-工作面停采线          | 瓦斯 | mid | 0 | - |
| 29-回采工作面巷道        | 瓦斯 | mid | 0 | - |
| 29-回采工作面巷道        | 粉尘 | start | 5 | 2 |

#### 2c. AQ1029 距离规则

`_AQ1029_DISTANCE_RULES`（locator.py:315-323）：

| keyword | sensor | 方向 | 米数 |
| ------- | ------ | ---- | ---- |
| T1      | *      | start | 5 |
| T2      | *      | end   | 12 |
| *       | 风速   | mid   | 0 |
| *       | 烟雾   | start | 3 |
| *       | 粉尘   | start | 3 |
| *       | 温度（硐室） | mid | 0 |

#### 2d. 关键词区间（无 T 标识）

| 关键词 | 区间 |
| ------ | ---- |
| 迎头   | 0-15% |
| 回风流 | 85-100% |

#### 2e. sensor_type 默认百分比

| sensor_type | 区间 |
| ----------- | ---- |
| 风速        | 40-60% |
| 烟雾 / 粉尘 | 0-20% |
| 温度        | 30-70% |
| 人员定位    | 50%（中点） |
| 其他        | 10-90%（均匀分布） |

### 3. 同组多设备分配

`_distribute_in_zone`（locator.py:486-494）：

- **默认 1m 步长**（`step=1.0`，locator.py:479）从 `lo` 起递增。
- 区间放不下时退化为均匀分布：`step_adj = (hi-lo)/(count-1)`。
- `count == 1` 取区间中点。

### 4. z 轴安装高度

`_SENSOR_INSTALL_HEIGHT`（locator.py:304-311）— 在折线插值的 z 上叠加传感器安装高度：

| sensor_type | z 偏移 (m) | 依据 |
| ----------- | ---------- | ---- |
| 瓦斯       | +0.3 | 距底板 ≥0.3m |
| 风速       | +0.2 | 距顶板 ≤0.3m |
| 烟雾       | +0.2 | 距顶板 ≤0.3m |
| 一氧化碳   | +0.2 | 距顶板 ≤0.3m |
| 温度       | +0.2 | 距顶板 ≤0.3m / 设备上方 |
| 粉尘       | +1.5 | 距底板 1.5-2m |

### 示例

- `七采区避难硐室生存室甲烷` → matched=`七采区避难硐室`, sensor=瓦斯, 区间默认 10-90%, 1m 步长
- `1号分站模拟量皮顺联络巷迎头激光甲烷` → matched=`皮顺联络巷`, keyword=`迎头`, 区间 0-15%
- `1号分站模拟量皮顺联络巷T2激光甲烷` → matched=`皮顺联络巷`, keyword=`T2`, 区间 length-15 ~ length
- `9209进风巷掘进面风速` → matched=`9209进风巷`, sensor=风速, 区间 40-60%
