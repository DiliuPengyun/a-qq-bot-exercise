"""工具定义 + 工具调用分发（search_web / should_quote / forget_memory）。"""

import asyncio
import json
import os
from typing import cast

from memstore import mem_delete
from models import DeepSeekMessage, ToolDef, ToolResponse


async def _do_web_search(query: str) -> str:
    """调用 Firecrawl CLI 搜索，返回格式化结果"""
    try:
        # Windows 需显式调用 .cmd
        exe = "firecrawl.cmd" if os.name == "nt" else "firecrawl"
        proc = await asyncio.create_subprocess_exec(
            exe, "search", query, "--scrape", "--limit", "3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if err:
            print(f"[Firecrawl] stderr: {err[:200]}")
        if not out.strip():
            return f"搜索无结果。（stderr: {err[:200]}）"
        return out[:3000]
    except asyncio.TimeoutError:
        return "搜索超时，请稍后重试。"
    except FileNotFoundError:
        return "Firecrawl CLI 未安装或不在 PATH 中。"
    except Exception as e:
        return f"搜索失败：{e}"


# ── 工具定义 ────────────────────────────────────────────

TOOLS: list[ToolDef] = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "联网搜索。当你需要查实时信息、不掌握的知识、或用户明确要求搜索时调用。"
                "传入自然语言搜索词，返回相关网页的标题、URL 和摘要。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词或自然语言问题",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "should_quote",
            "description": (
                "当你需要引用用户之前的某条消息来回复时调用此函数。"
                "调用后你的回复会以 QQ 引用气泡的形式发送。"
                "仅在确实需要引用时才调用，普通闲聊不要调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_memory",
            "description": (
                "当你判断某条记忆是错误或过时时调用此函数将其删除。"
                "例如：用户本人说的话与他人转述矛盾，采信本人版本并删除转述版本。"
                "仅在你有明确判断时才调用，不确定则不调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要删除的记忆的 id",
                    },
                },
                "required": ["memory_id"],
            },
        },
    },
]


async def handle_tool_calls(msg: DeepSeekMessage) -> tuple[bool, list[ToolResponse]]:
    """解析模型返回的所有工具调用
    返回: (是否引用, [工具响应消息列表])
    """
    quote = False
    responses: list[ToolResponse] = []

    for tc in msg.get("tool_calls", []):
        name = tc["function"]["name"]
        args_raw = tc["function"].get("arguments", "{}")
        try:
            args_parsed: object = json.loads(args_raw)
        except json.JSONDecodeError as e:
            # 模型输出的 arguments 不是合法 JSON（流式拼接切在非法位置等），
            # 不抛异常——把错误回传给模型，让它在下一轮用合法 JSON 重新调用。
            responses.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": f"arguments 不是合法 JSON：{e}。请用合法 JSON 重新调用该工具。",
            })
            continue
        args = cast(dict[str, object], args_parsed) if isinstance(args_parsed, dict) else {}

        if name == "should_quote":
            quote = True
            responses.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": "ok",
            })
        elif name == "forget_memory":
            mid = str(args.get("memory_id") or "")
            if mid:
                try:
                    mem_delete(mid)
                except Exception:
                    pass
            responses.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": "已删除",
            })
        elif name == "search_web":
            query = str(args.get("query") or "")
            print(f"[Firecrawl] 模型请求搜索: {query}")
            result_text = await _do_web_search(query)
            print(f"[Firecrawl] 搜索结果: {result_text[:100]}...")
            responses.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result_text,
            })

    return quote, responses
