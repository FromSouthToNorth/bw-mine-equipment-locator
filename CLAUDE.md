# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# 煤矿设备定位 skill (bw-mine-equipment-locator)

根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

**输入：** 用户提供的 username（如 `F18795450`），可选指定设备数据文件  
**输出：** 每个设备匹配到巷道/工作面后的 (x, y, z) 坐标

---

## 仓库架构

本仓库由 3 个协作 skill 组成流水线，**无构建步骤**，纯 Python 脚本 + JSON 数据：

```
用户请求 → bw-token-manager → bw-strategy-api-caller → bw-mine-equipment-locator
                (Step 1)              (Step 2/4)                  (Step 3)
```

### Skill 间调用机制

`locator.py` 并不直接调用 HTTP API，而是通过 **subprocess 委托**给另外两个 skill 的脚本：

- `locator.py` 内定义 `PROJECT_ROOT` 向上回溯 4 层定位仓库根目录，再拼出 `bw_token_manager.py` 和 `strategy_api.py` 的绝对路径
- 调用时使用 `_resolve_python_exe()` 自动探测带 `requests` 的 Python 解释器（优先级：`BW_LOCATOR_PYTHON` 环境变量 > `sys.executable` > `python3`/`python`/`py` > 历史 Windows 兜底路径）
- 所有 API 调用（token 获取、8373 拉取、8385 回写）均通过 `subprocess.run([_PYTHON_EXE, str(STRATEGY_API), ...])` 完成

这意味着修改 `strategy_api.py` 或 `bw_token_manager.py` 会立即影响 locator 的行为，无需重新安装或编译。

### 数据流与缓存

```
8373 API → data/output/data_8373_<mineName>.json
              ↓
         locator.py --match-only
              ↓
    data/output/locator_result_<username>_<mineName>.json
              ↓
         locator.py --writeback → 8385 API
              ↓
         data/cache/match_cache.json (EXACT 匹配缓存)
```

- **match_cache.json**：高置信度（layer=EXACT）匹配自动缓存，键为 `{mark_type}:{description}`，下次运行时直接复用
- **结果文件**：每次匹配自动保存到 `data/output/`，命名包含 username 和 mineName

### CesiumJS 可视化

匹配完成后，若系统安装了 `pyproj`，`locator.py` 自动调用 `data/output/generate_cesium_html.py` 生成 CesiumJS HTML（CGCS2000 → WGS84 坐标转换）。可用 `--html never` 跳过。

### 规则审计注册表

`locator.py` 文件末尾（line ~2460 起）有 `_RULES_REGISTRY` 字典，集中登记所有行业标准条款来源。修改 `_assign_distances`、`_SENSOR_TUNNEL_PREF` 等数据表时，必须同步更新此注册表。

---

## 开发与调试命令

本项目无传统单元测试框架，验证依靠本地测试数据和不同 `--output-mode` 的组合。

### 本地测试（不调用 API）

```bash
# 用测试数据跑完整匹配流程（不回写）
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only

# 只看汇总统计
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --output-mode summary

# 审查未匹配设备及其 Top-3 候选
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --output-mode unmatched

# 审计报告：查看高风险/中风险匹配列表
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --output-mode audit
```

### 数据分析（不执行匹配）

```bash
# 分析已拉取的 8373 数据结构
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --analyze data/output/data_8373_<mineName>.json
```

### 分步回写（从已有结果文件）

```bash
# 从保存的结果 JSON 直接回写 8385，不重新匹配
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

### 生成 CesiumJS 可视化

```bash
# 独立调用（需 pyproj）
python data/output/generate_cesium_html.py \
  data/output/locator_result_<username>_<mineName>.json \
  --data-8373 data/output/data_8373_<mineName>.json
```

### 依赖安装

```bash
pip install -r requirements.txt   # 仅 requests
pip install pyproj                # 可选，CesiumJS 可视化需要
```

**注意：** 本机 `python3` 不可用，使用 `python` 即可。若解释器探测失败，设置 `BW_LOCATOR_PYTHON` 环境变量。

---

## 自然语言调用

Claude 采用**两阶段交互流程**，不直接一键执行：

**阶段 1 — 数据获取 + 分析审查**（等用户确认后才进阶段 2）
**阶段 2 — 匹配定位 + 8385 回写**（匹配汇总展示后等用户确认再回写）

| 用户说 | Claude 行为 |
|--------|-------------|
| `设备定位 F09795450` | 阶段 1：拉 8373 数据 → 展示分析报告 → 等确认 → 阶段 2：匹配+回写 |
| `定位 F18795450` | 同上 |
| `跑一下 locator F09795450` | 同上 |
| `设备定位 F09795450 evals/devices.json` | 使用本地设备文件，其余同上 |

触发关键词：`定位`、`设备定位`、`locator` + 用户名（`F\d+` 格式）

**快捷跳过**：若用户明确说"直接跑"、"不用确认"、"自动跑完"，可跳过审查直接走完整流程。

### 交互式运行流程

**阶段 1：数据获取 + 分析审查**

Claude 先拉数据、做分析，展示给用户确认：

```bash
# Step 1: 获取 Token
python skill/bw-token-manager/scripts/bw_token_manager.py <username>

# Step 2: 拉取 8373 数据
python skill/bw-strategy-api-caller/scripts/strategy_api.py get_json \
  --id 8373 --param "MineName=<mineName>" --username <username>
```

Claude 展示分析报告（设备数、巷道数、mark_type/sensor_type/area 分布、**系统命名巷道占比**、地面/井下拆分），**等用户确认后**继续。

**阶段 2：匹配定位 + 回写**（用户确认后执行）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json
```

匹配完成后 Claude 展示汇总，**等用户确认后** locator.py 自动执行 8385 回写。

**一步到位（跳过审查）**：用户明确说"直接跑"时，可执行单命令（匹配+回写一步完成）：
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json
```

#### 输出模式 (`--output-mode`)

| 模式 | 说明 | stdout 内容 |
|------|------|-------------|
| `full` (默认) | 完整结果 | `results[]` + `unmatched_devices[]` + `warnings[]` |
| `summary` | 仅汇总 | `summary` 分级统计（高/中/低 + B14/B15/B16 + sensor_type 分布） |
| `unmatched` | 未匹配审查 | `summary` + `unmatched_devices[]`（每条含 Top-3 候选 `candidates`） |
| `json-summary` | 汇总 JSON | 同 `summary` + `warnings[]`，stderr 打印人类可读表格 |
| `audit` | 审计报告 | `summary` + `audit`（高风险/中风险匹配列表）+ `results[]` + `unmatched_devices[]` |

**8385 回写始终使用 `full` 数据**，不受 `--output-mode` 影响。

### 命令行参数

```
usage: locator.py [-h] [--load PATH] [--load-devices PATH]
                  [--load-tunnels PATH] [--load-workfaces PATH]
                  [--output-mode {full,summary,unmatched,json-summary,audit}]
                  [--analyze PATH] [--match-only]
                  [--writeback RESULT_JSON] [--html MODE]
                  username [DEVICES_FILE]

positional arguments:
  username              用户名（如 F18795450）
  DEVICES_FILE          设备数据文件（自动识别为 --load-devices）

options:
  --load PATH           从本地文件加载完整 8373 数据（含 devices/tunnels/workfaces）
  --load-devices PATH   从本地文件加载设备数据
  --load-tunnels PATH   从本地文件加载巷道数据
  --load-workfaces PATH 从本地文件加载工作面数据
  --output-mode MODE    输出模式: full=完整结果(默认), summary=仅汇总,
                        unmatched=仅未匹配(含候选), json-summary=汇总JSON,
                        audit=审计报告(含风险匹配列表)
  --analyze PATH        分析 8373 数据文件的结构化报告（仅分析，不匹配退出）
  --match-only          仅匹配不回写（展示汇总后等用户确认，再单独 --writeback）
  --writeback RESULT_JSON  从已保存的结果文件回写 8385，不重复匹配
  --html MODE           CesiumJS 可视化: auto=自动, always=强制, never=跳过
```

---

## 工作流（两阶段交互式）

```
用户: 设备定位 <username>
         │
    ┌────▼────────────────────────────────────┐
    │ ★ 阶段 1: 数据获取 + 分析审查 ★       │
    │                                         │
    │ Step 1: bw-token-manager 获取 Token      │
    │ Step 2: strategy_api.py get_json 拉 8373 │
    │         → 数据落盘到 data/output/        │
    │                                         │
    │ Claude 分析数据并展示报告:               │
    │   设备/巷道/工作面总数                   │
    │   mark_type 分布 (B14/B15/B16)           │
    │   sensor_type 分布                       │
    │   系统命名巷道 vs 具名巷道 (⚠ 将被排除)  │
    │   地面 vs 井下设备                       │
    │   潜在难匹配设备 (无编码描述)             │
    │                                         │
    │ ▼ 等用户确认 ▼                          │
    └────┬────────────────────────────────────┘
         │ (用户确认)
    ┌────▼────────────────────────────────────┐
    │ ★ 阶段 2: 匹配定位 + 回写（两步） ★    │
    │                                         │
    │ Step 3: 匹配（--match-only，不回写）     │
    │   python locator.py <username>           │
    │     --load data_8373_<mineName>.json     │
    │     --match-only                         │
    │   ① 系统巷道(巷道NNN)从候选池排除        │
    │   ② 地面设备(area)跳过井下候选           │
    │   ③ 缓存命中直接复用                     │
    │   ④ 前缀剥离 → 别名扩展 → 编码提取      │
    │   ⑤ LCS评分 + 偏好/编码/类型加分         │
    │   ⑥ 选最佳匹配 → 置信度分层 → 坐标计算   │
    │                                         │
    │ Claude 展示匹配汇总:                     │
    │   匹配率 / 置信度分布 / 未匹配原因        │
    │   系统巷道排除数 / 设备密度分布           │
    │   **审查摘要**（自动输出到 stderr）:      │
    │     高风险匹配数 / 中风险匹配数           │
    │     风险类型: 短名称依赖 / 编码不一致      │
    │                                         │
    │ ▼ 等用户确认回写 ▼                      │
    │                                         │
    │ Step 4: 回写 8385（--writeback）         │
    │   python locator.py <username>           │
    │     --writeback locator_result_*.json    │
    │   → code: 100 表示成功                   │
    └─────────────────────────────────────────┘
```

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

**方式 A：`get_json`（推荐）— 返回平铺数组，locator 直接使用**
```bash
python skill/bw-strategy-api-caller/scripts/strategy_api.py get_json \
  --id 8373 --param "MineName=<mineName>" --username <username>
```
- 向 `GetStrategyJsonData` 发起 POST 请求
- 返回扁平数组，每条记录包含 `id`/`description`/`mark_type`/`area`（设备）或 `name`/`line`（巷道）或 `workFaceName`/`line`（工作面）
- 数据落盘到 `data/output/data_8373_<mineName>.json`
- **Claude 在此步后必须展示数据分析报告，等用户确认才继续**

**注意：** `strategy_api.py` 依赖 `requests`。locator.py 会自动探测带 `requests` 的解释器（优先 `sys.executable`/`python3`/`python`），找不到就报错。可用 `BW_LOCATOR_PYTHON` 显式覆盖。

### Step 3: 匹配设备 → 计算坐标（--match-only）

用户确认阶段 1 后，执行**仅匹配不回写**：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only
```

对每个 device 的处理流程：

1. **数据质量校验**
   - 相同 `id` + 相同 `description` → 正常去重（跳过重复）
   - 相同 `id` + **不同** `description` → **直接报错终止**，强制用户修复上游数据
   - 空 `description` → 跳过并警告
2. **候选过滤（匹配前）**
   - 系统巷道名称（如"巷道136"）→ 直接从候选池排除，记录到 `generic_tunnels_skipped`
   - 地面设备（area 含地面/洗选/磅房等关键词）→ 跳过所有井下候选
   - 匹配缓存命中（`match_cache.json`）→ 直接复用上次结果
3. 从 `description` 提取地点名称（先[剥离前缀](#前缀剥离)），按[匹配逻辑](#匹配逻辑)在**候选名称**中找到最佳匹配
4. **候选来源**（均来自 8373，已排除系统命名巷道）：
   - `tunnels` 数组中的 `name`（主候选，仅具名巷道）
   - `workfaces` 数组中的 `workFaceName`（补充候选）
5. 按[坐标计算](#坐标计算)规则计算 (x, y, z)
6. **Claude 展示匹配汇总后必须等用户确认**，再进入 Step 4

**重要**：locator 不会给重复 ID 加 `_1`、`_2` 后缀来回写。上游数据有重复 ID 时必须先到 BW-MES 后台清理。

**注意：** 必须加 `--match-only`，否则脚本会自动回写 8385。`--match-only` 不会写数据库，只输出 JSON 到 stdout 和保存结果文件。

### Step 4: 回写定位结果到策略 8385（--writeback）

用户确认匹配结果后，执行**单独回写**：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

- 从 Step 3 保存的结果文件加载完整数据
- 向 `ExecuteStrategyCom` 发起 POST 请求
- 返回 `{"code": 100}` 表示成功
- **8385 回写始终使用 `full` 数据**，不受 `--output-mode` 影响

**合并一步执行（用户明确说"直接跑"时）：** 不加 `--match-only`，locator.py 自动完成匹配+回写：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json
```

---

## 项目结构

```
F:\gis\Point\
├── CLAUDE.md              # 本文件
├── data/
│   ├── pdf/              # 煤矿安全规范等 PDF 文档
│   │   └── extracts/     # 条款摘要和 OCR 文本
│   ├── test/             # 测试数据
│   │   └── test_locator.json
│   ├── cache/            # 匹配缓存目录
│   │   └── match_cache.json
│   ├── output/           # 8373 数据、locator 结果、CesiumJS HTML
│   │   ├── generate_cesium_html.py
│   │   ├── data_8373_*.json
│   │   └── locator_result_*.json
│   └── sql/
│       ├── example_8373.json
│       └── index.sql
├── evals/
│   └── evals.json
└── skill/
    ├── bw-token-manager/
    │   └── scripts/bw_token_manager.py
    ├── bw-strategy-api-caller/
    │   └── scripts/strategy_api.py
    └── bw-mine-equipment-locator/
        ├── SKILL.md
        └── scripts/locator.py      # ~2500 行，核心逻辑与规则注册表
```

### 依赖

| Skill | 用途 | 调用时机 |
| ----- | ---- | -------- |
| `bw-token-manager` | 获取 BW-MES API token, mineName | Step 1 |
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

> **术语一致性注意：** `SKILL.md` 中部分 sensor_type 写为"摄像仪"，但代码与本文档统一使用"工业视频"。以 `locator.py` 源码为准。

### 候选来源（均来自 8373，已过滤系统命名巷道）

| 来源 | 字段 | category |
| ---- | ---- | -------- |
| `tunnels[]` | `name` | tunnel |
| `workfaces[]` | `workFaceName` | workface |

**系统生成巷道名称过滤**：形如"巷道136"的名称为系统自动生成，无实际语义含义，设备描述不可能包含此类名称。`_extract_candidates` 阶段直接从候选池排除，避免产生无效低分匹配。被排除数量记录在输出 `summary.generic_tunnels_skipped` 和 `warnings[]` 中（`type: generic_tunnels_excluded`）。

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
      + 5  if  device_code 在候选名内(精确匹配，通用前缀仅+1)
      + 3  if  device_code 是候选名中数字编码的前缀(前缀模糊匹配)
      + 3  if  候选 tunnelId 含 device_code(workface 关联)
      + n  巷道类型匹配关键词加分
      - 1  coalbed 不一致
      - 10 _LOCATION_SEMANTICS 语义冲突
      - 20 specific code 不在候选名/tunnelId 中（编码强制约束）
```

**硬性语义冲突（直接 REJECT，见下方 `_has_hard_semantic_conflict`）**：
- 地点锚定词冲突：描述含 `*采区`/`*翼`/特定地名，候选不含相同词 → REJECT
- 功能互斥冲突：`底抽` vs `回风`/`进风`，`回风` vs `进风`/`底抽` 等 → REJECT

**分层判定（layer）：**
| layer | 条件 | confidence |
|-------|------|------------|
| 1 (EXACT) | 编码精确命中（非通用前缀）且 lcs≥1 | 高 |
| 2 (LCS_PREF) | 前缀模糊命中 或 score≥5，**且 LCS≥2** | 中 |
| 3 (LOW) | score≥2 但无编码/前缀命中 | 低 |
| 4 (REJECT) | score<2 或 硬性语义冲突 | 极低(拒绝) |

- 最低门槛：`score ≥ 2`
- LCS_PREF 最低门槛：**`LCS ≥ 2`**（防止单字符靠短名称膨胀分数）
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

`_LOCATION_SEMANTICS`（locator.py:441）— 描述含此关键字时，候选必须含其一，否则扣 10（并在 `_has_hard_semantic_conflict` 中直接拒绝）：

| 描述含 | 候选必须含其一 |
| ------ | -------------- |
| 洗煤厂 | 洗煤厂 |
| 中央变电所 | 变电, 配电 |
| 避难硐室 | 硐室 |
| 井口   | 井口, 井筒, 副井, 主井 |
| 地面   | 地面, 洗煤厂, 空压机房 |
| 通风机 | 通风, 风机, 通风机 |
| 主扇   | 通风, 风机, 主扇 |
| 排矸   | 排矸 |
| 联巷   | 联巷, 联络巷 |
| 泵站/泵房 | 泵站, 泵房 |
| 瓦斯泵站/瓦斯泵房 | 瓦斯泵站, 瓦斯泵房, 瓦斯抽放泵站, 移动式瓦斯泵站, 瓦斯抽放 |
| 运输巷 | 运输巷, 运输大巷 |
| 充电硐室 | 充电硐室, 充电站 |
| **皮带** | **皮带, 运输, 输送机, 机头, 机尾, 转载, 胶运, 顺槽** |
| **机头** | **机头, 皮带, 运输, 输送机, 顺槽, 胶运** |
| **压带轮** | **皮带, 运输, 输送机, 顺槽, 机轨** |
| **卸料器** | **皮带, 运输, 输送机, 煤仓, 转载, 顺槽** |
| **运输大巷** | **运输, 大巷, 皮带, 轨道, 顺槽, 胶运** |
| **候车室** | **候车室, 车场** |
| **机轨** | **机轨, 机轨运输** |

**特殊放宽**：描述同时含"硐室"与"皮带"/"机头"/"压带轮"/"卸料器"时（如"皮带机头硐室"），视为特定硐室地点，不触发运输设备关键词的泛化语义拦截。

### 硬性语义冲突（直接 REJECT）

`_has_hard_semantic_conflict`（locator.py:770）— 地点/功能词不一致时**直接拒绝**，宁缺毋滥。在 `_score_candidates` 分层判定前调用，冲突则 layer=REJECT。

**1. 地点锚定词冲突**：描述含地点限定词，候选必须含相同词。

`_AREA_ANCHOR_RE` 匹配模式：
- `[一二三四五六七八九十百千\d]+采区`（六采区、一采区等）
- `[东西南北]翼`（西翼、北翼、东翼、南翼）
- 特定巷道/地名：`暗斜井`、`哈拉沟`、`马蹄沟`、`马蹄坡`（矿特有，后续按需扩展）

**2. 功能互斥冲突**：
| 描述含 | 候选若含以下词则冲突 |
| ------ | -------------------- |
| 底抽 | 回风、进风、皮顺、胶运 |
| 回风 | 进风、底抽、胶运 |
| 进风 | 回风、底抽、皮顺 |

**3. `_LOCATION_SEMANTICS` 冲突**：描述含 `_LOCATION_SEMANTICS` 关键字但候选不含允许词时，直接拒绝（编码/LCS 无法掩盖）。|

### 编码强制约束（score −20）

当 `device_code` 是 specific code（3+位数字/含字母/负数水平）时，**候选名或 tunnelId 必须包含该编码**，否则 score −20（直接 REJECT）。

避免 `1460排矸一台皮带CO` 匹配到不含 `1460` 的纯 `排矸`（短名称依赖+LCS 膨胀）。

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
- 若 area 含"地面/露天矿/洗选/销售/磅房/风机房/材料大库房/炸药库/计算机资源/档案室/队组楼/设备废料/井上"等地面关键词
- → 直接跳过所有井下候选，标记为 `AREA_SURFACE` 未匹配
- 避免地面设备（洗煤厂、磅房、地面机房硐室等）错误匹配到井下巷道/工作面

### 置信度

`_calc_confidence`（locator.py:1227-1235）— 基于分层 `layer`：

| layer | 条件 | confidence |
|-------|------|------------|
| EXACT (1) | 编码精确命中（非通用前缀）且 lcs≥1 | 高 |
| LCS_PREF (2) | 前缀模糊命中 或 score≥5，**且 LCS≥2** | 中 |
| LOW (3) | score≥2 但无编码/前缀命中 | 低 |
| REJECT (4) | score<2 或 硬性语义冲突 | 极低 |

### 未匹配拒绝原因

`unmatched_devices` 中每个设备带 `reason` 字段（locator.py:1244-1282）：

| reason | 含义 |
|--------|------|
| `NO_CANDIDATE` | 无可行候选（candidates 为空，或所有候选为系统命名巷道已被排除） |
| `CODE_MISMATCH` | 提取到编码但所有候选均不匹配（含前缀尝试，或编码仅出现在语义冲突候选中） |
| `SEMANTIC_CONFLICT` | 语义惩罚阻断所有候选（所有候选均扣 -10）或硬性语义冲突 |
| `LOW_LCS` | LCS 得分过低（< 2），无其他匹配途径 |
| `AREA_SURFACE` | area 语义为地面（非井下），排除巷道/工作面候选 |

**注意**：`NO_CANDIDATE` 在大量系统巷道被排除后更常见——设备可能本应对应某条"巷道NNN"但该巷道已被过滤，无其他具名候选可匹配。此行为是设计意图，宁缺毋滥。

### 匹配缓存

高置信度（layer=EXACT）匹配自动写入 `data/cache/match_cache.json`（locator.py:1084-1120）：
- 键：`{mark_type}:{description}`
- 值：`{matched_name, candidate_id, score, timestamp}`
- 下次运行时优先查缓存，命中则直接复用匹配结果

**缓存语义校验**（locator.py:1637）：缓存命中时也会调用 `_has_hard_semantic_conflict` 检查。若缓存结果与当前描述存在硬性语义冲突（如地点/功能词不一致），则忽略缓存重新匹配。防止历史错误缓存被复用。

### 系统巷道过滤与告警

**系统巷道排除**：`_is_generic_tunnel_name`（locator.py 新增）— 形如 `巷道\d+` 的名称在 `_extract_candidates` 阶段直接从候选池排除。

- 排除数量记录在 `summary.generic_tunnels_skipped`
- 输出 JSON 中 `warnings` 数组包含 `type: generic_tunnels_excluded` 条目

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
