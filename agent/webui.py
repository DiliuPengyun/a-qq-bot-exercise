"""
第六谷绫 WebUI —— 路由注册 + JSON API handler。
HTML 页面在 templates/ 目录，独立文件，编辑后无需重启。
"""
from __future__ import annotations

import os
import asyncio
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, TypedDict, cast
from aiohttp import web

if TYPE_CHECKING:
    from mem0 import Mem0Memory
    from models import (
        EmotionData,
        EmotionUser,
        EmotionEvent,
        EmotionEventInput,
        HistoryMsg,
        MemSearchItem,
        Mem0LogEntry,
    )

_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")


# ── 请求体 TypedDict ──

class MemAddBody(TypedDict, total=False):
    memory: str
    user_id: str
    spoken_by: str


class MemUpdateBody(TypedDict, total=False):
    id: str
    memory: str
    user_id: str
    spoken_by: str


class MemDeleteBody(TypedDict, total=False):
    id: str


class EmotionUpsertBody(TypedDict, total=False):
    person_id: str
    display_name: str
    summary_before_30d: str


class EmotionEventAddBody(TypedDict, total=False):
    person_id: str
    display_name: str
    start_at: str
    end_at: str
    dimension: str
    valence: str
    impact: int
    event: str


class EmotionEventUpdateBody(TypedDict, total=False):
    person_id: str
    index: int


class EmotionDeleteBody(TypedDict, total=False):
    person_id: str
    index: int


class SettleBody(TypedDict, total=False):
    user_id: str


def _page(name: str) -> str:
    """读取模板文件，返回 HTML 字符串。每次请求都重新读，支持热编辑。"""
    with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


# ── WebUI 上下文类型 ──────────────────────────────────────────

# agent.py 中 _webui_ctx 字典的所有 key 和对应值类型。
# 用 TypedDict 让 pyright strict 推断出每个 key 的值类型。


class WebuiCtx(TypedDict):
    """setup_routes 的 ctx 参数类型，由 agent.py 注入。"""

    # 文件路径
    SESSIONS_DIR: str
    EMOTIONAL_MEMORY_FILE: str

    # 异步锁
    mem0_lock: asyncio.Lock

    # Mem0 搜索日志
    mem0_log: list[Mem0LogEntry]

    # Mem0 同步操作（在锁内调用）
    mem_get_all: Callable[..., list[MemSearchItem]]
    mem_add: Callable[[str, str, str], Mem0Memory]
    mem_update: Callable[..., Mem0Memory]
    mem_delete: Callable[[str], None]

    # 情感表操作
    emotions_json: Callable[[], EmotionData]
    emotion_upsert_user: Callable[[str, str, str], EmotionUser]
    emotion_add_event: Callable[[str, str, EmotionEventInput], EmotionEvent]
    emotion_update_event: Callable[[str, int, EmotionEventInput], EmotionEvent]
    emotion_delete: Callable[[str, int | None], None]

    # 异步结算操作
    summarize_and_store: Callable[..., Coroutine[None, None, None]]
    update_emotional_memory: Callable[..., Coroutine[None, None, None]]
    update_emotions: Callable[..., Coroutine[None, None, None]]

    # 历史操作
    get_history: Callable[[str], list[HistoryMsg]]
    save_history: Callable[[str, list[HistoryMsg]], None]


# ── 路由注册 ────────────────────────────────────────────


def setup_routes(app: web.Application, ctx: WebuiCtx) -> None:
    """在 app 上注册所有 WebUI 路由。ctx 由 agent.py 注入。"""

    # ── 静态页面（从 templates/ 热读） ──

    async def home_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("home.html"), content_type="text/html")

    async def admin_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("admin.html"), content_type="text/html")

    async def mem0_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("mem0.html"), content_type="text/html")

    async def memories_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("memories.html"), content_type="text/html")

    # ── JSON / 功能 API ──

    async def list_sessions(request: web.Request) -> web.Response:
        sd = ctx["SESSIONS_DIR"]
        files = [f.replace(".json", "") for f in os.listdir(sd) if f.endswith(".json")]
        return web.json_response({"sessions": files})

    async def view_dynamic_prompt(request: web.Request) -> web.Response:
        df = ctx["EMOTIONAL_MEMORY_FILE"]
        if os.path.exists(df):
            with open(df, encoding="utf-8") as f:
                return web.Response(text=f.read())
        return web.Response(text="（未生成，下次结算后自动创建）")

    async def mem0_log_json(request: web.Request) -> web.Response:
        return web.json_response(ctx["mem0_log"])

    async def memories_json(request: web.Request) -> web.Response:
        uid = request.query.get("user_id")
        lock = ctx["mem0_lock"]
        get_all = ctx["mem_get_all"]
        async with lock:
            items = get_all(user_id=uid)
        return web.json_response(items)

    async def memories_add(request: web.Request) -> web.Response:
        body = cast(MemAddBody, await request.json())
        memory = (body.get("memory") or "").strip()
        user_id = (body.get("user_id") or "manual").strip()
        spoken_by = (body.get("spoken_by") or "手动添加").strip()
        if not memory:
            return web.json_response({"status": "error", "reason": "empty memory"})
        if not user_id:
            return web.json_response({"status": "error", "reason": "empty user_id"})
        lock = ctx["mem0_lock"]
        add_fn = ctx["mem_add"]
        async with lock:
            result = add_fn(memory, user_id, spoken_by)
        return web.json_response({"status": "ok", "result": result})

    async def memories_update(request: web.Request) -> web.Response:
        body = cast(MemUpdateBody, await request.json())
        mid = body.get("id", "")
        memory = (body.get("memory") or "").strip()
        user_id = (body.get("user_id") or "").strip()
        spoken_by = (body.get("spoken_by") or "").strip()
        if not mid:
            return web.json_response({"status": "error", "reason": "no id"})
        if not memory:
            return web.json_response({"status": "error", "reason": "empty memory"})
        if not user_id:
            return web.json_response({"status": "error", "reason": "empty user_id"})
        lock = ctx["mem0_lock"]
        update_fn = ctx["mem_update"]
        async with lock:
            result = update_fn(mid, memory, user_id, spoken_by)
        return web.json_response({"status": "ok", "result": result})

    async def memories_delete(request: web.Request) -> web.Response:
        body = cast(MemDeleteBody, await request.json())
        mid = body.get("id", "")
        if not mid:
            return web.json_response({"status": "error", "reason": "no id"})
        lock = ctx["mem0_lock"]
        delete_fn = ctx["mem_delete"]
        async with lock:
            delete_fn(mid)
        return web.json_response({"status": "ok"})

    async def emotions_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("emotions.html"), content_type="text/html")

    async def emotions_json(request: web.Request) -> web.Response:
        return web.json_response(ctx["emotions_json"]())

    async def emotions_user_upsert(request: web.Request) -> web.Response:
        body = cast(EmotionUpsertBody, await request.json())
        person_id = (body.get("person_id") or "").strip()
        display_name = (body.get("display_name") or person_id).strip()
        summary = (body.get("summary_before_30d") or "").strip()
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["emotion_upsert_user"](person_id, display_name, summary)
        return web.json_response({"status": "ok", "result": result})

    async def emotions_event_add(request: web.Request) -> web.Response:
        body = cast(EmotionEventAddBody, await request.json())
        person_id = (body.get("person_id") or "").strip()
        display_name = (body.get("display_name") or person_id).strip()
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["emotion_add_event"](person_id, display_name, cast(EmotionEventInput, dict(body)))
        return web.json_response({"status": "ok", "result": result})

    async def emotions_event_update(request: web.Request) -> web.Response:
        body = cast(EmotionEventUpdateBody, await request.json())
        person_id = (body.get("person_id") or "").strip()
        index = int(body.get("index", -1))
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["emotion_update_event"](person_id, index, cast(EmotionEventInput, dict(body)))
        return web.json_response({"status": "ok", "result": result})

    async def emotions_delete(request: web.Request) -> web.Response:
        body = cast(EmotionDeleteBody, await request.json())
        person_id = (body.get("person_id") or "").strip()
        index = body.get("index")
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        ctx["emotion_delete"](person_id, int(index) if index is not None else None)
        return web.json_response({"status": "ok"})

    async def force_settle(request: web.Request) -> web.Response:
        body = cast(SettleBody, await request.json())
        user_id = body.get("user_id", "unknown")
        gh = ctx["get_history"]
        history = gh(user_id)
        if not history:
            return web.json_response({"status": "skip", "reason": "无历史"})
        await ctx["summarize_and_store"](history, user_id)
        await ctx["update_emotional_memory"](history)
        history.clear()
        ctx["save_history"](user_id, history)
        return web.json_response({"status": "ok", "cleared": True})

    # ── 注册 ──

    app.router.add_get("/", home_page)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/sessions", list_sessions)
    app.router.add_get("/dynamic-prompt", view_dynamic_prompt)
    app.router.add_get("/mem0-log", mem0_log_json)
    app.router.add_get("/mem0", mem0_page)
    app.router.add_get("/memories", memories_page)
    app.router.add_get("/memories-json", memories_json)
    app.router.add_post("/memories/add", memories_add)
    app.router.add_post("/memories/update", memories_update)
    app.router.add_post("/memories/delete", memories_delete)
    app.router.add_get("/emotions", emotions_page)
    app.router.add_get("/emotions-json", emotions_json)
    app.router.add_post("/emotions/user", emotions_user_upsert)
    app.router.add_post("/emotions/event/add", emotions_event_add)
    app.router.add_post("/emotions/event/update", emotions_event_update)
    app.router.add_post("/emotions/delete", emotions_delete)
    app.router.add_post("/settle", force_settle)