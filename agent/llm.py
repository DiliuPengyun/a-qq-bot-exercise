"""DeepSeek 文本调用 + JSON 数组解析/修复 + 通用修复框架（结算专用）。"""

import json
from collections.abc import Callable
from typing import TypeVar, cast

import httpx

from config import (
    DEEPSEEK_KEY,
    DEEPSEEK_URL,
    EMOTION_REPAIR_PROMPT,
    MODEL,
    SETTLE_MODEL,
)
from models import EmotionUpdateItem, HistoryMsg
from utils import history_text

T = TypeVar("T")


async def call_deepseek_text(system_prompt: str, text: str, tag: str, *, model: str = MODEL) -> str | None:
    if len(text.strip()) < 50:
        return None
    try:
        headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}"}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                DEEPSEEK_URL, headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text[:30000]},
                    ],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[{tag}] 调用失败: {e}")
        return None


async def call_deepseek_for_settle(
    system_prompt: str, history: list[HistoryMsg], tag: str,
    *, extra_context: str = "",
) -> str | None:
    """结算辅助：从历史中提取用户消息 → 调 DeepSeek → 返回响应文本。

    extra_context 非空时，拼在 history text 之前。
    """
    text = history_text(history)
    if extra_context:
        text = extra_context + "\n\n" + text
    return await call_deepseek_text(system_prompt, text, tag, model=SETTLE_MODEL)


# ── 通用修复框架 ────────────────────────────────────────


async def repair_until_valid(
    parse_func: Callable[[str], T | None],
    repair_prompt: str,
    text: str,
    tag: str,
    max_retries: int = 5,
) -> T | None:
    """持续调模型修复输出直到 parse_func 成功，最多 max_retries 次。"""
    current = text
    for attempt in range(max_retries):
        result = parse_func(current)
        if result is not None:
            return result
        if attempt == max_retries - 1:
            print(f"[{tag}] 修复 {max_retries} 次仍失败，丢弃")
            return None
        repaired = await call_deepseek_text(
            repair_prompt, current, f"{tag}Repair{attempt + 1}", model=SETTLE_MODEL
        )
        if not repaired:
            return None
        current = repaired
    return None


# ── JSON 数组解析 ───────────────────────────────────────


def _extract_json_array(text: str) -> list[EmotionUpdateItem] | None:
    raw = text.strip()
    try:
        value: object = json.loads(raw)
        if isinstance(value, list):
            return cast(list[EmotionUpdateItem], value)
        return None
    except json.JSONDecodeError:
        pass
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        try:
            value2: object = json.loads(raw[start:end + 1])
            if isinstance(value2, list):
                return cast(list[EmotionUpdateItem], value2)
            return None
        except json.JSONDecodeError:
            return None
    return None


async def json_array_with_repair(text: str, tag: str) -> list[EmotionUpdateItem] | None:
    """用 repair_until_valid 持续修复 JSON 数组，不再限 2 次。"""
    return await repair_until_valid(
        _extract_json_array, EMOTION_REPAIR_PROMPT, text, tag, max_retries=5
    )
