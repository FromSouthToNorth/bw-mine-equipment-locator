# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# 煤矿设备定位系统 (bw-mine-equipment-locator)

根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

**输入：** username（如 `F18795450`），可选指定设备数据文件  
**输出：** 每个设备匹配到巷道/工作面后的 (x, y, z) 坐标，回写到策略 8385

---

## 快速开始（开发者）

```bash
# 依赖：仅 requests
pip install -r requirements.txt

# 可选：CesiumJS 可视化
pip install pyproj

# 本地测试（不调用 API）
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only

# 汇总统计
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --output-mode summary

# 审计报告（含高/中风险匹配列表）
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only --output-mode audit

# 分析已有 8373 数据
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --analyze data/output/data_8373_<mineName>.json
```

**Python 解释器探测优先级**：`BW_LOCATOR_PYTHON` 环境变量 > `sys.executable` > `python3`/`python`/`py` > Windows 兜底路径（`C:\Users\bw\AppData\Local\Programs\Python\Python310\python.exe`）。第一个能 `import requests` 的被采用。

---

## 仓库架构

3 个独立 skill 组成无构建流水线，纯 Python + JSON：

```
用户请求 → bw-token-manager → bw-strategy-api-caller → bw-mine-equipment-locator
                (Step 1)              (Step 2/4)               (Step 3)
```

### Skill 间调用

`locator.py` **不直接调用 HTTP API**，通过 `subprocess` 委托给另外两个 skill：

| 调用 | 触发方式 | 用途 |
|------|---------|------|
| `bw_token_manager.py <username>` | `subprocess.run` | Step 1: 获取 Token + mineName |
| `strategy_api.py get_json --id 8373` | `subprocess.run` | Step 2: 拉取设备/巷道/工作面数据 |
| `strategy_api.py execute --id 8385` | `subprocess.run` | Step 4: 回写定位结果 |

### ⛔ 修改边界（强制约定）

**仅 `skill/bw-mine-equipment-locator/` 下文件可修改**（核心：`locator.py`）。  
**`bw-token-manager` 和 `bw-strategy-api-caller` 禁止修改**。Bug 绕过方式：
- Token 失败 → 直接调 API 或手写 Python 脚本
- API 异常 → 用 `curl`/`requests` 直接调，或另写独立脚本
- 参数变更 → 在 `locator.py` subprocess 参数中适配

### 数据流

```
8373 API → data/output/data_8373_<mineName>.json
              ↓  locator.py --match-only
    data/output/locator_result_<username>_<mineName>.json  (全量保存)
              ↓  locator.py --writeback
    8385 API  ← 自动备份 originData 到 data/backup/
              ↓
    data/cache/match_cache.json (EXACT 匹配缓存, 键=mark_type:description)
```

- **结果文件始终保存全量数据**（含低置信度），不受 `--output-mode` 影响
- 回写使用 `_filter_low_confidence(include_low=True)`：**全部置信度结果均写入**，stderr 醒目提示低置信度数量

---

## 项目结构

```
<repo-root>/
├── CLAUDE.md                   # 本文件
├── README.md
├── requirements.txt            # 仅 requests
├── bw_tokens.json              # Token 缓存（自动生成）
├── data/
│   ├── test/test_locator.json  # 本地测试数据
│   ├── cache/match_cache.json  # 匹配缓存
│   ├── backup/                 # 回写前自动备份（data_8385_*.json）
│   ├── output/                 # 8373/结果/CesiumJS
│   │   ├── generate_cesium_html.py  # 结果→CesiumJS HTML (需 pyproj)
│   │   ├── generate_cesium_data.py  # CAD 标注→Cesium JSON (仿射变换)
│   │   └── export_geojson.py        # Cesium JSON→GeoJSON
│   ├── pdf/standards/          # 行业标准 PDF（AQ 1029-2019 等）
│   │   ├── extracts/           # OCR/摘要文本
│   │   └── scripts/            # PDF 提取脚本
│   └── sql/
│       ├── index.sql           # 8373 查询 SQL
│       └── example_8373.json
├── evals/
│   ├── evals.json
│   └── devices.json
└── skill/
    ├── bw-token-manager/scripts/bw_token_manager.py
    ├── bw-strategy-api-caller/scripts/strategy_api.py
    └── bw-mine-equipment-locator/
        ├── SKILL.md            # 完整匹配逻辑参考
        ├── INVOCATION_GUIDE.md  # 调用提示词大全
        └── scripts/locator.py  # ~4500 行，核心逻辑
```

---

## 两阶段交互式工作流

> **核心约束**：Claude 必须严格执行两阶段流程，每阶段后 STOP 等待用户确认。

```
用户: 设备定位 <username>
         │
    ┌────▼────────────────────────────────────────────┐
    │ ★ 阶段 1: 数据获取 + 分析审查 ★                │
    │ Step 1: bw-token-manager 获取 Token + mineName   │
    │ Step 2: strategy_api.py get_json 拉 8373 数据    │
    │ Step 2.5: locator.py --analyze 展示分析报告      │
    │   ⚠️ 必须醒目展示 originData（将被覆盖的数据量）  │
    │   ⚠️ 若 MineName 过滤返回空 → 尝试全量拉取      │
    │                                                  │
    │ ⛔ STOP — 等用户确认（"确认"/"继续"/"匹配"）    │
    └────┬─────────────────────────────────────────────┘
         │ (用户确认)
    ┌────▼────────────────────────────────────────────┐
    │ ★ 阶段 2: 匹配定位 + 回写 ★                    │
    │ Step 3: locator.py --load <8373> --match-only    │
    │   展示：匹配率/置信度分布/未匹配原因              │
    │   CAD 路标统计/回写计划/审查摘要                  │
    │   ⚠️ 醒目提示覆盖风险                            │
    │                                                  │
    │ ⛔ STOP — 等用户确认回写（"确认回写"/"回写"）    │
    │                                                  │
    │ Step 4: locator.py --writeback <结果文件>        │
    │   → 自动备份 originData → POST 8385              │
    │   → {"code": 100} 表示成功                       │
    └──────────────────────────────────────────────────┘
```

### 快捷跳过

用户明确说"直接跑"/"自动跑完"/"不用确认"时，可合并执行：

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --yes
```

`--yes` 是跳过确认的唯一方式。不加 `--yes` 时 locator.py 默认禁止一步完成（报错提示分步执行）。

---

## 关键命令参考

### 分步执行

```bash
# Step 1: Token
python skill/bw-token-manager/scripts/bw_token_manager.py <username>

# Step 2: 拉 8373
python skill/bw-strategy-api-caller/scripts/strategy_api.py get_json \
  --id 8373 --param "MineName=<mineName>" --username <username>

# Step 3: 匹配（仅匹配不回写）
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only

# Step 4: 回写（从已有结果文件）
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

### 输出模式

| 模式 | 用途 | stdout 内容 |
|------|------|-------------|
| `full` (默认) | 完整结果 | `results[]` + `unmatched_devices[]` + `warnings[]` |
| `summary` | 仅汇总 | 分级统计 + sensor_type 分布 |
| `unmatched` | 未匹配审查 | summary + 未匹配设备（含 Top-3 候选） |
| `json-summary` | 汇总 JSON | summary + warnings, stderr 人类可读 |
| `audit` | 审计报告 | 高/中风险匹配列表 + 完整结果 |

### 调试选项

```bash
# 只匹配指定设备
--device-ids ID1,ID2

# 从文件读取设备 ID 列表
--device-ids-file path.json

# 跳过 CesiumJS HTML 生成
--html never

# 本地测试数据
--load data/test/test_locator.json
```

---

## 匹配逻辑概要

完整规则在 `locator.py`，以下是高层流程。**代码是唯一真理源**（source of truth）。

### 处理流水线

```
设备描述 → 数据质量校验 → 系统巷道过滤 → 地面设备过滤 → 缓存查询
 → 前缀剥离 → 别名扩展 → 编码提取 → LCS 评分 + 加分/惩罚
 → 硬性语义冲突检测 → 分层判定 → 置信度计算 → 坐标计算
```

### 评分公式

```
score = round(LCS长度(别名扩展后) × 10 / 候选名长度)
      + 8  (CAD 路标匹配)
      + 2  (sensor_type 巷道偏好, LCS≥2)
      + 5  (编码精确命中, 通用前缀仅+1)
      + 3  (编码前缀模糊匹配)
      + 3  (tunnelId 含 device_code)
      + 3  (总回风→回风)
      + n  (巷道类型匹配加分)
      - 1  (coalbed 不一致)
      - 10 (地点语义冲突)
      - 20 (specific code 不在候选名/tunnelId 中)
```

### 分层判定

| layer | 条件 | 置信度 |
|-------|------|--------|
| EXACT (1) | 编码精确命中（非通用前缀）且 LCS≥1 | 高 |
| LCS_PREF (2) | 前缀模糊命中 或 score≥*(7 if LCS<3 else 5)*，且 LCS≥2 | 中 |
| LOW (3) | score≥2 但无编码/前缀命中 | 低 |
| REJECT (4) | score<2 或 硬性语义冲突 | 极低 |

**最低门槛：score ≥ 2**。LCS_PREF 要求 LCS ≥ 2。

### 坐标计算优先级

```
显式距离(米) > CAD 路标定位 > T标识规则 > 
巷道类型×sensor_type 规则 > AQ1029 距离规则 > 
关键词区间 > sensor_type 默认百分比
```

**详细规则表**（评分常量、冲突规则、坐标区间、安装高度）见：
- `locator.py` 中对应的字典/函数（行号见下方注册表）
- `skill/bw-mine-equipment-locator/SKILL.md` 的技术参考章节

### 硬性语义冲突（直接 REJECT）

`_has_hard_semantic_conflict` (`locator.py:979`) — 以下冲突直接拒绝：

| 冲突类型 | 说明 |
|---------|------|
| 地点锚定词 | 描述含 `*采区`/`*翼`/特定地名，候选不含同词 |
| 功能互斥 | `底抽↔回风/进风`、`回风↔进风/底抽` 等 |
| 石门 | 描述含"石门"但候选不含 |
| 硐室同名异址 | `X硐室`→`Y硐室` (X≠Y, LCS<3) |
| 联络巷冲突 | `X联络巷`→`Y联络巷` 且前缀不包含 |
| 通用巷道类型冲突 | 同一类型名限定语不同且互不包含 |
| 反向编码约束 | 候选含编码但描述完全不含 |
| 轨顺/皮顺→联络巷 | 描述说"轨顺/皮顺"但未说"联络巷" |
| 支架工作面约束 | 含"工作面N架"但候选不是工作面 |
| 轨顺/皮顺类型互斥 | 描述含"皮顺"而候选含"轨顺"（或反之）|
| 轨顺/皮顺→切眼 | 描述说"轨顺/皮顺"但候选是"切眼"（描述含"切眼"时豁免）|

### 未匹配原因

| reason | 含义 |
|--------|------|
| `NO_CANDIDATE` | 无可行候选（含系统巷道被全部排除） |
| `CODE_MISMATCH` | 提取到编码但所有候选均不匹配 |
| `SEMANTIC_CONFLICT` | 语义惩罚阻断所有候选 |
| `LOW_LCS` | LCS 得分过低(<2) |
| `AREA_SURFACE` | area/description 语义为地面设施 |

### 规则审计注册表

`locator.py` 中 `_RULES_REGISTRY`（`locator.py:4359`）集中登记所有行业标准条款来源。修改 `_assign_distances`、`_SENSOR_TUNNEL_PREF` 等数据表时**必须同步更新注册表**。

---

## 重要注意事项

1. **重复 ID 处理**：locator 不会给重复 ID 加 `_1`/`_2` 后缀。相同 ID + 不同 description → 直接报错终止。必须到 BW-MES 后台清理上游数据。
2. **系统巷道**：形如"巷道136"或纯数字名的巷道自动从候选池排除（无实际语义）。
3. **缓存语义校验**：EXACT 缓存命中时仍会调用 `_has_hard_semantic_conflict`，防止历史错误缓存复用。
4. **地面设备过滤**：area 或 description 含地面/洗选/磅房/风机房等关键词 → 跳过所有井下候选。井口设备（副井井口等）例外豁免。
5. **B16 工业视频**：mark_type=B16 但描述无"摄像/视频"字样时，默认 sensor_type=工业视频。
6. **Z 轴安装高度**：在折线插值 z 上叠加传感器安装高度（`_SENSOR_INSTALL_HEIGHT`, `locator.py:660`）。
7. **两阶段硬约束**：不加 `--match-only` 且不加 `--yes` 时报错退出，强制分步执行。
