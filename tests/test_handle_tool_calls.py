"""handle_tool_calls 行为契约测试。

核心关注点：tool_call 的 arguments 不是合法 JSON 时，
必须回传 role:tool 错误消息让模型重调，而不是抛异常炸掉整轮回复。
"""

import sys
from pathlib import Path

# 确保能 import agent 包内的模块（agent 目录下模块互相用裸名 import）
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

import pytest  # noqa: E402

from models import DeepSeekMessage, ToolCall  # noqa: E402
from tools import handle_tool_calls  # noqa: E402


def _tc(name: str, args: str, call_id: str = "call_0") -> ToolCall:
    """构造一个 tool_call。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": args},
    }


def _msg(*calls: ToolCall) -> DeepSeekMessage:
    return {"role": "assistant", "tool_calls": list(calls)}


# ── 合法路径 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_quote_sets_quote_true() -> None:
    """should_quote 调用后 quote 应为 True，且返回 ok 响应。"""
    msg = _msg(_tc("should_quote", "{}"))
    quote, responses = await handle_tool_calls(msg)
    assert quote is True
    assert len(responses) == 1
    assert responses[0]["role"] == "tool"
    assert responses[0]["content"] == "ok"


@pytest.mark.asyncio
async def test_no_tool_calls_returns_empty() -> None:
    """没有 tool_calls 时返回 quote=False、空响应列表。"""
    msg: DeepSeekMessage = {"role": "assistant"}
    quote, responses = await handle_tool_calls(msg)
    assert quote is False
    assert responses == []


@pytest.mark.asyncio
async def test_unknown_tool_name_still_returns_no_response() -> None:
    """未知工具名不匹配任何分支，responses 不含该项（当前实现静默跳过）。"""
    msg = _msg(_tc("nonexistent_tool", "{}"))
    quote, responses = await handle_tool_calls(msg)
    assert quote is False
    assert responses == []


# ── 非法 JSON 路径（本次改造核心）──────────────────────────


@pytest.mark.asyncio
async def test_invalid_json_args_does_not_raise() -> None:
    """arguments 是非法 JSON 时不抛异常，回传 role:tool 错误消息。"""
    msg = _msg(_tc("search_web", "{query: 未闭合引号"))
    # 不应抛异常
    quote, responses = await handle_tool_calls(msg)
    assert quote is False
    assert len(responses) == 1
    resp = responses[0]
    assert resp["role"] == "tool"
    assert resp["tool_call_id"] == "call_0"
    # 错误消息要让模型知道该重调
    assert "合法 JSON" in resp["content"]


@pytest.mark.asyncio
async def test_invalid_json_args_preserves_call_id() -> None:
    """多个非法 tool_call 各自的 tool_call_id 要正确回传，不能串。"""
    msg = _msg(
        _tc("search_web", "bad{", call_id="call_a"),
        _tc("forget_memory", "also{bad", call_id="call_b"),
    )
    quote, responses = await handle_tool_calls(msg)
    assert quote is False
    assert len(responses) == 2
    ids = [r["tool_call_id"] for r in responses]
    assert ids == ["call_a", "call_b"]


@pytest.mark.asyncio
async def test_mixed_valid_and_invalid_args() -> None:
    """一个合法一个非法的混合调用：合法的执行，非法的回传错误。"""
    msg = _msg(
        _tc("should_quote", "{}", call_id="call_ok"),
        _tc("search_web", "{not json", call_id="call_bad"),
    )
    quote, responses = await handle_tool_calls(msg)
    # should_quote 生效
    assert quote is True
    assert len(responses) == 2
    # 两条都应有响应（一条 ok，一条错误）
    by_id = {r["tool_call_id"]: r for r in responses}
    assert by_id["call_ok"]["content"] == "ok"
    assert "合法 JSON" in by_id["call_bad"]["content"]
