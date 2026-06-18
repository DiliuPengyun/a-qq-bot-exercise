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
import json
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


async def chat(request: web.Request) -> web.StreamResponse:
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

    # SSE 响应：每轮 message 作为 event 推送，adapter 收到就发 QQ
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)

    async def on_reply(msg: str, quote: bool) -> None:
        data = json.dumps({"reply": msg, "quote": quote}, ensure_ascii=False)
        await resp.write(f"data: {data}\n\n".encode("utf-8"))

    # 创建生成任务
    task = asyncio.create_task(call_deepseek(
        user_id, nickname, message, is_direct, group_info, mentioned, gender,
        sender_id, message_time, qq_name, group_card, bot_qq,
        draft=draft, ctx=ctx, on_reply=on_reply,
    ))
    ctx.task = task

    try:
        await task
        await resp.write(b"data: {\"done\": true}\n\n")
    except asyncio.CancelledError:
        # 任务被新请求取消：已推送的 message 已通过 SSE 发出
        # 如果 task 仍未完成（handler 被 aiohttp 取消），先清理 task
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await resp.write_eof()
            raise  # aiohttp 取消，向上传播
        await resp.write(b"data: {\"cancelled\": true}\n\n")
    except Exception as e:
        traceback.print_exc()
        err = json.dumps({"error": f"出错了：{e}"}, ensure_ascii=False)
        await resp.write(f"data: {err}\n\n".encode("utf-8"))
    finally:
        await resp.write_eof()
    return resp


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
