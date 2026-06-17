"""DeepSeek 文本调用 + JSON 数组解析/修复（结算专用）。"""

import json
from typing import cast

import httpx

from config import DEEPSEEK_KEY, DEEPSEEK_URL, EMOTION_REPAIR_PROMPT, MODEL, SETTLE_MODEL
from models import EmotionUpdateItem, HistoryMsg
from utils import history_text


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


async def call_deepseek_for_settle(system_prompt: str, history: list[HistoryMsg], tag: str) -> str | None:
    """结算辅助：从历史中提取用户消息 → 调 DeepSeek → 返回响应文本"""
    text = history_text(history)
    return await call_deepseek_text(system_prompt, text, tag, model=SETTLE_MODEL)


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
    current = text
    for attempt in range(5):
        value = _extract_json_array(current)
        if value is not None:
            return value
        repaired = await call_deepseek_text(EMOTION_REPAIR_PROMPT, current, f"{tag}Repair{attempt + 1}", model=SETTLE_MODEL)
        if not repaired:
            return None
        current = repaired
    print(f"[{tag}] JSON 修复超过上限，跳过写入")
    return None
