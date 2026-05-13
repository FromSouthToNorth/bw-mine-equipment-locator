# 煤矿设备定位 skill (bw-mine-equipment-locator)

根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

**输入：** 用户提供的 username（如 `F18795450`），可选指定设备数据文件  
**输出：** 每个设备匹配到巷道/工作面后的 (x, y, z) 坐标

### 自然语言调用

| 用户说 | 执行 |
|--------|------|
| `设备定位 F09795450` | `python skill/.../locator.py F09795450` |
| `定位 F18795450` | `python skill/.../locator.py F18795450` |
| `跑一下 locator F09795450` | `python skill/.../locator.py F09795450` |
| `设备定位 F09795450 evals/devices.json` | `python skill/.../locator.py F09795450 evals/devices.json` |
| `用本地设备文件定位 F09795450` | `python skill/.../locator.py F09795450 <文件路径>` |

触发关键词：`定位`、`设备定位`、`locator` + 用户名（`F\d+` 格式）

### 快速运行

从 API 拉取数据并定位：
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py F18795450
```

从本地设备文件定位（巷道/工作面仍从 API 获取）：
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py F09795450 evals/devices.json
```

输出 JSON 到 stdout（含每个设备的 matched_name 和 coordinates），汇总到 stderr。

### 命令行参数

```
usage: locator.py [-h] [--load PATH] [--load-devices PATH]
                  [--load-tunnels PATH] [--load-workfaces PATH]
                  username [DEVICES_FILE]

positional arguments:
  username              用户名（如 F18795450）
  DEVICES_FILE          设备数据文件（自动识别为 --load-devices）

options:
  --load PATH           从本地文件加载完整 8373 数据（含 devices/tunnels/workfaces）
  --load-devices PATH   从本地文件加载设备数据
  --load-tunnels PATH   从本地文件加载巷道数据
  --load-workfaces PATH 从本地文件加载工作面数据
```

注：本机 `python3` 不可用，使用 `python` 即可。

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
- 返回扁平数组，每条记录包含 `id`/`description`/`mark_type`/`area`（设备）或 `name`/`line`（巷道）或 `workFaceName`/`line`（工作面）
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
│   ├── test/             # 测试数据
│   │   └── test_locator.json  # 优化功能验证测试数据
│   ├── cache/            # 匹配缓存目录
│   └── sql/
│       ├── example_8373.json  # 策略 8373 示例数据（设备+巷道+工作面）
│       └── index.sql      # 策略 8373 的 SQL 查询（表结构参考）
├── evals/
│   └── evals.json         # 测试评估 prompt
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

| 文档 | 用途 |
| ---- | ---- |
| `data/pdf/standards/AQ 1029-2019 煤矿安全监控系统及检测仪器使用管理规范.pdf` | B14 安全监测设备描述、传感器安装位置、距离规则 |
| `data/pdf/standards/DB51T1412—2011煤矿井下人员定位系统安全技术规范.pdf` | B15 人员定位读卡器/分站安装位置（井口、井底、岔口、硐室、工作面等）。**安装高度引用** §5.2.3 |
| `data/pdf/standards/AQ1119-2023_煤矿井下人员定位系统通用技术条件.pdf` | B15 应急行业标准（替代 AQ 6210-2007）。区域分类：§3.10 重点区域、§3.11 限制区域、§3.13 准入区域 |
| `data/pdf/standards/MT-T1198-2023_煤矿井下人员位置监测系统使用与管理规范.pdf` | B15 设置规范。§5.1 定位分站位置、§5.2 位置监测卡位置（条款摘要见 `data/pdf/extracts/B15_条款摘要.md`） |
| `data/pdf/standards/煤矿工业视频安装及联网接入规范（试行）.pdf` | B16 工业视频系统安装位置及监视内容（煤矿工业视频安装及联网接入规范，2024-12） |

### 策略 8373 返回结构

**`get_json` 返回格式（扁平数组，设备+巷道+工作面混排）：**
```json
{
  "code": 100,
  "data": [
    { "id": "JKYHMK0030120001A01", "sysaliasname":"安全监测系统", "description": "1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯", "sensor_type": "瓦斯", "mark_type": "B14", "area": "采掘工作面" },
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

设备字段缺 `sensor_type` 时，从描述关键词按顺序推断（`_infer_sensor_type`，locator.py:186-217）：

`二氧化碳（CO2） > 氧气（O2） > 负压（风压） > 风速 > 烟雾 > 粉尘 > 温度 > 一氧化碳（CO/一氧化碳，排除 CO2） > 瓦斯（甲烷/CH4） > 开停 > 馈电 > 断电 > 人员定位（人数/人员） > 工业视频（工业视频/摄像头/视频监控/视频监测） > [兜底] mark_type=B16 → 工业视频`

- 新增 `二氧化碳/氧气/负压` 优先于其他类型识别（基于 AQ 1029-2019 公开知识，条款号 TBD）。
- **mark_type 与 sensor_type 是完全不同的概念**：B16 是系统大类（工业视频系统），sensor_type 应为设备类型 `工业视频`，不应混用。

### 编码提取

`extract_workface_code`（locator.py:354-372），按优先级：
1. **中文数字 + 采区/煤层/盘区/水平**：`九采区` → `9`、`七煤层` → `7`
2. 字母+3-4 数字：`C8302`、`F1302`
3. 负号+3-4 数字（水平标高）：`-490`、`-725`
4. 4 位纯数字（前后无字母）：`5318`、`9209`
5. 3 位纯数字（前后无字母）：`920`、`518`

### 别名映射

`_TUNNEL_ALIAS_MAP`（locator.py:260-280）+ `_expand_aliases` — 匹配前对 description 和候选 name 双向扩展，解决简称/全称差异：

| description 中出现 | 扩展为 |
|---|---|
| 皮顺 | 皮顺\|皮带顺槽\|辅运顺槽\|胶带顺槽 |
| 胶运 | 胶运\|胶带运输\|进风巷\|胶运顺槽 |
| 联络巷 | 联络巷\|联巷 |
| 切巷 | 切巷\|切眼 |

### 评分公式

`find_best_match`（locator.py:482-543）— **分层匹配策略**：

```
score = round(LCS_长度(别名扩展后) × 10 / 候选名长度)
      + 2  if  sensor_type 命中候选名巷道偏好且 LCS≥2
      + 5  if  device_code 在候选名内(精确匹配)
      + 3  if  device_code 是候选名中数字编码的前缀(前缀模糊匹配)
      + 3  if  候选 tunnelId 含 device_code(workface 关联)
      + n  巷道类型匹配关键词加分
      - 1  coalbed 不一致
      - 10 _LOCATION_SEMANTICS 语义冲突
```

**分层判定（layer）：**
| layer | 条件 | confidence |
|-------|------|------------|
| 1 (EXACT) | 编码精确命中 或 code_indices 命中 | 高 |
| 2 (LCS_PREF) | 前缀模糊命中 或 score≥5 | 中 |
| 3 (LOW) | score≥2 但无编码/前缀命中 | 低 |
| 4 (REJECT) | score<2 | 极低(拒绝) |

- 最低门槛：`score ≥ 2`
- 平局判定：编码命中 > LCS 长 > 候选名长

### sensor_type 巷道偏好（命中 +2）

`_SENSOR_TUNNEL_PREF`（locator.py:213-227）。基于 AQ 1029-2019 公开知识（条款号 TBD）+ 实际数据观察：

| sensor_type | 偏好关键字 |
| ----------- | ---------- |
| 瓦斯       | 回风巷, 进风巷, 切巷, 工作面, 顺槽, 石门, 大巷, 采空, 排瓦斯, 高冒 |
| 一氧化碳   | 隅角, 皮带, 硐室, 石门, 滚筒, 采空, 封闭火区, 采煤工作面 |
| 风速       | 测风站, 总回风, 回风巷, 一翼回风, 采区回风, 盘区回风 |
| 温度       | 硐室, 压风机, 工作面, 机电, 中央变电, 采区变电 |
| 烟雾       | 皮带, 运输, 机头, 机尾, 滚筒, 胶带, 胶运 |
| 粉尘       | 采煤, 掘进, 转载, 破碎, 装煤, 综采, 综掘, 回采 |
| 馈电 / 断电 | 配电, 变电, 开关, 馈电 |
| 开停       | 配电, 变电, 开关, 风机 |
| 人员定位   | 井口, 井底, 交叉口, 岔口, 分流, 联络巷, 大巷, 入口, 工作面, 采区, 采面, 运输巷, 回风巷, 进风巷, 副井, 运输斜井, 充电站, 硐室, 变电所, 水泵房, 重点, 准入, 限制 — MT/T 1198-2023 §5.1.2-5 + AQ 1119-2023 §3.10/3.13 |
| 氧气       | 工作面, 硐室, 采空 |
| 二氧化碳   | 采空, 封闭火区, 回风巷 |
| 负压       | 风机, 通风机, 风筒 |
| 工业视频 (B16) | 工作面, 顺槽, 运输巷, 回风巷, 进风巷, 大巷, 斜巷, 硐室, 变电所, 水泵房, 泵房, 车场, 井口, 井底, 煤仓, 皮带, 输送机, 转载点, 机头, 机尾, 避难, 绞车房, 调度, 提升, 通风, 空压, 瓦斯泵, 制氮, 灌浆, 坑木场, 工业广场, 煤场, 支架, 超前支护, 迎头, 乘车, 副立井, 罐笼 |

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

### area 语义过滤

`_AREA_SURFACE_PATTERNS` + `_is_surface_area` — area 字段标记设备所属区域。匹配前判断语义：
- 若 area 含"地面/洗选/销售/磅房/风机房/材料大库房/炸药库/计算机资源/档案室/队组楼/设备废料/井上"等地面关键词
- → 直接跳过所有井下候选，标记为 `AREA_SURFACE` 未匹配
- 避免地面设备（洗煤厂、磅房、地面机房硐室等）错误匹配到井下巷道/工作面

### 置信度

`_calc_confidence`（locator.py:1227-1235）— 基于分层 `layer`：

| layer | 条件 | confidence |
|-------|------|------------|
| EXACT (1) | 编码精确命中（device_code 在候选名内） | 高 |
| LCS_PREF (2) | 前缀模糊命中 或 score≥5 | 中 |
| LOW (3) | score≥2 但无编码命中 | 低 |
| REJECT (4) | score<2 | 极低 |

### 未匹配拒绝原因

`unmatched_devices` 中每个设备带 `reason` 字段（locator.py:1244-1282）：

| reason | 含义 |
|--------|------|
| `NO_CANDIDATE` | 无可行候选（candidates 为空） |
| `CODE_MISMATCH` | 提取到编码但所有候选均不匹配（含前缀尝试） |
| `SEMANTIC_CONFLICT` | 语义惩罚阻断所有候选（所有候选均扣 -10） |
| `LOW_LCS` | LCS 得分过低（< 2），无其他匹配途径 |
| `AREA_SURFACE` | area 语义为地面（非井下），排除巷道/工作面候选 |

### 匹配缓存

高置信度（layer=EXACT）匹配自动写入 `data/cache/match_cache.json`（locator.py:1084-1120）：
- 键：`{mark_type}:{description}`
- 值：`{matched_name, candidate_id, score, timestamp}`
- 下次运行时优先查缓存，命中则直接复用匹配结果

### 风速间距检查

`_check_wind_speed_spacing`（locator.py:1155-1195）— 同组风速传感器间距 < 10m 时告警：
- AQ 1029-2019 7.2.1：测风站前后 10m 无分支
- 输出 JSON 中 `warnings` 数组包含 `type: wind_speed_spacing` 条目

---

## 坐标计算

匹配成功后，沿命中巷道/工作面的 `line` 折线计算 (x, y, z)。

### 1. 分组键

`group_key = (matched_name, keyword)`（locator.py:984-989）。`keyword` 由 `_classify_keyword`（locator.py:624-644）决定：

- `T1/T2/T0/T3/T4`（从描述提取）
- `迎头` / `回风流`（描述含关键词）
- B15 关键词：`井口` / `井底` / `岔口` / `硐室` / `充电站` (MT/T 1198-2023 §5.1.4)
- B16 关键词：`机头` / `机尾` / `转载点` / `中部` / `超前支护` / `T2处` / `支架` / `煤仓` / `车场` / `地面`，以及房间类（水泵房/变电所/绞车房/避难硐室/调度室/提升机房/通风机房/空压机房/瓦斯泵站/制氮/灌浆站/坑木场）
- `default`（无）

→ 同巷道但不同关键词的设备分到不同组，不冲突。

### 2. 区间确定优先级

`_assign_distances`（locator.py:478-590）：

```
显式距离(米) > T 标识规则 > 巷道类型×sensor_type 规则 > AQ1029 距离规则 > 关键词区间 > sensor_type 默认百分比
```

**显式距离**：若 description 含 `NN米` 模式（如 `2730米`、`660米`、`10米`），提取距离值。当 keyword 有语义区间且描述含方向词时，从区间基准偏移（如 keyword=硐室+外西60米=50%-60m）；否则作为绝对距离（clamp 到折线总长）。

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
| 26-工作面回风巷(辅运顺槽) | 工业视频 | end | 17 | 5 | MT/T 1201.6 A.1#11 |
| 27-工作面进风巷(胶运顺槽) | 风速 | mid | 0  | （测风站） |
| 27-工作面进风巷(胶运顺槽) | 烟雾 | start | 3 | 1 |
| 27-工作面进风巷(胶运顺槽) | 粉尘 | start | 3 | 1 |
| 27-工作面进风巷(胶运顺槽) | 工业视频 | start | 12 | 3 | MT/T 1201.6 A.1#3 |
| 28-工作面切眼            | 瓦斯 | start | 5 | 2 |
| 28-工作面切眼            | 一氧化碳 | start | 5 | 2 |
| 28-工作面切眼            | 工业视频 | mid | 0 | - | MT/T 1201.6 A.1#1 |
| 3-煤仓                   | 瓦斯 | start | 2 | 1 |
| 25-工作面停采线          | 瓦斯 | mid | 0 | - |
| 29-回采工作面巷道        | 瓦斯 | mid | 0 | - |
| 29-回采工作面巷道        | 粉尘 | start | 5 | 2 |
| 29-回采工作面巷道        | 工业视频 | start | 12 | 3 | MT/T 1201.6 A.1#2 |

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

| 关键词 | 区间 | 依据 |
| ------ | ---- | ---- |
| 迎头   | 0-15% | AQ 1029-2019 6.3.1 |
| 回风流 | 85-100% | AQ 1029-2019 6.3.1 |
| 井口   | 0-10% | MT/T 1198-2023 §5.1.2 / DB51T1412-2011 5.1.8.1：出/入井口 |
| 井底   | 90-100% | DB51T1412-2011 5.1.8.1：井底处 |
| 岔口   | 10-25% | MT/T 1198-2023 §5.1.3 / DB51T1412-2011 5.1.8.2：主要交叉巷口/分流路口 |
| 硐室   | 40-60% | MT/T 1198-2023 §5.2.4（准入区域：变电所/水泵房）/ DB51T1412-2011 5.1.8.5 |
| 充电站 | 40-60% | MT/T 1198-2023 §5.1.4：井下专用充电站 |
| **B16 工业视频** | | MT/T 1201.6-2023 附录 A.1（与 2024-12 试行版同源） |
| 机头 / 机头及转载点 | 0-10% | 带式输送机/刮板输送机机头位置 |
| 机尾 / 机尾及转载机 | 90-100% | 带式输送机/刮板输送机机尾位置 |
| 转载点 | 0-15% | 溜煤眼/转载点 |
| 中部（皮带/输送机） | 40-60% | 皮带中部，每 500m 安设 |
| 超前支护 | 0-15% | 距工作面煤壁 10-15m |
| T2处 | 85-100% | 回风顺槽外口/回风口附近 |
| 支架 | 30-70% | 工作面支架，沿工作面均匀分布 |
| 煤仓 | 30-70% | 煤仓上/下口落煤处 |
| 车场 | 30-70% | 车场区域全景 |
| 地面（工业广场/煤场等） | 30-70% | 地面设施居中 |

#### 2e. sensor_type 默认百分比

| sensor_type | 区间 | 依据 |
| ----------- | ---- | ---- |
| 风速        | 40-60% | 测风站 |
| 烟雾 / 粉尘 | 0-20% | 产尘点 |
| 温度        | 30-70% | 设备上方 |
| 人员定位    | 40-60% | DB51T1412-2011 5.1.8.2：>1000m 时中部增设 |
| 其他        | 10-90%（均匀分布） | |

### 3. 同组多设备分配

`_distribute_in_zone`（locator.py:486-494）：

- **默认 1m 步长**（`step=1.0`，locator.py:479）从 `lo` 起递增。
- 区间放不下时退化为均匀分布：`step_adj = (hi-lo)/(count-1)`。
- `count == 1` 取区间中点。
- **B16 工业视频自定义步长**（MT/T 1201.6-2023 附录 A）：
  - 支架（A.1#1）：`step = 75m`（≤50架间距）
  - 中部（A.1#16）：`step = 500m`（主运输皮带）
  - 架空乘人（A.1#23）：`step = 100m`

### 4. z 轴安装高度

`_SENSOR_INSTALL_HEIGHT`（locator.py:304-314）— 在折线插值的 z 上叠加传感器安装高度：

| sensor_type | z 偏移 (m) | 依据 |
| ----------- | ---------- | ---- |
| 瓦斯       | +0.3 | 距底板 ≥0.3m |
| 风速       | +0.2 | 距顶板 ≤0.3m |
| 烟雾       | +0.2 | 距顶板 ≤0.3m |
| 一氧化碳   | +0.2 | 距顶板 ≤0.3m |
| 温度       | +0.2 | 距顶板 ≤0.3m / 设备上方 |
| 粉尘       | +1.5 | 距底板 1.5-2m |
| 氧气       | +0.2 | 距顶板 ≤0.3m，挂顶 |
| 二氧化碳   | +0.5 | 距底板 0.3-1.5m，CO2 重于空气 |
| 负压       | 0.0  | 风压表，贴风筒/风机出口 |
| 人员定位   | +0.3 | DB51T1412-2011 §5.2.3：读卡器靠近顶板及帮侧 300mm；分站距底板 ≥300mm。AQ 1119-2023 §5.1.3 + MT/T 1198-2023 §5.1.6 工艺约束：远离人员碰触位置、固定支撑良好 |
| 工业视频     | +1.8 | MT/T 1201.6-2023 §4.2 / 附录 A：以人眼高度近似（巷道/硐室通用） |

### 示例

- `七采区避难硐室生存室甲烷` → matched=`七采区避难硐室`, sensor=瓦斯, 区间默认 10-90%, 1m 步长
- `1号分站模拟量皮顺联络巷迎头激光甲烷` → matched=`皮顺联络巷`, keyword=`迎头`, 区间 0-15%
- `1号分站模拟量皮顺联络巷T2激光甲烷` → matched=`皮顺联络巷`, keyword=`T2`, 区间 length-15 ~ length
- `9209进风巷掘进面风速` → matched=`9209进风巷`, sensor=风速, 区间 40-60%
