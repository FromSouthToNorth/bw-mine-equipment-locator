# 问题报告：bw-mine-equipment-locator 匹配逻辑修复

## 概述

本次修复针对 D99795450（窑街煤电金河煤矿）设备定位过程中暴露的多类匹配错误，核心原则是**宁缺毋滥**——当描述与候选存在硬性语义冲突时，应拒绝匹配而非 fallback 到错误候选。

---

## 问题1：前缀剥离规则过于宽松，吃掉位置编码

**现象**：设备描述 `1号分站模拟量001A019308皮顺联络巷迎头激光甲烷瓦斯` 经 `strip_prefix` 后变为 `皮顺联络巷迎头激光甲烷瓦斯`，其中的位置编码 `9308` 被前缀正则一并吃掉，导致后续编码提取失败。

**根因**：`PREFIX_PATTERNS` 中 `[A-Za-z0-9_]+` 和 `\d+号分站(模拟量|开关量|多态量)[A-Za-z0-9_]+` 范围过大。

**修复**（`locator.py:185`）：

```python
# 收紧为只匹配通道编码格式，不吞位置编码
r"^\d+号分站(模拟量|开关量|多态量)\d{3}[A-Z]\d{2}"
r"^其他\d{6,}[A-Z]\d{2}"
```

---

## 问题2：编码提取截断5位数字编号

**现象**：描述含 `17216` 的设备被提取编码为 `1721`（4位截断），进而匹配到含 `1721` 的错误候选（如 `17215` 相关巷道）。

**根因**：`extract_workface_code` 中4位纯数字匹配在5位之前执行。

**修复**（`locator.py:693`）：

```python
# 5 位纯数字优先于 4 位，避免 17216 -> 1721
m = re.search(r'(?<![A-Z])(\d{5})(?![A-Z])', description)
if m: return m.group(1)
m = re.search(r'(?<![A-Z])(\d{4})(?![A-Z])', description)
if m: return m.group(1)
```

---

## 问题3：通用数字前缀获得过高编码加分

**现象**：编码 `1460` 出现在 `1460变电所`、`1460错车场`、`1460运输大巷` 等多个不同地点，却仍获得 `+5` 的 `code_hit`，导致 `1460变电所` 等短名称候选靠编码压倒语义匹配。

**根因**：所有编码统一 `+5`，未区分"特定编码"与"通用区域前缀"。

**修复**：新增 `_is_generic_code` 函数（`locator.py:860`）：

```python
def _is_generic_code(code, code_to_candidates, candidates) -> bool:
    # 5+ 位视为特定工作面/巷道编号，不通用
    if len(code) >= 5: return False
    # 3-4 位纯数字且对应超过 3 个不同候选名 -> 通用前缀
    ...
```

通用前缀 `code_hit` 降级为 `+1`，不再进入 `EXACT` 层。

---

## 问题4：缓存空 ID 导致跨编码误匹配

**现象**：F1302 相关设备被缓存错误地匹配到 `C8302综采工作面`，match_lcs=0。

**根因**：workface 候选缺少 `id` 字段（为空字符串），缓存键 `candidate_id=""` 匹配了第一个 `id` 为空的候选。

**修复**（`locator.py:1539`）：

```python
# 仅当 cached_id 非空时才按 ID 匹配
if cached_id and cand.get("id") == cached_id:
    return cand
```

同时候选 `id` 使用 `tunnelId` 作为 fallback。

---

## 问题5：纯数字巷道名未过滤

**现象**：`146`、`258`、`160` 等40+条纯数字命名的巷道作为候选参与匹配，设备被错误关联到这些无语义名称。

**根因**：`_GENERIC_TUNNEL_NAME_PATTERN` 只匹配 `巷道\d+`，未覆盖纯数字名。

**修复**（`locator.py:420`）：

```python
_GENERIC_TUNNEL_NAME_PATTERN = re.compile(r'^巷道\d+$|^\d+$')
```

---

## 问题6：纯数字编码缺少边界匹配

**现象**：编码 `7` 误命中 `7300皮顺`，编码 `1721` 误命中 `17216`。

**根因**：`_code_in_name` 对纯数字编码使用直接子串匹配。

**修复**（`locator.py:801`）：

```python
if code.isdigit():
    return bool(re.search(r'(?<!\d)' + re.escape(code) + r'(?!\d)', name))
```

---

## 问题7：硬性语义冲突被编码/LCS 命中掩盖（核心问题）

**现象A**：`哈拉沟瓦斯泵房` 匹配到 `六采区移动式瓦斯泵站`（地点完全不同）。

**现象B**：`17208底抽联巷` 匹配到 `17208回风联巷`（编码相同，但底抽 != 回风）。

**现象C**：缓存中的错误匹配（底抽->回风联巷）在重新运行时被直接复用。

**根因**：评分公式中 `code_hit (+5)` 和 `LCS` 可以压倒一切，`_LOCATION_SEMANTICS` 的 `-10` 惩罚不足以阻止编码强匹配；缓存命中跳过语义检查。

**修复**（多处）：

### 7.1 新增 `_has_hard_semantic_conflict`（`locator.py:769`）

```python
def _has_hard_semantic_conflict(description: str, candidate_name: str) -> bool:
    """硬性语义冲突检测。
    当描述中有明确的地点限定词或功能词，而候选中缺少对应词或含冲突词时，
    直接拒绝匹配，避免编码/LCS 命中掩盖语义错误（宁缺毋滥）。
    """
    if not description or not candidate_name:
        return False

    # 1. 地点锚定词冲突：描述含地点限定词，候选必须含相同地点词
    for match in _AREA_ANCHOR_RE.finditer(description):
        anchor = match.group(0)
        if anchor and anchor not in candidate_name:
            return True

    # 2. 功能互斥冲突
    # 底抽 vs 回风/进风/皮顺/胶运
    if "底抽" in description and "底抽" not in candidate_name:
        if any(kw in candidate_name for kw in ["回风", "进风", "皮顺", "胶运"]):
            return True
    # 回风 vs 进风/底抽/胶运
    if "回风" in description and "回风" not in candidate_name:
        if any(kw in candidate_name for kw in ["进风", "底抽", "胶运"]):
            return True
    # 进风 vs 回风/底抽/皮顺
    if "进风" in description and "进风" not in candidate_name:
        if any(kw in candidate_name for kw in ["回风", "底抽", "皮顺"]):
            return True

    return False
```

地点锚定词正则（`locator.py:761`）：

```python
_AREA_ANCHOR_RE = re.compile(
    r'([一二三四五六七八九十百千\d]+采区|'  # 六采区、一采区等
    r'[东西南北]翼|'  # 西翼、北翼、东翼、南翼
    r'哈拉沟|马蹄沟|马蹄坡)',  # 特定地名（矿特有，后续按需扩展）
    re.UNICODE,
)
```

### 7.2 分层判定前硬性拒绝（`locator.py:926`）

```python
has_conflict = _has_hard_semantic_conflict(cleaned, name)
if has_conflict:
    layer = _MATCH_LAYER_REJECT
```

### 7.3 缓存语义校验（`locator.py:1637`）

缓存命中时若冲突则忽略缓存，重新匹配：

```python
if not _has_hard_semantic_conflict(cleaned, cand_name):
    cache_hits += 1
    match = {...}
    match_entries.append(...)
    continue
```

### 7.4 编码缺失语义感知（`locator.py:895`）

`specific_code_missing` 检查排除被语义冲突阻断的候选，避免编码仅出现在冲突候选中时 fallback 到无关候选：

```python
for c in candidates:
    name = c.get("name") or ""
    if (_code_in_name(device_code, name) or device_code in (c.get("tunnelId") or "")):
        if not _has_hard_semantic_conflict(cleaned, name):
            has_anywhere = True
            break
```

---

## 问题8：结果文件保存不完整

**现象**：`--match-only --output-mode summary` 后执行 `--writeback`，加载到 0 条结果。

**根因**：`_save_and_writeback` 保存的是根据 `output_mode` 裁剪后的 `output`（summary 模式不含 `results` 数组）。

**修复**（`locator.py:2106`）：

```python
# 文件始终保存完整数据，不受 output_mode 裁剪影响
save_data = output_full if output_full is not None else output
```

---

## 修复后数据对比（D99795450 窑街煤电金河煤矿）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 总设备 | 1605 | 1605 |
| 匹配成功 | 1055 | **963** |
| 未匹配 | 550 | **642** |
| 哈拉沟 -> 六采区（错误） | 5 | **0** |
| 17208底抽 -> 17208回风（错误） | 25 | **0** |
| 17208底抽 -> 17216施工联巷（错误fallback） | 有 | **0** |

减少的 92 个匹配均为语义错误关联，现在正确标记为未匹配（`CODE_MISMATCH` 或 `LOW_LCS`）。

---

## 仍存在的限制

1. **数据缺失无法匹配**：`哈拉沟瓦斯泵房`、`17208底抽联巷`、`17208底抽巷` 等正确巷道名称在 8373 数据源中不存在，这些设备当前只能标记为未匹配。

2. **地点锚定词正则需按需扩展**：当前覆盖 `*采区`、`*翼`、`哈拉沟` 等模式，若其他煤矿出现新的地名后缀（如 `*沟`、`*坡`），需继续补充 `_AREA_ANCHOR_RE`。

---

*报告生成时间：2026-05-18*
