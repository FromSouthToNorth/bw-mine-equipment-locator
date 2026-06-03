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
- 调用时使用 `_resolve_python_exe()`（locator.py:36）自动探测带 `requests` 的 Python 解释器（优先级：`BW_LOCATOR_PYTHON` 环境变量 > `sys.executable` > `python3`/`python`/`py` > 历史 Windows 兜底路径）
- 所有 API 调用（token 获取、8373 拉取、8385 回写）均通过 `subprocess.run([_PYTHON_EXE, str(STRATEGY_API), ...])` 完成

这意味着修改 `strategy_api.py` 或 `bw_token_manager.py` 会立即影响 locator 的行为，无需重新安装或编译。

> **强制约定：Skill 修改边界**
> 本仓库中，**仅允许修改 `bw-mine-equipment-locator` skill 的代码**（即 `skill/bw-mine-equipment-locator/` 目录下的文件，核心为 `locator.py`）。
> **`bw-token-manager` 和 `bw-strategy-api-caller` 两个 skill 的代码禁止修改**。如果它们存在 bug 或行为不符合预期，应通过以下方式绕过或上报，而不是直接修改：
> - Token 获取失败 → 直接调用 API 获取 token，或手写 Python 脚本完成同等功能
> - API 调用异常 → 使用 `curl` / `requests` 直接调用，或另写独立脚本
> - 参数格式变更 → 在 `locator.py` 的 subprocess 调用参数中适配
>
> 此约定确保上游 skill 保持独立性和可替换性，避免将业务逻辑泄漏到通用基础设施中。

### 数据流与缓存

```
8373 API → data/output/data_8373_<mineName>.json
              ↓
         locator.py --match-only
              ↓
    data/output/locator_result_<username>_<mineName>.json  (全量保存)
              ↓
    回写过滤: _filter_low_confidence(include_low=True) → 全部回写，低置信度带警告
              ↓
         locator.py --writeback → 8385 API
              ↓
    data/cache/match_cache.json (EXACT 匹配缓存)
    data/backup/data_8385_<mineName>_<timestamp>.json (回写前自动备份)
```

- **match_cache.json**：高置信度（layer=EXACT）匹配自动缓存，键为 `{mark_type}:{description}`，下次运行时直接复用
- **结果文件**：每次匹配自动保存到 `data/output/`，命名包含 username 和 mineName，**始终保存全量结果**（含低置信度），供后续审计
- **回写前备份**：回写 8385 前自动将当前 `originData` 备份到 `data/backup/`，以便出问题恢复
- **低置信度行为**：当前代码使用 `include_low=True`，所有置信度结果均写入 8385，但 stderr 会醒目提示低置信度数量，用户可在确认环节取消

### CesiumJS 可视化

匹配完成后，若系统安装了 `pyproj`，`locator.py` 自动调用 `data/output/generate_cesium_html.py` 生成 CesiumJS HTML（CGCS2000 → WGS84 坐标转换）。可用 `--html never` 跳过。

辅助脚本（非主流程，独立使用）：
- `data/output/generate_cesium_data.py` — 从 8373 数据 + CAD 标注生成 Cesium 可用的 JSON 数据（含仿射变换）
- `data/output/export_geojson.py` — 将 Cesium 数据导出为 GeoJSON（LineString 巷道 + Point 设备）

### 规则审计注册表

`locator.py` 中 `_RULES_REGISTRY` 字典（locator.py:4359）集中登记所有行业标准条款来源。修改 `_assign_distances`、`_SENSOR_TUNNEL_PREF` 等数据表时，必须同步更新此注册表。

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

# 只匹配指定设备（调试用）
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --device-ids ID1,ID2
```

### 数据分析（不执行匹配）

```bash
# 分析已拉取的 8373 数据结构（含设备/巷道/工作面/CAD 数据）
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --analyze data/output/data_8373_<mineName>.json
```

分析输出包含：设备/巷道/工作面总数、mark_type/sensor_type 分布、系统命名巷道 vs 具名巷道、地面 vs 井下拆分、**CAD 数据分析**（标注点分类统计、路标覆盖、噪声/有效路标占比）、originData 覆盖风险提示。

### 分步回写（从已有结果文件）

```bash
# 从保存的结果 JSON 直接回写 8385，不重新匹配
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json

# 跳过确认提示（自动化/CI）
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json --yes
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
| `设备定位 F09795450` | **阶段 1**：拉 8373 数据 → `--analyze` 展示分析报告 → **STOP 等用户确认** → **阶段 2**：`--match-only` 匹配 → 展示汇总 → **STOP 等用户确认回写** → `--writeback` 回写 |
| `定位 F18795450` | 同上（严格执行两阶段，每阶段后 STOP 等待确认） |
| `跑一下 locator F09795450` | 同上 |
| `设备定位 F09795450 evals/devices.json` | 使用本地设备文件，其余同上 |

触发关键词：`定位`、`设备定位`、`locator` + 用户名（`F\d+` 格式）

**⚠️ 平台约束（openclaw 等 Skill 平台）**：
- **阶段 1 必须 STOP**：拉取 8373 数据后运行 `--analyze` 展示分析报告，**严禁自动进入阶段 2**，必须等待用户明确回复"确认"/"继续"/"匹配"。
- **阶段 2 必须 STOP**：运行 `--match-only` 展示匹配汇总后，**严禁自动回写**，必须等待用户明确回复"确认回写"/"回写"/"确定"。
- **每次调用独立**：前一次用户说"直接跑"不影响下一次调用，每次触发默认执行两阶段流程。
- **`--analyze` 不是可选步骤**：阶段 1 必须执行 `--analyze` 并展示完整报告（含 CAD 数据分析、originData 覆盖风险提示）。

**快捷跳过**：若用户明确说"直接跑"、"不用确认"、"自动跑完"，可跳过审查直接走完整流程。脚本层面有 `--yes` 可绕过确认提示：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --yes
```

> **跨平台约束说明**：`--yes` 是显式跳过确认的唯一方式。不加 `--yes` 时，locator.py 会在回写前交互式提示用户确认，无论运行在 Claude Code、人工终端或 CI 中，行为一致。

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

Claude 执行 `--analyze` 展示完整分析报告（设备数、巷道数、mark_type/sensor_type/area 分布、**系统命名巷道占比**、**无名巷道数**、地面/井下拆分、**CAD 数据分析**），**等用户确认后**继续。

> **⚠️ 原始 8385 数据醒目展示**：分析报告中必须将 `originData`（当前 8385 已有标注数据）放在报告显著位置，使用 ⚠️ 标记、**粗体**、边框线等方式醒目体现已有设备总数和已有坐标数，让用户一眼看到本次操作会覆盖的数据范围。

**特别关注**：若 MineName 过滤返回空数据，应自动尝试不带 MineName 参数拉取全量数据，并检查设备 ID 前缀是否全部属于同一矿。

**阶段 2：匹配定位 + 回写**（用户确认后执行）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only
```

匹配完成后 Claude 展示汇总 + **回写计划**，**等用户确认后**再执行 8385 回写。

Claude 应明确展示：
- 匹配率、置信度分布（高/中/低）、未匹配原因分布
- **CAD 路标统计**：路标总数 / 覆盖巷道数 / 通过路标精确定位的设备数
- **回写计划**：待回写 N 条（含低置信度数量警告）
- 审查摘要（高/中风险匹配数）

**重要**：locator 不会给重复 ID 加 `_1`、`_2` 后缀来回写。上游数据有重复 ID 时必须先到 BW-MES 后台清理。

**注意：** `--match-only` 不会写 8385，只输出 JSON 到 stdout 和保存结果文件。

### Step 4: 回写定位结果到策略 8385（--writeback）

用户确认匹配结果后，执行**单独回写**：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

- 从 Step 3 保存的结果文件加载完整数据
- **回写前自动备份** `originData` 到 `data/backup/`
- **低置信度包含在回写中**（`include_low=True`），stderr 会提示低置信度数量
- 向 `ExecuteStrategyCom` 发起 POST 请求时发送全部结果
- 回写前交互式确认：用户可取消；加 `--yes` 则自动跳过
- 返回 `{"code": 100}` 表示成功

#### 输出模式 (`--output-mode`)

| 模式 | 说明 | stdout 内容 |
|------|------|-------------|
| `full` (默认) | 完整结果 | `results[]` + `unmatched_devices[]` + `warnings[]` |
| `summary` | 仅汇总 | `summary` 分级统计（高/中/低 + B14/B15/B16 + sensor_type 分布） |
| `unmatched` | 未匹配审查 | `summary` + `unmatched_devices[]`（每条含 Top-3 候选 `candidates`） |
| `json-summary` | 汇总 JSON | 同 `summary` + `warnings[]`，stderr 打印人类可读表格 |
| `audit` | 审计报告 | `summary` + `audit`（高风险/中风险匹配列表）+ `results[]` + `unmatched_devices[]` |

结果文件始终保存全量数据。不受 `--output-mode` 影响。

### 命令行参数

```
usage: locator.py [-h] [--load PATH] [--load-devices PATH]
                  [--load-tunnels PATH] [--load-workfaces PATH]
                  [--output-mode {full,summary,unmatched,json-summary,audit}]
                  [--analyze PATH] [--html MODE] [-y]
                  [--match-only] [--writeback RESULT_JSON]
                  [--device-ids IDS] [--device-ids-file PATH]
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
  --analyze PATH        分析 8373 数据文件的结构化报告（含 CAD 数据分析，仅分析退出）
  --html MODE           CesiumJS 可视化: auto=自动, always=强制, never=跳过
  -y, --yes             跳过回写前的覆盖确认提示（用于脚本自动化）
  --match-only          仅匹配不回写（阶段 1 必需标记。展示汇总后等用户确认，再单独 --writeback）
  --writeback RESULT_JSON  从已保存的结果文件回写 8385，不重复匹配
  --device-ids IDS      只匹配指定的设备 ID（逗号分隔），如 ID1,ID2,ID3
  --device-ids-file PATH  从文件读取设备 ID 列表（JSON 数组 / 逗号分隔 /
                        含 json:/text:/userinput: 前缀的标签行均可）
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
    │   ⚠ **原始 8385 数据醒目展示**:          │
    │     已有标注设备数 / 已有坐标数（覆盖范围）│
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
    │   ① 数据质量校验（去重/空值/重复ID检测） │
    │   ② 系统巷道(巷道NNN)从候选池排除        │
    │   ③ 地面设备(area+description)跳过井下   │
    │   ④ 缓存命中直接复用                     │
    │   ⑤ 前缀剥离 → 别名扩展 → 编码提取      │
    │   ⑥ LCS评分 + 偏好/编码/类型加分         │
    │   ⑦ 选最佳匹配 → 置信度分层 → 坐标计算   │
    │                                         │
    │ Claude 展示匹配汇总:                     │
    │   匹配率 / 置信度分布 / 未匹配原因        │
    │   **CAD 路标统计**: 路标数/巷道数/定位设备数│
    │   **回写计划**（自动输出到 stderr）:       │
    │     待回写: N 条 (含低置信度警告)         │
    │   **审查摘要**:                           │
    │     高风险匹配数 / 中风险匹配数           │
    │     风险类型: 短名称依赖 / 编码不一致      │
    │                                         │
    │ ▼ 等用户确认回写 ▼                      │
    │                                         │
    │ Step 4: 回写 8385（--writeback）         │
    │   python locator.py <username>           │
    │     --writeback locator_result_*.json    │
    │   → 自动备份 originData                  │
    │   → 全部结果回写（低置信度带警告）        │
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
- **分析报告必须醒目展示 `originData`（当前 8385 已有标注数据）**：使用 ⚠️ **粗体** + 边框线突出显示已有设备总数和已有坐标数，让用户一眼看到本次会覆盖的数据量
- **若 MineName 过滤返回空数据**：Claude 应自动尝试不带 MineName 参数拉取全量数据，检查设备 ID 前缀确认是否全部属于该矿，若属于则使用全量数据继续流程

**注意：** `strategy_api.py` 依赖 `requests`。locator.py 会自动探测带 `requests` 的解释器（优先 `sys.executable`/`python3`/`python`），找不到就报错。可用 `BW_LOCATOR_PYTHON` 显式覆盖。

### Step 3: 匹配设备 → 计算坐标（--match-only）

用户确认阶段 1 后，执行**仅匹配不回写**：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only
```

对每个 device 的处理流程：

1. **数据质量校验**（`_validate_devices`，locator.py:2241）
   - 相同 `id` + 相同 `description` → 正常去重（跳过重复）
   - 相同 `id` + **不同** `description` → **直接报错终止**，强制用户修复上游数据
   - 空 `description` → 跳过并警告
   - 字段类型错误 → 跳过并警告
2. **候选过滤（匹配前）**
   - 系统巷道名称（如"巷道136"）→ 直接从候选池排除，记录到 `generic_tunnels_skipped`
   - 地面设备（`area` 含地面/洗选/磅房等关键词，或 `description` 含地面关键词）→ 跳过所有井下候选，但**井口设备例外**（`_is_shaft_mouth`，locator.py:538）
   - 匹配缓存命中（`match_cache.json`）→ 直接复用上次结果
3. 从 `description` 提取地点名称（先[剥离前缀](#前缀剥离)），按[匹配逻辑](#匹配逻辑)在**候选名称**中找到最佳匹配
4. **候选来源**（均来自 8373，已排除系统命名巷道）：
   - `tunnels` 数组中的 `name`（主候选，仅具名巷道）
   - `workfaces` 数组中的 `workFaceName`（补充候选）
5. **可选：CAD 路标定位** — 若数据含 `cadData`：
   - `_build_landmarks`（locator.py:1814）从 CAD 标注构建路标表，传感器位置标注（CH4/CO/风筒/烟雾等）不再被 `_NOISE_CONTENTS` 过滤，`_TUNNEL_KWS` 放行传感器标注进入路标表；**安装地点标签**（"安装地点：XXX"）从路标表排除
   - `_find_landmark_ratio`（locator.py:1992）返回 `(ratio, matched_name)` 元组：支持括号/空格/末尾标点归一化（`CH4（T1)` ↔ `CH4 (T1)`）、组合路标部分匹配（`CO、烟雾` 中的 `CO` 匹配 `总回风CO`）、T 标识路标传感器部分匹配（`CH4` 匹配 `CH4(T2)`）
   - **T 标识精确匹配**：有 T 标识时优先匹配含对应 T 的路标；无对应 T 路标时**回退**到不含 T 的路标（如 `CH4(T2)` 设备回退匹配 `CH4` 路标）
   - **端点优先**：同路标多个标注点长度相同时，优先选择**更靠近端点**的
   - 结果 JSON 中自动带上 `_landmark_cad_id` 字段（CAD 标注的原始 ID）
   - 用于精确定位，替代默认区间分配
6. 按[坐标计算](#坐标计算)规则计算 (x, y, z)
7. **Claude 展示匹配汇总后必须等用户确认**，再进入 Step 4

   Claude 应明确展示：
   - 匹配率、置信度分布（高/中/低）、未匹配原因分布
   - **回写计划**：待回写 N 条（含低置信度数量警告）
   - 审查摘要（高/中风险匹配数）

**重要**：locator 不会给重复 ID 加 `_1`、`_2` 后缀来回写。上游数据有重复 ID 时必须先到 BW-MES 后台清理。

### Step 4: 回写定位结果到策略 8385（--writeback）

用户确认匹配结果后，执行**单独回写**：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

- 从 Step 3 保存的结果文件加载完整数据
- **回写前备份**：`_backup_origin_data`（locator.py:3741）将当前 8385 `originData` 保存到 `data/backup/data_8385_<mineName>_<timestamp>.json`
- **低置信度包含**：`_filter_low_confidence(include_low=True)` 将**全部结果**（高/中/低）标记为待回写，stderr 输出低置信度数量警告。用户可在确认环节取消
- 向 `ExecuteStrategyCom` 发起 POST 请求
- **回写确认时醒目提示覆盖风险**：必须用 ⚠️ 标记 + **粗体** 明确提示会覆盖的原始数据条数（如"⚠ 会覆盖原有 **76 条**标注数据"），用户确认后再执行
- 返回 `{"code": 100}` 表示成功

**合并一步执行（用户明确说"直接跑"时）：** 需要显式 `--yes`：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --yes
```

> **两阶段流程硬约束**：locator.py 默认禁止一步完成匹配+回写。当同时满足以下条件时直接报错退出：
> - 传入了 `--load` 参数（从文件加载数据）
> - **没有** `--match-only`（阶段 1 未标记）
> - **没有** `--yes`（未显式跳过确认）
>
> 报错信息会提示正确的分步命令：先 `--match-only` 匹配 → 等确认 → 再 `--writeback` 回写。
>
> `--yes` 是显式跳过确认的唯一方式，仅用于自动化/CI。不加 `--yes` 时 locator.py 会在回写前交互式提示确认。

---

## 项目结构

```
<repo-root>/
├── CLAUDE.md              # 本文件
├── data/
│   ├── pdf/              # 煤矿安全规范等 PDF 文档
│   │   └── extracts/     # 条款摘要和 OCR 文本
│   ├── test/             # 测试数据
│   │   └── test_locator.json
│   ├── cache/            # 匹配缓存目录
│   │   └── match_cache.json
│   ├── backup/           # 回写前自动备份的 8385 数据
│   ├── output/           # 8373 数据、locator 结果、CesiumJS HTML
│   │   ├── generate_cesium_html.py
│   │   ├── generate_cesium_data.py    # CAD → Cesium JSON（仿射变换）
│   │   ├── export_geojson.py          # Cesium JSON → GeoJSON
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
        ├── INVOCATION_GUIDE.md
        └── scripts/locator.py      # ~4475 行，核心逻辑与规则注册表
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

**系统生成巷道名称过滤**：形如"巷道136"的名称为系统自动生成，无实际语义含义，设备描述不可能包含此类名称。`_extract_candidates`（locator.py:2143）阶段直接从候选池排除，避免产生无效低分匹配。被排除数量记录在输出 `summary.generic_tunnels_skipped` 和 `warnings[]` 中（`type: generic_tunnels_excluded`）。

### 前缀剥离

设备描述常带分站编号或编码前缀，匹配前需剥离（`PREFIX_PATTERNS`，locator.py:268）：

```
^\d+号分站(模拟量|开关量|多态量)\d{3}[A-Z]\d{2}
^其他\d{6,}[A-Z]\d{2}
```

收紧后只匹配通道编码格式，不吞位置编码（如 9308、C8302）。

| 原始描述 | 剥离后 |
| -------- | ------ |
| `1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯` | `皮顺联络巷迎头激光甲烷瓦斯` |
| `其他999602085J00暗斜井猴车下口基站人数` | `暗斜井猴车下口基站人数` |
| `14号分站开关量014D01回风暗斜井风门风门` | `回风暗斜井风门风门` |

注：**编码提取在原始描述上做**（避免被前缀剥离误删），其他匹配在剥离后描述上做。

### sensor_type 推断

设备字段缺 `sensor_type` 时，从描述关键词按顺序推断（`_infer_sensor_type`，locator.py:284）：

`二氧化碳（CO2） > 氧气（O2） > 负压（风压） > 风速 > 烟雾 > 粉尘 > 温度 > 一氧化碳（CO/一氧化碳，排除 CO2） > 瓦斯（甲烷/CH4） > 开停 > 馈电 > 断电 > 人员定位（人数/人员） > 工业视频（工业视频/摄像头/视频监控/视频监测） > [兜底] mark_type=B16 → 工业视频`

- 新增 `二氧化碳/氧气/负压` 优先于其他类型识别（基于 AQ 1029-2019 公开知识，条款号 TBD）。
- **mark_type 与 sensor_type 是完全不同的概念**：B16 是系统大类（工业视频系统），sensor_type 应为设备类型 `工业视频`，不应混用。
- **B16 兜底**：`mark_type=B16` 但描述无"摄像/视频"字样时，默认 sensor_type=`工业视频`（依据 MT/T 1201.6-2023 附录 A）。

### 编码提取

`extract_workface_code`（locator.py:855），按优先级：
1. **中文数字 + 采区/煤层/盘区/水平**：`九采区` → `9`、`七煤层` → `7`
2. 字母+3-4 数字：`C8302`、`F1302`
3. 负号+3-4 数字（水平标高）：`-490`、`-725`
4. **3-5 位纯数字**（前后无字母）：按长度降序匹配，排除以下常见误提取上下文后返回：
   - 分站编号上下文（如 `130分站`、`分站130`）
   - 电压值（如 `1140v`、`660V`、`380伏`）
   - 距离值（如 `600米`、`300m`）
   - 与已排除的更长数字范围重叠时跳过（如 `1140` 被排除后 `114` 不再匹配）
5. 中文数字 + 采区/煤层/盘区/水平（兜底）

### 别名映射

`_TUNNEL_ALIAS_MAP`（locator.py:380）+ `_expand_aliases`（locator.py:407）— 匹配前对 description 和候选 name 双向扩展，解决简称/全称差异：

| description 中出现 | 扩展为 |
|---|---|
| 皮顺 | 皮顺\|皮带顺槽\|辅运顺槽\|胶带顺槽 |
| 胶运 | 胶运\|胶带运输\|进风巷\|胶运顺槽 |
| 联络巷 | 联络巷\|联巷 |
| 切巷 | 切巷\|切眼 |
| 东大 | 东大\|东部\|东翼 |
| 副井 | 副井\|副斜井 |
| 主井 | 主井\|主斜井 |

使用占位符避免递归替换（如"顺槽"不会二次替换"皮带顺槽"中的内容）。

### 设备数据校验

`_validate_devices`（locator.py:2241）— 数据加载后首先进行设备数据清洗：

| 场景 | 处理 | 行为 |
|------|------|------|
| 相同 id + 相同 description | 去重（保留第一条） | 继续运行 |
| 相同 id + **不同** description | **ValueError 终止程序** | 强制修复上游数据 |
| 空 description | 跳过并 stderr 警告 | 继续运行 |
| 字段类型错误 | 跳过并 stderr 警告 | 继续运行 |

**绝不**给重复 ID 加 `_1`、`_2` 后缀。上游数据有重复 ID 时必须先到 BW-MES 后台清理后再运行定位。

### 评分公式

`_score_candidates`（locator.py:1227）— **分层匹配策略**：

```
score = round(LCS_长度(别名扩展后) × 10 / 候选名长度)
      + 8  if  设备描述匹配到该巷道的 CAD 路标（含组合路标部分匹配、T标识路标传感器部分匹配）
      + 2  if  sensor_type 命中候选名巷道偏好且 LCS≥2
      + 5  if  device_code 在候选名内(精确匹配，通用前缀仅+1)
      + 3  if  device_code 是候选名中数字编码的前缀(前缀模糊匹配)
      + 3  if  候选 tunnelId 含 device_code(workface 关联)
      + 3  if  "总回风" in 描述且 "回风" in 候选名
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
| 2 (LCS_PREF) | 前缀模糊命中 或 score≥**(7 if LCS<3 else 5)**，**且 LCS≥2** | 中 |
| 3 (LOW) | score≥2 但无编码/前缀命中 | 低 |
| 4 (REJECT) | score<2 或 硬性语义冲突 | 极低(拒绝) |

- 最低门槛：`score ≥ 2`
- LCS_PREF 最低门槛：**`LCS ≥ 2`**（防止单字符靠短名称膨胀分数）。LCS=2 时 score 需≥7 才给予中置信度，避免"轨道"等短 LCS 蹭 sensor_type 偏好蒙上中匹配
- 平局判定：编码命中 > LCS 长 > 候选名长

#### sensor_type 巷道偏好（命中 +2）

`_SENSOR_TUNNEL_PREF`（locator.py:328）。基于 AQ 1029-2019 公开知识（条款号 TBD）+ 实际数据观察：

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

`_TUNNEL_TYPE_MATCH_BONUS`（locator.py:648）：

| 描述含 | 候选 type | 加分 |
| ------ | --------- | ---- |
| 煤仓   | `3-煤仓`                       | +3 |
| 切眼   | `28-工作面切眼`                | +3 |
| 回风   | `26-工作面回风巷(辅运顺槽)`    | +2 |
| 进风   | `27-工作面进风巷(胶运顺槽)`    | +2 |
| 停采   | `25-工作面停采线`              | +2 |
| 硐室   | `0-普通巷道`                   | +1 |

### 地点语义惩罚（-10）

`_LOCATION_SEMANTICS`（locator.py:550）— 描述含此关键字时，候选必须含其一，否则扣 10（并在 `_has_hard_semantic_conflict` 中直接拒绝）：

| 描述含 | 候选必须含其一 |
| ------ | -------------- |
| 洗煤厂 | 洗煤厂 |
| 选煤楼 | 选煤楼 |
| 中央变电所 | 变电, 配电 |
| 避难硐室 | 硐室 |
| 硐室   | 硐室 |
| 井口   | 井口, 井筒, 副井, 主井, 斜井 |
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

`_has_hard_semantic_conflict`（locator.py:979）— 地点/功能词不一致时**直接拒绝**，宁缺毋滥。在 `_score_candidates` 分层判定前调用，冲突则 layer=REJECT。

**1. 地点锚定词冲突**：描述含地点限定词，候选必须含相同词。

`_AREA_ANCHOR_RE`（locator.py:968）匹配模式：
- `[一二三四五六七八九十百千\d]+采区`（六采区、一采区等）
- `[东西南北]翼`（西翼、北翼、东翼、南翼）
- 特定巷道/地名：`暗斜井`、`哈拉沟`、`马蹄沟`、`马蹄坡`、`交岔点`（矿特有，后续按需扩展）

**2. 功能互斥冲突**（`_check_functional_conflict`，locator.py:601）：
| 描述含 | 候选若含以下词则冲突 |
| ------ | -------------------- |
| 底抽 | 回风、进风、皮顺、胶运 |
| 回风 | 进风、底抽、胶运 |
| 进风 | 回风、底抽、皮顺 |

**3. 石门冲突**：描述含"石门"但候选不含"石门" → REJECT（石门是特定巷道类型）。

**4. 硐室同名异址冲突**：描述含"X硐室"但候选是"Y硐室"（X≠Y，且公共子串 < 3 字符）→ REJECT。
- 避免"机头硐室"匹配到"永久避难硐室"，"单轨吊充电检修硐室"匹配到"架空乘人器硐室"
- 公共子串 ≥ 3 字符时豁免（如"架空乘人装置"与"架空乘人器"LCS ≥ 3）

**5. 联络巷冲突（放宽版）**：描述含"X联络巷"但候选是"Y联络巷"（X≠Y，且描述前缀不包含候选前缀且候选前缀不包含描述前缀）→ REJECT。
- 允许描述前缀包含候选前缀（如"天宝公司-辅运"包含"辅运"）

**6. 通用巷道类型名冲突**：描述和候选含同一通用巷道类型名（大巷/斜巷/顺槽/底抽巷/高抽巷/回风巷/进风巷/运输巷）但具体限定语不同 → REJECT。
- 避免"保安煤矿皮带大巷"匹配到"沿9号煤皮带大巷"（LCS=4 仅落在通用"皮带大巷"上）、"保安煤矿轨道斜巷"匹配到"9号煤轨道斜巷"等
- 限定语互不包含且双方长度≥2字时拒绝。一方无特定限定语时不触发（如"皮带大巷"≈无限定语，与"沿9号煤皮带大巷"不冲突）

**7. 反向编码约束**：候选名含 specific code（3+位纯数字 / 字母+数字 / 负数水平），但描述完全不含候选的任何 specific code → REJECT。
- 避免"三部强力皮带"靠"皮带"短 LCS 蹭到"8301皮带顺槽"等带工作面编码的候选
- 同样保护 6301/6302/15103 等所有"短编码巷道"，防止主运输/装载站皮带误挂到工作面顺槽
- specific code 提取正则：`-?\d{3,}` 或 `[A-Za-z]\d{2,}`，与 `_is_specific_code`（locator.py:1198）一致

**8. `_LOCATION_SEMANTICS` 冲突**：描述含 `_LOCATION_SEMANTICS` 关键字但候选不含允许词时，直接拒绝（编码/LCS 无法掩盖）。|

**9. 轨顺/皮顺→联络巷冲突**：描述说"轨顺"/"皮顺"但未说"联络巷"，候选是"X轨顺联络巷"/"X皮顺联络巷"时 → REJECT。轨顺/皮顺是主要运输巷道，联络巷是连接巷道，不同地点。

**10. 支架工作面约束**：描述含"工作面\\d+[#]?架"（液压支架编号）但候选不含工作面/切眼/停采关键词时 → REJECT。支架必须在回采工作面/切眼上。

**11. 轨顺/皮顺类型互斥**：描述含"皮顺"而候选含"轨顺"（或反之）时 → REJECT。皮带顺槽和轨道顺槽是完全不同的巷道类型。

**12. 轨顺/皮顺→切眼冲突**：描述说"轨顺"/"皮顺"/"轨道顺槽"/"皮带顺槽"但候选是"切眼"（工作面切眼）且描述不含"切眼"时 → REJECT。运输顺槽与工作面切眼完全不同，不应混淆。符合"XX轨顺切眼"描述的设备（描述本身含"切眼"）豁免，匹配到切眼并定位到顺槽交汇处（见下方切眼交汇点定位）。

### 编码强制约束（score −20）

当 `device_code` 是 specific code（3+位数字/含字母/负数水平）时，**候选名或 tunnelId 必须包含该编码**，否则 score −20（直接 REJECT）。

避免 `1460排矸一台皮带CO` 匹配到不含 `1460` 的纯 `排矸`（短名称依赖+LCS 膨胀）。

### mark_type → 系统大类

`_MARK_TYPE_TO_SYSTEM`（locator.py:372）：

| mark_type | 系统           |
| --------- | -------------- |
| B14       | 安全监测系统   |
| B15       | 人员定位系统   |
| B16       | 工业视频系统   |

`mark_type` 是系统大类，与 `sensor_type`（具体传感器）是不同维度。

### area 语义过滤

`_AREA_SURFACE_PATTERNS`（locator.py:462）+ `_is_surface_area`（locator.py:484）— area 字段标记设备所属区域。匹配前判断语义：
- 若 area 含"地面/露天矿/洗选/销售/磅房/风机房/材料大库房/炸药库/计算机资源/档案室/队组楼/设备废料/井上"等地面关键词
- → 直接跳过所有井下候选，标记为 `AREA_SURFACE` 未匹配
- 避免地面设备（洗煤厂、磅房、地面机房硐室等）错误匹配到井下巷道/工作面

### description 地面语义过滤

`_DESCRIPTION_SURFACE_PATTERNS`（locator.py:498）+ `_is_surface_description`（locator.py:528）— 从 description 本身判断地面设施。与 area 过滤互补：
- 若 description 含"地面/井上/洗煤厂/空压机房/风机房/通风机房/材料库/炸药库/磅房/销售/档案室/队组楼"等关键词
- → 同样标记为地面设备，跳过井下候选

**井口例外**：`_is_shaft_mouth`（locator.py:538）检测 "主井井口"/"副井井口"/"回风井井口"/"风井井口" 等模式。这类设备 area 虽标记为井上，但实际是井下斜井的入口点，应匹配到对应斜井起始位置，而非被 AREA_SURFACE 过滤掉。

### 置信度

`_calc_confidence`（locator.py:2578）— 基于分层 `layer`：

| layer | 条件 | confidence |
|-------|------|------------|
| EXACT (1) | 编码精确命中（非通用前缀）且 lcs≥1 | 高 |
| LCS_PREF (2) | 前缀模糊命中 或 score≥**(7 if LCS<3 else 5)**，**且 LCS≥2** | 中 |
| LOW (3) | score≥2 但无编码/前缀命中 | 低 |
| REJECT (4) | score<2 或 硬性语义冲突 | 极低(拒绝) |

**回写行为**：当前代码使用 `_filter_low_confidence(results_list, include_low=True)`（locator.py:3844, 3920），**全部置信度结果均参与回写**，stderr 会醒目提示低置信度数量（如"含 5 条低置信度 ⚠"）。用户可在确认环节取消。此行为与早期版本（仅高+中回写）不同。

### 未匹配拒绝原因

`unmatched_devices` 中每个设备带 `reason` 字段（locator.py:2819 附近）：

| reason | 含义 |
|--------|------|
| `NO_CANDIDATE` | 无可行候选（candidates 为空，或所有候选为系统命名巷道已被排除） |
| `CODE_MISMATCH` | 提取到编码但所有候选均不匹配（含前缀尝试，或编码仅出现在语义冲突候选中） |
| `SEMANTIC_CONFLICT` | 语义惩罚阻断所有候选（所有候选均扣 -10）或硬性语义冲突 |
| `LOW_LCS` | LCS 得分过低（< 2），无其他匹配途径 |
| `AREA_SURFACE` | area 语义为地面（非井下），排除巷道/工作面候选 |

**注意**：`NO_CANDIDATE` 在大量系统巷道被排除后更常见——设备可能本应对应某条"巷道NNN"但该巷道已被过滤，无其他具名候选可匹配。此行为是设计意图，宁缺毋滥。

### 匹配缓存

高置信度（layer=EXACT）匹配自动写入 `data/cache/match_cache.json`（locator.py:2472-2513）：
- 键：`{mark_type}:{description}`
- 值：`{matched_name, candidate_id, score, timestamp}`
- 下次运行时优先查缓存，命中则直接复用匹配结果

**缓存语义校验**（locator.py:2610）：缓存命中时也会调用 `_has_hard_semantic_conflict` 检查。若缓存结果与当前描述存在硬性语义冲突（如地点/功能词不一致），则忽略缓存重新匹配。防止历史错误缓存被复用。

### 系统巷道过滤与告警

**系统巷道排除**：`_is_generic_tunnel_name`（locator.py:521）— 形如 `巷道\d+` 或纯数字名（如"146"）的名称在 `_extract_candidates` 阶段直接从候选池排除。

- 排除数量记录在 `summary.generic_tunnels_skipped`
- 输出 JSON 中 `warnings` 数组包含 `type: generic_tunnels_excluded` 条目

**无名巷道排除**：`name` 字段为空的巷道在候选提取阶段跳过（stderr 输出 `跳过 tunnels[N]: name 为空`）。排除数量记录在 `summary.unnamed_tunnels_skipped`。无名巷道是匹配率低的主因之一——这些巷道虽在数据库中有记录但缺少关键标识信息。

### 风速间距检查

`_check_wind_speed_spacing`（locator.py:2525）— 同组风速传感器间距 < 10m 时告警：
- AQ 1029-2019 7.2.1：测风站前后 10m 无分支
- 输出 JSON 中 `warnings` 数组包含 `type: wind_speed_spacing` 条目

### CAD 路标定位（可选增强）

当 8373 数据包含 `cadData`（CAD 图纸标注点）时，locator.py 启用路标定位增强：

1. **`_group_sensor_fragments`**（locator.py:1670）：将相邻 CAD 标注点聚合成完整传感器标识（如 "CH" + "4" → "CH4"，"T" + "CO" → "TCO"）
2. **`_build_landmarks`**（locator.py:1814）：
   - 过滤噪声（高程数字、图签文字等），但**传感器位置标注**（CH4/CO/风筒/烟雾/T1/T2）**不再被过滤**，作为有效路标保留
   - `_TUNNEL_KWS` 放行传感器标注进入路标表（即使不含传统巷道关键词）
   - 计算每个有意义标注点到最近巷道折线的投影比例，构建 `{tunnel_name: {landmark_name: ratio}}` 路标表
3. **`_find_landmark_ratio`**（locator.py:1992）：
   - 设备描述匹配路标名称时，返回该路标在巷道上的投影比例
   - **归一化**：全角括号→半角、移除空格、去除末尾标点（`CH4（T1)` ↔ `CH4 (T1)`）
   - **组合路标拆分**：`CO、烟雾` 中的 `CO` 可匹配 `总回风CO`
   - **T 标识路标传感器部分匹配**：`CH4` 可匹配 `CH4(T2)` 路标（无 T 设备允许匹配传感器部分）
   - **T 标识精确过滤**：设备有 T 标识时，路标名也必须包含对应 T 标识（避免 `CH4` 路标覆盖 `CH4(T1)` 设备）
   - **端点优先**：同路标多个标注点长度相同时，优先选择更靠近端点的（T 传感器通常在端点）
   - 替代默认区间分配，实现更精确的定位

---

## 坐标计算

匹配成功后，沿命中巷道/工作面的 `line` 折线计算 (x, y, z)。

### 1. 分组键

`group_key = (matched_name, keyword)`（locator.py:2531）。`keyword` 由 `_classify_keyword`（locator.py:2118）决定：

- `T1/T2/T0/T3/T4`（从描述提取）
- `迎头` / `回风流`（描述含关键词）
- B15 关键词：`井口` / `井底` / `入口` / `岔口` / `硐室` / `充电站` (MT/T 1198-2023 §5.1.4)
- B16 关键词：`机头` / `机尾` / `转载点` / `中部` / `超前支护` / `T2处` / `支架` / `煤仓` / `车场` / `地面`，以及房间类（水泵房/变电所/绞车房/避难硐室/调度室/提升机房/通风机房/空压机房/瓦斯泵站/制氮/灌浆站/坑木场）
- `default`（无）

→ 同巷道但不同关键词的设备分到不同组，不冲突。

### 2. 区间确定优先级

`_assign_distances`（locator.py:2013）：

```
显式距离(米) > CAD 路标定位 > T 标识规则 > 巷道类型×sensor_type 规则 > AQ1029 距离规则 > 关键词区间 > sensor_type 默认百分比
```

**CAD 路标定位优先级说明**：
- 设备描述匹配到巷道上的 CAD 路标时，直接使用路标的投影比例定位（最精确）
- T 标识设备（T1/T2/T0/T4）不再完全跳过路标定位，而是要求路标名**精确包含**对应 T 标识（如 `CH4(T1)` 设备匹配 `CH4（T1)` 路标）
- 无 T 标识设备允许匹配 T 标识路标的**传感器部分**（如 `CH4` 匹配 `CH4(T2)` 路标）
- 路标匹配失败时回退到 T 标识规则或默认区间

**显式距离**：若 description 含 `NN米` 模式（如 `2730米`、`660米`、`10米`），提取距离值。当 keyword 有语义区间且描述含方向词时，从区间基准偏移（如 keyword=硐室+外西60米=50%-60m）；否则作为绝对距离（clamp 到折线总长）。

#### 2a. T 标识区间

`_T_POSITION_RULES`（locator.py:450）：

| T 标识 | 比例区间 | 精确米数（若 line 够长） | 含义 |
| ------ | -------- | ----------------------- | ---- |
| T0     | 0-5%     | -                       | 上隅角（工作面回风端） |
| T1     | 0-5%     | 0 ~ 5m                  | 掘进迎头 |
| T2     | 85-100%  | length-15 ~ length      | 掘进回风流 |
| T3     | 30-50%   | -                       | 混合风流（风机附近） |
| T4     | 90-100%  | length-10 ~ length      | 掘进回风巷口 |

#### 2b. 巷道类型 × sensor_type 规则

`_TUNNEL_TYPE_RULES`（locator.py:614）：

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

`_AQ1029_DISTANCE_RULES`（locator.py:676）：

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
| 入口   | 0-10% | 巷道入口处起点 |
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

`_distribute_in_zone`（locator.py:2029）：

- **默认 1m 步长**（`step=1.0`）从 `lo` 起递增。
- 区间放不下时退化为均匀分布：`step_adj = (hi-lo)/(count-1)`。
- `count == 1` 取区间中点。
- **B16 工业视频自定义步长**（MT/T 1201.6-2023 附录 A）：
  - 支架（A.1#1）：`step = 75m`（≤50架间距）
  - 中部（A.1#16）：`step = 500m`（主运输皮带）
  - 架空乘人（A.1#23）：`step = 100m`

### 3b. 切眼交汇点定位

描述同时含顺槽名（轨顺/皮顺/轨道顺槽/皮带顺槽）和"切眼"的设备（如 `8301轨顺切眼_人数`）→ 定位到切眼折线 **起点 (0%)**，即顺槽与切眼的交汇处。

- 匹配仍然对到切眼（豁免规则 12，因描述本身含"切眼"）
- 定位到切眼起点而非默认的区间中点，因为该点正是顺槽终点（已验证：8301轨道顺槽终点 = 8301切眼起点）
- 实现：主循环分组后检测 `is_junction`，直接设 `implicit_distances = [0.0] × count`，跳过 `_assign_distances`

### 4. z 轴安装高度

`_SENSOR_INSTALL_HEIGHT`（locator.py:660）— 在折线插值的 z 上叠加传感器安装高度：

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
