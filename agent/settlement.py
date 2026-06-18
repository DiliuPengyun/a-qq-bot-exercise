"""每日结算：时间文件 + 摘要存库 + 日记 + 情感更新 + 情绪基线 + 后台定时循环。"""

import asyncio
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import cast

from config import (
    DIARY_PROMPT,
    DIARY_REPAIR_PROMPT,
    EMOTIONAL_MEMORY_FILE,
    LOCAL_TZ,
    SESSIONS_DIR,
    SETTLE_FILE,
    SUMMARY_PROMPT,
    SUMMARY_REPAIR_PROMPT,
)
from emotions import update_emotions
from llm import call_deepseek_for_settle, repair_until_valid
from memstore import mem0_lock, memory
from models import HistoryMsg
from mood import adjust_mood_baseline
from sessions import get_history, save_history
from knownfacts import load_known_facts, merge_known_facts, save_known_facts
from utils import person_id, settlement_boundary, split_spoken_by


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


# ── 辅助 ────────────────────────────────────────────────


def _escape_stray_ampersands(text: str) -> str:
    """转义游离的 &，避免 XML 解析失败。"""
    return re.sub(
        r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)",
        "&amp;",
        text,
    )


def _current_date() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _build_allowed_person_ids(
    history: list[HistoryMsg], bot_qq: str, user_id: str
) -> set[str]:
    """构建合法 person_id 集合：emotions.json 已有用户 + history 用户 + Bot 自己。"""
    from emotions import emotions_json

    allowed: set[str] = set()

    # emotions.json 已有用户
    data = emotions_json()
    for pid in data.get("users", {}):
        allowed.add(str(pid))

    # history 用户
    for h in history:
        if h.get("role") != "user":
            continue
        sid = str(h.get("sender_id") or "")
        if sid:
            allowed.add(person_id(user_id, sid))

    # Bot 自己
    if bot_qq:
        allowed.add(person_id(user_id, bot_qq))

    return allowed


# ── 1. 理性模型（SUMMARY_PROMPT） ───────────────────────


def _parse_summary_output(text: str) -> tuple[str, list[tuple[str, str]]] | None:
    """解析 SUMMARY_PROMPT 输出。

    返回 (first_class_xml, second_class_facts) 或 None（解析失败）。
    first_class_xml: <第一类>...</第一类> 的 XML 字符串
    second_class_facts: [(spoken_by, fact_text), ...]
    """
    escaped = _escape_stray_ampersands(text)
    try:
        root = ET.fromstring(f"<root>{escaped}</root>")
    except ET.ParseError:
        return None

    # 提取第一类
    first_class = root.find("第一类")
    if first_class is not None:
        first_class_xml = ET.tostring(first_class, encoding="unicode")
    else:
        first_class_xml = ""

    # 提取第二类事实
    second_class = root.find("第二类")
    facts: list[tuple[str, str]] = []
    if second_class is not None:
        add_elem = second_class.find("新增")
        search_root = add_elem if add_elem is not None else second_class
        for fact in search_root.findall("fact"):
            spoken_by = fact.get("spoken_by", "")
            fact_text = (fact.text or "").strip()
            if fact_text:
                facts.append((spoken_by, fact_text))

    if not first_class_xml and not facts:
        return None

    return (first_class_xml, facts)


async def summarize_and_store(
    history: list[HistoryMsg], user_id: str, bot_qq: str = ""
) -> None:
    """取出未结算对话 → DeepSeek 摘要 → 合并 known_facts.xml + 存 Mem0"""
    allowed = _build_allowed_person_ids(history, bot_qq, user_id)
    known_facts = load_known_facts()
    extra = f"当前日期：{_current_date()}\n\n当前 known_facts.xml：\n{known_facts}"

    output = await call_deepseek_for_settle(
        SUMMARY_PROMPT, history, "Settle", extra_context=extra
    )
    if not output:
        return

    parsed = await repair_until_valid(
        _parse_summary_output, SUMMARY_REPAIR_PROMPT, output, "SummaryParse"
    )
    if parsed is None:
        return

    first_class_xml, second_facts = parsed

    # 合并 known_facts.xml
    merged = merge_known_facts(first_class_xml, allowed)
    if merged is not None:
        save_known_facts(merged)
        print(f"[Settle] known_facts.xml 已更新")

    # 存入 Mem0
    stored: list[str] = []
    for spoken_by_str, fact in second_facts:
        sources = split_spoken_by(spoken_by_str) or [spoken_by_str] if spoken_by_str else []
        try:
            async with mem0_lock:
                memory.add(
                    fact, user_id=user_id, agent_id="qq-bot",
                    infer=False,
                    metadata={"spoken_by": sources} if sources else {"spoken_by": ["未知"]},
                )
            stored.append(fact)
        except Exception as e:
            print(f"[Settle] 存入失败: {e}")
    if stored:
        print(f"[Settle] {user_id}: 第二类记忆 {len(stored)} 条 → {stored[:3]}...")


# ── 2. 感性模型（DIARY_PROMPT） ─────────────────────────


def _load_emotional_memory() -> str:
    if os.path.exists(EMOTIONAL_MEMORY_FILE):
        with open(EMOTIONAL_MEMORY_FILE, encoding="utf-8") as f:
            return f.read().strip()
    return ""


_DIARY_HEADERS = [
    "=== 最近7天日记 ===",
    "=== 近30天概要 ===",
    "=== 更早概要 ===",
]


def _validate_diary_output(text: str) -> str | None:
    """校验日记输出三段式结构。返回修正后的文本或 None（校验失败）。"""
    text = text.strip()
    if not text:
        return None

    # 检查是否包含所有段标题
    missing = [h for h in _DIARY_HEADERS if h not in text]

    if not missing:
        return text  # 完整

    # 尝试程序化修复：补缺失段标题
    if missing:
        # 把缺失的段标题加到末尾
        for h in missing:
            text += f"\n\n{h}\n"
        return text

    return None


async def update_emotional_memory(history: list[HistoryMsg]) -> None:
    """DIARY_PROMPT 调用 → 三段式日记 → 覆盖写入 emotional_memory.txt"""
    em = _load_emotional_memory()
    extra = f"今天是 {_current_date()}。\n\n当前 emotional_memory.txt 内容：\n{em or '（空）'}"

    output = await call_deepseek_for_settle(
        DIARY_PROMPT, history, "Diary", extra_context=extra
    )
    if not output:
        return

    # 程序化修复段标题
    fixed = _validate_diary_output(output)
    if fixed is None:
        # 走 repair
        fixed = await repair_until_valid(
            _validate_diary_output, DIARY_REPAIR_PROMPT, output, "DiaryParse"
        )
    if fixed is None:
        print("[Diary] 输出校验失败，跳过写入")
        return

    with open(EMOTIONAL_MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write(fixed)
    print(f"[Diary] emotional_memory.txt 已更新: {fixed[:80]}...")


# ── 结算编排 ────────────────────────────────────────────


async def safe_settle(user_id: str, history: list[HistoryMsg], bot_qq: str = "") -> None:
    """结算包装：捕获异常，防止后台任务崩溃"""
    try:
        await check_and_settle(user_id, history, bot_qq)
    except Exception as e:
        print(f"[Settle] 后台结算失败: {e}")


async def check_and_settle(
    user_id: str, history: list[HistoryMsg], bot_qq: str = ""
) -> None:
    """如果需要结算，取出未结算对话，摘要并存库，然后清空旧历史"""
    boundary = settlement_boundary()
    boundary_str = boundary.isoformat()

    settlements = _get_settlements()
    last = settlements.get(user_id, "")

    if last >= boundary_str:
        return  # 已结算

    # 1. 理性模型（SUMMARY_PROMPT）
    await summarize_and_store(history, user_id, bot_qq)
    # 2. 感性模型（DIARY_PROMPT）
    await update_emotional_memory(history)
    # 3. 情感模型（EMOTION_PROMPT）
    await update_emotions(history, user_id, bot_qq)
    # 4. 情绪基线调整
    adjust_mood_baseline()

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

# bot_qq 由 agent.py 在收到第一个 /chat 请求时填充
_bot_qq: str = ""


def set_bot_qq(qq: str) -> None:
    """由 agent.py 调用，设置 bot_qq 供 auto_settle_loop 使用。"""
    global _bot_qq
    _bot_qq = qq


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

        if not _bot_qq:
            print("[Settle] bot_qq 未设置，跳过自动结算")
            continue

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
                await check_and_settle(uid, history, _bot_qq)
            except Exception as e:
                print(f"[Settle] 自动结算 {uid} 失败: {e}")
