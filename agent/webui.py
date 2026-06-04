"""
第六谷绫 WebUI —— 路由注册 + JSON API handler。
HTML 页面在 templates/ 目录，独立文件，编辑后无需重启。
"""
import os
from aiohttp import web

_TEMPLATES = os.path.join(os.path.dirname(__file__), "templates")


def _page(name: str) -> str:
    """读取模板文件，返回 HTML 字符串。每次请求都重新读，支持热编辑。"""
    with open(os.path.join(_TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


# ── 路由注册 ────────────────────────────────────────────


def setup_routes(app: web.Application, ctx: dict) -> None:
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
        df = ctx["DYNAMIC_PROMPT_FILE"]
        if os.path.exists(df):
            with open(df, encoding="utf-8") as f:
                return web.Response(text=f.read())
        return web.Response(text="（未生成，下次结算后自动创建）")

    async def mem0_log_json(request: web.Request) -> web.Response:
        return web.json_response(ctx["_mem0_log"])

    async def memories_json(request: web.Request) -> web.Response:
        uid = request.query.get("user_id")
        lock = ctx["_mem0_lock"]
        get_all = ctx["_mem_get_all"]
        async with lock:
            items = get_all(user_id=uid)
        return web.json_response(items)

    async def memories_add(request: web.Request) -> web.Response:
        body = await request.json()
        memory = (body.get("memory") or "").strip()
        user_id = (body.get("user_id") or "manual").strip()
        spoken_by = (body.get("spoken_by") or "手动添加").strip()
        if not memory:
            return web.json_response({"status": "error", "reason": "empty memory"})
        if not user_id:
            return web.json_response({"status": "error", "reason": "empty user_id"})
        lock = ctx["_mem0_lock"]
        add_fn = ctx["_mem_add"]
        async with lock:
            result = add_fn(memory, user_id, spoken_by)
        return web.json_response({"status": "ok", "result": result})

    async def memories_update(request: web.Request) -> web.Response:
        body = await request.json()
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
        lock = ctx["_mem0_lock"]
        update_fn = ctx["_mem_update"]
        async with lock:
            result = update_fn(mid, memory, user_id, spoken_by)
        return web.json_response({"status": "ok", "result": result})

    async def memories_delete(request: web.Request) -> web.Response:
        body = await request.json()
        mid = body.get("id", "")
        if not mid:
            return web.json_response({"status": "error", "reason": "no id"})
        lock = ctx["_mem0_lock"]
        delete_fn = ctx["_mem_delete"]
        async with lock:
            delete_fn(mid)
        return web.json_response({"status": "ok"})

    async def emotions_page(request: web.Request) -> web.Response:
        return web.Response(text=_page("emotions.html"), content_type="text/html")

    async def emotions_json(request: web.Request) -> web.Response:
        return web.json_response(ctx["_emotions_json"]())

    async def emotions_user_upsert(request: web.Request) -> web.Response:
        body = await request.json()
        person_id = (body.get("person_id") or "").strip()
        display_name = (body.get("display_name") or person_id).strip()
        summary = (body.get("summary_before_30d") or "").strip()
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["_emotion_upsert_user"](person_id, display_name, summary)
        return web.json_response({"status": "ok", "result": result})

    async def emotions_event_add(request: web.Request) -> web.Response:
        body = await request.json()
        person_id = (body.get("person_id") or "").strip()
        display_name = (body.get("display_name") or person_id).strip()
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["_emotion_add_event"](person_id, display_name, body)
        return web.json_response({"status": "ok", "result": result})

    async def emotions_event_update(request: web.Request) -> web.Response:
        body = await request.json()
        person_id = (body.get("person_id") or "").strip()
        index = int(body.get("index", -1))
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        result = ctx["_emotion_update_event"](person_id, index, body)
        return web.json_response({"status": "ok", "result": result})

    async def emotions_delete(request: web.Request) -> web.Response:
        body = await request.json()
        person_id = (body.get("person_id") or "").strip()
        index = body.get("index")
        if not person_id:
            return web.json_response({"status": "error", "reason": "no person_id"})
        ctx["_emotion_delete"](person_id, int(index) if index is not None else None)
        return web.json_response({"status": "ok"})

    async def force_settle(request: web.Request) -> web.Response:
        body = await request.json()
        user_id = body.get("user_id", "unknown")
        gh = ctx["get_history"]
        history = gh(user_id)
        if not history:
            return web.json_response({"status": "skip", "reason": "无历史"})
        await ctx["_summarize_and_store"](history, user_id)
        await ctx["_update_dynamic_prompt"](history)
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
