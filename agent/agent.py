"""
第六谷绫 Agent —— 入口模块。

负责装配 aiohttp 应用、注册 /chat 路由、注入 WebUI 上下文、启动后台结算。
核心逻辑拆分到同目录各子模块：

- models.py        类型定义（TypedDict）
- config.py        env 加载 / 路径 / 模型阈值 / 提示词
- utils.py         日期时间 + spoken_by / person_id / history_text
- sessions.py      会话历史持久化 + 缓存
- memstore.py      Mem0 客户端单例 + CRUD + 双门槛检索
- llm.py           DeepSeek 文本调用 + JSON 修复 + 通用修复框架
- emotions.py      emotions.json v2 读写 + 格式化 + CRUD + 结算
- mood.py          mood.json PAD 三维模型 + 墙钟衰减 + 动态基线
- usermap.py       user_map.json 用户身份映射表
- knownfacts.py    known_facts.xml 第一类理性记忆
- settlement.py    每日结算编排 + 后台定时循环
- tools.py         工具定义 + 调用分发
- concurrency.py   对话级并发控制（取消旧任务 + draft 打回）
- chat.py          核心对话流水线 call_deepseek（流式）
- webui.py         WebUI 路由（setup_routes 注入）

启动: python agent/agent.py
接口: POST /chat
      请求 {"user_id", "nickname", "message", "is_direct", "bot_qq",
            "sender_id", "message_time", "gender", "mentioned", "group_info",
            "qq_name", "group_card"}
      返回 {"replies": [{"reply": "...", "quote": true/false}, ...]}
"""

import asyncio
import traceback
from typing import cast

from aiohttp import web

from chat import call_deepseek
from concurrency import GenerationContext, acquire, register
from config import EMOTIONAL_MEMORY_FILE, SESSIONS_DIR
from emotions import (
    emotion_add_event,
    emotion_delete,
    emotion_update_event,
    emotion_upsert_user,
    emotions_json,
    update_emotions,
)
from memstore import (
    mem0_lock,
    mem0_log,
    mem_add,
    mem_delete,
    mem_get_all,
    mem_update,
)
from models import ChatRequestBody
from sessions import get_history, save_history
from settlement import (
    auto_settle_loop,
    set_bot_qq,
    summarize_and_store,
    update_emotional_memory,
)
from webui import setup_routes


# ── HTTP 接口 ────────────────────────────────────────────


async def chat(request: web.Request) -> web.Response:
    body = cast(ChatRequestBody, await request.json())
    user_id = body.get("user_id", "")
    nickname = body.get("nickname") or user_id
    message = body.get("message", "")
    is_direct = body.get("is_direct", True)
    mentioned = body.get("mentioned", False)
    gender = body.get("gender", "")
    sender_id = body.get("sender_id", "")
    message_time = body.get("message_time", "")
    qq_name = body.get("qq_name", "")
    group_card = body.get("group_card", "")
    bot_qq = body.get("bot_qq", "")

    # 存储 bot_qq 供 auto_settle_loop 使用
    if bot_qq:
        set_bot_qq(bot_qq)

    group_info = body.get("group_info")

    # ── 对话级并发控制 ──
    # 取消旧任务，获取 draft（被取消旧任务的未闭合 <message> 尾部）
    old_ctx = await acquire(user_id)
    draft = old_ctx.draft if old_ctx else ""

    # 注册新上下文
    ctx = GenerationContext()
    register(user_id, ctx)

    # 创建生成任务
    task = asyncio.create_task(call_deepseek(
        user_id, nickname, message, is_direct, group_info, mentioned, gender,
        sender_id, message_time, qq_name, group_card, bot_qq,
        draft=draft, ctx=ctx,
    ))
    ctx.task = task

    try:
        replies = await task
    except asyncio.CancelledError:
        # 任务被新请求取消：返回已闭合的 <message>（已发送给用户）
        # 如果 task 仍未完成（handler 被 aiohttp 取消），先清理 task
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise  # aiohttp 取消，向上传播
        partial = [(m, ctx.quote) for m in ctx.complete_messages]
        return web.json_response({
            "replies": [{"reply": r, "quote": q} for r, q in partial]
        })
    except Exception as e:
        traceback.print_exc()
        return web.json_response({"replies": [{"reply": f"出错了：{e}", "quote": False}]})

    return web.json_response({
        "replies": [{"reply": r, "quote": q} for r, q in replies]
    })


# ── WebUI 上下文 ─────────────────────────────────────────

_webui_ctx = {
    "mem0_log": mem0_log,
    "mem0_lock": mem0_lock,
    "mem_get_all": mem_get_all,
    "mem_add": mem_add,
    "mem_update": mem_update,
    "mem_delete": mem_delete,
    "emotions_json": emotions_json,
    "emotion_upsert_user": emotion_upsert_user,
    "emotion_add_event": emotion_add_event,
    "emotion_update_event": emotion_update_event,
    "emotion_delete": emotion_delete,
    "summarize_and_store": summarize_and_store,
    "update_emotional_memory": update_emotional_memory,
    "update_emotions": update_emotions,
    "get_history": get_history,
    "save_history": save_history,
    "SESSIONS_DIR": SESSIONS_DIR,
    "EMOTIONAL_MEMORY_FILE": EMOTIONAL_MEMORY_FILE,
}

_settle_task: asyncio.Task[None] | None = None


async def _on_startup(app: web.Application) -> None:
    global _settle_task
    _settle_task = asyncio.create_task(auto_settle_loop())


async def _on_cleanup(app: web.Application) -> None:
    if _settle_task:
        _settle_task.cancel()


app = web.Application()
app.on_startup.append(_on_startup)
app.on_cleanup.append(_on_cleanup)
app.router.add_post("/chat", chat)
setup_routes(app, cast(dict[str, object], _webui_ctx))

if __name__ == "__main__":
    HOST = None  # None = 双栈（IPv4 + IPv6）
    PORT = 8081
    print(f"Agent 启动: http://127.0.0.1:{PORT}")
    web.run_app(app, host=HOST, port=PORT)
