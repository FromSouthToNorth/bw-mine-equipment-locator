# bw-mine-equipment-locator 调用提示词大全

> 煤矿设备定位 skill — 根据设备描述（description）匹配到对应巷道或工作面，再从巷道/工作面的折线（line）坐标中计算得出设备的 (x, y, z) 坐标。

---

## 一、自然语言调用（向 Claude 发出请求）

### 标准两阶段流程（推荐）

```
设备定位 F15795450
定位 F09795450
跑一下 locator F18795450
```

触发两阶段交互：Phase 1 拉数据 → 展示分析报告 → 等确认 → Phase 2 匹配 → 展示汇总 → 等确认 → 回写

### 指定设备数据文件

```
设备定位 F09795450 evals/devices.json
设备定位 F09795450 data/test/test_locator.json
```

### 快捷跳过（一步到位）

```
直接跑设备定位 F15795450
自动跑完定位 F09795450
不用确认，直接定位 F18795450
```

跳过审查环节，自动完成匹配+回写

### 只匹配不回写

```
设备定位 F09795450 --match-only
定位 F15795450 --match-only
```

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

### 完整流程（匹配+回写一步）

```bash
python skill/bw-mine-equipment-locator/scripts/locator.py <username>
```

自动：获取 token → 拉 8373 → 匹配 → 过滤低置信度 → 回写 8385

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

## 四、触发词速查表

| 意图 | 关键词 |
|------|--------|
| 定位设备 | `定位`、`设备定位`、`locator` + `F\d+` 用户名 |
| 快捷执行 | `直接跑`、`自动跑完`、`不用确认` |
| 仅匹配 | `--match-only` |
| 仅分析 | `分析`、`--analyze` |
| 回写结果 | `回写`、`--writeback` |
| 审计模式 | `audit`、`审计` |
| 可视化 | `Cesium`、`HTML`、`可视化` |
