# 记忆模块流程图

## 每日结算（2:00 AM 分界触发）

```mermaid
flowchart TD
    BOUNDARY["消息到来"] --> CHECK{"上次结算 &lt; 今日/昨日 2:00？"}
    CHECK -- 否 --> SKIP["跳过"]
    CHECK -- 是 --> H["取全部 user 消息<br/>构建 allowed_person_ids"]

    H --> SUMMARY

    subgraph SUMMARY["🧠 SUMMARY_PROMPT（理性模型）"]
        S1["输入：今日 history + 当前 known_facts.xml"] --> S2["输出 &lt;第一类&gt; 增量 + &lt;第二类&gt; 事实"]
    end

    S2 --> MERGE["代码合并 known_facts.xml<br/>增量更新第一类"]
    S2 --> MEM0["memory.add(fact, infer=False)<br/>metadata.spoken_by"]

    H --> DIARY

    subgraph DIARY["📝 DIARY_PROMPT（感性模型）"]
        D1["输入：今日 history + 当前 emotional_memory.txt"] --> D2["输出新版 emotional_memory.txt<br/>三段式覆盖写入"]
    end

    H --> EMOTION

    subgraph EMOTION["🎭 EMOTION_PROMPT（情感模型）"]
        E1["输入：今日 history + emotions.json + 合法 person_id 映射表"] --> E2["输出情感事件 JSON"]
    end

    E2 --> APPLY["应用 Sutcliffe &amp; Wang 模型<br/>更新 emotions.json"]

    MERGE --> CLEAR["清空历史"]
    MEM0 --> CLEAR
    D2 --> CLEAR
    APPLY --> CLEAR
```

## 检索（用户发新消息时）

```mermaid
flowchart TD
    START["query = 用户消息<br/>CQ 码正则清洗"] --> SEARCH

    subgraph SEARCH["🔍 单轮检索（无数量上限，只按分数）"]
        S1["单次 memory.search<br/>filters={user_id: *}"] --> S2["Embedding 相似度 ≥ RERANK_MIN_SIMILARITY"]
        S2 --> S3["硅基流动 Reranker 精排"]
        S3 --> S4["相关性 ≥ RERANK_MIN_RELEVANCE<br/>达标即入选"]
    end

    S4 --> CONFLICT

    subgraph CONFLICT["⚠ 矛盾检测"]
        C1["同主题 + 不同 spoken_by<br/>+ 内容抵触"] --> C2["追加 ⚠ 标记"]
    end

    C2 --> FORMAT["格式化记忆<br/>[id] [spoken_by:显示名] memory（日期）"]

    FORMAT --> INJECT["注入 user prompt<br/>与 mood / user_map 同级"]
```

## 矛盾消除（模型裁决）

```mermaid
flowchart TD
    A["模型看到 ⚠ 矛盾对"] --> B{"模型判断"}
    B -- "能判断真假" --> C["调 forget_memory(假记忆 id)"]
    C --> D["Agent 执行 memory.delete()"]
    D --> E[("向量库更新")]
    B -- "判断不了" --> F["追问用户 / 两条都保留"]
```

## 整体（纵向）

```mermaid
flowchart TD
    subgraph 结算["📅 每日结算"]
        direction TB
        ST0["取全部 user 消息"] --> ST1["SUMMARY_PROMPT"]
        ST1 --> ST2["known_facts.xml 增量更新"]
        ST1 --> ST3["Mem0 第二类记忆"]
        ST0 --> ST4["DIARY_PROMPT"]
        ST4 --> ST5["emotional_memory.txt"]
        ST0 --> ST6["EMOTION_PROMPT"]
        ST6 --> ST7["emotions.json"]
    end

    结算 --> 存储

    subgraph 存储["💾 持久化"]
        direction LR
        F1[("known_facts.xml")]
        F2[("emotional_memory.txt")]
        F3[("emotions.json")]
        F4[("Qdrant")]
    end

    存储 --> 检索

    subgraph 检索["🔍 检索"]
        direction TB
        SR1["用户消息"] --> SR2["单轮 Embedding 海选 + Reranker 精排"]
        SR2 --> SR3["矛盾标记"]
    end

    F4 <-->|Embedding 语义搜索| SR2

    检索 --> 消费

    subgraph 消费["💬 拼 prompt / 模型裁决"]
        direction TB
        C1["第二类记忆"] --> C4["完整 user prompt"]
        C2["mood.json"] --> C4
        C3["user_map.json"] --> C4
        F1 --> C5["system prompt"]
        F2 --> C5
        F3 --> C5
        C4 --> C6["DeepSeek V4 Flash"]
        C5 --> C6
        C6 --> D{"矛盾可消除？"}
        D -- 是 --> E["forget_memory()"]
        D -- 否 --> F["保留"]
    end

    E --> F4
```

> **核心原则**：每日结算摘要 → 第一类进 XML、第二类进 Mem0、日记进 emotional_memory.txt、情感进 emotions.json → 单轮语义检索（无数量上限，只按双门槛分数）→ 矛盾标记 → 模型裁决闭环。第一类走 system prompt，第二类走 user prompt。
