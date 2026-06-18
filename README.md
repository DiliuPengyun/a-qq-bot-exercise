# NapCat + NcatBot + Agent 项目

一个跑在本地的 QQ 群聊 AI Bot。NapCat 对接 QQ 协议，NcatBot SDK 做适配，自研 Agent 做大脑（DeepSeek V4 Flash），支持长期记忆、情绪状态、情感关系、并发控制。

## 环境配置

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

1. `agent/.env` 填入：
   - `DEEPSEEK_API_KEY` — DeepSeek API Key
   - `SILICONFLOW_API_KEY` — 硅基流动 API Key（Embedding + Reranker）
2. 复制 `config.example.yaml` 为本地 `config.yaml`，填入 `ws_token`、`bot_uin`、`root` 等本机配置

## 启动

需要三个进程（NapCat 先启动）：

```bash
# 1. NapCat（双击 launcher.bat，扫码登录）

# 2. Agent
python agent/agent.py

# 3. Adapter
python adapter.py
```

## 架构

```
用户消息 → NapCat → NcatBot SDK → adapter.py
                                         ↓ POST /chat (+ bot_qq)
                                    agent.py (Agent, :8081)
                                      ├─ 并发控制 (acquire 取消旧任务 → draft 传递)
                                      ├─ 每日结算检查
                                      ├─ Mem0 单轮检索 (Embedding 海选 + Reranker 选拔, 双门槛)
                                      ├─ 矛盾检测
                                      ├─ 拼 system prompt (BASE + 感性记忆 + known_facts + 情感)
                                      ├─ 拼 user prompt (情绪 + 昵称映射 + 记忆 + 矛盾 + sender消息 + draft)
                                      ├─ 调 DeepSeek V4 Flash (SSE 流式, 增量扫描 <message>)
                                      ├─ 解析 <message>/<mood> 标签输出
                                      ├─ 墙钟衰减更新 mood.json
                                      ├─ 保存历史 (sender 标签格式) → 回复
                                      └─ 后台结算
                                         ↓
                                    adapter.py → QQ
```

三个进程：NapCat（QQ 协议）→ Agent（:8081）→ Adapter（NcatBot 事件循环）。

## 核心模块

| 文件 | 职责 |
|------|------|
| `adapter.py` | NcatBot 事件适配层，监听 QQ 消息并转发给 Agent |
| `agent/agent.py` | Agent 入口：aiohttp app + /chat handler（含并发控制）+ WebUI |
| `agent/chat.py` | 核心对话流水线（流式：检索→矛盾→prompt→DeepSeek SSE→增量解析→存历史→后台结算） |
| `agent/concurrency.py` | 对话级并发控制：同 user_id 互斥 + draft 传递 |
| `agent/memstore.py` | Mem0 客户端 + 双门槛检索（Embedding 海选 + Reranker 选拔）+ 矛盾检测 |
| `agent/settlement.py` | 每日结算编排（摘要存库/日记/情感/情绪基线）+ 后台定时循环 |
| `agent/emotions.py` | emotions.json v2（亲近度/信任度，Sutcliffe & Wang 数学模型） |
| `agent/mood.py` | mood.json PAD 三维情绪模型 + 墙钟衰减 + 动态基线 |
| `agent/knownfacts.py` | known_facts.xml 第一类理性记忆（始终在 system prompt） |
| `agent/usermap.py` | user_map.json 用户身份/昵称映射表 |
| `agent/llm.py` | DeepSeek 文本调用 + repair_until_valid 通用修复框架 |
| `agent/config.py` | env 加载 / 路径 / 模型阈值 / 提示词 / 身份常量 |

## 记忆系统

### 理性记忆（事实）
- **第一类**（`known_facts.xml`）：必须时刻记住的事实，始终在 system prompt，每日结算增量合并
- **第二类**（Mem0/Qdrant）：可暂时忘记的事实，按需检索注入 user prompt，`infer=False` 原样存入

### 感性记忆（日记）
- `emotional_memory.txt` 三段式：最近 7 天日记 → 近 30 天概要 → 更早抽象概要
- 每日结算时由模型写日记，代码负责归并和滚动

### 情感关系（emotions.json）
- 对每个人的长期态度：亲近度（A）+ 信任度（T），0-100
- 基于 Sutcliffe & Wang (2012) 数学模型更新
- 每日衰减 0.5，3×3 标签表推导当前态度

### 情绪状态（mood.json）
- PAD 三维模型：愉悦度（P）、激活度（A）、支配度（D），-1 ~ +1
- 每轮对话通过 `<mood>` 标签更新
- 墙钟指数衰减（τ=30min）向动态基线回归，单轮变化硬上限 ±0.7

### 用户映射（user_map.json）
- QQ 号 → 昵称/群名片映射及变更历史
- 由代码框架自动维护，不交给模型

## WebUI

Agent 启动后访问 `http://127.0.0.1:8081`：

| 路径 | 功能 |
|------|------|
| `/` | 控制面板首页 |
| `/admin` | 手动触发结算 + 查看感性记忆 |
| `/mem0` | Mem0 检索诊断日志（海选/选拔分数、淘汰原因） |
| `/memories` | 记忆库管理（搜索、按 user_id 过滤、删除） |

## 开发约定

- 修改 `agent/` 下任何 `.py` 后重启 `python agent/agent.py`
- 只改 `agent/templates/*.html` 不用重启 Agent（模板热读）
- 修改 `adapter.py` 后重启 `python adapter.py`
- NapCat 不用重启
- 清空记忆库：停掉 Agent → `rmdir /s agent\qdrant_data` → 重启
- 手动触发结算：`/admin` 界面或 `curl -X POST http://127.0.0.1:8081/settle -H "Content-Type: application/json" -d '{"user_id":"group_xxx"}'`
- `pyright strict` 0 errors，不允许 `Any` 或 `# type: ignore`
