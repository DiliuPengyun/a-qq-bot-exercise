# Plan: sender 标签 + 记忆系统重构

> **实施状态**（2026-06-18）：
> - Part A ~ E + Part G + Part H：✅ 已完成，代码已落地，pyright strict 0 errors
> - Part F：后续 TODO，不在本次实施范围
> - Verification：⏳ 待运行时验证

## 目标

解决长期记忆中的**张冠李戴、来源不清、身份随昵称漂移**等问题，把第六谷绫的长期记忆拆成层级清晰、来源可溯的结构。

## 核心原则

1. **存储层用纯 QQ 号指人**：事实文本、情感事件、`user_map.json` 主键全部用 QQ 号，不用昵称/代词。
2. **`person_id` / `spoken_by` 保留群/私前缀**：群聊 `group_xxx:123456`，私聊 `private_123456`。
3. **回复正文用昵称**：模型看到的记忆是 QQ 号，但输出到 QQ 时必须映射为当前昵称/群名片；除非用户明确要求或话题必须写 QQ 号。
4. **每条长期记忆必须有可追溯来源**。
5. **映射表由代码框架维护**：`user_map.json` 的更新不交给大模型。

---

## ~~Part A: sender 标签统一消息格式~~ ✅ 已完成

~~Part A 全部实施完毕：sender 标签格式、4 个提示词修改、代码逻辑修改（adapter bot_qq 传递、call_deepseek sender 构造、历史存储格式、history_text 兼容旧消息）。~~

---

## ~~Part A.5: 模型输出格式（`<message>` + `<mood>`）~~ ✅ 已完成

~~Part A.5 全部实施完毕：`<message>`/`<mood>` XML 解析、NO_REPLY 退役、ElementTree 容错解析。~~

---

## ~~Part B: 记忆系统重构~~ ✅ 已完成

~~Part B 全部实施完毕：~~
- ~~B1: known_facts.xml 第一类理性记忆（load/save/merge 增量合并）~~
- ~~B2: Mem0 第二类理性记忆（infer=False，spoken_by metadata）~~
- ~~B2.5: 单轮检索（取消滚雪球）~~
- ~~B3: emotional_memory.txt 三段式日记~~
- ~~B4: emotions.json v2（v1→v2 迁移、Sutcliffe & Wang 公式、每日衰减、A/T 3×3 标签表）~~
- ~~B5: 每日结算 4 步流程（SUMMARY → DIARY → EMOTION → mood 基线）~~
- ~~B5.1: call_deepseek_for_settle extra_context 参数~~
- ~~B5.2: bot_qq 模块级变量获取~~
- ~~B6: 删除 SYSTEM_PROMPT_VARIABLE，记忆注入移到 user prompt~~

---

## ~~Part C: 模型输出格式校验 / 修复~~ ✅ 已完成

~~Part C 全部实施完毕：~~
- ~~C1: repair_until_valid 通用重试框架（TypeVar，max_retries=5）~~
- ~~C2: SUMMARY_PROMPT 输出 XML 校验 + 合并 known_facts.xml~~
- ~~C3: DIARY_PROMPT 三段式校验 + 程序化修复段标题~~
- ~~C4: EMOTION_PROMPT 重试上限移除（改用 repair_until_valid）~~

---

## ~~Part D: 情绪状态表（Mood Table）持久化方案~~ ✅ 已完成

~~Part D 全部实施完毕（mood.py）：~~
- ~~D1-D2: 设计目标 + 与情感表区分~~
- ~~D3: PAD 三维数据模型 + today_log~~
- ~~D4: `<mood>` 隐藏标签输出格式~~
- ~~D5: 每轮更新机制（即使不回复也更新）~~
- ~~D6: 校验与平滑（±0.7 硬上限、墙钟衰减 τ=1800s）~~
- ~~D7: 动态基线（定积分日均值 + EWMA α=0.3）~~
- ~~D8: user prompt 注入（数字 + 标签 + 原因 + 时间差标注）~~

---

## ~~Part E: 用户身份映射表（user_map.json）~~ ✅ 已完成

~~Part E 全部实施完毕（usermap.py）：~~
- ~~E1: 设计目标~~
- ~~E2: 文件格式（QQ 号主键、qq_name_history、group_card_history）~~
- ~~E3: 更新规则（asyncio.Lock、改名检测、空变空不触发）~~
- ~~E4: Prompt 注入格式（4 列映射表：person_id | QQ号 | 群名片 | QQ昵称）~~

---

## ~~Part H: 回复延迟方案（已废弃）~~ ✅ 已废弃（无代码变更）

~~回复延迟已取消，代码中不再有任何延迟逻辑。~~

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

## ~~Part G: 对话级并发控制~~ ✅ 已完成

~~Part G 全部实施完毕：~~
- ~~DeepSeek 调用改为流式（`stream=True`），SSE 增量解析 content + tool_call deltas~~
- ~~`_extract_complete_and_draft()` 增量扫描 buffer，提取已闭合 `<message>` 和未闭合草稿~~
- ~~`_ToolCallAccumulator` 流式累积工具调用 deltas（按 index 聚合 id/name/arguments）~~
- ~~`concurrency.py` 新建：`GenerationContext` + `acquire()`（取消旧任务）+ `register()`~~
- ~~`chat.py` 改为流式调用，`ctx` 增量更新 `complete_messages`/`draft`/`quote`~~
- ~~`agent.py` `chat()` handler：acquire → register → create_task → await，CancelledError 区分（acquire 取消 vs aiohttp 取消）~~
- ~~`<draft>` 注入 user prompt（`<draft>...</draft>\n\n<new_message>...</new_message>`）~~
- ~~取消时不保存历史、不结算、不更新 mood（后处理代码在 `async with` 之后，CancelledError 不会到达）~~
- ~~已闭合的 `<message>` 不写入对话历史（避免历史污染）~~

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

- `agent/agent.py` — 入口模块：_bot_qq 模块变量、set_bot_qq()、WebUI 上下文注入、**对话级并发控制（acquire/register/task 取消）**
- `agent/chat.py` — 核心对话流水线：sender 标签、`<message>`/`<mood>` 解析、system/user prompt 组装、**流式 DeepSeek 调用 + 增量 message 扫描 + draft 注入 + 取消时不保存历史**
- `agent/concurrency.py` — **新增**：GenerationContext + acquire/register（同一 user_id 互斥 + draft 传递）
- `agent/config.py` — 4 个提示词 + SYSTEM_PROMPT_BASE + 文件路径 + 删除 SYSTEM_PROMPT_VARIABLE
- `agent/utils.py` — person_id() 签名修改、history_text() sender 格式、extract_msg_content()
- `agent/models.py` — v2 情感类型、MoodData、ParsedMood、UserMapEntry
- `agent/memstore.py` — 单轮检索（取消滚雪球）
- `agent/llm.py` — extra_context 参数、repair_until_valid 通用修复框架（TypeVar）
- `agent/emotions.py` — v1→v2 迁移、Sutcliffe & Wang 公式、每日衰减、A/T 标签表
- `agent/mood.py` — **新增**：PAD 三维模型 + 墙钟衰减 + 定积分基线 + EWMA
- `agent/usermap.py` — **新增**：user_map.json 读写 + 昵称映射表构建
- `agent/knownfacts.py` — **新增**：known_facts.xml 加载/保存/增量合并
- `agent/settlement.py` — 4 步结算流程、XML 解析、emotional_memory、bot_qq
- `adapter.py` — 已有 bot_qq 传递，无需改动
- `agent/emotional_memory.txt` — 感性记忆持久化文件（本地文件，不提交）
- `agent/known_facts.xml` — 第一类理性记忆持久化文件（本地文件，不提交）
- `agent/emotions.json` — 情感记忆持久化文件（schema_version 2，本地文件，不提交）
- `agent/mood.json` — 情绪状态持久化文件（本地文件，不提交）
- `agent/user_map.json` — 用户身份/昵称映射表（本地文件，不提交）
