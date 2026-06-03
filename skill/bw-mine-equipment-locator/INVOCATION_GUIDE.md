# bw-mine-equipment-locator 调用提示词大全

> 煤矿设备定位 skill — 根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

---

## 一、自然语言调用（向 Claude 发出请求）

### ⚠️ 严格两阶段流程（默认）

用户触发词：
```
设备定位 F15795450
定位 F09795450
locator F18795450
```

Claude **必须**按以下流程执行，**严禁**自动跳过确认环节：

**阶段 1 — 数据获取 + 分析审查：**
1. 获取 Token + mineName（bw-token-manager）
2. 拉取 8373 数据（bw-strategy-api-caller）
3. 运行 `--analyze` 展示分析报告
4. **STOP — 等待用户明确确认**（如"确认"、"继续"、"匹配"）

**阶段 2 — 匹配定位 + 回写（用户确认后）：**
5. 运行 `--match-only` 执行匹配
6. 展示匹配汇总 + 回写计划
7. **STOP — 等待用户明确确认回写**（如"确认回写"、"回写"、"确定"）
8. 运行 `--writeback` 回写 8385

### 指定设备数据文件

```
设备定位 F09795450 evals/devices.json
设备定位 F09795450 data/test/test_locator.json
```

仍按两阶段流程执行（阶段1分析 → 等确认 → 阶段2匹配 → 等确认 → 回写）。

### 快捷跳过（用户明确说"不用确认"时才使用）

```
直接跑设备定位 F15795450
自动跑完定位 F09795450
不用确认，直接定位 F18795450
```

仅当用户**明确说**上述关键词时，才跳过阶段间的确认等待。否则必须严格执行两阶段流程。

### 只匹配不回写

```
设备定位 F09795450 --match-only
定位 F15795450 --match-only
```

运行匹配后展示汇总，**等待用户确认后再单独回写**。

### 指定输出模式

```
设备定位 F09795450 --output-mode summary     # 仅汇总统计
设备定位 F09795450 --output-mode unmatched    # 查看未匹配+Top3候选
设备定位 F09795450 --output-mode audit        # 审计报告（含风险匹配列表）
设备定位 F09795450 --output-mode json-summary # 汇总JSON
```

### 数据分析（不匹配）

```
分析 8373 数据 data/output/data_8373_济矿阳城分公司.json
分析定位数据 data/output/data_8373_山西保安煤业.json
```

分析输出包含：设备/巷道/工作面统计、mark_type/sensor_type 分布、地面/井下拆分、**CAD 数据分析**（标注点分类、路标覆盖、噪声占比）、originData 覆盖风险提示。

### 从已有结果回写

```
回写定位结果 F15795450 locator_result_F15795450_济矿阳城分公司.json
```

### 生成 CesiumJS 可视化

```
生成 Cesium 可视化 F09795450
生成 Cesium HTML locator_result_F09795450_山西保安煤业.json
跳过 HTML 生成: 设备定位 F15795450 --html never
```

---

## 二、命令行直接调用

### 两阶段分离（推荐，严格按阶段执行）

**阶段 1 — 分析（等用户确认后再进阶段 2）：**
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --analyze
```

**阶段 2 — 匹配（用户确认后执行）：**
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only
```

**回写（用户确认匹配结果后执行）：**
```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

### 一步到位（跳过确认，仅用于脚本/CI）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --yes
```

⚠️ `--yes` 是唯一跳过确认的方式。交互式场景严禁自动使用。

### 指定本地数据（不调 API）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json
```

### 分步匹配（仅匹配不回写）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --load data/output/data_8373_<mineName>.json --match-only
```

### 单独回写（从已有结果文件）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --writeback data/output/locator_result_<username>_<mineName>.json
```

### 测试模式（用本地测试数据）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py TESTUSER \
  --load data/test/test_locator.json --match-only
```

### 输出模式选项

```bash
# 完整结果（默认）
python ... --output-mode full

# 仅汇总
python ... --output-mode summary

# 未匹配设备+Top3候选
python ... --output-mode unmatched

# 审计报告（含风险匹配列表）
python ... --output-mode audit

# 汇总 JSON（机器可读）
python ... --output-mode json-summary
```

### 数据分析

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username> \
  --analyze data/output/data_8373_<mineName>.json
```

### 数据源控制

```bash
# 从本地文件加载完整 8373 数据
python ... --load data_8373_<mineName>.json

# 分别加载（有特殊需求时）
python ... --load-devices devices.json --load-tunnels tunnels.json --load-workfaces workfaces.json
```

### HTML 可视化控制

```bash
python ... --html auto    # 自动（有 pyproj 就生成）
python ... --html always  # 强制生成
python ... --html never   # 跳过
```

---

## 三、分步手动执行（底层 API 调用）

### Step 1: 获取 Token

```bash
python skill/bw-token-manager/scripts/bw_token_manager.py <username>
```

### Step 2: 拉取 8373 数据

```bash
python skill/bw-strategy-api-caller/scripts/strategy_api.py get_json \
  --id 8373 --param "MineName=<mineName>" --username <username>
```

### Step 4: 回写 8385（底层）

```bash
python skill/bw-strategy-api-caller/scripts/strategy_api.py execute \
  --id 8385 --param 'data=<JSON>' --username <username>
```

### 生成 CesiumJS（独立调用）

```bash
python data/output/generate_cesium_html.py \
  data/output/locator_result_<username>_<mineName>.json \
  --data-8373 data/output/data_8373_<mineName>.json
```

---

## 四、平台行为约束（openclaw 等 Skill 平台）

当本 skill 在 openclaw 等平台上被触发时，Claude **必须**严格遵守以下约束：

1. **阶段 1 必须 STOP**：拉取 8373 数据并运行 `--analyze` 后，展示分析报告给用户，**必须等待用户明确回复**"确认"/"继续"/"匹配"后才进入阶段 2。
2. **阶段 2 必须 STOP**：运行 `--match-only` 后，展示匹配汇总 + 回写计划给用户，**必须等待用户明确回复**"确认回写"/"回写"/"确定"后才执行 `--writeback`。
3. **严禁自动推进**：除非用户明确说"直接跑"、"不用确认"、"自动跑完"等快捷跳过关键词，否则不得自动执行匹配或回写。
4. **`--analyze` 不是可选步骤**：阶段 1 必须执行 `--analyze` 并展示完整报告（含 CAD 数据分析、originData 覆盖风险提示），不能只拉数据不分析。
5. **每次调用独立**：前一次用户说"直接跑"不影响下一次调用，每次触发默认执行两阶段流程。

## 五、触发词速查表

| 意图 | 关键词 |
|------|--------|
| 定位设备 | `定位`、`设备定位`、`locator` + `F\d+` 用户名 |
| 快捷执行（跳过确认） | `直接跑`、`自动跑完`、`不用确认` |
| 仅匹配 | `--match-only` |
| 仅分析 | `分析`、`--analyze` |
| 回写结果 | `回写`、`--writeback` |
| 审计模式 | `audit`、`审计` |
| 可视化 | `Cesium`、`HTML`、`可视化` |
