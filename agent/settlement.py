"""每日结算：时间文件 + 摘要存库 + 日记 + 情感更新 + 后台定时循环。"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from typing import cast

from config import (
    DIARY_PROMPT,
    DYNAMIC_PROMPT_FILE,
    LOCAL_TZ,
    SESSIONS_DIR,
    SETTLE_FILE,
    SUMMARY_PROMPT,
)
from emotions import update_emotions
from llm import call_deepseek_for_settle
from memstore import mem0_lock, memory
from models import HistoryMsg
from sessions import get_history, save_history
from utils import settlement_boundary, split_spoken_by


# ── 结算时间文件 ────────────────────────────────────────


def _load_settlements() -> dict[str, str]:
    if os.path.exists(SETTLE_FILE):
        with open(SETTLE_FILE, encoding="utf-8") as f:
            return cast(dict[str, str], json.load(f))
    return {}


def _save_settlements(data: dict[str, str]) -> None:
    with open(SETTLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


_settlement_cache: dict[str, str] | None = None


def _get_settlements() -> dict[str, str]:
    global _settlement_cache
    if _settlement_cache is None:
        _settlement_cache = _load_settlements()
    return _settlement_cache


# ── 结算编排 ────────────────────────────────────────────


async def summarize_and_store(history: list[HistoryMsg], user_id: str) -> None:
    """取出未结算对话 → DeepSeek 摘要 → memory.add()"""
    summary = await call_deepseek_for_settle(SUMMARY_PROMPT, history, "Settle")
    if not summary:
        return

    entries: list[tuple[list[str], str]] = []
    for line in summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            spoken_by_text, fact = line.split("\t", 1)
        else:
            spoken_by_text, fact = "每日结算", line
        sources = split_spoken_by(spoken_by_text) or ["每日结算"]
        fact = fact.strip()
        if fact:
            entries.append((sources, fact))
    if not entries:
        return

    # 逐条存入 Mem0
    stored_facts: list[str] = []
    for sources, fact in entries:
        try:
            async with mem0_lock:
                memory.add(
                    fact, user_id=user_id, agent_id="qq-bot",
                    infer=False, metadata={"spoken_by": sources},
                )
            stored_facts.append(fact)
        except Exception as e:
            print(f"[Settle] 存入失败: {e}")
    print(f"[Settle] {user_id}: 结算 {len(stored_facts)} 条 → {stored_facts}")


async def update_dynamic_prompt(history: list[HistoryMsg]) -> None:
    """第二次 DeepSeek 调用：写日记 → 更新 dynamic_prompt.txt"""
    diary = await call_deepseek_for_settle(DIARY_PROMPT, history, "DynamicPrompt")
    if not diary:
        return

    # 解析日记（情感已由 emotions.json 承担，不再写入此处）
    diary_text = ""
    diary_match = re.search(r"<日记>(.*?)</日记>", diary, re.DOTALL)
    if diary_match:
        diary_text = diary_match.group(1).strip()
    else:
        diary_text = diary.strip()

    dynamic = f"=== 昨日状态 ===\n{diary_text}"

    with open(DYNAMIC_PROMPT_FILE, "w", encoding="utf-8") as f:
        f.write(dynamic)
    print(f"[DynamicPrompt] 日记已更新: {diary_text[:80]}...")


async def safe_settle(user_id: str, history: list[HistoryMsg]) -> None:
    """结算包装：捕获异常，防止后台任务崩溃"""
    try:
        await check_and_settle(user_id, history)
    except Exception as e:
        print(f"[Settle] 后台结算失败: {e}")


async def check_and_settle(user_id: str, history: list[HistoryMsg]) -> None:
    """如果需要结算，取出未结算对话，摘要并存库，然后清空旧历史"""
    boundary = settlement_boundary()
    boundary_str = boundary.isoformat()

    settlements = _get_settlements()
    last = settlements.get(user_id, "")

    if last >= boundary_str:
        return  # 已结算

    # 结算：摘要全部历史 → 存库 → 更新动态提示词/情感日志
    await summarize_and_store(history, user_id)
    await update_dynamic_prompt(history)
    await update_emotions(history, user_id)

    # 清空历史，新一天从头开始
    history.clear()
    save_history(user_id, history)
    print(f"[Settle] {user_id}: 历史已清空，从新一天开始")

    global _settlement_cache
    settlements[user_id] = datetime.now(LOCAL_TZ).isoformat()
    _settlement_cache = settlements
    _save_settlements(settlements)


# ── 后台自动结算 ────────────────────────────────────────

_SETTLE_HOUR = 2  # 每天凌晨 2:00 自动结算


async def auto_settle_loop() -> None:
    """后台定时任务：每天凌晨 2:00 对所有会话执行结算"""
    while True:
        now = datetime.now(LOCAL_TZ)
        target = now.replace(hour=_SETTLE_HOUR, minute=0, second=5, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        wait = (target - now).total_seconds()
        print(f"[Settle] 下次自动结算: {target.strftime('%Y-%m-%d %H:%M')}")
        await asyncio.sleep(wait)

        # 扫描所有 session，对需要结算的执行
        boundary = settlement_boundary()
        settlements = _get_settlements()
        for fname in os.listdir(SESSIONS_DIR):
            if not fname.endswith(".json"):
                continue
            uid = fname[:-5]  # group_xxx / private_xxx
            if settlements.get(uid, "") >= boundary.isoformat():
                continue  # 已结算
            history = get_history(uid)
            if not history:
                continue
            try:
                await check_and_settle(uid, history)
            except Exception as e:
                print(f"[Settle] 自动结算 {uid} 失败: {e}")
