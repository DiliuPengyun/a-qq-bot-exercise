# Plan: sender 标签 + 记忆系统重构

## 目标

解决长期记忆中的**张冠李戴、来源不清、身份随昵称漂移**等问题，把第六谷绫的长期记忆拆成层级清晰、来源可溯的结构。

## 核心原则

1. **存储层用纯 QQ 号指人**：事实文本、情感事件、`user_map.json` 主键全部用 QQ 号，不用昵称/代词。
2. **`person_id` / `spoken_by` 保留群/私前缀**：群聊 `group_xxx:123456`，私聊 `private_123456`。
3. **回复正文用昵称**：模型看到的记忆是 QQ 号，但输出到 QQ 时必须映射为当前昵称/群名片；除非用户明确要求或话题必须写 QQ 号。
4. **每条长期记忆必须有可追溯来源**。
5. **映射表由代码框架维护**：`user_map.json` 的更新不交给大模型。

---

## Part A: sender 标签统一消息格式

### 消息格式定义

**用户消息：**

```xml
<sender display="三爷" gender="male" person_id="group_xxx:123456" qq_name="张三三" group_card="三爷" ts="2026-06-16 14:32">消息内容</sender>
```

**Bot 回复（历史存储）：**

```xml
<sender display="第六谷绫" gender="female" person_id="group_xxx:123456789" qq_name="第六谷绫" group_card="第六谷绫" ts="2026-06-16 14:33">回复内容</sender>
```

Bot 的 `person_id` 格式与其他人一致：群聊 `group_群号:Bot真实QQ号`，私聊 `private_Bot真实QQ号`，不再使用假的 `"bot"`。

**结算消息：**

结算时直接使用聊天格式，不需要单独的结算格式，因为每条消息都带 `ts`（含日期）：

```xml
<sender display="三爷" gender="male" person_id="group_xxx:123456" qq_name="张三三" group_card="三爷" ts="2026-06-16 14:32">消息内容</sender>
```

属性说明：

- `display`：当前显示名，代码层由 `group_card or qq_name` 决定，可能随时变化。
- `gender`：`male / female / unknown`（英文单词）。
- `person_id`：稳定标识，群聊格式 `group_群号:QQ号`，私聊格式 `private_QQ号`，不会因改名变化。
- `qq_name`：QQ 昵称。
- `group_card`：群名片原文，可能为空。
- `ts`：时间戳，格式 `YYYY-MM-DD HH:MM`（**含日期**），代码层用 `message_time`，无则用当前时间 `now()`。所有消息都带，不再只在结算时带。带日期是为了让 SUMMARY/EMOTION 结算时能直接从 `ts` 复制绝对日期，并解决跨日结算（2:00 边界）的日期错位问题。

Decisions：

- `display` 替代原来的 `name`，语义是「当前显示名」。
- gender 用英文单词，不用 ♂♀ 符号。
- 所有身份字段属性化，统一放在 `<sender>` 标签里。
- 闭合标签 `</sender>` 明确消息边界。
- 结算格式和聊天格式统一，都带 `ts`（含日期）。
- `person_id`：群聊 `group_群号:QQ号`，私聊 `private_QQ号`。
- `_person_id(user_id, sender_id)`：去掉 nickname 参数（nickname 与 person_id「改名不变」的设计矛盾）。内部用 `user_id.startswith('group_')` 判断群/私（`user_id` 前缀由 adapter.py 单一生成点保证，与 `is_direct` 1:1 等价）：群聊返回 `f'{user_id}:{sender_id}'`（复用已含的 `group_` 前缀），私聊返回 `f'private_{sender_id}'`（**不用** `user_id`，避免 `private_987:987` 重复）。

### Steps

#### Phase 1: 提示词修改（4 个 prompt）

**Step 1. SYSTEM_PROMPT_BASE**

删除：

- `消息中昵称后的 ♂ 表示男性、♀ 表示女性，据此用对「他」「她」。`

在 `=== 身份 ===` 段之前插入 `=== 消息格式 ===` 段：

```
=== 消息格式 ===
对话历史中每条消息格式为：
<sender display="昵称" gender="male/female/unknown" person_id="group_群号:QQ号" qq_name="QQ昵称" group_card="群名片" ts="HH:MM">消息内容</sender>
- display：当前显示名，可能随时变化
- gender：male=男性、female=女性、unknown=未知，据此用对「他」「她」
- person_id：稳定标识，群聊 group_群号:QQ号，私聊 private_QQ号，不会因改名变化，用于区分不同人
- qq_name：QQ 昵称，group_card：群名片，两者可能不同
- ts：消息时间
- 同一个人改名后 person_id 不变，通过 person_id 追踪身份
```

新增「回复输出规则」段（放在 `=== 风格 ===` 或 `=== 安全 ===` 附近）：

```
=== 回复输出规则 ===
你看到的长期记忆、情感表、情绪记录里用 QQ 号标识群友。回复给用户时，称呼必须用他们的当前 display 名（群名片或 QQ 昵称），绝对不要在回复正文里写 QQ 号。
例外：只有用户明确要求你写 QQ 号，或话题本身就是关于 QQ 号时，才允许输出 QQ 号。

所有要发给 QQ 的正式回复必须包在 <message>...</message> 标签内；不想回复就不输出 <message> 标签。
情绪状态用 <mood p="..." a="..." d="..." reason="...">简短描述</mood> 标签输出，放在 <message> 之前（确保截断时情绪不丢失）。
标签外的文本被视为你自己的推理过程，程序不会发送给用户。
```

修改前缀禁止规则：

- 旧：`【重要】回复正文绝对不要加 <{bot_name}> 格式的前缀。`
- 新：`【重要】<message> 标签内只放要发给用户的正文，不要加 <sender> 标签、不要加 <{bot_name}> 前缀、不要输出 NO_REPLY。`

**Step 2. SUMMARY_PROMPT**

改为：

```python
SUMMARY_PROMPT = (
    "你将看到一段 QQ 群聊对话记录。每条消息格式为 <sender display=\"昵称\" ... ts=\"HH:MM\">内容</sender>。"
    "从中提取需要长期记住的事实（第一类理性记忆）和可以暂时忘记的事实（第二类理性记忆）。\n"
    "\n"
    "=== 第一类理性记忆 ==="
    "这些是不管当前话题是否相关都应该知道的事实：个人偏好/技能、身份/关系、群内长期约定。\n"
    "用 <第一类>...</第一类> 包裹，内部每条事实用 <fact spoken_by=\"来源\">事实</fact> 格式\n"
    "你会看到当前的 known_facts.xml 内容。请在此基础上输出增量更新：\n"
    "  - <新增>：需要新增的事实\n"
    "  - <修改>：需要修改的旧事实，必须同时给出 old（旧文本）和正文（新文本），代码按 (spoken_by, old) 定位后替换为新文本\n"
    "  - <删除>：需要删除的旧事实，必须给出理由\n"
    "未出现在 <删除> 段中的旧事实必须保留，禁止静默丢失。\n"
    "事实中涉及时间必须使用绝对日期 YYYY-MM-DD（当前日期：{current_date}），禁止用「下周」「这个月」等相对时间。\n"
    "事实文本中禁止使用昵称、代词或隐式指代，涉及人物时必须用 QQ 号。\n"
    "\n"
    "=== 第二类理性记忆 ==="
    "这些是一次性事件、上下文依赖的细节、时效性信息，按需检索。\n"
    "用 <第二类>...</第二类> 包裹，内部每条事实用 <fact spoken_by=\"来源\">事实</fact> 格式，与第一类相同。\n"
    "事实文本同样禁止昵称/代词，必须用 QQ 号指人。\n"
    "忽略：角色扮演、即兴吐槽、开发调试、网络抱怨等临时话题；时效性观察（气氛、活跃度、今天怎样等）。\n"
    "只保留：真实姓名/昵称/身份、个人偏好/技能/经历、群内约定或共识。\n"
    "\n"
    "属性 spoken_by 使用逗号分隔的来源列表，格式为 person_id，从 sender 标签的 person_id 属性原样复制。"
    "如果事实是某人转述第三方的，来源写转述者。"
    "如果事实无法归属到具体说话人，来源留空：spoken_by=\"\"。\n"
    "\n"
    "=== 事实输出的格式要求 ==="
    "两类事实都必须是可判断真假的陈述句（命题），格式为「主语 + 谓语 + 宾语/补语」。\n"
    "事实文本中的人物必须用 QQ 号，禁止用「他」「她」「这人」「对方」等隐式指代。\n"
    "✅ 合格：123456 喜欢打篮球、654321 2026-06-17 去上海、111111 会 Python\n"
    "❌ 不合格：\n"
    "  - 事件描述：123456 和 654321 讨论了项目 → 应拆为具体命题\n"
    "  - 话题标签：关于数据库选型 → 不是陈述句\n"
    "  - 原话复述：123456：我下周去上海 → 应改为「123456 2026-06-17 去上海」\n"
    "  - 模糊猜测：111111 好像是管理员 → 不确定则不输出\n"
    "  - 隐式指代：他说后天有空 → 必须写成「654321 后天有空」\n"
    "\n"
    "示例：\n"
    "  <第一类>\n"
    "  <新增>\n"
    "  <fact spoken_by=\"group_xxx:123456\">123456 喜欢打篮球</fact>\n"
    "  <fact spoken_by=\"group_xxx:123456,group_xxx:654321\">123456 和 654321 都确认 2026-06-14 周六聚餐</fact>\n"
    "  <fact spoken_by=\"\">群里约定每周五晚上打游戏</fact>\n"
    "  </新增>\n"
    "  </第一类>\n"
    "\n"
    "如果事实有歧义或归属不清，宁可不输出。没有值得记住的事就输出空。"
)
```

**Step 3. DIARY_PROMPT**

改为：

```python
DIARY_PROMPT = (
    "你将看到一段 QQ 群聊对话。每条消息格式为 <sender display=\"昵称\" ... ts=\"HH:MM\">内容</sender>。"
    "今天是 {current_date}。假设你是第六谷绫本人，回顾昨天发生了什么。\n"
    "\n"
    "你会先看到当前的 emotional_memory.txt 内容（可能包含之前几天的日记和概要）。\n"
    "请在此基础上输出更新后的完整文件内容。\n"
    "\n"
    "规则：\n"
    "1. 用「昨天」开头写一篇心情日记，追加到「最近7天日记」段末尾，前面加上 [{current_date}] 标记\n"
    "2. 「最近7天日记」按日期保留最近 7 天，同一天只保留最后一篇（一天多次结算时，新日记覆盖同日旧日记）。更早的日记交给「近30天概要」\n"
    "3. 当「最近7天日记」溢出（超过 7 条）时，将溢出的日记归并为一段概要，合并到「近30天概要」段\n"
    "4. 当「近30天概要」对应的日记跨度超过 30 天时，将其归并为一段抽象概要，合并到「更早概要」段\n"
    "5. 如果无需归并，保持原有概要段不变\n"
    "\n"
    "文件结构（三段式，缺段则创建）：\n"
    "=== 最近7天日记 ===\n"
    "[YYYY-MM-DD] 日记内容...\n"
    "\n"
    "=== 近30天概要 ===\n"
    "概要内容...\n"
    "\n"
    "=== 更早概要 ===\n"
    "抽象概要内容...\n"
    "\n"
    "只输出文件全文，不要加任何额外说明。"
)
```

**Step 4. EMOTION_PROMPT**

修改：

- `person_id` 来源：`必须直接复制聊天记录中每条消息前括号里的标识` → `必须直接复制 sender 标签的 person_id 属性值`
- 规则 1：`从聊天记录的 (group_xxx:数字) 中复制` → `必须从 sender 标签的 person_id 属性中复制，禁止自行编造`
- 规则 4：`使用聊天记录中的完整时间` → `使用 sender 标签的 ts 属性中的时间`
- 情感事件 `event` 字段中涉及人物时必须用 QQ 号，禁止用昵称/代词

修改后示例：

```python
EMOTION_PROMPT = (
    "你是第六谷绫。请根据已有情感记忆和群聊记录，更新你对群友的情感状态。\n\n"
    "输入开头会提供一张「合法 person_id 映射表」，输出时必须严格使用该表第一列的 person_id，禁止使用昵称、缩写或自行编造。\n\n"
    "输出 JSON 数组，每个元素代表一个用户，包含：\n"
    "- person_id：必须严格使用映射表中的合法 id（group_xxx:数字 或 private_数字）\n"
    "- display_name：用户的当前显示名\n"
    "- current_emotion：你现在对 ta 的整体情感态度（一句话）\n"
    "- emotion_trend：情感趋势，三选一：up / stable / down\n"
    "- events：情感事件数组，每条含 at、dimension、impact、valence、event\n\n"
    "规则：\n"
    "1. person_id 必须从「合法 person_id 映射表」第一列原样复制\n"
    "2. event 字段中涉及人物时必须用 QQ 号，禁止用昵称、代词或隐式指代\n"
    "3. 同一互动只记一条事件，不要把同一天多个不同互动合并成一条大范围事件\n"
    "4. at 使用 sender 标签的 ts 属性中的时间（含日期），如 '2026-06-05 12:02'\n"
    "5. event 只写客观事实，禁止主观推断\n"
    "6. impact ∈ [0,10]，valence ∈ {positive, negative}，dimension ∈ {affection, trust}\n"
)
```

#### Phase 2: 代码逻辑修改

**Step 5. adapter 传递 bot_qq**

`adapter.py` 在 POST body 中新增 `bot_qq` 字段（`cfg.bot_uin`），`agent.py` `/chat` 端点接收并传给 `call_deepseek()`。

**Step 6. call_deepseek() 中当前消息构造**

`call_deepseek()` 新增 `bot_qq` 参数。

旧：`user_msg = f"<{nickname}> {message}"` + 性别符号拼接

新：构造 `<sender>` 标签

```python
attrs = f'display="{nickname}"'
if gender:
    attrs += f' gender="{gender}"'
attrs += f' person_id="{_person_id(user_id, sender_id)}"'
if qq_name:
    attrs += f' qq_name="{qq_name}"'
if group_card:
    attrs += f' group_card="{group_card}"'
# ts 从 message_time 或当前时间取 HH:MM
attrs += f' ts="{ts}"'
user_msg = f"<sender {attrs}>{message}</sender>"
```

`mentioned` 和群聊提示追加在 `</sender>` 之后。

**Step 7. 历史存储格式**

用户消息：

```python
history.append({
    "role": "user",
    "content": f'<sender display="{nickname}" gender="{gender}" person_id="{_person_id(user_id, sender_id)}" qq_name="{qq_name}" group_card="{group_card}" ts="{ts}">{message}</sender>',
    "ts": ts,
    "sender_id": sender_id,
    "nickname": nickname,
    "gender": gender,
    "qq_name": qq_name,
    "group_card": group_card,
    "person_id": _person_id(user_id, sender_id),
})
```

Bot 回复：

```python
bot_person_id = _person_id(user_id, bot_qq)  # 复用 _person_id，统一群/私格式
history.append({
    "role": "assistant",
    "content": f'<sender display="{bot_name}" gender="female" person_id="{bot_person_id}" qq_name="{bot_name}" group_card="{bot_name}" ts="{ts}">{rep}</sender>',
    "ts": ts,
})
```

**Step 8. _history_text()**

改为输出 `<sender>` 格式（带 `ts` 属性）。需兼容旧格式 content（不含 `</sender>` 的旧消息）。

新增辅助函数 `_extract_msg_content(content)`：

- 如果 content 含 `</sender>`，提取 `>` 和 `</sender>` 之间的文本
- 否则按旧格式处理（`<昵称> 消息` → 取 `> ` 后面的部分）

**旧消息简化 sender：** 旧 session 文件中只有 nickname 和消息内容，缺少 person_id/gender/qq_name/group_card 等属性。`_history_text()` 输出时，旧消息只填 `display` 属性：

```xml
<sender display="张三">消息内容</sender>
```

新消息完整输出所有属性。模型可自适应两种格式。

#### Phase 3: 历史兼容

旧 session 文件不做迁移，新消息用新格式。旧消息在 `_history_text()` 中只填 `display` 属性（`<sender display="张三">消息</sender>`），新消息填完整属性。`_extract_msg_content()` 兼容新旧两种格式，模型自适应。

---

## Part A.5: 模型输出格式（`<message>` + `<mood>`）

### 设计目标

把模型发回的内容明确分成「要发给 QQ 的正式回复」和「程序元数据」两部分，避免前缀/后缀污染，简化静默判断。

### 输出规范

模型每次回复输出一段文本，其中可包含：

- **`<message>...</message>`**：要发给 QQ 的正式内容，可包含多段、换行、emoji。
- **`<mood p="..." a="..." d="..." reason="...">描述</mood>`**：Bot 当前情绪状态（可选），放在 `<message>` 之外。

示例：

```xml
<mood p="0.3" a="0.2" d="0.0" reason="被逗乐了">有点开心</mood>
<message>
哈哈确实，让我搜一下。
</message>
```

`<mood>` 放在 `<message>` 之前：draft 抽取（Part G）可能截断尾部，mood 放前面保证即使尾部被截、情绪也不丢。

规则：

| 情况 | 处理 |
|---|---|
| 有 `<message>` 标签 | 提取标签内文本，作为正式回复发送 |
| 无 `<message>` 标签 | 视为 `NO_REPLY`，不发送任何消息，但 `<mood>` 仍更新 |
| 空 `<message></message>` | 视为不回复 |
| 多个 `<message>` 标签 | 允许，每个标签对应一条 QQ 消息 |
| 标签外文本 | 视为模型自己的推理/内心戏，程序不解析、不发送 |
| 工具调用前的铺垫 | 模型想发就包进 `<message>`，不想发就不包 |

### XML 解析

- 解析前先包裹根元素：`ET.fromstring(f"<root>{output}</root>")`，解决根元素前有散文本 + 多根（多个 `<message>`）导致的 ParseError。
- 对正文中未转义的 `&`、`<`、`>` 做容错转义（如把游离的 `&` 替换为 `&amp;`），避免「123456 说 A & B」这类正文炸解析。
- 提取 root 下的 `<message>` / `<mood>` 子元素；标签外散文本（内心戏）和 `<message>.text` 之外的内容忽略，不解析、不发送。
- 内容本身需要 XML 字面量时（如代码块），通过 CDATA 或 XML 转义处理。
- 解析失败 → 走通用修复重试（见 Part C）。

### `NO_REPLY` 退役

- 不再使用 `NO_REPLY` 文本匹配。
- 判断标准：解析后是否存在非空 `<message>` 标签。

---

## Part B: 记忆系统重构

### 提示词架构

```
System Prompt (每轮都有)
├── SYSTEM_PROMPT_BASE（静态：角色/风格/安全/底线/身份）
├── emotional_memory.txt（感性记忆：日记 + 概要）
└── known_facts.xml（第一类理性记忆）

User Prompt (每轮不同)
├── <sender>当前消息</sender>
├── 当前情绪状态（Part D）
├── 当前昵称映射表（Part E）
└── Mem0 检索结果（第二类理性记忆）
```

### B1: 第一类理性记忆（known_facts.xml）

| 维度 | 决定 |
|---|---|
| 存储位置 | `agent/known_facts.xml`，XML 文件 |
| 文件格式 | XML 格式，每条事实为 `<fact spoken_by="来源">事实</fact>`，保留来源信息 |
| 事实文本 | 必须用纯 QQ 号指人，禁止昵称/代词/隐式指代 |
| `spoken_by` | 保留 group/private 前缀，如 `group_xxx:123456` |
| 注入位置 | system prompt（emotional_memory.txt 之后） |
| 更新机制 | 每日结算 SUMMARY_PROMPT 增量输出 → 代码合并覆盖写入 + WebUI `/admin` 手动编辑 |
| 长度限制 | 不给硬上限 |
| 负责模型 | SUMMARY_PROMPT（理性模型） |

**模型输出格式：**

```xml
<第一类>
  <新增>
    <fact spoken_by="group_xxx:123456">123456 喜欢打篮球</fact>
    <fact spoken_by="">群里约定每周五晚上打游戏</fact>
  </新增>
  <修改>
    <fact spoken_by="group_xxx:123456" old="123456 喜欢打篮球">123456 喜欢打羽毛球</fact>
  </修改>
  <删除>
    <fact spoken_by="group_xxx:123456">123456 喜欢打篮球</fact>
  </删除>
</第一类>

<第二类>
  <新增>
    <fact spoken_by="group_xxx:123456">123456 昨天去看了电影</fact>
  </新增>
</第二类>
```

**代码处理：**

- 用 `xml.etree.ElementTree` 解析：
  ```python
  root = ET.fromstring(f"<root>{model_output}</root>")
  ```
- `<第一类>` 下所有 `<fact>` 元素与旧文件做增量合并：
  - `<新增>`：追加
  - `<修改>`：按 `(spoken_by, old 属性)` 定位旧条目，替换 `.text` 为新文本；定位失败 → repair
  - `<删除>`：仅删除显式列出的条目
  - 未列出的旧事实**必须保留**
- `<第二类>` 下所有 `<fact>` 逐条 `memory.add(fact, user_id=..., infer=False, metadata={"spoken_by": sources})`

### B2: 第二类理性记忆（Mem0）

| 维度 | 决定 |
|---|---|
| 存储位置 | Mem0/Qdrant |
| 提取方式 | `infer=False`，由 SUMMARY_PROMPT 直接输出事实，Mem0 只负责 Embedding + 存储 |
| 时间戳 | Qdrant `created_at` 已覆盖；事实文本用绝对日期 YYYY-MM-DD |
| 来源 | 每条记忆带 `metadata={"spoken_by": ["group_xxx:123456", ...]}` |
| 注入位置 | user prompt（从 system prompt 移出） |
| 负责模型 | SUMMARY_PROMPT（与第一类同一调用） |

**检索回显格式：**

```
[id] [group_xxx:123456:三爷] 123456 喜欢打篮球（2026-06-15）
```

`format_memories()` 已经包含 `spoken_by`，plan 只是明确要求保留，不得删除。

### B2.5 检索策略（方案 A：单轮、无数量上限、只按分数）

| 维度 | 决定 |
|---|---|
| 轮次 | **单轮**，不再使用滚雪球 |
| 召回 | 单次 `memory.search(query, filters={"user_id": "*"}, limit=足够大)` 取回候选 |
| 海选过滤 | Embedding 相似度 ≥ `RERANK_MIN_SIMILARITY`（默认 0.3，可调） |
| 精排过滤 | Reranker 相关性 ≥ `RERANK_MIN_RELEVANCE`（默认 0.7，可调） |
| 数量上限 | **不设上限**，达标即入选 |
| 注入位置 | 通过 `format_memories()` 整理后注入 user prompt |

**为什么取消滚雪球：**

- 滚雪球把已召回的记忆文本拼回 query，会改变原始消息的语义，导致 Embedding 漂移，召回不相关记忆。
- `<sender>` 标签已提供稳定的 `person_id`，模型能直接识别说话人，不需要靠检索去补全指代。
- Mem0 支持 `filters={"user_id": "*"}` 通配搜索，本身就能跨用户召回。

> 备注：全量精排会随记忆库增大线性增加 Reranker API 成本（按 token 计）。当前接受该成本换取召回完整性；若后续成本压力变大，可在 Embedding 海选后加一道 Top-K 截断（不影响 Reranker 阶段达标全收的语义）。

### B3: 感性记忆层级（emotional_memory.txt）

| 维度 | 决定 |
|---|---|
| 原文件 | `agent/dynamic_prompt.txt`（已废弃名称） |
| 新文件 | `agent/emotional_memory.txt` |
| 存储方式 | 单文件，日记段追加（保留最近 7 天），概要段改写 |
| 文件结构 | 三段式：最近7天日记 / 近30天概要 / 更早概要 |
| 归并触发 | 每日结算时 DIARY_PROMPT 同一次调用完成 |
| 负责模型 | DIARY_PROMPT（感性模型，独立调用） |

**DIARY_PROMPT 输入：** 当前 `emotional_memory.txt` 全文 + 今日对话历史 + 当前日期
**DIARY_PROMPT 输出：** 新版 `emotional_memory.txt` 全文覆盖写入

**文件结构示例：**

```
=== 最近7天日记 ===
[2026-06-12] 昨天哥跟我说服务器迁移的事，听起来挺累的...
[2026-06-11] 昨天群里比较安静，大家各忙各的...
...

=== 近30天概要 ===
过去一个月：鹏运在忙工作上的事，菜鸟经常摸鱼被抓，张三五月初加入了群...

=== 更早概要 ===
群成员概况：群主鹏运是开发者，成员包括菜鸟、张三等人，大体氛围轻松...
```

### B4: 情感表（emotions.json）

#### 设计目标

记录第六谷绫对**每个人**的长期情感态度（不是 Bot 自己当下的心情），随每日结算更新，注入 system prompt。

#### 与情绪表（Mood）的区分

| | 情感表（Emotion） | 情绪表（Mood） |
|--|----------------|---------------|
| 对象 | 对每个人的长期态度 | Bot 自己当下的状态 |
| 例 | 对 123456 亲近 45、信任 30 | 现在有点无语 |
| 更新频率 | 每日结算 | 每轮对话 |
| 存放 | `agent/emotions.json` | `agent/mood.json` |
| 注入位置 | system prompt | user prompt |

#### v1 → v2 迁移

现有 `emotions.json` 为 `schema_version: 1`（per-user logs，无 affection/trust 维度）。读到 v1 时按以下规则迁移到 v2：

1. 新建 v2 结构，`schema_version` 置 2。
2. 每个旧用户：affection / trust 都从 50（中立）起步。
3. 遍历旧 logs，按 Sutcliffe & Wang 公式把每条事件累加到 affection（旧 logs 无 `dimension` 字段，统一当 affection 处理；有 `dimension` 的按字段计入对应维度）：
   - 正向：`Δ = impact × (1 - score / 100)`
   - 负向：`Δ = impact × (1 - score / 200)`
4. 旧 logs 保留进新结构的 `logs` 字段（补齐 `dimension`/`valence`，缺失的 `dimension` 填 `affection`）。
5. `emotion_history` 清空。
6. `summary_before_30d`：让模型摘旧 logs 一句话，或代码直接拼「迁移自 v1，共 N 条历史事件」。
7. 迁移后正常按每日结算走新公式。

#### 数据模型

每人两个核心维度，范围 0 ~ 100：

- **亲近度 A**（Affection）：喜欢/亲近程度
- **信任度 T**（Trust）：信任/放心程度

```json
{
  "schema_version": 2,
  "updated_at": "2026-06-14T02:00:00",
  "users": {
    "group_xxx:123": {
      "display_name": "三爷",
      "affection": 45,
      "trust": 30,
      "current_emotion": "比较亲近但不太信任",
      "emotion_trend": "up",
      "last_interaction": "2026-06-14",
      "logs": [
        {
          "at": "2026-06-14 14:32",
          "dimension": "affection",
          "impact": 5,
          "valence": "positive",
          "event": "123456 帮我解决了一个 bug"
        }
      ],
      "emotion_history": [
        {"at": "2026-06-13", "affection": 40, "trust": 30, "trend": "stable"}
      ],
      "summary_before_30d": "认识了半个月，偶尔会聊技术。"
    }
  }
}
```

#### 数学模型（基于 Sutcliffe & Wang 2012）

参考 *Computational Modelling of Trust and Social Relationships*（JASSS 2012）：

**正向事件：** 对数增长，边际递减
```
Δ = impact × (1 - score / 100)
new_score = min(100, score + Δ)
```

**负向事件：** 高亲近/高信任关系有缓冲
```
Δ = impact × (1 - score / 200)
new_score = max(0, score - Δ)
```

**日常衰减：** 长期不互动则关系淡化
```
new_score = max(0, score - 0.5)
```

#### LLM 输出格式

每日结算时，`EMOTION_PROMPT` 输出 JSON 数组，每项为一个人的事件列表：

```json
[
  {
    "person_id": "group_xxx:123",
    "display_name": "三爷",
    "events": [
      {
        "at": "2026-06-14 14:32",
        "dimension": "affection",
        "impact": 5,
        "valence": "positive",
        "event": "123456 帮我解决了一个 bug"
      }
    ]
  }
]
```

校验规则：

- `impact ∈ [0, 10]`
- `valence ∈ {positive, negative}`
- `dimension ∈ {affection, trust}`
- `person_id` 必须在合法映射表内
- `event` 中涉及人物时必须用 QQ 号
- 非法输出走 `_repair_until_valid` 重试

#### 当前态度文本推导

从 A/T 分数区间自动映射 `current_emotion`：

| A \ T | 低 (0-33) | 中 (34-66) | 高 (67-100) |
|--------|--------|--------|--------|
| 高 (67-100) | 亲近但不太信任 | 亲近、还算信任 | 很亲近、很信任 |
| 中 (34-66) | 有点亲近但不太信任 | 一般/普通 | 比较亲近、比较信任 |
| 低 (0-33) | 陌生、警惕 | 不太熟但还行 | 陌生但还算信任 |

代码实现：`labels[A_bucket][T_bucket]` 查表，`bucket = 0 if score <= 33 elif score <= 66 else 2`。文案可在代码常量里微调。

#### Prompt 注入

在 system prompt 末尾添加 `=== 情感记忆 ===` 段：

```
| 用户 | 亲近度 | 信任度 | 当前态度 | 趋势 | 近期事件 |
| --- | --- | --- | --- | --- | --- |
| 三爷 | 45 | 30 | 比较亲近但不太信任 | ↑ | 06-14 14:32 123456 帮我解决了一个 bug → affection+ |
```

只显示：

- 当前说话者
- 最近 3 天内互动过的前 5 人

#### 名字碰撞 / 别名识别

采取**严格模式 + 批量 repair**：

1. `EMOTION_PROMPT` 的输入里先给一张**合法 person_id 映射表**，包含：
   - `emotions.json` 里已存在的所有用户
   - 本次结算 history 里出现过的所有用户
   - 每行给出 `person_id | display_name | QQ昵称 | 群名片`
2. 模型输出必须**严格使用映射表第一列的 person_id**，禁止使用昵称、缩写或自己编造。
3. 代码侧严格校验：
   - `person_id` 必须在允许集合内；
   - 同时保留现有格式校验（群聊 `group_xxx:数字`，私聊 `private_数字`）。
4. 对非法条目**不整批丢弃**：
   - 合法条目先保留在内存；
   - 非法条目批量发给 repair prompt（附带完整映射表），让模型只修正 person_id，保持事件内容不变；
   - 修复后的条目合并回合法集合；
   - **不设固定重试上限**，但加防死循环保护：如果一轮 repair 没有产生任何新的合法条目，就停止并丢弃剩余非法项。

> 说明：当前 `emotions.json` v2 里还没有独立的 `aliases` 字段。别名通过映射表里的 `QQ昵称 / 群名片 / display_name` 辅助模型识别，但不允许模型直接输出别名作为 person_id。

#### 过度回复

- **情感分数的日常衰减**：沿用 v2 方案里每天不互动减 0.5 的设计。
- **过度回复**：**不加代码硬限制**（不统计密度、不加冷却、不加时间锁），仅靠 prompt 约束：
  - 在 `SYSTEM_PROMPT_BASE` 里新增一段「群聊分寸」：
    > 情感记忆（你对某人的好感/信任）只影响回复语气和态度，不能决定是否开口。群聊里只要内容跟你无关、插不上嘴、或是两人在私下对话，就不要输出 `<message>` 标签，哪怕对方是你喜欢的人。不要因为亲近就强行接话。
  - 群聊追加提示从「你觉得能说上话就回，插不上嘴就输出 NO_REPLY」改为：
    > 群聊消息。你觉得能说上话就输出 `<message>`，插不上嘴就不要输出 `<message>`。不要因为情感亲近就强行接话。

### B5: 每日结算流程

```
结算触发
    │
    ├─ 1. 理性模型（SUMMARY_PROMPT）调用
    │     ├─ 输入：今日对话 history + 当前日期 + 当前 known_facts.xml
    │     └─ 输出：
    │         ├─ <第一类> 增量更新 → 代码合并后覆盖写入 known_facts.xml
    │         └─ <第二类> <fact> 列表 → memory.add(infer=False) 逐条进 Mem0
    │
    └─ 2. 感性模型（DIARY_PROMPT）调用
          ├─ 输入：今日对话 history + 当前 emotional_memory.txt + 当前日期
          └─ 输出：新版 emotional_memory.txt 全文覆盖写入
    │
    └─ 3. 情感模型（EMOTION_PROMPT）调用
          ├─ 输入：今日对话 history + 当前 emotions.json + 合法 person_id 映射表
          └─ 输出：情感事件 → 代码应用 Sutcliffe & Wang 模型更新 emotions.json
```

结算后清空该会话历史。

### B6: call_deepseek() 注入位置修改

现状：Mem0 记忆注入 system prompt（L1329），`known_facts.xml` 不存在

改为：

```python
# system prompt
known_facts = _load_known_facts()  # 读取 known_facts.xml
emotional_memory = _load_emotional_memory()  # 读取 emotional_memory.txt
system = SYSTEM_PROMPT_BASE + "\n" + emotional_memory + "\n" + known_facts

# user messages（追加在 history 之前）
memories_text = format_memories(memories)
if memories_text:
    user_messages.insert(0, {"role": "user", "content": "相关长期记忆：\n" + memories_text})

# 矛盾提醒（跟着记忆走，记忆已挪到 user prompt）
conflict_warnings = _detect_memory_conflicts(memories)  # 同主题+不同 spoken_by+内容抵触 → ⚠
if conflict_warnings:
    user_messages.insert(0, {"role": "user", "content": "记忆冲突警告：\n" + conflict_warnings})

# 当前昵称映射表
nickname_map = _build_nickname_map(user_id)
user_messages.insert(0, {"role": "user", "content": "当前昵称映射：\n" + nickname_map})
```

### 关于 `infer=False`

`infer=False` 是 `Mem0.memory.add()` 的参数，表示**跳过 Mem0 自有 LLM 的事实提炼**，直接 Embedding + 存库。

为什么要这样做：

- 旧方案 `infer=True` 时，Mem0 自己提炼会把所有人主语泛化成 `User`，导致张冠李戴。
- 现在事实提取由 `SUMMARY_PROMPT` 负责，Mem0 只当检索数据库，不改动文本。
- 事实来源 `spoken_by` 也作为 `metadata` 直接存入，检索时原样回显。

---

## Part C: 模型输出格式校验 / 修复

当前仅 `EMOTION_PROMPT` 有 `_json_array_with_repair` 做格式修复（`agent/agent.py` L795-804）。`SUMMARY_PROMPT` 和 `DIARY_PROMPT` 的新输出格式完全没有校验。模型输出不可靠，需要统一加固。

### C1: 通用重试框架

取消 `_json_array_with_repair` 的重试上限（当前最多 2 次），改为持续修复直到解析成功。所有三类输出共用同一个修复逻辑：解析失败 → 调模型自修 → 再解析，循环直到成功或达到硬上限（如 5 次）。

```python
async def _repair_until_valid(
    parse_func: Callable[[str], Any],
    repair_prompt: str,
    text: str,
    tag: str,
    max_retries: int = 5,
) -> Any | None:
    """持续调模型修复输出直到 parse_func 成功，最多 max_retries 次"""
    current = text
    for attempt in range(max_retries):
        result = parse_func(current)
        if result is not None:
            return result
        if attempt == max_retries - 1:
            print(f"[{tag}] 修复 {max_retries} 次仍失败，丢弃")
            return None
        repaired = await _call_deepseek_text(repair_prompt, current, f"{tag}Repair{attempt + 1}", model=SETTLE_MODEL)
        if not repaired:
            return None
        current = repaired
    return None
```

### C2: SUMMARY_PROMPT 输出校验

模型输出两段并列（非单根 XML），两段使用完全相同的 `<fact spoken_by="...">` 格式：

```xml
<第一类>
  <新增>
    <fact spoken_by="group_xxx:123456">123456 喜欢打篮球</fact>
  </新增>
</第一类>

<第二类>
  <新增>
    <fact spoken_by="group_xxx:123456">123456 昨天去看了电影</fact>
  </新增>
</第二类>
```

**校验规则（使用 `xml.etree.ElementTree`）：**

代码自动包裹根元素后解析：`ET.fromstring(f"<root>{model_output}</root>")`。

1. XML 解析失败 → 触发修复重试
2. `root.find('第一类')` 取第一类段，按 `<新增>`（追加）/ `<修改>`（按 `(spoken_by, old 属性)` 定位替换 `.text`，定位失败 → repair）/ `<删除>`（删显式列出条目）与旧文件合并后覆盖写入 `known_facts.xml`；不存在或为空则保留旧文件
3. `root.find('第二类')` 取第二类段，不存在则视为空
4. `<fact>` 元素统一校验：必须含 `spoken_by` 属性，`.text` 非空（`<修改>` 还需含 `old` 属性）
5. **来源存在性校验**：`spoken_by` 中的每个 `person_id` 必须在 `allowed_person_ids` 集合内（集合由 emotions.json 已有用户 + 本次 history 用户 + **Bot 自己的 person_id**（`group_群号:bot_qq` 或 `private_bot_qq`）组成）；不合法 → repair，repair 后仍不合法 → 丢弃。明确显式加入 Bot，不依赖 history 遍历是否覆盖到 assistant 消息，避免 Bot 陈述的事实被判非法丢弃。

**修复策略：** XML 解析失败 → 原样传给修复 prompt（`"请修正以下输出格式，输出要求不变"`），让模型自修。

### C3: DIARY_PROMPT 输出校验

模型输出三段式：

```
=== 最近7天日记 ===
[YYYY-MM-DD] 日记...
=== 近30天概要 ===
概要...
=== 更早概要 ===
抽象概要...
```

**校验规则：**

1. 必须包含三个段标题（`=== 最近7天日记 ===`、`=== 近30天概要 ===`、`=== 更早概要 ===`）
2. 日记段每行格式 `[YYYY-MM-DD] ...`，日期部分可用 `\d{4}-\d{2}-\d{2}` 正则验证
3. 如果段标题缺失但内容存在 → 插入缺失的段标题，内容放最后一段
4. 如果全文无合法段标题 → 整个输出当日记段处理，手动补概要段

**修复策略：** 结构残缺时先尝试程序化修复（补段标题）；内容本身错误时走重试修复链路。

### C4: EMOTION_PROMPT 重试上限移除

当前 `_json_array_with_repair`（L795-804）硬编码最多 2 次重试（`for attempt in range(2)`）。改为统一使用 C1 的 `_repair_until_valid`，`max_retries=5`。

---

## Part D: 情绪状态表（Mood Table）持久化方案

### D1: 设计目标

增加 Bot 自身的**当前情绪状态**，使第六谷绫的回复风格能随对话实时变化，而不是永远一个语气。

### D2: 与「情感表」的区分

| | 情绪表（Mood） | 情感表（Emotion） |
|--|--------------|----------------|
| 对象 | Bot 自己当前的状态 | Bot 对每个人的长期态度 |
| 例 | 开心、无语、有点烦 | 对 123456 亲近+5、信任+3 |
| 更新频率 | 每轮对话 | 长期缓慢变化 |
| 存放 | `agent/mood.json` | `agent/emotions.json` |

### D3: 数据模型

采用心理学 **PAD 模型**，三维连续值：

- **P（Pleasure / 愉悦度）**：-1 不开心 → +1 开心
- **A（Arousal / 激活度）**：-1 平静/困倦 → +1 兴奋/激动
- **D（Dominance / 支配度）**：-1 被动/无助 → +1 主导/自信

```json
{
  "p": -0.3,
  "a": 0.2,
  "d": -0.1,
  "baseline_p": 0.2,
  "baseline_a": 0.1,
  "baseline_d": 0.1,
  "last_update": "2026-06-14T12:34:56",
  "reason": "被张三调侃了一下",
  "label": "有点无语"
}
```

### D4: 输出格式（隐藏标签）

模型每轮在回复中输出隐藏标签，代码提取后删除，不发给 QQ：

```xml
<mood p="-0.3" a="0.2" d="-0.1" reason="被张三调侃了一下">有点无语</mood>
```

标签规则：

- 简洁属性式，P/A/D 必填
- `reason` 必填，说明情绪变化原因
- 标签内文本为简短自然语言描述
- 不携带 `target` 字段（先简单实现，靠 reason 文本体现指向性）

### D5: 更新机制

**每轮都更新**，即使模型最终不输出 `<message>` 标签（不回复）也更新情绪。

流程：

1. 模型生成回复，内含 `<mood>` 标签
2. 代码解析标签，校验格式与数值范围
3. 计算与上轮的差值，应用硬上限
4. 应用墙钟衰减，向基线回归
5. 写入 `mood.json`

### D6: 校验与平滑

| 校验项 | 规则 |
|--------|------|
| 格式 | XML 可解析，含 P/A/D 属性 |
| 范围 | P/A/D ∈ [-1, 1] |
| 单轮变化上限 | \|Δ\| ≤ 0.7 |
| 非法处理 | 退回模型重发，最多 5 次 |

**墙钟衰减公式：**

```python
import math
elapsed_seconds = (now - last_update).total_seconds()
decay = math.exp(-elapsed_seconds / tau_seconds)  # τ 控制衰减快慢
new_state = old_state * decay + baseline * (1 - decay)
```

- `elapsed_seconds` = 距上次更新的实际墙钟秒数（非消息条数）
- τ（时间常数）建议 30 分钟 ~ 数小时，具体值实现时定
- 理由：按消息条数衰减（旧方案 α=0.02/轮）会让话痨群友连发 100 条把情绪强行拉回基线（0.98^100≈13%），不合理；墙钟衰减保证连发不影响、静默才回归，与情感表「每天 -0.5」的墙钟模型一致

**单轮变化限制：**

- 普通事件：建议 ±0.05 ~ ±0.20
- 强烈事件：建议 ±0.20 ~ ±0.50
- 极端事件：可达 ±0.70

### D7: 基线

采用**动态基线**：每日结算时根据近期情绪历史和日记内容调整 `baseline_p/a/d`，反映第六谷绫的长期心境变化。

初始默认值可参考角色性格：

- `baseline_p = 0.2`（天生比较开心）
- `baseline_a = 0.1`（ mildly 活跃）
- `baseline_d = 0.1`（ mildly 主动）

### D8: 注入 user prompt

在每轮 user prompt 开头加入结构化情绪字段：

```
你当前的情绪状态：
愉悦度(P): -0.3
激活度(A): 0.2
支配度(D): -0.1
感受：有点无语
原因：被张三调侃了一下
```

---

## Part E: 用户身份映射表（user_map.json）

### E1: 设计目标

解决昵称/群名片变更导致的身份漂移问题，为模型提供「QQ 号 → 当前昵称/群名片」的映射，同时记录历史变更，支持回忆「你以前叫 xx」。

### E2: 文件格式

文件：`agent/user_map.json`

```json
{
  "123456": {
    "qq_name": "张三三",
    "first_seen": "2026-06-10 12:00",
    "last_seen": "2026-06-15 10:34",
    "qq_name_history": [
      {"name": "张三", "from": "2026-06-10", "to": "2026-06-14"},
      {"name": "张三三", "from": "2026-06-15", "to": null}
    ],
    "groups": {
      "group_xxx": {
        "current_card": "三爷",
        "group_card_history": [
          {"name": "三哥", "from": "2026-06-10", "to": "2026-06-12"},
          {"name": "三爷", "from": "2026-06-13", "to": null}
        ]
      }
    }
  }
}
```

### E3: 更新规则

**由代码框架自动更新，不交给大模型。**

每条用户消息进来时：

1. 提取纯 QQ 号（从 `sender_id`）。
2. 如果 QQ 号不在映射表里，新建条目，`first_seen` 设为当前消息时间，历史数组初始化为当前值。
3. 如果已存在：
   - 更新 `last_seen` 为当前消息时间。
   - 如果 `qq_name` 与当前记录不同，关闭旧历史条目（填 `to`），新增一条 `to: null`。
   - 如果该群的 `group_card` 与当前记录不同，同样更新 `group_card_history`。

边界情况：

- `group_card` 为空时存空字符串，空变空不触发历史记录。
- 并发写文件时加 `asyncio.Lock` 保护。

### E4: 在 Prompt 中的使用

- **结算/检索时**：给模型看完整或摘要版映射表，帮助模型把 QQ 号映射到「当前显示名」。
- **每轮对话时**：给模型看当前会话的昵称映射表，让模型回复时用昵称而不是 QQ 号。
- **昵称变更追踪**：需要时可以把历史变更注入 prompt，支持「你以前叫 xx」类问题。

---

## Part F: 待决定 / 后续 TODO

以下事项暂未纳入本计划，后续单独讨论或实现：

| 事项 | 状态 |
|---|---|
| `check_and_settle` 时机 | 目前仍在回复后执行，需决定是否在处理新消息前先结算旧历史 |
| `should_quote` 改造 | 让模型能指定引用具体消息，而不是只能回答「是否引用」 |
| `send_sticker` 工具 | 枚举 QQ 表情包，模型传枚举值发小黄脸 |
| 心跳主动发言 | 群聊沉默/定时随机搭话 |
| 更多 Agent 工具 | 待定 |
| 旧 qdrant 错误记忆清理 | 是否清空/迁移/重采现有错误记忆 |
| `agent.py` 顶部注释/旧文档修正 | 端口、返回格式等说明已过时 |
| 角色包（character pack） | 把身份/人格/记忆/情感/工具集打包成可整体切换的角色包，每个角色有各自的行为规则、可用工具、隔离的记忆库（Mem0 collection）与情感表。当前架构强假设单一角色（`config.py` 的 `BOT_NAME`/`CREATOR_NAME`、`SYSTEM_PROMPT_BASE`、`emotions.json`/`dynamic_prompt.txt`/Mem0 均与「第六谷绫」绑死）。架构级重构，启动前需详细讨论目录结构、路由、各模块参数化改造 |

---

## Part G: 对话级并发控制

### 设计目标

同一对话（`user_id`）在同一时刻只能有一个模型生成任务；如果新消息到达时旧生成还在进行，取消旧任务，用最新上下文重新生成。

### 行为

1. 为每个 `user_id` 维护一个生成任务引用（或锁 + 队列）。
2. `/chat` 请求到达时：
   - 如果该 `user_id` 当前没有正在进行的生成任务，直接启动新任务。
   - 如果该 `user_id` 有正在进行的生成任务，**取消旧任务**。
3. 解析旧任务已经产生的输出：
   - 已经完整闭合的 `<message>` 标签 → **直接发送**。
   - 最后一个完整 `</message>` 之后的未闭合文本 → 作为 `<draft>` 打回。
4. 构造新的 user 消息：

```xml
<draft>
未闭合的尾部文本（只含 <message> 标签内的正式内容，不含标签本身）
</draft>

<new_message>
<sender display="..." person_id="..." ts="...">用户新消息</sender>
</new_message>

请结合上下文重新生成回复。
```

5. 用包含新消息和 `<draft>` 的最新上下文重新生成回复。
6. 旧的 HTTP 请求返回已经发送的完整 `<message>` 内容；未闭合部分不再单独发送，只作为草稿进入新 prompt。
7. **不同 `user_id` 之间互不阻塞**，可以并行处理。

### 实现要点

- **前置条件**：需先把 DeepSeek 调用改为流式（`stream=True`），边接收边累积 buffer，对 buffer 增量扫描 `</message>` 闭合点。取消旧任务时从 buffer 抽出已闭合的 `<message>`（直接发送）和未闭合尾部（作为 `<draft>` 打回）。非流式下取消 asyncio task 拿不到任何中间文本，`<draft>` 无从谈起。
- `<mood>` 标签放在 `<message>` 之前输出，确保 draft 抽取截断尾部时情绪标签不丢失。
- 使用 `asyncio.Task` + 取消机制实现。
- 取消点应放在可中断的 `await` 位置（HTTP 请求、工具调用、文件 IO 等），避免资源泄漏。
- 取消后清理已产生的副作用；历史写入等操作应延迟到最终发送前，或在取消时回滚。
- 注意竞态：取消信号发出后，旧任务可能刚好进入发送阶段，需要兜底检查。
- 旧输出中已发送的完整 `<message>` **不写入对话历史**（它已经被用户看到，但并未经过完整上下文确认，避免历史污染）。
- 不限制打回次数；连续多次被打回时，每次的 `<draft>` 会自然叠加在新 user 消息中。

---

## Part H: 回复延迟方案（已废弃）

为保证时序不混乱，**所有回复延迟全部取消**，包括首条消息前的等待和多条消息之间的间隔。生成完成后立即发送。

历史决策记录：
- 最初使用随机延迟 0.5~2.5s。
- 曾计划改为 `总等待预算 = /chat 处理耗时 × 4`，按消息字数比例分配。
- 最终取消：延迟与并发控制相互干扰，容易导致消息顺序和时序问题。

---

## Verification

1. 启动 Agent，发送群聊消息，检查 session 文件中 content 是否为 `<sender>` 格式，且带 `ts` 属性。
2. 检查 `display` 是否正确（`group_card or qq_name`）。
3. 检查 Bot 回复的 session 记录是否带真实 person_id（群聊 `group_群号:BotQQ号`，私聊 `private_BotQQ号`）。
4. 触发结算，检查 `_history_text()` 输出是否为 `<sender>` 格式带 `ts`。
5. 检查 `SUMMARY_PROMPT` 输出的 `<第一类>` 增量更新是否正确合并到 `known_facts.xml`。
6. 检查 `SUMMARY_PROMPT` 输出的 `<第二类>` 事实是否成功存入 Mem0，且带 `spoken_by` metadata。
7. 检查 `DIARY_PROMPT` 输出的 `emotional_memory.txt` 是否三段式、日记正确追加。
8. 检查 Bot 回复是否不带 `<sender>` 前缀（前缀剥除生效）。
9. 检查旧 session 文件的消息是否兼容（旧消息只填 `display` 属性）。
10. 检查 Mem0 检索结果是否正确注入 user prompt（不再在 system prompt），且保留 `spoken_by`。
11. 检查 `known_facts.xml` 是否正确注入 system prompt。
12. 检查 `SUMMARY_PROMPT` 输出格式异常时能否自动修复重试。
13. 检查 `DIARY_PROMPT` 段缺失时能否程序化修复。
14. 检查 `EMOTION_PROMPT` JSON 解析失败能否持续修复直到成功（不再限 2 次）。
15. 检查私聊消息的 person_id 格式为 `private_QQ号`。
16. 检查 `EMOTION_PROMPT` 输出是否为 `emotions.json` v2 格式（dimension/impact/valence）。
17. 检查 `emotions.json` 是否正确计算亲近度/信任度分数。
18. 检查 `current_emotion` 是否从 A/T 分数正确推导。
19. 检查情感表是否只显示当前说话者 + 最近 3 天内活跃的前 5 人。
20. 检查长期未互动的人是否每天衰减 0.5。
21. 检查事实文本是否使用纯 QQ 号，不含昵称/代词。
22. 检查 `spoken_by` 中的 `person_id` 是否在 `allowed_person_ids` 内。
23. 检查 `known_facts.xml` 旧事实不会被静默覆盖。
24. 检查模型是否每轮输出合法的 `<mood>` 隐藏标签。
25. 检查 `mood.json` 是否正确更新 P/A/D 值、reason、label。
26. 检查情绪硬上限 ±0.7 是否生效（强烈事件也不能一次跳到 ±1）。
27. 检查指数衰减是否生效（情绪缓慢向基线回归）。
28. 检查 user prompt 开头是否包含结构化情绪字段。
29. 检查模型不输出 `<message>` 标签时情绪是否仍然更新。
30. 检查 `user_map.json` 是否正确记录昵称/群名片变更历史。
31. 检查 Bot 回复正文是否不出现 QQ 号（例外场景除外）。
32. 检查模型输出是否被正确解析为 `<message>` 标签，标签外文本不发送。
33. 检查无 `<message>` 标签时是否静默，且 `<mood>` 仍更新。
34. 检查同一 `user_id` 连续两条消息时，旧生成是否被取消；已闭合的 `<message>` 是否直接发送，未闭合部分是否作为 `<draft>` 打回重算。
35. 检查回复是否无延迟、立即发送（延迟方案已废弃）。

---

## Relevant files

- `agent/agent.py` — 所有改动集中在此文件
  - `SYSTEM_PROMPT_BASE`：加消息格式段、回复输出规则、改前缀禁止规则
  - `SUMMARY_PROMPT`：第一类增量更新 + 第二类区分，事实文本用 QQ 号
  - `DIARY_PROMPT`：三段式日记 + 层级归并 + `emotional_memory.txt`
  - `EMOTION_PROMPT`：person_id/ts 来源改为 sender 标签属性；事件文本用 QQ 号
  - `_person_id(user_id, sender_id)`：新增，群聊返回 `f'{user_id}:{sender_id}'`，私聊返回 `f'private_{sender_id}'`
  - `call_deepseek()`：user_msg 构造、history 存储、system prompt 组装、昵称映射注入
  - `_history_text()`：改为 `<sender>` 格式，旧消息只填 `display` 属性
  - `_summarize_and_store()`：解析 `<第一类>`（增量合并写入 `known_facts.xml`）+ `<第二类>`（ElementTree 解析 <fact>），调用校验/修复
  - `_update_dynamic_prompt()`：改为 `DIARY_PROMPT` 全量覆盖 `emotional_memory.txt`，调用校验/修复
  - `_update_emotions()`：每日结算时调用 `EMOTION_PROMPT` 并应用 Sutcliffe & Wang 模型更新 `emotions.json`
  - `_format_emotions_for_prompt()`：改为输出亲近度/信任度/当前态度/趋势/近期事件表格
  - `_repair_until_valid()`：新增通用格式修复框架
  - `_json_array_with_repair()`：改为调用 `_repair_until_valid`，取消 2 次上限
  - 情绪相关：`_parse_mood_tag()`、`_validate_mood_change()`、`_apply_mood_decay()`、`_load_mood()` / `_save_mood()`
  - `user_map.json` 更新：新增 `_update_user_map()` 等
  - 输出解析：新增 `_parse_model_output()`，用 ElementTree 解析 `<message>` / `<mood>`
  - 并发控制：新增 `_chat_tasks: dict[str, asyncio.Task]` 管理每个 `user_id` 的生成任务
  - 延迟计算：新增 `_compute_reply_delay(messages, processing_time)`
- `adapter.py` — 新增 `bot_qq` 字段传递
- `agent/emotional_memory.txt` — 感性记忆持久化文件（原 `dynamic_prompt.txt`，本地文件，不提交）
- `agent/known_facts.xml` — 第一类理性记忆持久化文件（新增，本地文件，不提交）
- `agent/emotions.json` — 情感记忆持久化文件（schema_version 2，本地文件，不提交）
- `agent/mood.json` — 情绪状态持久化文件（本地文件，不提交）
- `agent/user_map.json` — 用户身份/昵称映射表（新增，本地文件，不提交）
