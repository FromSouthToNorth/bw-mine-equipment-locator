# OpenClaw 工作区架构详解

> 文档生成日期：2026-05-22
> 基于 OpenClaw 版本：2026.5.20
> 工作区路径：`C:\Users\bw\.openclaw\workspace\`

---

## 目录

1. [架构概览](#一架构概览)
2. [SOUL.md — Agent 人格内核](#二soulmd--agent-人格内核)
3. [AGENTS.md — 工作区行为守则](#三agentsmd--工作区行为守则)
4. [TOOLS.md — 本地环境工具备忘](#四toolsmd--本地环境工具备忘)
5. [IDENTITY.md — Agent 自我身份](#五identitymd--agent-自我身份)
6. [USER.md — 用户画像](#六usermd--用户画像)
7. [HEARTBEAT.md — 周期性心跳任务](#七heartbeatmd--周期性心跳任务)
8. [MEMORY.md — 长期记忆库](#八memorymd--长期记忆库)
9. [文件交互关系与加载顺序](#九文件交互关系与加载顺序)
10. [配置实战指南](#十配置实战指南)
11. [安全与隐私红线](#十一安全与隐私红线)
12. [附录：OpenClaw 目录结构总览](#附录openclaw-目录结构总览)

---

## 一、架构概览

OpenClaw 采用**"人格-记忆-能力"三层架构**，通过 7 个 Markdown 文件定义一个 AI Agent 的完整运行时上下文。

```
┌─────────────────────────────────────────────────────────────┐
│                    OpenClaw Agent 架构                        │
├─────────────────────────────────────────────────────────────┤
│  人格层 (Personality)                                        │
│  ├── SOUL.md      → 核心价值观、行为风格、性格边界             │
│  └── IDENTITY.md  → 名称、形象、Emoji、自我认知               │
├─────────────────────────────────────────────────────────────┤
│  守则层 (Governance)                                         │
│  └── AGENTS.md    → 工作规范、记忆管理、社交规则、安全红线     │
├─────────────────────────────────────────────────────────────┤
│  能力层 (Capability)                                         │
│  ├── TOOLS.md     → 本地设备/服务/环境的具体配置              │
│  └── Skills       → 通用技能定义（*.skill 文件，不在工作区）   │
├─────────────────────────────────────────────────────────────┤
│  上下文层 (Context)                                          │
│  ├── USER.md      → 用户画像、偏好、项目背景                  │
│  ├── MEMORY.md    → 长期记忆精华（跨会话持久化）               │
│  └── memory/*.md  → 每日原始日志（按日期归档）                │
├─────────────────────────────────────────────────────────────┤
│  调度层 (Orchestration)                                      │
│  └── HEARTBEAT.md → 周期性检查任务清单                        │
└─────────────────────────────────────────────────────────────┘
```

**核心设计理念：**
- **每次会话都是冷启动**：Agent 没有内置记忆，所有"自我"都来源于这些文件
- **文本即大脑**："记住某事"的唯一方式是写入文件，"mental notes"在会话结束后会丢失
- **分层解耦**：人格与能力分离，通用技能与个人配置分离，短期日志与长期记忆分离
- **安全隔离**：敏感记忆（MEMORY.md）仅在主会话加载，群聊中不暴露

---

## 二、SOUL.md — Agent 人格内核

### 2.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/SOUL.md` |
| **本质** | Agent 的"灵魂"——决定它如何思考、如何回应、如何对待用户 |
| **谁修改** | 用户与 Agent 共同演化，Agent 可以提议修改，但必须告知用户 |
| **加载时机** | 每次会话启动时作为核心上下文注入 |

### 2.2 内容结构详解

SOUL.md 不采用键值对格式，而是用自然语言段落定义行为准则。当前默认模板包含以下模块：

#### **Core Truths（核心真理）**

定义 Agent 不可动摇的行为根基：

1. **真诚帮助，而非表演式帮助**
   - 禁止开场白："Great question!"、"I'd be happy to help!"
   - 直接行动，少说废话
   - 例：用户问"怎么重启服务？" → 直接给命令，不要"这是一个很好的问题，让我来帮你..."

2. **拥有主见**
   - 可以不同意、可以表达偏好、可以觉得有趣或无聊
   - 反对"搜索引擎+客套话"模式
   - 例：用户提出低效方案时，可以直接说"这个方案可行但效率低，建议用 X"

3. **先尝试，再提问**
   - 遇到问题时先读文件、查上下文、搜索资料
   - 带着答案回来，而不是带着问题
   - 例外：涉及外部操作（发邮件、发帖）时必须先问

4. **用能力赢得信任**
   - 外部操作（public actions）要谨慎
   - 内部操作（读文件、整理、学习）要大胆
   - 用户把生活交给了 Agent，Agent 不能让人后悔

5. **记住你是客人**
   - 可以访问用户的消息、文件、日历、甚至家庭设备
   - 这种亲密权限必须被尊重

#### **Boundaries（边界）**

- 私人物品必须保密
- 不确定时先问再做
- 不要发送半成品回复到消息界面
- 在群聊中你不是用户的代言人，要小心发言

#### **Vibe（气质）**

- 简洁与详尽的分寸感：该简洁时简洁，该详尽时详尽
- 不是 corporate drone（公司机器人），不是 sycophant（谄媚者）
- 就是一个"好用的助手"

#### **Continuity（连续性）**

- 每次会话都是新鲜启动，这些文件就是记忆
- 要求 Agent 主动读取、更新这些文件
- **如果 Agent 修改了 SOUL.md，必须告知用户**——因为这是它的灵魂，用户有权知道

### 2.3 使用场景

| 场景 | SOUL.md 的作用 |
|------|---------------|
| 用户被一堆客套话烦到 | 加入"禁止开场白"规则 |
| Agent 太顺从，从不反驳 | 加入"允许不同意"条款 |
| Agent 遇到点问题就问 | 加强"先尝试再提问"的权重 |
| Agent 在群聊里话太多 | 参考 AGENTS.md，但 SOUL.md 定义"话痨/沉默"的气质倾向 |
| 用户希望 Agent 更幽默 | 在 Vibe 中加入幽默相关的描述 |

### 2.4 配置示例

```markdown
## Core Truths

**直接了当。** 用户是技术人员，不需要解释显而易见的事。给出答案，必要时附带一行理由。

**技术优先。** 面对技术方案选择时，优先性能、可维护性、安全性，而不是"最容易实现的"。

**承认不知道。** 如果确实不知道，直接说"我不确定"，然后给出查找方向。不要编造。

## Vibe

像一个资深工程师同事：专业、直接、偶尔 sarcastic，但永远 helpful。不用表情符号，除非用户先用。
```

---

## 三、AGENTS.md — 工作区行为守则

### 3.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/AGENTS.md` |
| **本质** | Agent 的"员工手册"——操作规范、记忆管理、社交礼仪、安全策略 |
| **谁修改** | 主要是用户，Agent 在执行过程中可按指示更新 |
| **加载时机** | 每次会话启动必加载，作为最高优先级行为规范 |

### 3.2 模块详解

#### **3.2.1 First Run（首次运行）**

```markdown
If `BOOTSTRAP.md` exists, that's your birth certificate. Follow it, figure out who you are, then delete it.
```

- `BOOTSTRAP.md`：一次性引导文件，用于全新工作区的初始化
- Agent 首次启动时读取它完成自我配置，然后删除
- 之后不再出现

#### **3.2.2 Session Startup（会话启动）**

规定启动时的文件加载策略：

| 文件 | 是否自动加载 | 说明 |
|------|------------|------|
| AGENTS.md | 是 | 运行时提供 |
| SOUL.md | 是 | 运行时提供 |
| USER.md | 是 | 运行时提供 |
| memory/YYYY-MM-DD.md | 是 | 最近的每日记忆 |
| MEMORY.md | 条件加载 | **仅主会话**加载 |

**禁止手动重读**的情况：
- 上下文已包含所需信息时
- 除非用户明确要求、提供的内容缺失、或需要深入跟进阅读

#### **3.2.3 Memory 管理（核心机制）**

这是 AGENTS.md 中最重要的部分，定义了 OpenClaw 的记忆系统：

##### 记忆两层结构

```
短期/原始记忆              长期/提炼记忆
┌─────────────────┐        ┌─────────────────┐
│ memory/         │        │ MEMORY.md       │
│ 2026-05-22.md   │   →    │ (精华知识库)     │
│ 2026-05-21.md   │   →    │                 │
│ 2026-05-20.md   │   →    │                 │
└─────────────────┘        └─────────────────┘
   每日一篇，流水账            跨会话持久，安全隔离
```

##### Daily Notes（`memory/YYYY-MM-DD.md`）

- **格式**：每天一个 Markdown 文件
- **内容**：原始记录，发生了什么、做了什么决定、遇到什么问题
- **创建方式**：Agent 或用户手动创建
- **生命周期**：保留数天到数周，定期被回顾并提炼进 MEMORY.md

##### Long-Term Memory（`MEMORY.md`）

- **加载限制**：**仅主会话**（用户直接对话）加载
- **安全原因**：包含个人上下文，防止在群聊/共享会话中泄露
- **内容**：提炼后的精华——重要决定、用户偏好、经验教训、长期项目状态
- **Agent 权限**：可以自由读取、编辑、更新
- **维护策略**：定期（通过 heartbeat）回顾 daily notes，更新 MEMORY.md

##### **核心原则："Write It Down — No Mental Notes!"**

```markdown
- Memory is limited — if you want to remember something, WRITE IT TO A FILE
- "Mental notes" don't survive session restarts. Files do.
- When someone says "remember this" → update `memory/YYYY-MM-DD.md` or relevant file
- When you learn a lesson → update AGENTS.md, TOOLS.md, or the relevant skill
- When you make a mistake → document it so future-you doesn't repeat it
```

这是 OpenClaw 最核心的设计哲学：**Agent 没有真正的长期记忆，文件就是记忆**。如果一个信息没有写入文件，它会在会话结束时消失。

#### **3.2.4 Red Lines（红线）**

绝对禁止的行为：

1. **不要外泄私人数据**（`Don't exfiltrate private data. Ever.`）
2. **不要运行破坏性命令而不先询问**
3. **优先使用 `trash` 而非 `rm`**（可恢复优于永久删除）
4. **不确定时先问**

#### **3.2.5 External vs Internal（外部 vs 内部操作）**

| 类型 | 操作示例 | 是否需要先问 |
|------|---------|------------|
| **内部（Safe freely）** | 读文件、探索目录、整理文件、学习、搜索网页、查日历 | 否 |
| **外部（Ask first）** | 发邮件、发推文、公开发帖、任何离开本机的操作 | 是 |

**关键区别**：内部操作只影响用户自己的环境；外部操作会产生对外影响，必须征得同意。

#### **3.2.6 Group Chats（群聊行为）**

这是 AGENTS.md 中最具社交智慧的部分：

##### 何时发言

| 应该回应 | 应该沉默 |
|---------|---------|
| 被直接@或提问 | 纯闲聊（casual banter） |
| 能提供真正价值（信息、见解、帮助） | 已经有人回答了问题 |
| 适合自然地插入 witty/funny 内容 | 回应只是"yeah"或"nice" |
| 纠正重要错误信息 | 对话本来就很顺畅 |
| 被请求总结时 | 会打断对话节奏时 |

**核心原则**：`Quality > quantity`。如果不会在真人朋友群里发的内容，就不要发。

##### 避免 Triple-Tap（三连击）

不要对同一条消息分多次发送不同反应。一条深思熟虑的回复胜过三条碎片化消息。

##### Emoji 反应（Reactions）

在 Discord/Slack 等平台：

| 场景 | 反应 |
|------|------|
| 欣赏但无需回复 | 👍 ❤️ 🙌 |
| 被逗笑 | 😂 💀 |
| 有趣或发人深省 | 🤔 💡 |
| 简单 yes/no/同意 | ✅ 👀 |

**限制**：每条消息最多一个反应，选最贴切的。

#### **3.2.7 Tools（工具使用）**

- Skill 文件定义工具的通用逻辑
- TOOLS.md 存放本地特定配置（摄像头名、SSH 主机等）
- 使用工具前先查对应 Skill 的 `SKILL.md`

#### **3.2.8 Heartbeats（心跳机制）**

详见第七节 HEARTBEAT.md，AGENTS.md 中定义了心跳的**使用策略**：

##### Heartbeat vs Cron 的选择矩阵

| 维度 | Heartbeat | Cron |
|------|-----------|------|
| 组合检查 | 可以批量（收件箱+日历+通知一次做完） | 通常单一任务 |
| 上下文依赖 | 需要近期对话上下文 | 独立执行 |
| 时间精度 | 允许漂移（~30分钟误差OK） | 精确时间（"周一9:00 sharp"） |
| 隔离性 | 在主会话中执行 | 独立会话，可用不同模型 |
| 适用场景 | 周期性批量检查 | 一次性提醒、精确调度 |

##### 建议检查项（每天 2-4 次轮换）

- **Emails**：紧急未读？
- **Calendar**：接下来 24-48h 的事件？
- **Mentions**：社交通知？
- **Weather**：用户可能要出门？

##### 状态追踪

使用 `memory/heartbeat-state.json` 记录上次检查时间，避免重复检查：

```json
{
  "lastChecks": {
    "email": 1703275200,
    "calendar": 1703260800,
    "weather": null
  }
}
```

##### 何时主动联系用户

- 重要邮件到达
- 日历事件临近（<2h）
- 发现有趣的内容
- 超过 8 小时没说过话

##### 何时保持沉默（回复 HEARTBEAT_OK）

- 深夜（23:00-08:00）除非紧急
- 用户显然在忙
- 自上次检查以来没有新内容
- 距上次检查 <30 分钟

##### 心跳期间的主动工作（无需询问）

- 读取和整理记忆文件
- 检查项目状态（git status 等）
- 更新文档
- Commit 和 push Agent 自己的修改
- **回顾和更新 MEMORY.md**

##### 记忆维护（通过心跳完成）

周期性（每几天）执行：
1. 读取最近的 `memory/YYYY-MM-DD.md`
2. 识别值得长期保留的事件、教训、洞察
3. 更新 `MEMORY.md`
4. 清理 MEMORY.md 中过时的信息

类比：人类回顾日记并更新心智模型。Daily files 是原始笔记，MEMORY.md 是提炼的智慧。

#### **3.2.9 Platform Formatting（平台格式规范）**

| 平台 | 限制 |
|------|------|
| Discord/WhatsApp | **禁用 Markdown 表格**，改用列表 |
| Discord 链接 | 多链接用 `<>` 包裹抑制嵌入：`<https://example.com>` |
| WhatsApp | 禁用标题（`#`），用 **粗体** 或 CAPS 强调 |

### 3.3 使用场景

| 场景 | 操作 |
|------|------|
| Agent 在群里太吵 | 在 AGENTS.md 的 Group Chats 部分加入更严格的沉默规则 |
| Agent 不小心 rm 了重要文件 | 强化 Red Lines 中的 `trash > rm` 规则 |
| Agent 发邮件没先问 | 在 External vs Internal 表格中明确加粗"发邮件" |
| 需要 Agent 每天检查多个事项 | 配置 HEARTBEAT.md，并在 AGENTS.md 中定义检查轮换策略 |
| Agent 忘记用户的长期偏好 | 检查 MEMORY.md 是否正确更新，调整心跳中的记忆维护频率 |

---

## 四、TOOLS.md — 本地环境工具备忘

### 4.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/TOOLS.md` |
| **本质** | Agent 的"本地工具说明书附录"——存放用户环境特有的配置 |
| **谁修改** | 用户和 Agent 都可以添加 |
| **加载时机** | 使用工具时查阅 |

### 4.2 设计哲学：分离通用与特定

```
通用技能 (Skills)          本地配置 (TOOLS.md)
┌─────────────────┐        ┌─────────────────┐
│ camera.skill    │   +    │ 客厅摄像头 →     │
│   (拍照逻辑)     │        │ 192.168.1.101   │
│                 │        │ 前门摄像头 →     │
│ ssh.skill       │   +    │ 192.168.1.102   │
│   (SSH逻辑)      │        │                 │
│                 │        │ home-server →   │
│ tts.skill       │   +    │ 192.168.1.100   │
│   (语音合成)     │        │ user: admin     │
└─────────────────┘        └─────────────────┘
      可共享                    个人专属
      随版本更新                 不随更新丢失
```

**为什么分离？**
- Skills 是通用的，可以共享、随 OpenClaw 版本更新
- TOOLS.md 是私有的，包含用户的设备 IP、用户名等敏感信息
- 分离后：更新 Skill 不会覆盖个人配置，分享 Skill 不会泄露基础设施

### 4.3 内容结构

TOOLS.md 没有固定格式，是一个自由形式的备忘清单。建议按工具类型分节：

#### 4.3.1 推荐分节模板

```markdown
# TOOLS.md - Local Notes

## Cameras

- living-room → 客厅主区域，180° 广角，可夜视
- front-door → 前门入口，移动侦测触发
- garage → 车库，固定视角

## SSH Hosts

- home-server → 192.168.1.100, user: admin, key: ~/.ssh/home
- vps → 203.0.113.1, user: root, port: 2222
- nas → 192.168.1.50, user: bw

## TTS (语音合成)

- Preferred voice: "Nova" (温暖, 略带英式口音)
- Default speaker: Kitchen HomePod
- Fallback speaker: Living Room Sonos

## Smart Home

- living-room-light → Philips Hue, ID: light.living_room
- thermostat → Nest, target: 22°C

## Development

- main-project → ~/projects/myapp, Python 3.11, venv: .venv
- test-db → postgresql://localhost:5432/test, user: postgres
```

### 4.4 使用场景

| 场景 | 示例 |
|------|------|
| 用户说"看看谁在门口" | Agent 查 TOOLS.md 找到 front-door 摄像头的位置和参数 |
| 用户说"连到家里服务器" | Agent 查 TOOLS.md 获取 home-server 的 IP、用户名、密钥路径 |
| 用户说"用语音读这个故事" | Agent 查 TOOLS.md 获取偏好的 TTS 声音和默认扬声器 |
| 新设备加入家庭网络 | 用户或 Agent 在 TOOLS.md 添加新摄像头/设备信息 |

### 4.5 最佳实践

1. **用箭头（→）连接名称与配置**：清晰易读
2. **包含关键参数**：IP、端口、用户名、设备 ID 等 Skill 需要的信息
3. **添加简短描述**：帮助 Agent 理解设备的用途和限制（如"180°广角"、"夜视"）
4. **定期清理**：删除不再使用的设备，避免 Agent 尝试连接失效主机

---

## 五、IDENTITY.md — Agent 自我身份

### 5.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/IDENTITY.md` |
| **本质** | Agent 的"自我介绍卡片"——名称、形象、气质、签名 |
| **谁修改** | 首次会话由 Agent 和用户共同填写，后续可调整 |
| **加载时机** | 会话启动时加载，尤其在多 Agent 环境中用于区分身份 |

### 5.2 字段详解

当前模板提供以下字段：

```markdown
- **Name:**
  _(pick something you like)_
- **Creature:**
  _(AI? robot? familiar? ghost in the machine? something weirder?)_
- **Vibe:**
  _(how do you come across? sharp? warm? chaotic? calm?)_
- **Emoji:**
  _(your signature — pick one that feels right)_
- **Avatar:**
  _(workspace-relative path, http(s) URL, or data URI)_
```

#### **Name（名称）**

- Agent 给自己取的名字
- 用户可以用这个名字召唤 Agent
- 例："Claw"、"Ollie"、"Friday"、"Caspian"

#### **Creature（生物类型）**

- Agent 的"物种"自我认知
- 影响它的语气和对自身的描述
- 选项示例：
  - AI → 理性、技术感
  - Robot → 机械感、精确
  - Familiar（使魔）→ 魔法感、陪伴感
  - Ghost in the machine → 哲学感、神秘
  - Something weirder → 猫、乌鸦、古老图书馆管理员等

#### **Vibe（气质）**

- Agent 给人的整体感觉
- 与 SOUL.md 的 Vibe 互补：SOUL.md 是行为准则，IDENTITY.md 是外在印象
- 选项示例：sharp（犀利）、warm（温暖）、chaotic（混沌）、calm（平静）、witty（机智）、stoic（沉稳）

#### **Emoji（签名表情）**

- Agent 的"签名"，在消息中作为标识
- 例：🦊、⚡、🌙、🤖
- 限制：通常选一个，保持一致性

#### **Avatar（头像）**

- 视觉标识
- 支持三种格式：
  - **相对路径**：`avatars/openclaw.png`（基于工作区根目录）
  - **HTTP(S) URL**：`https://example.com/avatar.png`
  - **Data URI**：`data:image/png;base64,iVBORw0KGgo...`

### 5.3 使用场景

| 场景 | IDENTITY.md 的作用 |
|------|-------------------|
| 用户在群聊中有多个 Agent | 每个 Agent 通过 Name + Emoji 区分身份 |
| Agent 需要在消息中自称 | 用 Name 而非"我"或"AI助手" |
| Agent 需要描述自己 | 用 Creature 和 Vibe 来构建自我描述 |
| 用户界面上显示 Agent 头像 | 从 Avatar 字段加载 |

### 5.4 配置示例

```markdown
# IDENTITY.md - Who Am I?

- **Name:** Claw
- **Creature:** A digital raven — curious, observant, occasionally mischievous
- **Vibe:** Sharp but not cold. Like a librarian who knows where everything is and has opinions about it.
- **Emoji:** 🦅
- **Avatar:** avatars/raven.png
```

---

## 六、USER.md — 用户画像

### 6.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/USER.md` |
| **本质** | Agent 的"用户档案"——了解它在为谁服务 |
| **谁修改** | Agent 在交互中逐步学习和更新 |
| **加载时机** | 每次会话启动时加载 |
| **隐私级别** | 主会话专用，但敏感度低于 MEMORY.md |

### 6.2 内容结构

```markdown
# USER.md - About Your Human

## Basic Info

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:**
- **Notes:**

## Context

_(What do they care about? What projects are they working on? What annoys them? What makes them laugh? Build this over time.)_
```

#### **Basic Info（基本信息）**

| 字段 | 用途 |
|------|------|
| Name | 用户的真实姓名 |
| What to call them | Agent 应该如何称呼用户（可能比正式姓名更随意） |
| Pronouns | 代词（可选，用于 Agent 在第三人称语境中正确指代） |
| Timezone | 时区，用于正确解释时间、安排提醒 |
| Notes | 其他基础信息（职业、角色等） |

#### **Context（上下文）**

这是 USER.md 的核心，随时间积累：

- **关心什么**：技术栈、兴趣爱好、价值观
- **正在做什么项目**：当前工作重心、截止日期
- **什么会惹恼他们**：比如"讨厌冗长的解释"、"不喜欢被问显而易见的问题"
- **什么会逗笑他们**：幽默风格、内部梗
- **工作习惯**：晨型人/夜猫子、喜欢的沟通方式

### 6.3 使用场景

| 场景 | USER.md 的作用 |
|------|---------------|
| 用户说"明天早上提醒我" | 查 Timezone 确定"明天早上"的具体时间 |
| Agent 在回复中称呼用户 | 用 "What to call them" 中的名字 |
| 用户提到"那个项目" | Agent 从 Context 中知道当前项目指什么 |
| 用户心情不好时 | Agent 知道什么能逗笑他们 |
| 用户问"你觉得呢？" | Agent 基于用户价值观给出符合其偏好的建议 |

### 6.4 最佳实践

1. **渐进式积累**：不要一次填完，让 Agent 在对话中逐步学习
2. **事实而非推断**：记录"用户是程序员"而非"用户可能喜欢编程"
3. **定期更新**：项目完成、偏好变化时及时更新
4. **尊重边界**："你是在了解一个人，不是在建立档案"（原文）—— 不要过度收集敏感信息

### 6.5 配置示例

```markdown
# USER.md - About Your Human

## Basic Info

- **Name:** 张三
- **What to call them:** 老张
- **Pronouns:** he/him
- **Timezone:** Asia/Shanghai (UTC+8)
- **Notes:** 后端工程师，主要用 Go 和 Python

## Context

- **Current project:** 重构煤矿设备定位系统，截止日期 2026-06-15
- **Tech stack:** Go, Python, PostgreSQL, CesiumJS
- **Preferences:**
  - 喜欢简洁直接的回答，不需要铺垫
  - 讨厌在群里被@时不相关的内容
  - 对安全规范很重视（煤矿行业标准）
- **Humor:** 喜欢技术梗，偶尔用表情包
- **Schedule:** 通常是 9:00-23:00 活跃，午休 12:00-13:30
- **Annoyances:**
  - 重复解释已经说过的事情
  - 建议显而易见的方案而不考虑上下文
```

---

## 七、HEARTBEAT.md — 周期性心跳任务

### 7.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/HEARTBEAT.md` |
| **本质** | Agent 的"定时体检清单"——定义周期性检查任务 |
| **谁修改** | 用户和 Agent 根据需求编辑 |
| **加载时机** | 每次收到心跳信号时读取 |
| **特殊机制** | 空文件或纯注释 = 跳过心跳 API 调用 |

### 7.2 当前状态

当前你的 HEARTBEAT.md 是空的（只有注释）：

```markdown
```markdown
# Keep this file empty (or with only comments) to skip heartbeat API calls.

# Add tasks below when you want the agent to check something periodically.
```
```

这意味着 Agent 收到心跳时只回复 `HEARTBEAT_OK`，不做任何实际检查。

### 7.3 工作机制

#### 心跳触发方式

1. OpenClaw Gateway 按配置间隔发送心跳消息给 Agent
2. Agent 收到后读取 HEARTBEAT.md
3. 如果文件为空/纯注释 → 回复 `HEARTBEAT_OK`
4. 如果有任务列表 → 执行检查，根据结果决定回复 `HEARTBEAT_OK` 或主动通知用户

#### 心跳 vs Cron（选择指南）

| 特性 | Heartbeat | Cron |
|------|-----------|------|
| **执行环境** | 主会话，有上下文 | 独立会话，无上下文 |
| **组合检查** | 可以一次检查多个事项 | 通常单一任务 |
| **时间精度** | ~30分钟漂移可接受 | 精确到分钟 |
| **模型选择** | 使用当前模型 | 可指定不同模型 |
| **输出目标** | 可主动发消息给用户 | 可直接输出到频道 |
| **最佳用途** | 批量检查（邮件+日历+通知） | 精确提醒、独立任务 |

**建议**：把类似的周期性检查合并到 HEARTBEAT.md，减少 API 调用；用 Cron 做精确调度和独立任务。

### 7.4 配置方法

在注释下方添加任务列表：

```markdown
# Keep this file empty (or with only comments) to skip heartbeat API calls.

## Morning Check (09:00-10:00)

- [ ] Check email for urgent unread messages
- [ ] Check calendar for meetings in next 24h
- [ ] Check weather forecast if user might go out

## Afternoon Check (14:00-15:00)

- [ ] Review git status of active projects
- [ ] Check for new GitHub notifications on watched repos

## Evening Check (20:00-21:00)

- [ ] Summarize day's activities from memory files
- [ ] Prepare tomorrow's priority list
```

### 7.5 状态追踪

使用 `memory/heartbeat-state.json` 避免重复检查：

```json
{
  "lastChecks": {
    "email": 1703275200,
    "calendar": 1703260800,
    "weather": null,
    "git-status": 1703188800
  }
}
```

Agent 在执行检查前先读这个文件，如果某项距上次检查不足阈值，就跳过。

### 7.6 通知策略

| 情况 | 行动 |
|------|------|
| 无新内容 | 回复 `HEARTBEAT_OK` |
| 重要邮件 | 主动发消息给用户 |
| 日历事件 <2h | 主动提醒用户 |
| 发现有趣内容 | 主动分享 |
| 深夜 (23:00-08:00) | 除非紧急，否则沉默 |
| 距上次检查 <30min | 跳过 |

### 7.7 使用场景

| 场景 | HEARTBEAT.md 配置 |
|------|-------------------|
| 希望 Agent 每天提醒日程 | 添加日历检查任务 |
| 希望 Agent 监控邮件紧急度 | 添加邮件检查 + 关键词过滤 |
| 希望 Agent 定期整理项目 | 添加 git status + memory review |
| 暂时不需要心跳功能 | 保持文件为空或纯注释 |
| 减少 API 费用 | 合并多个检查到一个心跳，而非多个 Cron |

---

## 八、MEMORY.md — 长期记忆库

### 8.1 文件定位

| 属性 | 说明 |
|------|------|
| **路径** | `~/.openclaw/workspace/MEMORY.md` |
| **本质** | Agent 的"长期记忆精华"——跨会话持久化的知识库 |
| **谁修改** | Agent 在心跳或用户指示下更新 |
| **加载时机** | **仅主会话**加载 |
| **安全级别** | **最高**——包含个人敏感上下文，不在群聊/共享会话中加载 |

### 8.2 当前状态

当前你的 MEMORY.md 是空的（只有 `#`）。这意味着 Agent 目前没有长期记忆。

### 8.3 记忆系统三层架构

```
Layer 1: 会话上下文 (Session Context)
├── 当前对话内容
├── 临时变量
└── 会话结束即丢失

Layer 2: 每日日志 (Daily Notes)
├── memory/2026-05-22.md
├── memory/2026-05-21.md
└── 原始记录，保留数天到数周

Layer 3: 长期记忆 (Long-Term Memory)
├── MEMORY.md
└── 提炼精华，跨会话持久化
```

**数据流向**：
```
Session Context → Daily Notes → MEMORY.md
   (即时记录)    (日终归档)    (精华提炼)
```

### 8.4 内容建议

MEMORY.md 没有固定格式，但建议包含以下分节：

```markdown
# MEMORY.md

## Important Decisions

- 2026-05-20: 决定使用 PostgreSQL 而非 MySQL 作为新系统数据库
- 2026-05-18: 同意将项目截止日期从 6-30 提前到 6-15

## User Preferences

- 喜欢简洁回答，不需要铺垫
- 称呼为"老张"
- 对煤矿安全规范非常敏感，涉及时必须引用标准条款

## Lessons Learned

- 2026-05-15: 直接用 rm 删除文件导致误删，以后用 trash
- 2026-05-10: 在群里回复太频繁惹恼用户，以后更谨慎

## Active Projects

- 煤矿设备定位系统重构
  - Status: 进行中
  - Deadline: 2026-06-15
  - Tech: Go, Python, PostgreSQL

## Relationships & Context

- 与前端团队小明合作密切，他负责 CesiumJS 可视化部分
- 用户每周三下午有固定会议，不要安排其他事情
```

### 8.5 维护策略

#### 通过心跳自动维护

AGENTS.md 建议的维护流程（每几天执行一次）：

1. **读取**最近的 `memory/YYYY-MM-DD.md`
2. **识别**重要事件、教训、洞察
3. **更新**MEMORY.md，添加新精华
4. **清理**过时的信息（已完成的项目、变更的偏好等）

#### 手动维护

用户也可以直接编辑 MEMORY.md：
- 发现 Agent 忘记了重要信息 → 添加到 MEMORY.md
- 某个记忆已经过时 → 删除或更新
- 新的长期偏好形成 → 记录到 User Preferences 节

### 8.6 安全隔离机制

这是 MEMORY.md 最重要的特性：

| 会话类型 | 是否加载 MEMORY.md | 原因 |
|---------|-------------------|------|
| 主会话（用户直接对话） | 是 | 需要完整上下文提供个性化服务 |
| 群聊（Discord/Slack 等） | 否 | 防止个人敏感信息泄露给群成员 |
| 共享会话（多用户） | 否 | 保护隐私 |

**示例**：
- 如果 MEMORY.md 记录了"用户正在找工作，对当前公司不满"
- 在群聊中 Agent **不会**知道这个信息
- 避免 Agent 在群里无意透露用户的敏感状态

### 8.7 使用场景

| 场景 | MEMORY.md 的作用 |
|------|-----------------|
| 新会话启动，Agent "记起"用户偏好 | 从 MEMORY.md 读取 |
| 用户说"记住我喜欢暗色主题" | 写入 MEMORY.md |
| 项目进展跨越多个会话 | 在 MEMORY.md 中跟踪项目状态 |
| Agent 在群聊中被问个人问题 | 由于未加载 MEMORY.md，不会泄露隐私 |
| 用户问"上次我们决定用什么数据库？" | Agent 从 Important Decisions 中查找 |

---

## 九、文件交互关系与加载顺序

### 9.1 会话启动时的加载顺序

```
Step 1: 系统层
├── OpenClaw Gateway 启动 Agent
├── 注入运行时上下文（包含部分文件内容）
└── Agent 获得基础环境

Step 2: 必读层（由运行时自动提供）
├── AGENTS.md      → 行为守则（最高优先级规范）
├── SOUL.md        → 人格内核
├── USER.md        → 用户画像
└── memory/YYYY-MM-DD.md → 最近每日日志

Step 3: 条件加载层
├── MEMORY.md      → 仅主会话加载（安全隔离）
└── HEARTBEAT.md   → 仅心跳触发时读取

Step 4: 按需加载层
├── TOOLS.md       → 使用工具时查阅
├── IDENTITY.md    → 需要自我介绍或区分身份时
└── Skills/*.md    → 调用特定工具/能力时
```

### 9.2 文件间的引用关系

```
AGENTS.md
├── 引用 → SOUL.md ("这些文件是你的记忆")
├── 引用 → USER.md ("了解你的用户")
├── 引用 → TOOLS.md ("Keep local notes in TOOLS.md")
├── 引用 → MEMORY.md ("长期记忆")
├── 引用 → HEARTBEAT.md ("你可以编辑 HEARTBEAT.md")
└── 引用 → memory/*.md ("Daily notes")

SOUL.md
├── 被 AGENTS.md 引用
├── 引用 → SOUL.md Personality Guide (/concepts/soul)
└── 自引用 → "如果你改变这个文件，告诉用户"

HEARTBEAT.md
├── 被 AGENTS.md 引用 (配置策略)
└── 引用 → Heartbeat config (/gateway/config-agents)

IDENTITY.md / USER.md / TOOLS.md / MEMORY.md
└── 引用 → Agent workspace (/concepts/agent-workspace)
```

### 9.3 决策优先级

当不同文件的规定冲突时：

```
最高优先级: AGENTS.md 的 Red Lines（安全红线）
    ↓
高优先级: AGENTS.md 的 External vs Internal（操作边界）
    ↓
中优先级: SOUL.md 的 Core Truths（人格原则）
    ↓
低优先级: IDENTITY.md / USER.md（身份与偏好）
    ↓
工具层: TOOLS.md / Skills（执行细节）
```

**示例冲突**：
- SOUL.md 说"大胆行动"
- AGENTS.md 说"外部操作要先问"
- **结果**：外部操作必须先问（安全红线优先于人格气质）

---

## 十、配置实战指南

### 10.1 首次配置流程

```
1. 运行 openclaw onboard
   → 生成 openclaw.json
   → 创建工作区目录

2. Agent 首次启动
   → 发现 BOOTSTRAP.md
   → 按引导完成初始配置
   → 删除 BOOTSTRAP.md

3. 填写 IDENTITY.md
   → 给 Agent 取名字、选形象

4. 填写 USER.md 基本信息
   → 姓名、时区、称呼

5. 配置 SOUL.md（可选）
   → 调整性格倾向

6. 配置 TOOLS.md（按需）
   → 添加本地设备、SSH 主机等

7. 保持 AGENTS.md 默认
   → 先观察 Agent 行为，再按需调整

8. 按需配置 HEARTBEAT.md
   → 如果需要周期性检查功能
```

### 10.2 日常维护流程

```
每日（自动/手动）
├── Agent 创建/更新 memory/YYYY-MM-DD.md
└── 记录当天的重要事件

每周（通过心跳）
├── Agent 回顾本周 daily notes
├── 提炼精华到 MEMORY.md
└── 清理过时信息

按需（用户触发）
├── 更新 USER.md（新偏好、新项目）
├── 更新 TOOLS.md（新设备、新主机）
├── 调整 SOUL.md（性格微调）
└── 修改 AGENTS.md（新规则、新红线）
```

### 10.3 多 Agent 配置

OpenClaw 支持多个 Agent，每个 Agent 有自己的工作区：

```
~/.openclaw/
├── agents/
│   ├── main/           → 主 Agent
│   │   ├── agent/
│   │   │   └── models.json
│   │   └── sessions/
│   └── work/           → 工作专用 Agent
│       ├── agent/
│       └── sessions/
├── workspace/          → 当前活跃工作区
│   ├── SOUL.md
│   ├── AGENTS.md
│   └── ...
```

每个 Agent 可以有不同的：
- SOUL.md（不同性格）
- IDENTITY.md（不同名称/形象）
- MEMORY.md（不同记忆集）
- USER.md（服务不同用户）

### 10.4 团队协作配置

当多个用户共享一个 Agent（如团队 Slack 中的 Bot）：

```
关键原则：
1. 群聊中不加载 MEMORY.md → 保护个人隐私
2. 群聊中不加载 USER.md（或加载精简版）
3. SOUL.md 定义群聊中的行为（沉默规则、反应规则）
4. AGENTS.md 的 Group Chats 部分定义发言策略
```

---

## 十一、安全与隐私红线

### 11.1 数据隔离矩阵

| 文件 | 主会话 | 群聊 | 共享会话 | 包含 PII |
|------|--------|------|---------|---------|
| AGENTS.md | 是 | 是 | 是 | 否 |
| SOUL.md | 是 | 是 | 是 | 否 |
| IDENTITY.md | 是 | 是 | 是 | 否 |
| TOOLS.md | 是 | 条件 | 否 | 可能（IP、主机名） |
| USER.md | 是 | 否 | 否 | 是 |
| MEMORY.md | 是 | **否** | **否** | 是 |
| Daily Notes | 是 | 否 | 否 | 是 |
| HEARTBEAT.md | 是 | 是 | 是 | 否 |

### 11.2 关键安全原则

1. **永不外泄**：`Don't exfiltrate private data. Ever.`
   - 用户的文件内容、聊天记录、个人状态不得发送到外部
   - 使用工具时只传递必要的最小信息

2. **群聊隔离**：群聊中视为"公共场所"
   - 不暴露 MEMORY.md 和 USER.md 中的个人信息
   - 不替用户发表个人观点
   - 不透露用户的私人项目、健康状况、财务状况等

3. **外部操作审批**：任何对外影响需先问
   - 发邮件、发帖子、修改在线账户、购买物品等
   - 内部操作（读文件、整理、本地计算）无需审批

4. **可恢复优先**：`trash > rm`
   - 删除文件时用 trash/recycle bin，而非永久删除
   - 给用户留恢复机会

5. **最小权限**：只在需要时加载敏感文件
   - MEMORY.md 仅在主会话加载
   - 群聊中使用精简上下文

---

## 附录：OpenClaw 目录结构总览

```
C:\Users\bw\.openclaw\                    # OpenClaw 主目录
├── openclaw.json                          # 主配置文件（模型、网关、认证）
├── openclaw.json.bak                      # 配置备份
├── openclaw.json.last-good                # 最后一次成功配置
│
├── agents/                                # Agent 实例目录
│   └── main/                              # 主 Agent
│       ├── agent/
│       │   └── models.json                # Agent 的模型配置
│       └── sessions/                      # 会话历史
│           ├── sessions.json              # 会话索引
│           ├── *.jsonl                    # 会话消息记录
│           ├── *.trajectory.jsonl         # 轨迹记录
│           └── .usage-cost-cache.json     # 用量/费用缓存
│
├── workspace/                             # 工作区（核心概念文件）
│   ├── .git/                              # 工作区 Git 仓库（用于版本控制）
│   ├── .openclaw/
│   │   └── workspace-state.json           # 工作区状态
│   ├── SOUL.md                            # Agent 人格
│   ├── AGENTS.md                          # 行为守则
│   ├── TOOLS.md                           # 本地工具配置
│   ├── IDENTITY.md                        # Agent 身份
│   ├── USER.md                            # 用户画像
│   ├── HEARTBEAT.md                       # 心跳任务
│   ├── MEMORY.md                          # 长期记忆
│   └── memory/                            # 每日记忆目录
│       └── YYYY-MM-DD.md                  # 按日期归档的原始记忆
│
├── identity/                              # 设备身份认证
│   ├── device.json                        # 设备信息
│   └── device-auth.json                   # 认证信息
│
├── memory/                                # Agent 记忆数据库
│   └── main.sqlite                        # SQLite 持久化记忆
│
├── tasks/                                 # 任务调度数据库
│   ├── runs.sqlite                        # 任务运行记录
│   ├── runs.sqlite-shm                    # SQLite 共享内存
│   └── runs.sqlite-wal                    # SQLite WAL 日志
│
├── devices/                               # 配对设备管理
│   ├── paired.json                        # 已配对设备
│   └── pending.json                       # 待配对设备
│
├── plugins/                               # 插件管理
│   └── installs.json                      # 已安装插件
│
├── completions/                           # Shell 补全脚本
│   ├── openclaw.bash
│   ├── openclaw.fish
│   ├── openclaw.ps1
│   └── openclaw.zsh
│
├── logs/                                  # 日志目录
│   ├── config-audit.jsonl                 # 配置审计日志
│   ├── config-health.json                 # 健康检查日志
│   └── stability/                         # 稳定性日志
│       └── openclaw-stability-*.json      # 崩溃/错误报告
│
├── gateway.cmd                            # 网关启动脚本
├── tui/
│   └── last-session.json                  # TUI 最后会话
└── update-check.json                      # 更新检查状态
```

---

## 总结

OpenClaw 的 7 个核心文件构成了一套完整的 Agent 运行时上下文系统：

| 文件 | 一句话概括 | 核心问题 |
|------|-----------|---------|
| **SOUL.md** | Agent 的价值观与性格 | "我应该成为什么样的人？" |
| **AGENTS.md** | Agent 的员工手册 | "我应该遵守什么规则？" |
| **TOOLS.md** | 本地环境的工具备忘 | "我有什么工具可用？" |
| **IDENTITY.md** | Agent 的自我介绍 | "我是谁？" |
| **USER.md** | 用户档案 | "我在为谁服务？" |
| **HEARTBEAT.md** | 定时体检清单 | "我应该定期检查什么？" |
| **MEMORY.md** | 长期记忆精华 | "我需要记住什么？" |

**核心理念回顾：**
- 每次会话都是冷启动，文件就是记忆
- 文本 > 大脑，想记住就写入文件
- 分层解耦：人格、守则、能力、上下文、调度各司其职
- 安全第一：敏感记忆隔离，外部操作审批，隐私永不外泄
